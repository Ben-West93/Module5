# exercises/jwt/app/utils/security.py
# L8 — password hashing, tokens, and the current-user dependency

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.exceptions import UnauthorizedException
from app.models.user import User

logger = logging.getLogger("jwt_api")

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
#
# The signing key comes from the environment. The fallback exists so the
# exercise runs with no setup, and it is deliberately obvious: anyone holding
# this string can mint a token for any user. A real deployment sets SECRET_KEY
# and never ships a default.

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-insecure-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# bcrypt hashes at most 72 bytes and silently ignores the rest, so without
# this limit "<72 bytes><anything>" and "<72 bytes><something else>" would be
# the same password. Rejecting is better than silently truncating.
MAX_PASSWORD_BYTES = 72


def _build_password_context() -> CryptContext:
    """Prefer bcrypt, fall back to pbkdf2_sha256 if the backend is unusable.

    passlib 1.7.4 reads `bcrypt.__about__.__version__`, which bcrypt 4.1+
    removed. When that lookup fails passlib mis-detects the backend and
    rejects every password with a bogus "longer than 72 bytes" error — even a
    7-character one. requirements.txt pins bcrypt to a working version, but
    this probe means the app still starts if someone installs a newer one.

    Both schemes stay registered either way, so hashes created under one are
    still verifiable after a switch.
    """
    preferred = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated="auto")
    try:
        preferred.hash("backend-probe")
        return preferred
    except Exception as exc:  # pragma: no cover - depends on installed bcrypt
        logger.warning(
            "bcrypt backend unusable (%s); falling back to pbkdf2_sha256. "
            "Install the pinned bcrypt from requirements.txt to use bcrypt.",
            exc,
        )
        return CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")


pwd_context = _build_password_context()

# Used by the login path when a username does not exist — see
# verify_dummy_password() below.
_DUMMY_HASH = pwd_context.hash("not-a-real-password")

# auto_error=False so a missing header reaches our own code instead of
# raising FastAPI's 403 with its own body shape. Every auth failure should
# come back in the same envelope as every other error in this API.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Paste the access_token returned by POST /auth/login.",
)


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Hashes a plaintext password. Never store the plaintext anywhere."""
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return pwd_context.hash(password)


def verify_dummy_password(plain: str) -> None:
    """Burns roughly the same time a real verification would.

    Called on the login path when the username does not exist. Without it,
    a missing user is rejected immediately while a wrong password costs a
    full bcrypt round, and the timing difference tells an attacker which
    usernames are real — which is exactly what the identical error message
    was there to hide.

    The hash is generated at import from a real password rather than being a
    hand-written string: a made-up "$2b$12$xxxx..." has invalid padding bits
    and makes passlib emit a warning on every failed login.
    """
    verify_password(plain, _DUMMY_HASH)


def verify_password(plain: str, hashed: str) -> bool:
    """Checks a password against a stored hash.

    Returns False rather than raising on a malformed hash, so a corrupt row
    is a failed login rather than a 500.
    """
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        logger.warning("Password verification failed against a malformed hash")
        return False


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


def create_access_token(user: User, expires_delta: Optional[timedelta] = None) -> str:
    """Creates a signed JWT identifying `user`.

    `sub` is the user id as a string — the JWT spec requires a string, and
    python-jose will not complain if you pass an int, but other libraries
    will reject the token.
    """
    now = datetime.now(timezone.utc)
    expires = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Verifies a token's signature and expiry, or raises 401.

    Every failure mode — bad signature, expired, malformed, wrong algorithm —
    produces the same message on purpose. Telling a caller which one it was
    helps an attacker more than it helps a legitimate client.
    """
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        logger.info("Rejected token: %s", exc)
        raise UnauthorizedException("Invalid or expired token")


# --------------------------------------------------------------------------
# Dependency
# --------------------------------------------------------------------------


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolves the bearer token into the User it identifies, or raises 401.

    Add `Depends(get_current_user)` to any endpoint that should require a
    login. The user is re-read from the database on each request rather than
    trusted from the token body, so a deleted account stops working
    immediately instead of when its token happens to expire.
    """
    if credentials is None:
        raise UnauthorizedException("Not authenticated. Send an Authorization: Bearer <token> header.")

    payload = decode_access_token(credentials.credentials)

    subject = payload.get("sub")
    if subject is None:
        raise UnauthorizedException("Invalid or expired token")

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise UnauthorizedException("Invalid or expired token")

    user = db.scalars(select(User).where(User.id == user_id)).first()
    if user is None:
        raise UnauthorizedException("Invalid or expired token")

    return user
