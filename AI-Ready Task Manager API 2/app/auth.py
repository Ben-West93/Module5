# project/starter/app/auth.py
# Module 5 Project — JWT and password utilities

from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from app.database import get_db
from app.models.user import User
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# ---------- Custom exceptions ----------
# Routers raise these instead of FastAPI's HTTPException. The global handler
# in main.py turns every one of them into the same JSON error format.
class AppException(Exception):
    """Base class for all custom API errors."""

    status_code = 500
    error = "server_error"

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class NotFoundException(AppException):
    """Raised when a requested resource does not exist (or isn't the user's)."""

    status_code = 404
    error = "not_found"


class DuplicateException(AppException):
    """Raised when creating a resource that must be unique already exists."""

    status_code = 400
    error = "duplicate"


class InvalidUpdateException(AppException):
    """Raised when an update request has no usable fields or invalid nulls."""

    status_code = 400
    error = "invalid_update"


# ---------- Password hashing and JWT ----------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# auto_error=False so we can return a consistent 401 (HTTPBearer defaults to 403)
http_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if the plain password matches the stored hash."""
    try:
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        # Malformed hash or invalid input — treat as a failed match, never crash
        return False


def create_access_token(data: dict) -> str:
    """Create a signed JWT that expires after ACCESS_TOKEN_EXPIRE_MINUTES."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(http_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Extract the bearer token, decode the JWT, and return the matching user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or not credentials.credentials:
        raise credentials_exception

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    return user
