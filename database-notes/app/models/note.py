# exercises/database-notes/app/models/note.py
# L5 — SQLAlchemy 2.0 Note model

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Text, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# Key concept: The ORM model describes the database TABLE and column types.
# The Pydantic schema (in schemas/note.py) describes the API request/response.
class Note(Base):
    """SQLAlchemy model for the notes table."""
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Mapped[Optional[str]] is what makes the column nullable.
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # default takes a callable, not a value: it's invoked at insert time. A
    # plain datetime.now(timezone.utc) here would be evaluated once at import
    # and stamp every note with the same timestamp.
    #
    # The stub suggested datetime.utcnow, which Python 3.12 deprecates:
    # "datetime.utcnow() is deprecated and scheduled for removal in a future
    # version." The lambda below stores the same UTC instant without the
    # warning. To match the stub exactly, swap it for default=datetime.utcnow.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
