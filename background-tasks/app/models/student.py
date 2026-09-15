# exercises/background-tasks/app/models/student.py
# L6 — Student ORM model
#
# Field set is the union of the project brief and the GitHub stub files.
# See README.md ("Brief vs. stub reconciliation") for details.

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Student(Base):
    """SQLAlchemy model for the students table."""

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, index=True
    )

    # 1-12 is enforced in the Pydantic schemas, not by SQLite.
    grade_level: Mapped[int] = mapped_column(Integer, nullable=False)

    # From the stub files; optional so a brief-only client can ignore it.
    major: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # 0.0-4.0 is enforced in the Pydantic schemas.
    gpa: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    is_enrolled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # server_default means the DB stamps the row on INSERT; the value comes
    # back on db.refresh() without the client ever sending it.
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Student id={self.id} name={self.name!r} email={self.email!r}>"
