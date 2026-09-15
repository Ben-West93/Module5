# exercises/auth-system/app/routers/auth.py
# L8 — Auth endpoints

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.user import User
from app.schemas.user import TokenResponse, UserCreate, UserResponse

router = APIRouter()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Username or email already registered"}},
)
def register(user: UserCreate, db: Session = Depends(get_db)):
    """Registers a new user."""
    # Compared case-insensitively so "Ada" can't be registered alongside "ada".
    # Emails arrive already lower-cased from UserCreate.
    existing = db.scalar(
        select(User).where(
            or_(
                func.lower(User.username) == user.username.lower(),
                User.email == user.email,
            )
        )
    )
    if existing is not None:
        field = "Username" if existing.username.lower() == user.username.lower() else "Email"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{field} is already registered",
        )

    new_user = User(
        username=user.username,
        email=user.email,
        hashed_password=hash_password(user.password),
    )
    db.add(new_user)

    try:
        db.commit()
    except IntegrityError:
        # The check above can lose a race with a concurrent registration; the
        # unique constraints are the real guarantee, so turn that into the same
        # 409 rather than a 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email is already registered",
        )

    db.refresh(new_user)
    # response_model=UserResponse strips everything not declared there, so the
    # hash cannot leak even though new_user carries it.
    return new_user


@router.post(
    "/token",
    response_model=TokenResponse,
    responses={401: {"description": "Incorrect username or password"}},
)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Authenticates user credentials and returns a JWT access token.

    OAuth2PasswordRequestForm parses a form-encoded body with fields 'username'
    and 'password' — the standard format that makes the Swagger UI 'Authorize'
    button work out of the box.
    """
    # Matched the same way registration checks for duplicates, so the casing
    # someone typed at signup isn't something they have to remember.
    username = form_data.username.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.username) == username))

    # Same error for "no such user" and "wrong password" so the endpoint can't
    # be used to enumerate valid usernames.
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(access_token=create_access_token({"sub": user.username}))


@router.get(
    "/me",
    response_model=UserResponse,
    responses={401: {"description": "Missing or invalid token"}},
)
def get_me(current_user: User = Depends(get_current_user)):
    """Returns the currently authenticated user.

    Depends(get_current_user) runs before this function. If the token is
    missing or invalid, get_current_user raises a 401 and this body never runs.
    """
    return current_user


@router.get("/dashboard", responses={401: {"description": "Missing or invalid token"}})
def get_dashboard(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A second protected endpoint — returns a small summary for the caller."""
    total_users = db.scalar(select(func.count()).select_from(User))
    return {
        "message": f"Welcome back, {current_user.username}",
        "user_id": current_user.id,
        "email": current_user.email,
        "registered_users": total_users,
    }
