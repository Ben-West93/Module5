# exercises/polished-docs/tests/test_items.py
# L12 — Item API tests (merged L10 item API + L12 fields and endpoints)
#
# Each test gets 10 requests from the rate limiter, so tests that need many
# rows insert them through db_session instead of the API.

from datetime import datetime

import pytest

from app.ids import SQLITE_MAX_INTEGER
from app.models.item import Item
from tests.helpers import (
    ITEM,
    assert_error_envelope,
    call,
    create_item,
    json_of,
    stored_item,
    stored_item_names,
)


def add_items(db_session, *rows):
    db_session.add_all(Item(**{**ITEM, **row}) for row in rows)
    db_session.commit()


# ============================================================================
# Success paths
# ============================================================================


def test_create_item(client, db_session):
    response = call(client, "POST", "/items", json=ITEM, expect=201)
    body = json_of(response)
    assert set(body) == {"id", "name", "description", "price", "category", "created_at"}
    assert {k: body[k] for k in ITEM} == ITEM
    assert isinstance(body["id"], int)
    datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))
    assert stored_item(db_session, body["id"]) == {"id": body["id"], **ITEM}


def test_description_is_optional_and_defaults_to_empty(client, db_session):
    sent = {"name": "Cable", "price": 5, "category": "electronics"}
    body = json_of(call(client, "POST", "/items", json=sent, expect=201))
    assert body["description"] == ""
    assert body["price"] == 5.0
    assert stored_item(db_session, body["id"])["description"] == ""


def test_list_items_returns_every_item_in_id_order(client):
    assert json_of(call(client, "GET", "/items", expect=200)) == []
    first = create_item(client, name="First")
    second = create_item(client, name="Second", category="books")
    assert json_of(call(client, "GET", "/items", expect=200)) == [first, second]


def test_get_item_by_id(client):
    created = create_item(client)
    assert json_of(call(client, "GET", f"/items/{created['id']}", expect=200)) == created


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"name": "Mouse"}, ["Mouse"]),
        ({"name": "<b>Mouse</b>"}, ["Mouse"]),          # sanitized like stored names
        ({"name": "mouse"}, []),                         # exact, case-sensitive
        ({"category": "books"}, ["Novel", "Atlas"]),
        ({"category": "books", "name": "Atlas"}, ["Atlas"]),
        ({"category": "books", "name": "Mouse"}, []),    # filters combine with AND
        ({"name": "", "category": ""}, ["Mouse", "Novel", "Atlas"]),  # blank is no filter
        ({"name": "' OR '1'='1"}, []),                   # SQL injection is just a name
    ],
    ids=["name", "name-sanitized", "case-sensitive", "category", "both", "and", "blank", "injection"],
)
def test_list_items_filters(client, db_session, params, expected):
    add_items(db_session, {"name": "Mouse"}, {"name": "Novel", "category": "books"}, {"name": "Atlas", "category": "books"})
    body = json_of(call(client, "GET", "/items", params=params, expect=200))
    assert [item["name"] for item in body] == expected
    assert len(stored_item_names(db_session)) == 3  # the injection payload changed nothing


def test_create_item_strips_markup_before_storing(client, db_session):
    body = create_item(
        client,
        name="  <b>Gadget</b>  ",
        description="<script>steal()</script>Useful",
        category="<i>tools</i><img src=x onerror=alert(1)//",
    )
    expected = {"name": "Gadget", "description": "steal()Useful", "category": "tools"}
    assert {k: body[k] for k in expected} == expected
    assert {k: stored_item(db_session, body["id"])[k] for k in expected} == expected


def test_values_at_the_limits_are_accepted(client):
    body = create_item(client, name="n" * 100, description="d" * 500, category="c" * 50, price=1_000_000)
    assert (len(body["name"]), len(body["description"]), len(body["category"]), body["price"]) == (100, 500, 50, 1_000_000)
    assert create_item(client, name="Cheap", price=0.01)["price"] == 0.01


# ============================================================================
# 422 validation failures
# ============================================================================


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "<b></b>"},
        {"name": "\u200b"},
        {"name": None},
        {"name": 123},
        {"name": "x" * 101},
        {"description": None},
        {"description": "d" * 501},
        {"price": 0},
        {"price": -5},
        {"price": 1_000_000.01},
        {"price": "cheap"},
        {"price": None},
        {"category": ""},
        {"category": "<i></i>"},
        {"category": "c" * 51},
        {"colour": "red"},
    ],
    ids=[
        "name-empty", "name-whitespace", "name-markup-only", "name-invisible", "name-null", "name-number",
        "name-101", "description-null", "description-501", "price-zero", "price-negative", "price-over-max",
        "price-text", "price-null", "category-empty", "category-markup-only", "category-51", "unknown-field",
    ],
)
def test_invalid_item_bodies_are_422_and_store_nothing(client, db_session, overrides):
    response = call(client, "POST", "/items", json={**ITEM, **overrides}, expect=422)
    body = assert_error_envelope(response, "ValidationError")
    assert body["errors"], body
    assert stored_item_names(db_session) == []


@pytest.mark.parametrize("missing", ["name", "price", "category"])
def test_each_required_field_is_required(client, db_session, missing):
    sent = {k: v for k, v in ITEM.items() if k != missing}
    body = assert_error_envelope(call(client, "POST", "/items", json=sent, expect=422), "ValidationError")
    assert body["errors"][0]["loc"] == ["body", missing]
    assert body["errors"][0]["type"] == "missing"
    assert stored_item_names(db_session) == []


@pytest.mark.parametrize("raw", [b'{"name": "x", "price": NaN, "category": "c"}', b'{"name": "x", "price": Infinity, "category": "c"}'])
def test_non_finite_prices_are_422_not_500(client, db_session, raw):
    call(client, "POST", "/items", content=raw, headers={"Content-Type": "application/json"}, expect=422)
    assert stored_item_names(db_session) == []


def test_malformed_json_is_422(client):
    response = call(client, "POST", "/items", content=b'{"name": ', headers={"Content-Type": "application/json"}, expect=422)
    assert_error_envelope(response, "ValidationError")


@pytest.mark.parametrize("item_id", ["abc", "0", "-1", "+1", "01", "1_0", str(SQLITE_MAX_INTEGER + 1)])
def test_invalid_item_ids_are_422(client, item_id):
    assert_error_envelope(call(client, "GET", f"/items/{item_id}", expect=422), "ValidationError")


def test_overlong_name_filter_is_422(client):
    call(client, "GET", "/items", params={"name": "n" * 101}, expect=422)


# ============================================================================
# 404, 409 and other edge cases
# ============================================================================


def test_get_nonexistent_item_returns_404(client):
    body = assert_error_envelope(call(client, "GET", "/items/9999", expect=404), "NotFound")
    assert body["detail"] == "Item 9999 not found"
    call(client, "GET", f"/items/{SQLITE_MAX_INTEGER}", expect=404)


def test_duplicate_item_name_returns_409(client, db_session):
    create_item(client, name="Unique")
    body = assert_error_envelope(call(client, "POST", "/items", json={**ITEM, "name": "<em>Unique</em>"}, expect=409), "Conflict")
    assert body["detail"] == "An item with that name already exists"
    assert stored_item_names(db_session) == ["Unique"]


def test_item_ids_are_never_reused(client, db_session):
    create_item(client, name="Older")
    newest = create_item(client, name="Newest")
    db_session.query(Item).filter(Item.id == newest["id"]).delete()
    db_session.commit()
    assert create_item(client, name="After")["id"] > newest["id"]


def test_items_and_tasks_are_in_one_database(client, db_session):
    create_item(client)
    call(client, "POST", "/tasks", json={"title": "Restock mice"}, expect=201)
    assert stored_item_names(db_session) == [ITEM["name"]]
    assert len(json_of(call(client, "GET", "/tasks", expect=200))) == 1


def test_item_routes_answer_head_and_list_allowed_methods(client):
    created = create_item(client)
    head = call(client, "HEAD", f"/items/{created['id']}", expect=200)
    assert head.content == b""
    response = call(client, "DELETE", f"/items/{created['id']}", expect=405)
    assert response.headers["allow"] == "GET, HEAD"
