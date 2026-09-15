# exercises/auth-system/app/models/user.py
# L8 — User ORM model

from sqlalchemy import Index, String, column, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class User(Base):
    """SQLAlchemy model for the users table."""
    __tablename__ = "users"

    # Emails are stored lower-cased by the schema, so the plain unique index
    # below is already case-insensitive for them. Usernames keep the casing the
    # user chose, so uniqueness needs an index on the lower-cased value —
    # otherwise "Ada" and "ada" are two accounts people will confuse.
    __table_args__ = (
        Index("ix_users_username_lower", func.lower(column("username")), unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # Only the bcrypt hash is ever stored — the plain-text password is discarded
    # as soon as it has been hashed in the /register endpoint.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} username={self.username!r}>"
