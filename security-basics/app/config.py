# exercises/security-basics/app/config.py
# L10 — Settings read from the environment
#
# Every setting is validated when the app starts. A value that is wrong stops
# the server with a message, instead of running with a limit or a secret that
# silently does not do what the environment says it does.

import logging
import math
import os
import secrets

from limits import parse as parse_limit

logger = logging.getLogger("security_api")


def read_positive_number(name: str, default: float, *, integer: bool) -> float:
    """Reads a positive number from the environment, or fails at startup.

    A typo like "3O" silently falling back to the default would leave someone
    believing a setting is in force that is not. A server that refuses to
    start is a problem found in seconds.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        kind = "a whole number" if integer else "a number"
        raise ValueError(f"{name} must be {kind}, got {raw!r}") from None
    # isfinite() only applies to floats. Called on a very large int it raises
    # OverflowError, which would crash startup with a traceback instead.
    if value <= 0 or (isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{name} must be greater than zero, got {raw!r}")
    return value


# --------------------------------------------------------------------------
# Rate limits (SlowAPI syntax: "<count>/<period>")
# --------------------------------------------------------------------------
#
# One limit per kind of endpoint, as the reviewer asked:
#
#   login         5/minute   POST /auth/login           (password guessing)
#   create       20/minute   POST /items, POST /auth/register
#   list / read  60/minute   GET /items, GET /auth/me, GET /
#
# Login is the tightest because each attempt is a password guess. Every
# endpoint keeps its own count, so exhausting one does not block the others.


def read_rate_limit(name: str, default: str) -> str:
    """Reads a SlowAPI limit string, or fails at startup.

    SlowAPI only parses the string on the first request, and logs and IGNORES
    one it cannot parse, so "5/fortnight" would leave login unlimited without
    an error. Parsing here turns that into a startup failure.
    """
    raw = os.getenv(name)
    value = default if raw is None or not raw.strip() else raw.strip()
    try:
        item = parse_limit(value)
    except ValueError:
        raise ValueError(f"{name} must look like '5/minute', got {value!r}") from None
    if item.amount <= 0:
        raise ValueError(f"{name} must allow at least one request, got {value!r}")
    return value


LOGIN_LIMIT = read_rate_limit("RATE_LIMIT_LOGIN", "5/minute")
CREATE_LIMIT = read_rate_limit("RATE_LIMIT_CREATE", "20/minute")
LIST_LIMIT = read_rate_limit("RATE_LIMIT_LIST", "60/minute")


# --------------------------------------------------------------------------
# Login tokens
# --------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(read_positive_number("JWT_EXPIRE_MINUTES", 30, integer=True))

# HS256 is only as strong as its key. 32 bytes is the length RFC 7518 asks for.
MIN_SECRET_LENGTH = 32


def read_jwt_secret() -> str:
    """The signing key for login tokens.

    Unset, a random key is generated for this process. That is safe (nobody
    can guess it) but tokens stop working when the server restarts, which is
    fine for development and wrong for production, so it is logged.
    """
    raw = os.getenv("JWT_SECRET_KEY")
    if raw is None or not raw.strip():
        logger.warning(
            "JWT_SECRET_KEY is not set; using a random key for this process. "
            "Login tokens will stop working when the server restarts."
        )
        return secrets.token_urlsafe(48)
    if len(raw.encode("utf-8")) < MIN_SECRET_LENGTH:
        raise ValueError(f"JWT_SECRET_KEY must be at least {MIN_SECRET_LENGTH} bytes long")
    return raw


JWT_SECRET_KEY = read_jwt_secret()
