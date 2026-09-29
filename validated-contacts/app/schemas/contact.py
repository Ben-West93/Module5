# exercises/validated-contacts/app/schemas/contact.py
# L3 — Pydantic schemas for Contact
#
# Three Pydantic v2 schemas for the contact book API, using Field() for
# constraints, a custom validator for email, and an Enum for category.

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContactCategory(str, Enum):
    """Allowed contact categories. Any other value is rejected with a 422."""
    personal = "personal"
    work = "work"
    family = "family"


def _check_email(value: Optional[str]) -> Optional[str]:
    """Shared email rule used by both ContactCreate and ContactUpdate."""
    if value is None:
        return value
    if "@" not in value:
        raise ValueError("email must contain @")
    return value


class ContactCreate(BaseModel):
    """Schema for creating a new contact."""
    first_name: str = Field(..., min_length=1, max_length=50, examples=["Ada"])
    last_name: str = Field(..., min_length=1, max_length=50, examples=["Lovelace"])
    email: str = Field(..., examples=["ada@example.com"])
    phone: Optional[str] = Field(None, min_length=10, max_length=15, examples=["5155551234"])
    category: ContactCategory = Field(..., examples=["work"])

    @field_validator("email")
    @classmethod
    def email_must_contain_at(cls, v: str) -> str:
        return _check_email(v)


class ContactUpdate(BaseModel):
    """Schema for partially updating a contact (all fields optional).

    The same constraints still apply to any field the client does send.
    """
    first_name: Optional[str] = Field(None, min_length=1, max_length=50)
    last_name: Optional[str] = Field(None, min_length=1, max_length=50)
    email: Optional[str] = None
    phone: Optional[str] = Field(None, min_length=10, max_length=15)
    category: Optional[ContactCategory] = None

    @field_validator("email")
    @classmethod
    def email_must_contain_at(cls, v: Optional[str]) -> Optional[str]:
        return _check_email(v)


class ContactResponse(ContactCreate):
    """Schema for returning contact data in API responses."""
    id: int
    created_at: str

    # Needed when building from ORM objects later in L5+
    model_config = ConfigDict(from_attributes=True)
