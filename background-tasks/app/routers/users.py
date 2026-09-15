# exercises/background-tasks/app/routers/users.py
# L8 — the current user's own profile

from fastapi import APIRouter, Depends

from app.models.user import User
from app.schemas.auth import UserResponse
from app.schemas.errors import ErrorResponse
from app.utils.security import get_current_user

router = APIRouter()


@router.get(
    "/me",
    response_model=UserResponse,
    responses={401: {"model": ErrorResponse, "description": "Missing or invalid token"}},
)
def read_current_user(current_user: User = Depends(get_current_user)):
    """Returns the profile of whoever the bearer token identifies.

    There is no user id in the path: the caller cannot ask for someone
    else's profile, because the only user this endpoint can return is the one
    the token resolves to.

    The response model is UserResponse, which has no password field — so even
    if the ORM object carries the hash, it cannot reach the client.
    """
    return current_user
