# exercises/jwt/app/database.py
# L6 — Database setup (same pattern as L5)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from typing import Generator

DATABASE_URL = "sqlite:///./students.db"

# check_same_thread=False is SQLite-specific: FastAPI may hand the same
# connection to a different thread than the one that opened it.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """Provides a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
