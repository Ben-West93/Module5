# exercises/polished-docs/app/database.py
# L11 — Database setup, shared by the item (L10) and task (L11) endpoints
#
# L10 and L11 each shipped their own database.py. They are merged here into one
# engine and one Base, so both tables live in the same database and one
# get_db() dependency serves every router. That single dependency is also what
# lets tests/conftest.py swap the whole API onto an in-memory database with
# one override.

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Overridable so tests/conftest.py, verify_security.py and demo_rate_limit.py
# can point away from the app.db a developer is using. It is read at import
# time, so it must be set before anything imports app.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

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
