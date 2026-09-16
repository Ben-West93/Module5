# exercises/polished-docs/tests/helpers.py
# Shared request helpers for the pytest suite
#
# Every test request goes through call(). It checks the status code before
# anything reads the body, so an unexpected error fails with its status and
# body in the message, rather than as a KeyError three lines later when a test
# indexes into the JSON of an error envelope it did not expect.

from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.item import Item
from app.models.task import Task


def call(client, method: str, path: str, *, expect: int, **kwargs) -> httpx.Response:
    """Sends a request and asserts its status before returning the response.

    Redirects are not followed. The routes are declared without a trailing
    slash precisely so that no request costs a redirect (and a second slot of
    the rate-limit budget), so a 307 here is a regression and should fail the
    status check rather than be silently followed.
    """
    kwargs.setdefault("follow_redirects", False)
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        pytest.fail(f"{method} {path} could not be sent: {type(exc).__name__}: {exc}")
    assert response.status_code == expect, (
        f"{method} {path}: expected {expect}, got {response.status_code}: {response.text[:300]}"
    )
    return response


def json_of(response: httpx.Response) -> Any:
    """The parsed body, failing the test with the raw text if it is not JSON."""
    try:
        return response.json()
    except ValueError:
        pytest.fail(f"body is not JSON: {response.text[:300]!r}")


def assert_error_envelope(response: httpx.Response, error: str) -> dict:
    """Checks the API's standard error body and returns it."""
    body = json_of(response)
    assert set(body) >= {"error", "detail", "status_code"}, body
    assert body["error"] == error
    assert body["status_code"] == response.status_code
    return body


def create_task(client, **fields) -> dict:
    """POSTs a task, asserts 201, and returns the created task."""
    return json_of(call(client, "POST", "/tasks", json=fields, expect=201))


def stored_task(db_session: Session, task_id: int) -> dict | None:
    """The row as saved in the database, independent of anything the API said.

    expire_all() first, so the session re-reads the row instead of returning
    a copy it cached from an earlier read in the same test.
    """
    db_session.expire_all()
    task = db_session.get(Task, task_id)
    if task is None:
        return None
    return {"id": task.id, "title": task.title, "description": task.description, "completed": task.completed}


def stored_titles(db_session: Session) -> list[str]:
    db_session.expire_all()
    return list(db_session.scalars(select(Task.title).order_by(Task.id)))


# ---- items (L12) --------------------------------------------------------------

ITEM = {"name": "Wireless Mouse", "description": "2.4 GHz", "price": 24.99, "category": "electronics"}


def create_item(client, **fields) -> dict:
    """POSTs an item (ITEM, overridden by `fields`), asserts 201, returns it."""
    return json_of(call(client, "POST", "/items", json={**ITEM, **fields}, expect=201))


def stored_item(db_session: Session, item_id: int) -> dict | None:
    """The item row as saved, without created_at (compared separately)."""
    db_session.expire_all()
    item = db_session.get(Item, item_id)
    if item is None:
        return None
    return {"id": item.id, "name": item.name, "description": item.description, "price": item.price, "category": item.category}


def stored_item_names(db_session: Session) -> list[str]:
    db_session.expire_all()
    return list(db_session.scalars(select(Item.name).order_by(Item.id)))
