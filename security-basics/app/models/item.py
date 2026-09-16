# exercises/security-basics/app/models/item.py
# L10 — Item ORM model

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Item(Base):
    """SQLAlchemy model for the items table.

    Column lengths match the ItemCreate schema. Sanitizing can only shorten a
    value, so anything that passed the schema's max_length fits here.
    """

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Item id={self.id} name={self.name!r}>"
