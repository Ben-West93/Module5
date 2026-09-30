# exercises/security-basics/app/database.py
# L10 — Database setup (same pattern as L5/L6)
#
# The items table is real rather than a hardcoded sample list so that the SQL
# injection note in routers/items.py describes code that actually runs, and
# verify_security.py can throw injection payloads at a real query.

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Overridable so verify_security.py can point at a scratch database instead of
# the items.db a developer is using.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./items.db")

# check_same_thread=False is SQLite-specific: FastAPI runs sync endpoints in a
# thread pool, so a connection may be used by a thread other than its creator.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """Provides a database session per request, closed when the request ends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
