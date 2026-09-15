# exercises/auth-system/app/auth.py
# L8 — JWT utility functions

import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User

# ── Config ─────────────────────────────────────────────────────────────────────
# In production, SECRET_KEY should come from an environment variable.
# Never hard-code secrets in real code! The default below exists only so the
# exercise runs out of the box; set AUTH_SECRET_KEY to override it.
SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# bcrypt is deliberately slow, which is what makes brute-forcing a stolen
# password table expensive. deprecated="auto" lets passlib flag hashes made
# with an older scheme if we ever add one.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTPBearer extracts the token from the "Authorization: Bearer <token>" header.
# Swagger UI will show a single "Value" field to paste your token directly.
http_bearer = HTTPBearer()

# One shared 401 for every authentication failure. Returning the same generic
# message regardless of *why* validation failed avoids telling an attacker
# whether a username exists, whether a token expired, or whether it was forged.
credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def hash_password(password: str) -> str:
    """Returns the bcrypt hash of the given plain-text password.

    UserCreate already rejects NULL bytes and anything over 72 bytes, but this
    function is public, so it checks again rather than letting passlib raise an
    unhandled error and turn into a 500.
    """
    if "\x00" in password:
        raise ValueError("password must not contain NULL bytes")
    if len(password.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes (bcrypt ignores the rest)")
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Returns True if plain matches the hashed password."""
    # No stored password can exceed 72 bytes (UserCreate enforces it), and
    # bcrypt only compares the first 72 bytes — so without this check, any
    # longer string sharing that prefix would authenticate.
    if len(plain.encode("utf-8")) > 72 or "\x00" in plain:
        return False
    # A malformed or empty hash makes passlib raise rather than return False,
    # so a corrupt row can't turn into a 500 on the login path.
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Creates a signed JWT access token.

    The payload (claims) is readable by anyone holding the token — only the
    signature requires SECRET_KEY. Never put secrets in it. The "exp" claim
    makes the token stop being accepted on its own.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(http_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Dependency that extracts and validates the JWT, returning the current user.

    FastAPI runs this before the endpoint body. If it raises, the endpoint
    never executes and the error is returned instead.
    """
    token = credentials.credentials

    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            # Without require_exp, a token minted with no expiry claim would be
            # accepted forever. Fail closed instead.
            options={"require_exp": True},
        )
    except JWTError:
        # Covers a bad signature, a malformed token and an expired one.
        raise credentials_exception

    username = payload.get("sub")
    if not username:
        raise credentials_exception

    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        # The signature was valid but the account is gone — e.g. deleted after
        # the token was issued. Still a 401, not a 404.
        raise credentials_exception

    return user
