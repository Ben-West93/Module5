# exercises/jwt/app/routers/auth.py
# L8 — registration and login

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.exceptions import DuplicateException, UnauthorizedException
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.schemas.errors import ErrorResponse
from app.utils.db import commit_or_conflict
from app.utils.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    hash_password,
    verify_dummy_password,
    verify_password,
)

logger = logging.getLogger("jwt_api")

router = APIRouter()

DUPLICATE = {409: {"model": ErrorResponse, "description": "Username or email already registered"}}
UNAUTHORIZED = {401: {"model": ErrorResponse, "description": "Bad credentials"}}


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={**DUPLICATE},
)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Creates an account and returns a token, so the caller is logged in already.

    Username and email are checked separately so the message says which one
    is taken. That is a deliberate trade-off: it confirms to a stranger that
    an address is registered, but a registration form that just says
    "something is taken" is close to unusable.
    """
    existing = db.scalars(
        select(User).where(
            (User.username == payload.username) | (User.email == payload.email)
        )
    ).first()
    if existing is not None:
        field = "username" if existing.username == payload.username else "email"
        raise DuplicateException(f"That {field} is already registered")

    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    # Not a bare db.commit(): two simultaneous sign-ups for the same username
    # both pass the check above, and the loser must get a 409 rather than a 500.
    commit_or_conflict(db, "That username or email is already registered")
    db.refresh(user)

    # Never log the password, and never put it in a response.
    logger.info("Registered user %s (id=%s)", user.username, user.id)

    return TokenResponse(
        access_token=create_access_token(user),
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/login", response_model=TokenResponse, responses={**UNAUTHORIZED})
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Exchanges a username and password for a bearer token.

    An unknown username and a wrong password return exactly the same 401.
    Distinguishing them would let anyone enumerate which accounts exist.

    The password is still verified against a dummy hash when the user does
    not exist, so both paths take about the same time. Returning early would
    make a missing user measurably faster to reject, which leaks the same
    information the identical message was meant to hide.
    """
    user = db.scalars(select(User).where(User.username == payload.username)).first()

    if user is None:
        # Constant-ish work so timing does not reveal that the user is absent.
        verify_dummy_password(payload.password)
        raise UnauthorizedException("Incorrect username or password")

    if not verify_password(payload.password, user.hashed_password):
        logger.info("Failed login for %s", payload.username)
        raise UnauthorizedException("Incorrect username or password")

    return TokenResponse(
        access_token=create_access_token(user),
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
