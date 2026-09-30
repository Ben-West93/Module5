# exercises/database-notes/app/schemas/note.py
# L5 — Pydantic schemas for Note

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class NoteCreate(BaseModel):
    """Schema for creating a new note."""

    # max_length mirrors String(200) on the model, so an oversized title is
    # rejected with a 422 instead of reaching the database.
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    category: Optional[str] = Field(default=None, max_length=50)
    is_pinned: bool = False

    @field_validator("title", "content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """
        Trim surrounding whitespace and reject what's left if it's empty.

        min_length counts spaces, so "   " would otherwise pass as a title.
        """
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty or whitespace only")
        return cleaned

    @field_validator("category")
    @classmethod
    def _normalize_category(cls, value: Optional[str]) -> Optional[str]:
        """Treat an empty or whitespace-only category as "no category"."""
        if value is None:
            return None
        return value.strip() or None


class NoteResponse(NoteCreate):
    """Schema for returning note data in API responses."""

    # from_attributes lets FastAPI build this straight from a SQLAlchemy Note
    # object by reading its attributes, instead of requiring a dict.
    model_config = ConfigDict(from_attributes=True)

    # Inherits title, content, category and is_pinned from NoteCreate, then
    # adds the two fields the database assigns.
    id: int
    created_at: datetime

    # title and content are redeclared without length limits on purpose.
    # Inherited fields bring their validators with them, which means the rules
    # meant for INPUT would also police OUTPUT: a row already in the table that
    # doesn't satisfy them (say a 250-character title inserted before the limit
    # existed) makes the whole GET fail with a 500 instead of returning data.
    # A response model should describe what comes back, not re-police it.
    title: str
    content: str
