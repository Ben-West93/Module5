# exercises/polished-docs/app/schemas/item.py
# L12 — Item schema with documentation
#
# Merged with the L10 item API: name and description keep the L10 sanitizer,
# and price and category are the L12 fields. Item holds the documented field
# definitions; ItemCreate adds the input rules (sanitizing, unknown fields
# refused); ItemResponse adds the server-assigned values. ItemResponse
# inherits from Item rather than ItemCreate so stored rows are never run back
# through the input sanitizer on the way out.

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.sanitization import has_visible_text, sanitize_text
from app.schemas.task import reject_surrogates

EXAMPLE = {
    "name": "Wireless Mouse",
    "description": "Ergonomic 2.4 GHz mouse with a USB receiver",
    "price": 24.99,
    "category": "electronics",
}


class Item(BaseModel):
    """An item in the inventory."""

    name: str = Field(..., min_length=1, max_length=100, description="Unique item name; HTML tags are stripped")
    description: str = Field("", max_length=500, description="Optional details; HTML tags are stripped")
    price: float = Field(..., gt=0, le=1_000_000, allow_inf_nan=False, description="Unit price, greater than 0")
    category: str = Field(..., min_length=1, max_length=50, description="Category, such as `electronics`")

    model_config = ConfigDict(json_schema_extra={"example": EXAMPLE})


class ItemCreate(Item):
    """Schema for creating an item."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": EXAMPLE})

    @field_validator("name", "category")
    @classmethod
    def required_text(cls, value: str, info) -> str:
        reject_surrogates(value)
        cleaned = sanitize_text(value)
        if not has_visible_text(cleaned):
            raise ValueError(f"{info.field_name} must contain visible text once HTML tags are removed")
        return cleaned

    @field_validator("description")
    @classmethod
    def optional_text(cls, value: str) -> str:
        reject_surrogates(value)
        return sanitize_text(value)


class ItemResponse(Item):
    """Schema returned to clients — includes server-assigned id."""

    id: int = Field(..., description="Server-assigned id")
    created_at: datetime = Field(..., description="When the item was created (UTC)")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": {"id": 1, **EXAMPLE, "created_at": "2026-09-16T12:00:00Z"}},
    )
