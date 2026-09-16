# exercises/security-basics/app/main.py
# L10 — Security Hardened API
#
# Run with: uvicorn app.main:app --reload  (from security-basics/ folder)

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
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from app.database import Base, engine
from app.models.item import Item  # noqa: F401 — registers the items table on Base
from app.routers import items

logger = logging.getLogger("security_api")

app = FastAPI(
    title="Security Basics API",
    description=(
        "Item endpoints behind CORS restricted to named origins, a per-IP "
        "rate limit, and schema-level HTML sanitization."
    ),
    version="1.0.0",
)

Base.metadata.create_all(bind=engine)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def read_positive_number(name: str, default: float, *, integer: bool) -> float:
    """Reads a positive number from the environment, or fails at startup.

    Failing loudly is the point. RATE_LIMIT_REQUESTS=0 would reject every
    request, and a typo like "1O" silently falling back to the default would
    leave someone believing a limit is in force that is not. A server that
    refuses to start is a problem found in seconds; a wrong limit can go
    unnoticed until it matters.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        kind = "a whole number" if integer else "a number"
        raise ValueError(f"{name} must be {kind}, got {raw!r}") from None
    # isfinite() only applies to floats. Called on a very large int (a
    # 400-digit RATE_LIMIT_REQUESTS) it raises OverflowError, which crashed
    # startup with a traceback instead of this function's message.
    if value <= 0 or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{name} must be greater than zero, got {raw!r}")
    return value


# The two origins below are the same dev server to a person and two different
# origins to a browser, which compares the scheme, host and port as strings.
# Listing only one is a common reason a local frontend "randomly" gets CORS
# errors depending on which URL was typed.
DEFAULT_ALLOWED_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


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
RATE_LIMIT_REQUESTS = int(read_positive_number("RATE_LIMIT_REQUESTS", 10, integer=True))
RATE_LIMIT_WINDOW_SECONDS = read_positive_number("RATE_LIMIT_WINDOW_SECONDS", 60.0, integer=False)


# --------------------------------------------------------------------------
# Error response format
# --------------------------------------------------------------------------
#
# Every error this API returns has the same three keys, as in L7:
#
#     {"error": "TooManyRequests", "detail": "...", "status_code": 429}
#
# The rate limiter builds its 429 with build_error_response() directly rather
# than raising. Middleware sits OUTSIDE FastAPI's exception handling, so an
# HTTPException raised there would never reach the handlers below — it would
# surface as a 500.


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


def _json_safe_validation_errors(errors: list[dict]) -> list[dict]:
    """Makes Pydantic's error list safe to put in a JSON response.

    - "ctx" holds the original exception object (e.g. the ValueError raised
      by the sanitizer's empty-name check). It is dropped.
    - "input" is replaced by a bounded, JSON-safe preview; see
      _echoable_input().
    - "loc" can contain strings that came from the client, so they get the
      same surrogate cleanup.

    verify_security.py sections [4], [5] and [15] send each of the bodies that
    used to break this.
    """
    cleaned = []
    for item in errors:
        entry = {key: value for key, value in item.items() if key not in ("ctx", "input")}
        if "input" in item:
            entry["input"] = _echoable_input(item["input"])
        if "loc" in entry:
            entry["loc"] = [_json_safe_text(part) if isinstance(part, str) else part for part in entry["loc"]]
        cleaned.append(jsonable_encoder(entry))
    return cleaned


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Schema-validation failures, including a name that sanitized to nothing."""
    errors = exc.errors()
    noun = "error" if len(errors) == 1 else "errors"
    return build_error_response(
        422,
        "ValidationError",
        f"Request failed validation with {len(errors)} {noun}",
        errors=_json_safe_validation_errors(list(errors)),
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
    labels = {400: "BadRequest", 404: "NotFound", 405: "MethodNotAllowed"}
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
# Rate limiting
# --------------------------------------------------------------------------
#
# A sliding log: for each client IP, the timestamps of the requests it was
# served in the last RATE_LIMIT_WINDOW_SECONDS. A request is allowed if fewer
# than RATE_LIMIT_REQUESTS timestamps are still inside the window.
#
# Why a sliding log rather than a counter that resets every minute: a fixed
# window lets a client send 10 requests at 0:59 and 10 more at 1:00, which is
# 20 requests in two seconds while never breaking the rule. A sliding window
# never allows more than the limit in ANY 60-second span.
#
# Design decisions, each checked in verify_security.py:
#
# - Only SERVED requests are recorded. A rejected request does not add a
#   timestamp, so a client that keeps hammering during its lockout is not
#   locked out forever. The limit means exactly "10 served per rolling
#   minute", and Retry-After can say precisely when the next one will be.
#   It also bounds memory: no IP's list can ever hold more than 10 entries.
#
# - time.monotonic(), not time.time(). The wall clock can jump when NTP
#   corrects it. Jumping forward would free every client early; jumping back
#   would lock everyone out until the clock caught up. `clock` is a module
#   variable so the verification can move time forward without sleeping.
#
# - The client is identified by the socket's peer address. X-Forwarded-For
#   is ignored, because any client can send any value in it, and trusting it
#   would give an attacker a fresh budget per request just by changing a
#   header. Behind a real reverse proxy every request would appear to come
#   from the proxy; the fix there is uvicorn's --proxy-headers together with
#   --forwarded-allow-ips naming the proxy, which rewrites the peer address
#   only for requests that genuinely came through it.
#
# - No lock. The middleware is `async` and runs on the event loop, and the
#   read-filter-append sequence in check_rate_limit() contains no `await`, so
#   no other request can run in the middle of it. Section [13] sends 30
#   concurrent requests to a live server and gets exactly 10 through. (A sync
#   `def` middleware running in a thread pool would need a lock.)
#
# - IPs that have gone quiet are swept out once per window. Without the
#   sweep, the dict gains a key for every address that ever connected, which
#   is unbounded memory under a spread-out attack.

request_counts: dict[str, list[float]] = {}
clock = time.monotonic
_last_sweep: float | None = None


def client_ip(request: Request) -> str:
    """The peer address of the connection. See the note above on proxies."""
    # request.client is None when the server has no peer address to report,
    # e.g. over a Unix socket. All such requests share one bucket.
    return request.client.host if request.client else "unknown"


def check_rate_limit(ip: str, now: float) -> tuple[bool, int, float]:
    """Records a request for `ip` if it is within the limit.

    Returns (allowed, remaining, retry_after_seconds). `remaining` is how
    many more requests this IP may make right now; `retry_after_seconds` is
    how long until one frees up, and is 0.0 when the request was allowed.
    """
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    # Strictly greater: a request exactly one window old has left the window.
    recent = [stamp for stamp in request_counts.get(ip, ()) if stamp > window_start]

    if len(recent) >= RATE_LIMIT_REQUESTS:
        request_counts[ip] = recent
        # The oldest timestamp is the next one to leave the window.
        return False, 0, recent[0] + RATE_LIMIT_WINDOW_SECONDS - now

    recent.append(now)
    request_counts[ip] = recent
    return True, RATE_LIMIT_REQUESTS - len(recent), 0.0


def sweep_idle_clients(now: float) -> int:
    """Drops IPs with no requests inside the window. Returns how many."""
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    idle = [ip for ip, stamps in request_counts.items() if not stamps or stamps[-1] <= cutoff]
    for ip in idle:
        del request_counts[ip]
    return len(idle)


def reset_rate_limiter() -> None:
    """Forgets every client. For the verification script, not for endpoints."""
    global _last_sweep
    request_counts.clear()
    _last_sweep = None


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Returns 429 once an IP exceeds RATE_LIMIT_REQUESTS per window.

    Every request counts, including ones that end in a 404 or 422. A
    limit that only counted successful requests would do nothing against a
    client probing for endpoints or fuzzing a schema.
    """
    global _last_sweep
    now = clock()

    if _last_sweep is None or now - _last_sweep >= RATE_LIMIT_WINDOW_SECONDS:
        sweep_idle_clients(now)
        _last_sweep = now

    allowed, remaining, retry_after = check_rate_limit(client_ip(request), now)

    if not allowed:
        # Retry-After must be a whole number of seconds. Rounded UP, so a
        # client that obeys it exactly never arrives a fraction early and gets
        # a second 429.
        wait = max(1, math.ceil(retry_after))
        return build_error_response(
            429,
            "TooManyRequests",
            f"Rate limit of {RATE_LIMIT_REQUESTS} requests per "
            f"{RATE_LIMIT_WINDOW_SECONDS:g} seconds exceeded. Retry in {wait} second(s).",
            headers={
                "Retry-After": str(wait),
                "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                "X-RateLimit-Remaining": "0",
            },
        )

    try:
        response = await call_next(request)
    except Exception as exc:
        # An unhandled bug in an endpoint. Left alone, it would propagate to
        # Starlette's outermost error middleware, which sits OUTSIDE CORS, so
        # the 500 would carry no Access-Control-Allow-Origin. A browser then
        # reports a CORS failure rather than a server error, and whoever is
        # debugging goes looking at the CORS config. Building the 500 here,
        # inside CORS, fixes that, and it gets the rate-limit headers too.
        # Same body and same logging as handle_unexpected(), which still
        # covers anything raised outside this middleware.
        response = handle_unexpected(request, exc)

    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_REQUESTS)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
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
#   the page. curl, scripts and other servers ignore CORS entirely. Section
#   [6] shows a disallowed origin's GET being served in full.
#
# - Not protection for "simple" requests on its own. A browser sends a
#   form-style POST (Content-Type text/plain or form-encoded) cross-origin
#   WITHOUT asking first. What closes that door here is that POST /items only
#   accepts application/json, and a JSON POST forces the browser to send a
#   preflight, which a disallowed origin fails. Section [4] asserts that a
#   text/plain body is refused.
#
# ORDER MATTERS. Starlette wraps middleware so that the one added LAST is
# OUTERMOST. The rate limiter is registered above this line, so CORS wraps
# it, and that decides two things:
#
# 1. A 429 still carries Access-Control-Allow-Origin. With the order reversed,
#    the limiter would answer before CORS ever saw the response, the browser
#    would treat the 429 as a CORS failure, and the frontend could not read
#    the status code or Retry-After — it would just see a network error.
#
# 2. CORS answers preflight OPTIONS requests itself, without calling the
#    app, so preflights do not spend the rate-limit budget. A browser sends a
#    preflight before every JSON POST (until Access-Control-Max-Age expires),
#    so counting them would halve a real user's allowance. The trade-off is
#    that preflights are not limited at all; they touch no database and cost
#    little, but it is recorded as a limitation in the README.

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # False, not the starter's True. This API sets no cookies and has no
    # login, so there are no credentials for a browser to send. Allowing them
    # anyway would widen what a listed origin can do for no benefit. Flip it
    # when the API gains cookie-based auth, and not before.
    allow_credentials=False,
    # Only the methods this API has. PUT and DELETE from the starter would
    # advertise support for requests that can only ever 405.
    allow_methods=["GET", "POST"],
    # Content-Type is needed for a JSON POST. Authorization is left out
    # because this API has no auth header to read.
    allow_headers=["Content-Type"],
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
        "description": "Too many requests from this IP; see the Retry-After header",
        "content": {
            "application/json": {
                "example": {
                    "error": "TooManyRequests",
                    "detail": "Rate limit of 10 requests per 60 seconds exceeded. Retry in 42 second(s).",
                    "status_code": 429,
                }
            }
        },
    }
}

# Declared on the router so Swagger shows the 429. FastAPI cannot infer it:
# the response comes from middleware, which the route knows nothing about.
app.include_router(items.router, prefix="/items", tags=["items"], responses=RATE_LIMITED)


@app.get("/", tags=["meta"], responses=RATE_LIMITED)
def read_root():
    return {"message": "Security Basics API — see /docs for the interactive UI"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
