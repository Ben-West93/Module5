# exercises/test-suite/app/models/task.py
# L11 — Task ORM model

from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional
from app.database import Base


class Task(Base):
    """SQLAlchemy model for the tasks table."""
    __tablename__ = "tasks"
    # Without AUTOINCREMENT, SQLite gives a new row the highest existing id
    # plus one. Delete the newest task and the next task created gets its id,
    # so an old link, bookmark or delayed PATCH silently reaches a different
    # task. AUTOINCREMENT means an id is never handed out twice.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
