# exercises/database-notes/app/database.py
# L5 — Database setup

from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from sqlalchemy.pool import NullPool

# Database URL — SQLite stored in a local file
DATABASE_URL = "sqlite:///./notes.db"

# check_same_thread=False is SQLite-only. By default SQLite refuses to use a
# connection from a thread other than the one that opened it, and FastAPI runs
# sync endpoints in a thread pool.
#
# poolclass=NullPool fixes a failure that only shows up under load: SQLAlchemy's
# default pool allows 5 connections plus 10 overflow, but uvicorn will run up to
# 40 sync requests at once. The surplus threads queue for a connection and
# eventually raise "QueuePool limit of size 5 overflow 10 reached" — a 500.
# NullPool opens a connection per session instead, so there's no cap to hit.
# Connecting to a local SQLite file is cheap, so pooling buys little here.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    poolclass=NullPool,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """
    Applied to every new connection.

    WAL lets readers work while a write is in progress instead of everything
    serializing on one lock. busy_timeout tells SQLite to wait for a lock
    rather than immediately raising "database is locked".
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


# autocommit/autoflush off means nothing reaches the database until an explicit
# commit, so an endpoint that fails partway leaves no half-written rows.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# SQLAlchemy 2.0 style: inherit from DeclarativeBase instead of calling
# declarative_base(). This is already correct — no changes needed here.
class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides a database session per request.

    The rollback matters: if an endpoint raises mid-transaction the session is
    left in a failed state, and because connections get recycled a later
    request could inherit it. The finally block runs either way, so no
    connection is ever left open.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
