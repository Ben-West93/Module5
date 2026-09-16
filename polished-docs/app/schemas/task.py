# exercises/polished-docs/app/schemas/task.py
# L11 — Task schemas (with the L10 sanitizer; L12 examples for Swagger)

import unicodedata
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.sanitization import has_visible_text, sanitize_text

TITLE_REQUIRED = "title must contain visible text once HTML tags are removed"


def reject_surrogates(value):
    """A lone surrogate (JSON "\\ud800") is valid to the JSON parser but cannot
    be stored or returned as UTF-8, so it would become a 500 later."""
    if isinstance(value, str) and any("\ud800" <= ch <= "\udfff" for ch in value):
        raise ValueError("text must be valid Unicode")
    return value


def _normalize_title(value: str) -> str:
    """NFC plus removal of format characters, BEFORE sanitizing (bug 9).

    Done after sanitizing, "<" + zero-width space + "script>" would pass the
    sanitizer as text and then become a live tag.
    """
    composed = unicodedata.normalize("NFC", value)
    return "".join(ch for ch in composed if unicodedata.category(ch) != "Cf")


def clean_title(value: str) -> str:
    reject_surrogates(value)
    cleaned = sanitize_text(_normalize_title(value))
    if not has_visible_text(cleaned):
        raise ValueError(TITLE_REQUIRED)
    return cleaned


def clean_description(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    reject_surrogates(value)
    return sanitize_text(value)


class TaskCreate(BaseModel):
    """Schema for creating a task."""

    title: str = Field(..., min_length=1, max_length=200, description="Unique; HTML tags are stripped")
    description: Optional[str] = Field(None, max_length=500, description="Optional notes; HTML tags are stripped")
    completed: bool = Field(False, description="Whether the task is done")

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"title": "Buy groceries", "description": "Milk, eggs, bread", "completed": False}},
    )

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, value):
        return clean_title(value)

    @field_validator("description")
    @classmethod
    def sanitize_description(cls, value):
        return clean_description(value)


class TaskPatch(BaseModel):
    """Schema for a partial update. Only the fields sent are applied."""

    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=500)
    completed: Optional[bool] = None

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"completed": True}})

    @field_validator("title", "completed", mode="before")
    @classmethod
    def not_null(cls, value, info):
        # Optional only so the field can be omitted. An explicit null would be
        # written into a NOT NULL column (bug 2).
        if value is None:
            raise ValueError(f"{info.field_name} cannot be null")
        return value

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, value):
        return clean_title(value)

    @field_validator("description")
    @classmethod
    def sanitize_description(cls, value):
        return clean_description(value)


class TaskResponse(BaseModel):
    """A stored task. A separate model, so output is never re-validated as input."""

    id: int
    title: str
    description: Optional[str]
    completed: bool

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": {"id": 1, "title": "Buy groceries", "description": "Milk, eggs, bread", "completed": False}},
    )
