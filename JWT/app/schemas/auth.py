# exercises/jwt/app/schemas/auth.py
# L8 — Pydantic schemas for registration, login, and tokens

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.security import MAX_PASSWORD_BYTES


def _check_password_bytes(value: str) -> str:
    """bcrypt hashes at most 72 bytes and ignores the rest.

    The check is on the encoded byte length, not len(): "é" is one character
    but two bytes, so a password of 70 accented characters is 140 bytes and
    would be silently truncated.
    """
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return value


def _normalize_identifier(value: str) -> str:
    """Trims and lowercases a username or email.

    Without this, "alice", "ALICE" and "  alice  " are three separate
    accounts. That is not just untidy: an account named "ALICE" is a
    convincing impersonation of "alice", and a user who signs up with
    "Alice@example.com" cannot then log in as "alice@example.com".

    Normalizing on the way in means the unique index does the rest of the
    work — there is no second code path that could forget to do it.
    """
    return value.strip().lower()


class RegisterRequest(BaseModel):
    """Body for POST /auth/register."""

    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., max_length=200)
    password: str = Field(..., min_length=8, description="8 characters minimum, 72 bytes maximum")

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        value = _normalize_identifier(value)
        if "@" not in value:
            raise ValueError("email must contain @")
        return value

    _normalize_username = field_validator("username")(_normalize_identifier)

    # Passwords are NOT normalized: case is meaningful in a password, and
    # trimming one would silently change what the user typed.
    _check_password = field_validator("password")(_check_password_bytes)


class LoginRequest(BaseModel):
    """Body for POST /auth/login."""

    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1)

    # Same normalization as registration, so logging in as "Alice" finds the
    # account registered as "alice". Applied here rather than in the router
    # so the two can never drift apart.
    _normalize_username = field_validator("username")(_normalize_identifier)


class TokenResponse(BaseModel):
    """Returned by POST /auth/login and POST /auth/register.

    `token_type` is "bearer" to match the Authorization header the client
    then sends: `Authorization: Bearer <access_token>`.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Seconds until the token expires")


class UserResponse(BaseModel):
    """Public view of a user — note there is no password field of any kind."""

    id: int
    username: str
    email: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
