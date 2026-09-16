# exercises/polished-docs/app/main.py
# L12 — Polished Documentation (built on L11 Test Suite and L10 Security Basics)
#
# Run with: uvicorn app.main:app --reload  (from polished-docs/ folder)
# Docs at:  http://127.0.0.1:8000/docs
#
# One app for the whole module so far:
#   L10  CORS for named origins, a per-IP rate limit, sanitized input
#   L11  the /tasks router, tested by tests/
#   L12  API metadata, tag descriptions and documented responses (this file,
#        app/routers/items.py, app/schemas/item.py)
#
# Middleware, innermost first (Starlette makes the last one added outermost):
#   BodySizeLimit  -> 413 for bodies over 64 KB, before they fill memory
#   ServerError    -> 500 in the envelope, INSIDE CORS and the limiter
#   HeadAsGet      -> HEAD answered like GET, without a body
#   RateLimit      -> 429 once an IP has used its budget
#   CORS           -> outermost: every response, 429 included, is readable by
#                     an allowed browser origin, and preflights cost nothing

import ipaddress
import json
import logging
import math
import os
import re
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from app.database import Base, engine
from app.models import item as _item_model, task as _task_model  # noqa: F401  (registers the tables)
from app.routers import items, tasks
from app.schemas.error import ErrorResponse

logger = logging.getLogger("security_api")

# ============================================================================
# Settings, validated at import so a bad value stops the server with a message
# ============================================================================

DEFAULT_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LABEL = re.compile(r"[a-z0-9-]{1,63}")


def _normalize_origin(entry: str) -> str:
    """Rewrites an origin the way a browser sends it, or raises ValueError.

    CORSMiddleware compares origins as exact strings, so an entry that is not
    written exactly like the browser's Origin header silently never matches.
    """
    if "*" in entry or entry.lower() == "null":
        raise ValueError(f"CORS origin {entry!r}: wildcards and 'null' are not allowed; name each origin")
    parts = urlsplit(entry)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.netloc:
        raise ValueError(f"CORS origin {entry!r}: must be http:// or https:// followed by a host")
    if parts.path or parts.query or parts.fragment or entry.endswith(("?", "#")):
        raise ValueError(f"CORS origin {entry!r}: must not have a path or trailing slash")
    netloc = parts.netloc
    if "@" in netloc:
        raise ValueError(f"CORS origin {entry!r}: must not contain user info")

    if netloc.startswith("["):
        host, sep, rest = netloc[1:].partition("]")
        if not sep:
            raise ValueError(f"CORS origin {entry!r}: unclosed IPv6 address")
        try:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        except ValueError:
            raise ValueError(f"CORS origin {entry!r}: invalid IPv6 address") from None
        if rest and not rest.startswith(":"):
            raise ValueError(f"CORS origin {entry!r}: invalid host")
        port_text = rest[1:] if rest else None
    else:
        host, sep, port_text = netloc.partition(":")
        port_text = port_text if sep else None
        host = host.lower()
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError(f"CORS origin {entry!r}: invalid host") from None
        if not all(_LABEL.fullmatch(label) for label in host.split(".")):
            raise ValueError(f"CORS origin {entry!r}: invalid host")

    port = None
    if port_text is not None:
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise ValueError(f"CORS origin {entry!r}: port must be a number from 1 to 65535")
        port = int(port_text)
    if port == _DEFAULT_PORTS[scheme]:
        port = None
    return f"{scheme}://{host}" + (f":{port}" if port else "")


def parse_allowed_origins(raw: str | None) -> list[str]:
    """Parses CORS_ALLOWED_ORIGINS: comma-separated, normalized, de-duplicated."""
    if raw is None or not raw.strip():
        raw = DEFAULT_ORIGINS
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not entries:
        raise ValueError("CORS_ALLOWED_ORIGINS contains no origins")
    origins: list[str] = []
    for entry in entries:
        origin = _normalize_origin(entry)
        if origin not in origins:
            origins.append(origin)
    return origins


def read_positive_number(name: str, default, *, integer: bool):
    """Reads a positive number from the environment, or raises ValueError."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    raw = raw.strip()
    if integer:
        if not raw.isascii() or not raw.isdigit() or int(raw) <= 0:
            raise ValueError(f"{name} must be a positive whole number, got {raw!r}")
        return int(raw)
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive number, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number, got {raw!r}")
    return value


ALLOWED_ORIGINS = parse_allowed_origins(os.environ.get("CORS_ALLOWED_ORIGINS"))
RATE_LIMIT_REQUESTS = read_positive_number("RATE_LIMIT_REQUESTS", 10, integer=True)
RATE_LIMIT_WINDOW_SECONDS = read_positive_number("RATE_LIMIT_WINDOW_SECONDS", 60.0, integer=False)
MAX_BODY_BYTES = 64 * 1024

# ============================================================================
# Error envelope
# ============================================================================

ERROR_LABELS = {
    400: "BadRequest",
    404: "NotFound",
    405: "MethodNotAllowed",
    409: "Conflict",
    413: "PayloadTooLarge",
    422: "ValidationError",
    429: "TooManyRequests",
    500: "InternalServerError",
}


def error_response(status_code: int, detail: str, headers: dict | None = None, **extra) -> JSONResponse:
    body = {"error": ERROR_LABELS.get(status_code, "Error"), "detail": detail, "status_code": status_code, **extra}
    return JSONResponse(body, status_code=status_code, headers=headers)


# ============================================================================
# Rate limiter: a sliding log of request times per client IP
# ============================================================================

clock = time.monotonic  # replaced by a fake clock in verify_security.py
request_counts: dict[str, list[float]] = {}
_last_sweep = clock()


def reset_rate_limiter() -> None:
    global _last_sweep
    request_counts.clear()
    _last_sweep = clock()


class RateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        global _last_sweep
        limit, window = RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS
        now = clock()
        cutoff = now - window

        # Forget idle IPs once per window, so the dict does not grow forever.
        if now - _last_sweep >= window:
            for idle in [ip for ip, stamps in request_counts.items() if not stamps or stamps[-1] <= cutoff]:
                del request_counts[idle]
            _last_sweep = now

        # The socket peer, never X-Forwarded-For, which any client can set.
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        stamps = [t for t in request_counts.get(ip, ()) if t > cutoff]

        # No await between the check and the append, so nothing interleaves.
        if len(stamps) >= limit:
            request_counts[ip] = stamps  # refused requests are not recorded
            retry_after = max(1, math.ceil(stamps[len(stamps) - limit] + window - now))
            response = error_response(
                429,
                f"Rate limit exceeded: {limit} requests per {window:g} seconds. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after), "X-RateLimit-Limit": str(limit), "X-RateLimit-Remaining": "0"},
            )
            await response(scope, receive, send)
            return

        stamps.append(now)
        request_counts[ip] = stamps
        remaining = str(max(0, limit - len(stamps)))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-RateLimit-Limit"] = str(limit)
                headers["X-RateLimit-Remaining"] = remaining
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ============================================================================
# Body size limit, HEAD support, 500 envelope
# ============================================================================

TOO_LARGE = f"Request body is larger than the {MAX_BODY_BYTES // 1024} KB limit"


class BodySizeLimitMiddleware:
    """Refuses bodies over max_bytes: up front from Content-Length, or while reading."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await error_response(413, TOO_LARGE)(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # FastAPI re-raises HTTPException from body reading, so this
                    # reaches the handler below as a 413 rather than a 400.
                    raise StarletteHTTPException(status_code=413, detail=TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)


class HeadAsGetMiddleware:
    """Answers HEAD with GET's status and headers and no body.

    Middleware rather than extra routes, so OpenAPI gains no "head" operations.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def send_without_body(message):
            if message["type"] == "http.response.body":
                if message.get("more_body", False):
                    return
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


class ServerErrorMiddleware:
    """Turns an unhandled exception into the 500 envelope inside CORS and the
    limiter. Starlette's own handler sits outside every middleware, so its 500
    would carry no CORS headers and a browser would report a CORS failure."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            await error_response(500, "An unexpected error occurred")(scope, receive, send)


# ============================================================================
# The app (L12 metadata)
# ============================================================================

RATE_LIMITED = {
    429: {
        "model": ErrorResponse,
        "description": f"Too many requests: the caller's IP has used its budget. Wait `Retry-After` seconds.",
        "headers": {"Retry-After": {"description": "Seconds until a request will be accepted", "schema": {"type": "integer"}}},
    }
}

DESCRIPTION = """
Manage **tasks** and **inventory items** from one API.

## What you can do

* **Tasks**: create, list, read, partially update and delete to-do tasks.
* **Items**: create, list (with `name` and `category` filters) and read inventory items.

## Rules that apply to every endpoint

* **Rate limit:** 10 requests per 60 seconds per IP, shared across all endpoints.
  Every response has `X-RateLimit-Limit` and `X-RateLimit-Remaining`; a **429**
  has `Retry-After`. Loading this page itself uses two requests.
* **Errors** always use one envelope:
  `{"error": "NotFound", "detail": "Task 7 not found", "status_code": 404}`.
* **Input** is sanitized: HTML tags are stripped from text fields, and unknown
  fields are refused with **422**.
* **Bodies** larger than 64 KB are refused with **413**.
* **CORS** allows only the configured origins (`CORS_ALLOWED_ORIGINS`).
"""

openapi_tags = [
    {
        "name": "tasks",
        "description": "Manage to-do tasks. Titles are unique after sanitizing, and ids are never reused.",
    },
    {
        "name": "items",
        "description": "Manage inventory items. Each item has a unique **name**, a **price** above 0 and a **category**.",
    },
    {"name": "meta", "description": "Information about the API itself."},
]

app = FastAPI(
    title="Tasks and Inventory API",
    description=DESCRIPTION,
    version="1.2.0",
    contact={"name": "Ben", "email": "bmw885961@outlook.com"},
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
    openapi_tags=openapi_tags,
)

Base.metadata.create_all(bind=engine)

# ---- exception handlers ------------------------------------------------------

_METHOD_ORDER = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


def allowed_methods(scope) -> str:
    """Every method any route on this path accepts. Starlette's own Allow
    header names only the first matching route's methods."""
    methods = {
        method
        for method in _METHOD_ORDER
        if any(route.matches({**scope, "method": method})[0] == Match.FULL for route in app.router.routes)
    }
    if "GET" in methods:
        methods.add("HEAD")
    return ", ".join(m for m in _METHOD_ORDER if m in methods)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    headers = dict(exc.headers or {})
    if exc.status_code == 405:
        headers["Allow"] = allowed_methods(request.scope)
    detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
    return error_response(exc.status_code, detail, headers)


_PREVIEW = 200


def _safe_text(text: str) -> str:
    """Valid UTF-8, at most 200 characters plus an ellipsis."""
    text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return text if len(text) <= _PREVIEW else text[:_PREVIEW] + "…"


def _json_safe(value, depth: int = 0):
    """A bounded copy of an invalid input that JSON output can always carry
    (no lone surrogates, NaN, Infinity or bytes; at most 3 levels, 20 entries)."""
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, bytes):
        return _safe_text(value.decode("utf-8", "backslashreplace"))
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value if not isinstance(value, int) or abs(value) < 10**18 else _safe_text(str(value))
    if depth >= 3:
        return "…"
    if isinstance(value, dict):
        return {_safe_text(str(k)): _json_safe(v, depth + 1) for k, v in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, depth + 1) for v in list(value)[:20]]
    return _safe_text(str(value))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors()[:20]:
        entry = {
            "type": err.get("type"),
            "loc": [part if isinstance(part, int) else _safe_text(str(part)) for part in err.get("loc", ())],
            "msg": _safe_text(str(err.get("msg", ""))),
        }
        if "input" in err:
            entry["input"] = _json_safe(err["input"])
        errors.append(entry)
    first = errors[0] if errors else {"loc": [], "msg": "Invalid request"}
    detail = f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}" if first["loc"] else first["msg"]
    return error_response(422, detail, errors=errors)


# ---- routes ------------------------------------------------------------------


@app.get("/", tags=["meta"], summary="API index", responses=RATE_LIMITED)
def root():
    """Points to the interactive documentation."""
    return {"message": "Tasks and Inventory API. Interactive docs at /docs, schema at /openapi.json."}


app.include_router(tasks.router, prefix="/tasks", tags=["tasks"], responses=RATE_LIMITED)
app.include_router(items.router, prefix="/items", tags=["items"], responses=RATE_LIMITED)

# ---- middleware (added innermost first) ---------------------------------------

app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)
app.add_middleware(ServerErrorMiddleware)
app.add_middleware(HeadAsGetMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=600,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
