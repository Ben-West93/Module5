# exercises/security-basics/app/models/user.py
# L10 — User ORM model (for the login endpoint)

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """An account that can log in.

    Only a bcrypt hash of the password is stored, never the password.
    Usernames are stored lowercased, so "Ben" and "ben" are one account.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    # A bcrypt hash is always 60 characters: "$2b$12$" + salt + hash.
    password_hash: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"
