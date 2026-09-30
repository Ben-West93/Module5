# exercises/security-basics/app/main.py
# L10 — Security Hardened API
#
# Run with: uvicorn app.main:app --reload  (from security-basics/ folder)
#
# ==========================================================================
# SECURITY SUMMARY
# ==========================================================================
#
# CORS            CORSMiddleware, below. Named origins only, never "*":
#                 React (localhost:3000) and Streamlit (localhost:8501), each
#                 also as 127.0.0.1. Methods GET and POST; headers
#                 Content-Type and Authorization. No credentials (cookies).
#                 Override the origins with CORS_ALLOWED_ORIGINS.
#
# Rate limiting   SlowAPI, per IP, one count per endpoint (app/limiter.py):
#                   POST /auth/login       5/minute   (password guessing)
#                   POST /items           20/minute   (create)
#                   POST /auth/register   20/minute   (create)
#                   GET  /items           60/minute   (list)
#                   GET  /auth/me, GET /  60/minute   (read)
#                 Over the limit: 429 with Retry-After. Moving window, so no
#                 burst at the minute boundary. Limits set in app/config.py.
#
# Input           ItemCreate sanitizes name and description (HTML tags and
# sanitization    surrounding whitespace removed) before anything is stored,
#                 against stored XSS. Every schema has length limits.
#                 app/routers/items.py
#
# SQL injection   Every query is parameterized (SQLAlchemy select() with bound
#                 values). Vulnerable vs safe example: the docstring of
#                 list_items() in app/routers/items.py.
#
# Passwords       bcrypt-hashed (cost 12); only the hash is stored. Login
#                 returns a signed JWT (HS256). app/security.py
#
# Errors          Every error is {"error", "detail", "status_code"}. A 500
#                 reveals nothing; a 422 on a login or register body never
#                 echoes the password back.
# ==========================================================================

import ipaddress
import json
import logging
import math
import os
import re
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from app.config import LIST_LIMIT
from app.database import Base, engine
from app.limiter import limiter
from app.models.item import Item  # noqa: F401 — registers the items table on Base
from app.models.user import User  # noqa: F401 — registers the users table on Base
from app.routers import auth, items

logger = logging.getLogger("security_api")

app = FastAPI(
    title="Security Basics API",
    description=(
        "Item endpoints behind CORS restricted to named origins, per-endpoint "
        "SlowAPI rate limits, and schema-level HTML sanitization, plus "
        "registration and a rate-limited login."
    ),
    version="2.0.0",
)

# SlowAPI finds the limiter here when it handles a request.
app.state.limiter = limiter

Base.metadata.create_all(bind=engine)


# --------------------------------------------------------------------------
# CORS configuration
# --------------------------------------------------------------------------


# The two frontends this API expects: a React dev server on port 3000, and
# Streamlit on port 8501 (added after reviewer feedback).
#
# Each is listed twice. localhost and 127.0.0.1 are the same server to a
# person and two different origins to a browser, which compares the scheme,
# host and port as strings. Listing only one is a common reason a local
# frontend "randomly" gets CORS errors depending on which URL was typed.
DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:3000",   # React
    "http://127.0.0.1:3000",
    "http://localhost:8501",   # Streamlit
    "http://127.0.0.1:8501",
)


_DEFAULT_PORTS = {"http": 80, "https": 443}
_HOST_NAME = re.compile(r"[a-z0-9._-]+")


def parse_allowed_origins(raw: str | None) -> list[str]:
    """Turns CORS_ALLOWED_ORIGINS (comma-separated) into a validated list.

    CORSMiddleware compares each browser's Origin header to this list as an
    exact string. So every entry has to be written exactly the way a browser
    writes an origin, or it silently never matches. Each rule below exists
    because the mistake it catches fails that way:

    - "*" is refused outright. That is the brief's rule, and in Starlette it
      is worse than it looks: with allow_credentials=True, a wildcard makes
      the middleware echo back WHATEVER origin asked, so every site on the
      internet is individually allowed. verify_security.py section [7]
      demonstrates it.
    - A "*" inside an origin ("https://*.example.com") is refused too.
      Starlette does not expand it, so the entry would never match.
    - "null" is refused. Sandboxed iframes and file:// pages send
      Origin: null, so allowing it allows every one of them at once.
    - A trailing slash or path is refused rather than guessed at. Browsers
      never send one, and a message gets the config file fixed too.
    - A default port is dropped. A browser at https://app.example.com:443
      sends "https://app.example.com", so ":443" would never match.
    - An empty port ("http://localhost:") and port 0 are refused. No browser
      can send either.
    - A non-ASCII domain is converted to the punycode form browsers send:
      "bücher.example" becomes "xn--bcher-kva.example".
    - An IPv6 address is written in its compressed form, as browsers do:
      "[0:0:0:0:0:0:0:1]" becomes "[::1]".
    - A host with characters no domain can contain (a space, say) is refused.
    - Scheme and host are lowercased, because browsers send them that way.

    The port, IDN, IPv6 and host rules were added after probing found entries
    that this function accepted but that could never match a real browser.
    """
    if raw is None or not raw.strip():
        return list(DEFAULT_ALLOWED_ORIGINS)

    origins: list[str] = []
    for entry in raw.split(","):
        origin = entry.strip()
        if not origin:
            continue
        if "*" in origin:
            raise ValueError(
                f"CORS_ALLOWED_ORIGINS may not contain a wildcard ({origin!r}); list each origin explicitly"
            )

        parts = urlsplit(origin)
        try:
            port = parts.port  # raises ValueError on a non-numeric or out-of-range port
        except ValueError:
            raise ValueError(f"CORS origin {origin!r} has an invalid port") from None
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(
                f"CORS origin {origin!r} must look like http://host or https://host:port"
            )
        if parts.path or parts.query or parts.fragment or "@" in parts.netloc:
            raise ValueError(
                f"CORS origin {origin!r} must be scheme://host[:port] with no path, "
                "query or trailing slash — browsers never send one"
            )
        if parts.netloc.endswith(":") or port == 0:
            raise ValueError(f"CORS origin {origin!r} has an empty or zero port")

        host = parts.hostname  # already lowercased, IPv6 brackets removed
        if ":" in host:
            try:
                host = f"[{ipaddress.IPv6Address(host).compressed}]"
            except ValueError:
                raise ValueError(f"CORS origin {origin!r} has an invalid IPv6 address") from None
        else:
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError:
                raise ValueError(f"CORS origin {origin!r} does not have a valid host name") from None
            if not _HOST_NAME.fullmatch(host):
                raise ValueError(f"CORS origin {origin!r} does not have a valid host name")

        normalized = f"{parts.scheme}://{host}"
        if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
            normalized += f":{port}"
        if normalized not in origins:
            origins.append(normalized)

    if not origins:
        raise ValueError("CORS_ALLOWED_ORIGINS was set but contained no origins")
    return origins


ALLOWED_ORIGINS = parse_allowed_origins(os.getenv("CORS_ALLOWED_ORIGINS"))


# --------------------------------------------------------------------------
# Error response format
# --------------------------------------------------------------------------
#
# Every error this API returns has the same three keys, as in L7:
#
#     {"error": "TooManyRequests", "detail": "...", "status_code": 429}
#
# SlowAPI raises RateLimitExceeded from inside the endpoint, so its 429 goes
# through handle_rate_limited() below and gets the same envelope.


def build_error_response(
    status_code: int,
    error: str,
    detail: str,
    headers: dict[str, str] | None = None,
    **extra,
) -> JSONResponse:
    """Single place where the error body is assembled."""
    payload = {"error": error, "detail": detail, "status_code": status_code}
    payload.update(extra)
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


# How much of an invalid value a 422 repeats back to the client.
MAX_ECHOED_INPUT = 200


def _json_safe_text(text: str) -> str:
    """Replaces lone surrogates, which JSON (as UTF-8) cannot carry."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _echoable_input(value):
    """A short, JSON-safe preview of the value that failed validation.

    Echoing the rejected value is useful, so a client can see what it sent.
    But Pydantic hands back the raw value, and several things a client can
    send cannot be turned back into JSON. Before this existed, each one made
    the 422 handler raise inside itself, and the client got a 500 instead:

    - a lone surrogate ("\\ud800"), which the stdlib JSON parser accepts
      but UTF-8 cannot encode
    - NaN or Infinity, which Python's parser accepts but JSON forbids
    - raw bytes, when the body was not JSON at all

    It was also unbounded: a 5 MB invalid name came back as a 5 MB error.
    Values are now previewed to MAX_ECHOED_INPUT characters. Lists and
    objects are previewed as their JSON text, since a partial structure is
    not valid JSON.
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)

    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError, RecursionError):
            text = f"<{type(value).__name__}>"

    text = _json_safe_text(text)
    if len(text) > MAX_ECHOED_INPUT:
        text = text[:MAX_ECHOED_INPUT] + "…"
    return text


def _json_safe_validation_errors(errors: list[dict], *, echo_input: bool = True) -> list[dict]:
    """Makes Pydantic's error list safe to put in a JSON response.

    - "ctx" holds the original exception object (e.g. the ValueError raised
      by the sanitizer's empty-name check). It is dropped.
    - "input" is replaced by a bounded, JSON-safe preview; see
      _echoable_input().
    - "loc" can contain strings that came from the client, so they get the
      same surrogate cleanup.

    - With `echo_input=False`, "input" is left out entirely. See
      CREDENTIAL_PATHS.

    verify_security.py sections [4], [5] and [17] send each of the bodies that
    used to break this.
    """
    cleaned = []
    for item in errors:
        entry = {key: value for key, value in item.items() if key not in ("ctx", "input")}
        if "input" in item and echo_input:
            entry["input"] = _echoable_input(item["input"])
        if "loc" in entry:
            entry["loc"] = [_json_safe_text(part) if isinstance(part, str) else part for part in entry["loc"]]
        cleaned.append(jsonable_encoder(entry))
    return cleaned


# Paths whose bodies contain a password. A 422 normally repeats the rejected
# value back, which is useful for an item name and wrong for a password: the
# response could end up in a log, a proxy cache or a screenshot. For a missing
# field, Pydantic's "input" is the WHOLE body, password included, so the
# value is dropped entirely rather than filtered by field name.
CREDENTIAL_PATHS = {"/auth/register", "/auth/login"}


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Schema-validation failures, including a name that sanitized to nothing."""
    errors = exc.errors()
    echo_input = request.url.path not in CREDENTIAL_PATHS
    noun = "error" if len(errors) == 1 else "errors"
    return build_error_response(
        422,
        "ValidationError",
        f"Request failed validation with {len(errors)} {noun}",
        errors=_json_safe_validation_errors(list(errors), echo_input=echo_input),
    )


_CANDIDATE_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


def allowed_methods_for(request: Request) -> list[str]:
    """Every method that some route would accept at this request's path.

    Starlette's own 405 names only the methods of the FIRST route whose path
    matches. GET /items and POST /items are two routes, so a DELETE to /items
    came back with "Allow: GET", telling the client POST was not allowed
    either.

    This asks the router, method by method, whether the same request would
    have fully matched. It uses only the public route.matches() rather than
    walking the route list's internals, because FastAPI has changed how
    included routers are stored between the versions requirements.txt allows.
    """
    return [
        method
        for method in _CANDIDATE_METHODS
        if any(
            route.matches(dict(request.scope, method=method))[0] == Match.FULL
            for route in request.app.router.routes
        )
    ]


@app.exception_handler(StarletteHTTPException)
def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Framework-level errors such as 404 on an unknown path or 405.

    exc.headers is passed through, and a 405 gets a complete Allow header.
    HTTP requires Allow on a 405, and it tells the client which methods would
    have worked. The first version of this handler dropped it entirely.
    """
    labels = {
        400: "BadRequest",
        401: "Unauthorized",
        404: "NotFound",
        405: "MethodNotAllowed",
        409: "Conflict",
    }
    headers = dict(exc.headers or {})
    if exc.status_code == 405:
        methods = allowed_methods_for(request)
        if methods:
            headers["Allow"] = ", ".join(methods)
    return build_error_response(
        exc.status_code,
        labels.get(exc.status_code, "Error"),
        str(exc.detail),
        headers=headers or None,
    )


@app.exception_handler(Exception)
def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """A bug. The body says nothing specific; the traceback goes to the log."""
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return build_error_response(
        500, "InternalServerError", "An unexpected error occurred. Please try again later."
    )


# --------------------------------------------------------------------------
# Rate limiting (SlowAPI)
# --------------------------------------------------------------------------
#
# The limits themselves are decorators on each endpoint:
#
#     @router.post("/login")
#     @limiter.limit(LOGIN_LIMIT)          # "5/minute"
#     def login(request: Request, ...):
#
# This replaced a hand-written middleware that kept a dict of timestamps per
# IP with one limit (10/minute) for every endpoint. The reviewer asked for
# SlowAPI with a different limit per kind of endpoint, and a single global
# limit of 10 would cap the 20 and 60 limits anyway, so they cannot both be
# in force. The properties the old limiter was built for are kept, and
# verify_security.py checks each one:
#
# - The window slides. limiter.py sets strategy="moving-window"; SlowAPI's
#   default fixed window would allow a burst either side of each minute.
#
# - Only requests that were let through are counted, so hammering during a
#   lockout does not extend it.
#
# - The client is the socket's peer address. X-Forwarded-For is ignored.
#
# - The 429 carries CORS headers. RateLimitExceeded is raised inside the
#   endpoint, so the response passes back out through CORSMiddleware.
#
# - Preflight OPTIONS requests are free. CORSMiddleware answers them itself,
#   before any endpoint (and so any limit) is reached.
#
# - The limit is checked before the endpoint body runs, so a POST refused
#   with 429 writes nothing.
#
# One thing changes. The old middleware counted every request, including
# 404s, 422s and /docs. SlowAPI counts requests that reach a limited
# endpoint: a body that fails validation is refused before the limit is
# checked, and so is not counted. It is also not processed any further, so
# it costs the server almost nothing; a wrong password, which does reach
# bcrypt, is counted.


def retry_after_seconds(reset_at: float, now: float) -> int:
    """Whole seconds to wait until a request will be allowed again.

    The moving window frees a slot only once the oldest request is MORE than
    a window old, so waiting exactly until `reset_at` is one instant too
    early. floor() + 1 is the first whole second strictly after it: a client
    that obeys Retry-After exactly never gets a second 429.
    """
    return max(1, math.floor(reset_at - now) + 1)


@app.exception_handler(RateLimitExceeded)
def handle_rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 in the standard envelope, with the headers a client needs."""
    limit_item, identifiers = request.state.view_rate_limit
    reset_at, _ = limiter.limiter.get_window_stats(limit_item, *identifiers)
    wait = retry_after_seconds(reset_at, time.time())
    return build_error_response(
        429,
        "TooManyRequests",
        f"Rate limit of {exc.detail} exceeded. Retry in {wait} second(s).",
        headers={
            "Retry-After": str(wait),
            "X-RateLimit-Limit": str(limit_item.amount),
            "X-RateLimit-Remaining": "0",
        },
    )


@app.middleware("http")
async def rate_limit_headers_and_errors(request: Request, call_next):
    """Two jobs that must happen INSIDE CORSMiddleware.

    1. Adds X-RateLimit-Limit and X-RateLimit-Remaining to every response
       from a rate-limited endpoint, including a 401 from a wrong password,
       so a client can see its budget before it runs out. (SlowAPI's own
       header injection is off; see app/limiter.py.)

    2. Turns an unhandled bug into the standard 500 here. Left alone, it
       would escape to Starlette's outermost error middleware, which sits
       OUTSIDE CORS, so the 500 would carry no Access-Control-Allow-Origin
       and a browser would report a CORS failure instead of a server error.
    """
    try:
        response = await call_next(request)
    except Exception as exc:
        response = handle_unexpected(request, exc)

    current = getattr(request.state, "view_rate_limit", None)
    if current is not None and response.status_code != 429:
        limit_item, identifiers = current
        _, remaining = limiter.limiter.get_window_stats(limit_item, *identifiers)
        response.headers["X-RateLimit-Limit"] = str(limit_item.amount)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
    return response


# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------
#
# Key concept: CORS (Cross-Origin Resource Sharing) controls which web origins
# a BROWSER will let read this API's responses. Two things it is not:
#
# - Not access control. A request from a disallowed origin still reaches the
#   server and still runs; the browser just refuses to hand the response to
#   the page. curl, scripts and other servers ignore CORS entirely.
#
# - Not protection for "simple" requests on its own. A browser sends a
#   form-style POST (Content-Type text/plain or form-encoded) cross-origin
#   WITHOUT asking first. What closes that door here is that every POST only
#   accepts application/json, and a JSON POST forces the browser to send a
#   preflight, which a disallowed origin fails.
#
# ORDER MATTERS. Starlette wraps middleware so that the one added LAST is
# OUTERMOST. The middleware above is registered first, so CORS wraps it and
# everything inside: 429s, 401s and 500s all carry CORS headers, and CORS
# answers preflights before any rate limit is reached.

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # False, not the starter's True. Logging in returns a token that the
    # frontend sends in the Authorization header; there are no cookies, so
    # there are no browser credentials to allow. Allowing them anyway would
    # widen what a listed origin can do for no benefit.
    allow_credentials=False,
    # Only the methods this API has. PUT and DELETE from the starter would
    # advertise support for requests that can only ever 405.
    allow_methods=["GET", "POST"],
    # Content-Type for JSON bodies; Authorization for the login token sent
    # to GET /auth/me.
    allow_headers=["Content-Type", "Authorization"],
    # A browser only lets page JavaScript read a short list of "safe" response
    # headers. Without this, a frontend would get the 429 but could not read
    # how long to wait.
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=600,
)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

RATE_LIMITED = {
    429: {
        "description": "Too many requests from this IP to this endpoint; see the Retry-After header",
        "content": {
            "application/json": {
                "example": {
                    "error": "TooManyRequests",
                    "detail": "Rate limit of 5 per 1 minute exceeded. Retry in 42 second(s).",
                    "status_code": 429,
                }
            }
        },
    }
}

# Declared on the routers so Swagger shows the 429. FastAPI cannot infer it:
# SlowAPI raises it from inside a decorator the route knows nothing about.
app.include_router(items.router, prefix="/items", tags=["items"], responses=RATE_LIMITED)
app.include_router(auth.router, prefix="/auth", tags=["auth"], responses=RATE_LIMITED)


@app.get("/", tags=["meta"], responses=RATE_LIMITED)
@limiter.limit(LIST_LIMIT)
def read_root(request: Request):
    return {"message": "Security Basics API — see /docs for the interactive UI"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
