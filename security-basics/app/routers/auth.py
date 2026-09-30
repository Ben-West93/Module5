# exercises/security-basics/app/routers/auth.py
# L10 — Registration and login
#
# Added after reviewer feedback, which asked for a login endpoint limited to
# 5 requests per minute and a test showing the sixth attempt refused. The
# login is real: bcrypt-hashed passwords in a users table, and a signed JWT
# for a correct password. The item endpoints stay public; the token is what a
# successful login returns, and GET /auth/me shows it works.

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import CREATE_LIMIT, JWT_EXPIRE_MINUTES, LIST_LIMIT, LOGIN_LIMIT
from app.database import get_db
from app.limiter import limiter
from app.models.user import User
from app.security import (
    DUMMY_HASH,
    MAX_PASSWORD_BYTES,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

router = APIRouter()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
#
# Usernames are restricted to letters, digits and . _ - so there is nothing
# in one to sanitize: no tags, no look-alike characters from other scripts,
# no invisible characters. Passwords are NOT sanitized or stripped. Removing
# "<b>" or a trailing space from a password would silently change it, and a
# password is never displayed, so it cannot carry stored XSS.

USERNAME_PATTERN = r"^[A-Za-z0-9._-]+$"


def _password_fits_bcrypt(value: str) -> str:
    # max_length counts characters; bcrypt counts bytes. 30 emoji are 30
    # characters and 120 bytes.
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return value


class UserCreate(BaseModel):
    """Body for POST /auth/register."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=3, max_length=30, pattern=USERNAME_PATTERN)
    password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_BYTES)

    @field_validator("username")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.lower()

    @field_validator("password")
    @classmethod
    def _password_bytes(cls, value: str) -> str:
        return _password_fits_bcrypt(value)


class LoginRequest(BaseModel):
    """Body for POST /auth/login.

    The same length limits as registration, so no request can make the
    server hash an unbounded password. The pattern is not applied: a
    malformed username simply matches no account, the same as any other
    wrong username.
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=30)
    password: str = Field(..., min_length=1, max_length=MAX_PASSWORD_BYTES)

    @field_validator("password")
    @classmethod
    def _password_bytes(cls, value: str) -> str:
        return _password_fits_bcrypt(value)


class UserResponse(BaseModel):
    """An account as the API shows it. The password hash is never included."""

    id: int
    username: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
#
# Each decorated endpoint needs a `request: Request` parameter: SlowAPI reads
# the client address from it. @limiter.limit must sit BELOW @router.post, so
# FastAPI registers the rate-limited function rather than the bare one.


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(CREATE_LIMIT)
def register(request: Request, body: UserCreate, db: Session = Depends(get_db)):
    """Creates an account. Limited as a create endpoint (20 per minute)."""
    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # The unique index is the real check. Looking the name up first
        # would leave a gap in which two simultaneous registrations both see
        # it free; the database cannot be raced.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken") from None
    db.refresh(user)
    return user


# One message for an unknown username and a wrong password, so a failed
# login does not reveal which usernames exist.
BAD_CREDENTIALS = "Incorrect username or password"


@router.post("/login", response_model=TokenResponse)
@limiter.limit(LOGIN_LIMIT)
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """
    Exchanges a username and password for an access token.

    Rate limited to 5 attempts per minute per IP. Every attempt counts,
    right or wrong: the limit exists to slow down password guessing, and a
    limit that only counted failures would still allow unlimited guesses
    from someone who also logs in successfully now and then.
    """
    user = db.scalar(select(User).where(User.username == body.username.lower()))

    # Always run bcrypt, even for an unknown user, so both failures take the
    # same time. See DUMMY_HASH in app/security.py.
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not password_ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            BAD_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(access_token=create_access_token(user.id), expires_in=JWT_EXPIRE_MINUTES * 60)


bearer = HTTPBearer(auto_error=False)


@router.get("/me", response_model=UserResponse)
@limiter.limit(LIST_LIMIT)
def read_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    """The account a token belongs to. Send it as `Authorization: Bearer <token>`."""
    user_id = decode_access_token(credentials.credentials) if credentials else None
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing, invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
