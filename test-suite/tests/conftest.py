# exercises/test-suite/tests/conftest.py
# L11 — Test fixtures
#
# An isolated in-memory test database, with the get_db dependency overridden
# so every endpoint uses it instead of app.db.

import os

# --- must happen before anything imports app --------------------------------
#
# app/database.py builds its engine from DATABASE_URL at import time, and
# app/main.py runs Base.metadata.create_all() on that engine at import time.
# Left alone, merely importing the app below would create app.db in the
# folder pytest was run from, before any override could apply. Pointing it at
# memory means the real database file is never opened, not even by an import.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# The tests check the real limits and origins, so nothing in the developer's
# shell (a raised RATE_LIMIT_REQUESTS left over from exploring /docs, say) may
# change them.
for _var in ("CORS_ALLOWED_ORIGINS", "RATE_LIMIT_REQUESTS", "RATE_LIMIT_WINDOW_SECONDS"):
    os.environ.pop(_var, None)
# -----------------------------------------------------------------------------

from contextlib import contextmanager  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app, reset_rate_limiter  # noqa: E402

# Key concept: tests use a SEPARATE in-memory database, so they never touch the
# real database file.
#
# StaticPool is required for ":memory:". Every new SQLite connection to
# ":memory:" opens a brand-new, empty database. Without StaticPool, the tables
# created in the fixture would live on one connection and the endpoint's
# session would get another, and every request would fail with "no such
# table". StaticPool hands out the same single connection every time.
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},  # TestClient runs endpoints in a worker thread
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    """Stands in for app.database.get_db: same shape, test database."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    """A TestClient whose endpoints all use a fresh, empty in-memory database.

    Key concept — the dependency_overrides pattern: FastAPI looks up every
    Depends(get_db) in app.dependency_overrides first. Mapping get_db to
    override_get_db swaps the database for every endpoint at once, without
    changing any endpoint's code.

    Each test gets empty tables and a clean rate limiter:

    - Tables are created before and dropped after every test, so no test can
      see another's rows. test_tasks.py has a pair of tests that would fail if
      that ever stopped being true.
    - The rate limiter's state is module-level in app.main and outlives any
      one TestClient. Every TestClient request comes from the same address
      ("testclient"), so without the reset the whole suite would share one
      budget of 10 requests and start failing with 429 partway through.

    Cleanup is in `finally`, so a failing test still leaves nothing behind for
    the next one.
    """
    Base.metadata.create_all(bind=test_engine)
    app.dependency_overrides[get_db] = override_get_db
    reset_rate_limiter()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        reset_rate_limiter()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session(client) -> Session:
    """A session on the same test database, for reading rows back directly.

    A response can say one thing while the table holds another (an endpoint
    that echoed its input instead of the stored row, say). Tests use this to
    check what was actually saved. It depends on `client`, so the tables exist
    and the two always share one database.
    """
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def deleted_mid_request(client):
    """Deletes a task at an exact point inside the next request, as a racing DELETE would.

    Two requests only collide when their timing lines up, which a test cannot
    arrange by sending them together; that would be flaky. Instead this hooks
    the test database and removes the row at the precise moment another
    request's DELETE could have landed:

    - "before UPDATE": just before the next UPDATE statement runs
    - "before DELETE": just before the next DELETE statement runs
    - "at commit":     inside the next commit, after the request's own
                       statements have run

    Usage:  with deleted_mid_request(task_id, "at commit"): ...

    The hook fires once and is always removed, even if the test fails.
    """

    @contextmanager
    def arrange(task_id: int, moment: str):
        fired = False

        def delete_now(dbapi_connection):
            # Committed straight away, as the other request's DELETE would be.
            # Left uncommitted, it would share the request's transaction and be
            # undone by the request's own rollback.
            nonlocal fired
            fired = True
            dbapi_connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            dbapi_connection.commit()

        if moment in ("before UPDATE", "before DELETE"):
            prefix = moment.split()[1]

            def hook(conn, cursor, statement, parameters, context, executemany):
                if not fired and statement.lstrip().upper().startswith(prefix):
                    delete_now(cursor.connection)

            event_name = "before_cursor_execute"
        elif moment == "at commit":

            def hook(conn):
                if not fired:
                    delete_now(conn.connection.driver_connection)

            event_name = "commit"
        else:
            raise ValueError(f"unknown moment {moment!r}")

        event.listen(test_engine, event_name, hook)
        try:
            yield
        finally:
            event.remove(test_engine, event_name, hook)
        assert fired, f"the request never reached {moment!r}, so the race was not exercised"

    return arrange
