# exercises/student-crud/app/schemas/student.py
# L6 — Pydantic schemas for Student

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_email(value: Optional[str]) -> Optional[str]:
    """Minimal email check. A real app would use pydantic[email] / EmailStr.

    Tolerates None so that an explicit `"email": null` is handled by the
    null-rejection validator below rather than crashing here on `in`.
    """
    if value is None:
        return value
    if "@" not in value:
        raise ValueError("email must contain @")
    return value


class StudentCreate(BaseModel):
    """Schema for creating a student (POST)."""

    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=200)
    grade_level: int = Field(..., ge=1, le=12)
    major: Optional[str] = Field(None, max_length=100)
    gpa: Optional[float] = Field(None, ge=0.0, le=4.0)
    is_enrolled: bool = True

    _check_email = field_validator("email")(_validate_email)


class StudentUpdate(BaseModel):
    """Schema for fully replacing a student (PUT).

    Key concept — PUT vs PATCH:
      PUT    replaces the entire resource — every field must be sent
      PATCH  updates only the fields sent — every field is optional

    Note the difference between these two declarations:

        major: Optional[str] = Field(None)   # optional to send, defaults to null
        major: Optional[str] = Field(...)    # REQUIRED to send, may be null

    PUT needs the second form. The field is nullable, but the client has to
    say so explicitly — that is what makes it a replacement rather than a
    partial update, and it is what keeps this schema distinct from
    StudentPatch below.
    """

    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=200)
    grade_level: int = Field(..., ge=1, le=12)
    major: Optional[str] = Field(..., max_length=100)
    gpa: Optional[float] = Field(..., ge=0.0, le=4.0)
    is_enrolled: bool = Field(...)

    _check_email = field_validator("email")(_validate_email)


class StudentPatch(BaseModel):
    """Schema for partially updating a student (PATCH). Every field optional.

    "Optional to send" is not the same as "allowed to be null". `name`,
    `email`, `grade_level` and `is_enrolled` map to NOT NULL columns, so an
    explicit `"name": null` has to be a 422 here — otherwise it reaches the
    database, trips the constraint, and surfaces as a confusing 5xx or a
    mis-labelled conflict. `major` and `gpa` are nullable and may be cleared.

    The validator below only fires for fields the client actually sent:
    Pydantic does not validate defaults unless `validate_default=True`, so an
    omitted field keeps its `None` default and is filtered out later by
    `model_dump(exclude_unset=True)`.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    grade_level: Optional[int] = Field(None, ge=1, le=12)
    major: Optional[str] = Field(None, max_length=100)
    gpa: Optional[float] = Field(None, ge=0.0, le=4.0)
    is_enrolled: Optional[bool] = None

    @field_validator("name", "email", "grade_level", "is_enrolled")
    @classmethod
    def _reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("field may be omitted, but cannot be set to null")
        return value

    _check_email = field_validator("email")(_validate_email)


class StudentResponse(StudentCreate):
    """Schema for returning student data."""

    id: int
    created_at: datetime

    # from_attributes lets Pydantic read straight off the SQLAlchemy object
    # instead of requiring a dict.
    model_config = ConfigDict(from_attributes=True)
