# exercises/auth-system/app/schemas/user.py
# L8 — User schemas

import unicodedata

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# bcrypt reads at most 72 bytes of a password and silently ignores the rest.
BCRYPT_MAX_BYTES = 72


class UserCreate(BaseModel):
    """Schema for user registration."""

    username: str = Field(min_length=3, max_length=50, examples=["ada"])
    email: EmailStr = Field(examples=["ada@example.com"])
    # Plain text on the way in only — hashed before it ever touches the DB.
    password: str = Field(min_length=8, examples=["hunter2hunter2"])

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = unicodedata.normalize("NFKC", v).strip()
        if len(v) < 3:
            raise ValueError("username must be at least 3 characters after trimming whitespace")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise ValueError("username must not contain control characters")
        return v

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        # EmailStr lowercases the domain but leaves the local part alone, so
        # "Ada@x.com" and "ada@x.com" would otherwise be two separate accounts.
        return v.lower()

    @field_validator("password")
    @classmethod
    def check_password_bytes(cls, v: str) -> str:
        # Length has to be measured in bytes, not characters: 40 accented
        # characters are 80 bytes, which bcrypt would quietly cut down to 72.
        if len(v.encode("utf-8")) > BCRYPT_MAX_BYTES:
            raise ValueError(f"password must be at most {BCRYPT_MAX_BYTES} bytes")
        # bcrypt refuses NULL bytes outright and raises if one reaches it.
        if "\x00" in v:
            raise ValueError("password must not contain NULL bytes")
        return v


class UserResponse(BaseModel):
    """Schema for returning user data (no password fields)."""

    # from_attributes lets FastAPI build this straight from a SQLAlchemy User
    # object. Because hashed_password is not declared here, it can never be
    # serialized out by accident.
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr


class TokenResponse(BaseModel):
    """Schema for the JWT response."""

    access_token: str
    token_type: str = "bearer"
