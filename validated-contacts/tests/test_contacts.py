# exercises/validated-contacts/tests/test_contacts.py
# L3 — Tests for the Validated Contact Book API
#
# Run with: pytest -v  (from validated-contacts/ folder)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import contacts as contacts_router

client = TestClient(app)

VALID_CONTACT = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "phone": "5155551234",
    "category": "work",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def request(method: str, url: str, expected_status: int, **kwargs):
    """Shared request helper.

    Sends the request, checks the status code BEFORE the body is used, and
    returns the parsed JSON. Any network/parsing problem fails the test with
    a readable message instead of a raw traceback.
    """
    try:
        response = client.request(method, url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - surface any client error clearly
        pytest.fail(f"{method} {url} raised {type(exc).__name__}: {exc}")

    assert response.status_code == expected_status, (
        f"{method} {url}: expected {expected_status}, "
        f"got {response.status_code} -> {response.text}"
    )
    try:
        return response.json()
    except ValueError:
        pytest.fail(f"{method} {url} did not return JSON: {response.text!r}")


def create(expected_status: int = 201, **overrides):
    payload = {**VALID_CONTACT, **overrides}
    return request("POST", "/contacts/", expected_status, json=payload)


def error_fields(body: dict) -> set[str]:
    """Field names that a 422 validation error points at."""
    return {err["loc"][-1] for err in body["detail"]}


@pytest.fixture(autouse=True)
def reset_storage():
    """Give every test a clean, empty contact book."""
    contacts_router.contacts.clear()
    contacts_router._next_id = 1
    yield
    contacts_router.contacts.clear()
    contacts_router._next_id = 1


# ---------------------------------------------------------------------------
# POST /contacts — happy paths
# ---------------------------------------------------------------------------

def test_create_contact_returns_201_and_echoes_every_field():
    body = create()
    for key, value in VALID_CONTACT.items():
        assert body[key] == value, f"{key}: sent {value!r}, got {body[key]!r}"
    assert body["id"] == 1
    assert isinstance(body["created_at"], str) and body["created_at"]


def test_create_without_phone_defaults_to_none():
    payload = {k: v for k, v in VALID_CONTACT.items() if k != "phone"}
    body = request("POST", "/contacts/", 201, json=payload)
    assert body["phone"] is None


def test_ids_increment():
    first = create()
    second = create(first_name="Grace", email="grace@example.com")
    assert (first["id"], second["id"]) == (1, 2)


@pytest.mark.parametrize("category", ["personal", "work", "family"])
def test_every_category_is_accepted(category):
    assert create(category=category)["category"] == category


@pytest.mark.parametrize("name", ["A", "B" * 50])
def test_name_length_boundaries_accepted(name):
    body = create(first_name=name, last_name=name)
    assert body["first_name"] == name and body["last_name"] == name


@pytest.mark.parametrize("phone", ["1" * 10, "1" * 15, "+1 515-555-0100"])
def test_phone_length_boundaries_accepted(phone):
    assert create(phone=phone)["phone"] == phone


# ---------------------------------------------------------------------------
# POST /contacts — validation failures (422)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["first_name", "last_name"])
@pytest.mark.parametrize("bad_value", ["", "x" * 51])
def test_name_length_violations(field, bad_value):
    body = create(422, **{field: bad_value})
    assert field in error_fields(body)


@pytest.mark.parametrize("phone", ["1" * 9, "1" * 16])
def test_phone_length_violations(phone):
    assert "phone" in error_fields(create(422, phone=phone))


def test_email_without_at_uses_custom_message():
    body = create(422, email="not-an-email")
    assert "email" in error_fields(body)
    assert any("email must contain @" in err["msg"] for err in body["detail"])


@pytest.mark.parametrize("category", ["friends", "WORK", ""])
def test_invalid_category_rejected(category):
    assert "category" in error_fields(create(422, category=category))


@pytest.mark.parametrize("missing", ["first_name", "last_name", "email", "category"])
def test_required_field_missing(missing):
    payload = {k: v for k, v in VALID_CONTACT.items() if k != missing}
    body = request("POST", "/contacts/", 422, json=payload)
    assert missing in error_fields(body)


def test_wrong_types_rejected():
    body = create(422, first_name=123, email=None)
    assert {"first_name", "email"} <= error_fields(body)


def test_multiple_errors_reported_together():
    body = create(422, first_name="", email="nope", category="enemy")
    assert {"first_name", "email", "category"} <= error_fields(body)


def test_failed_create_does_not_store_anything():
    create(422, email="nope")
    assert request("GET", "/contacts/", 200) == []


# ---------------------------------------------------------------------------
# GET /contacts and GET /contacts/{id}
# ---------------------------------------------------------------------------

def test_list_empty():
    assert request("GET", "/contacts/", 200) == []


def test_list_returns_what_was_created():
    created = [create(), create(first_name="Grace", category="family")]
    assert request("GET", "/contacts/", 200) == created


def test_list_filter_by_category():
    work = create(category="work")
    family = create(first_name="Grace", category="family")
    create(first_name="Alan", category="personal")

    assert request("GET", "/contacts/", 200, params={"category": "work"}) == [work]
    assert request("GET", "/contacts/", 200, params={"category": "family"}) == [family]


def test_list_filter_with_no_matches_returns_empty():
    create(category="work")
    assert request("GET", "/contacts/", 200, params={"category": "family"}) == []


def test_list_filter_invalid_category_is_422():
    body = request("GET", "/contacts/", 422, params={"category": "enemies"})
    assert "category" in error_fields(body)


def test_get_single_contact_matches_created():
    created = create()
    assert request("GET", f"/contacts/{created['id']}", 200) == created


def test_get_missing_contact_is_404():
    body = request("GET", "/contacts/999", 404)
    assert body["detail"] == "Contact with id 999 not found"


def test_get_non_integer_id_is_422():
    assert "contact_id" in error_fields(request("GET", "/contacts/abc", 422))


# ---------------------------------------------------------------------------
# PATCH /contacts/{id}
# ---------------------------------------------------------------------------

def test_patch_updates_only_sent_fields():
    created = create()
    body = request("PATCH", f"/contacts/{created['id']}", 200, json={"last_name": "Byron"})

    assert body["last_name"] == "Byron"
    for key in ("first_name", "email", "phone", "category", "id", "created_at"):
        assert body[key] == created[key], f"{key} changed unexpectedly"

    # The change is persisted, not just echoed back
    assert request("GET", f"/contacts/{created['id']}", 200) == body


def test_patch_multiple_fields():
    created = create()
    changes = {"email": "ada@newmail.com", "category": "family", "phone": "5155559999"}
    body = request("PATCH", f"/contacts/{created['id']}", 200, json=changes)
    for key, value in changes.items():
        assert body[key] == value


def test_patch_empty_body_changes_nothing():
    created = create()
    assert request("PATCH", f"/contacts/{created['id']}", 200, json={}) == created


def test_patch_can_clear_optional_phone():
    created = create()
    body = request("PATCH", f"/contacts/{created['id']}", 200, json={"phone": None})
    assert body["phone"] is None


@pytest.mark.parametrize(
    "bad_update, field",
    [
        ({"email": "no-at-sign"}, "email"),
        ({"first_name": ""}, "first_name"),
        ({"last_name": "x" * 51}, "last_name"),
        ({"phone": "123"}, "phone"),
        ({"category": "coworkers"}, "category"),
    ],
)
def test_patch_validation_errors(bad_update, field):
    created = create()
    body = request("PATCH", f"/contacts/{created['id']}", 422, json=bad_update)
    assert field in error_fields(body)
    # The stored contact must be untouched after a rejected update
    assert request("GET", f"/contacts/{created['id']}", 200) == created


def test_patch_null_required_field_is_rejected():
    created = create()
    body = request("PATCH", f"/contacts/{created['id']}", 422, json={"email": None})
    assert "email" in body["detail"]
    assert request("GET", f"/contacts/{created['id']}", 200) == created


def test_patch_missing_contact_is_404():
    request("PATCH", "/contacts/999", 404, json={"first_name": "Nobody"})


def test_patch_moves_contact_between_category_filters():
    created = create(category="work")
    request("PATCH", f"/contacts/{created['id']}", 200, json={"category": "family"})
    assert request("GET", "/contacts/", 200, params={"category": "work"}) == []
    family = request("GET", "/contacts/", 200, params={"category": "family"})
    assert [c["id"] for c in family] == [created["id"]]


# ---------------------------------------------------------------------------
# App wiring / OpenAPI documentation
# ---------------------------------------------------------------------------

def test_root_health_check():
    assert "running" in request("GET", "/", 200)["message"]


def test_openapi_documents_routes_and_constraints():
    spec = request("GET", "/openapi.json", 200)
    assert {"/contacts/", "/contacts/{contact_id}"} <= set(spec["paths"])
    assert {"get", "post"} <= set(spec["paths"]["/contacts/"])
    assert {"get", "patch"} <= set(spec["paths"]["/contacts/{contact_id}"])

    schemas = spec["components"]["schemas"]
    first_name = schemas["ContactCreate"]["properties"]["first_name"]
    assert (first_name["minLength"], first_name["maxLength"]) == (1, 50)
    assert set(schemas["ContactCategory"]["enum"]) == {"personal", "work", "family"}
    assert {"id", "created_at"} <= set(schemas["ContactResponse"]["properties"])
