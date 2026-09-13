# exercises/validated-contacts/tests/test_contacts.py
# Run with: pytest  (from the validated-contacts/ folder)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.contacts import reset_storage

client = TestClient(app)

VALID = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "phone": "5155550142",
    "category": "work",
}


@pytest.fixture(autouse=True)
def clean_storage():
    """Storage is module-level state, so reset it before every test."""
    reset_storage()


# --- happy paths ------------------------------------------------------------

def test_create_returns_201_with_server_fields():
    r = client.post("/contacts/", json=VALID)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == 1
    assert body["created_at"]


def test_phone_is_optional():
    payload = {k: v for k, v in VALID.items() if k != "phone"}
    r = client.post("/contacts/", json=payload)
    assert r.status_code == 201
    assert r.json()["phone"] is None


def test_list_and_category_filter():
    client.post("/contacts/", json=VALID)
    client.post("/contacts/", json={**VALID, "email": "bo@example.com", "category": "family"})

    assert len(client.get("/contacts/").json()) == 2

    work_only = client.get("/contacts/", params={"category": "work"}).json()
    assert [c["email"] for c in work_only] == ["ada@example.com"]


def test_get_single_contact():
    client.post("/contacts/", json=VALID)
    assert client.get("/contacts/1").json()["first_name"] == "Ada"


def test_patch_updates_only_sent_fields():
    client.post("/contacts/", json=VALID)
    r = client.patch("/contacts/1", json={"category": "personal"})
    assert r.status_code == 200
    assert r.json()["category"] == "personal"
    assert r.json()["first_name"] == "Ada"  # untouched


def test_patch_empty_body_is_a_no_op():
    client.post("/contacts/", json=VALID)
    r = client.patch("/contacts/1", json={})
    assert r.status_code == 200
    assert r.json()["first_name"] == "Ada"


# --- validation (the 422s) --------------------------------------------------

@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("email", "no-at-sign"),
        ("category", "frenemy"),
        ("phone", "123"),              # under 10 chars
        ("phone", "1" * 16),           # over 15 chars
        ("first_name", ""),            # under 1 char
        ("first_name", "A" * 51),      # over 50 chars
        ("first_name", "   "),         # whitespace-only
    ],
)
def test_invalid_create_returns_422(field, bad_value):
    r = client.post("/contacts/", json={**VALID, field: bad_value})
    assert r.status_code == 422


def test_missing_required_field_returns_422():
    payload = {k: v for k, v in VALID.items() if k != "email"}
    assert client.post("/contacts/", json=payload).status_code == 422


def test_patch_validates_too():
    """The update path must enforce the same rules as create."""
    client.post("/contacts/", json=VALID)
    assert client.patch("/contacts/1", json={"email": "broken"}).status_code == 422


# --- edge cases -------------------------------------------------------------

def test_missing_contact_returns_404():
    assert client.get("/contacts/99").status_code == 404
    assert client.patch("/contacts/99", json={"category": "work"}).status_code == 404


def test_explicit_null_on_required_field_is_ignored():
    """Regression: this used to write None and 500 on the way out."""
    client.post("/contacts/", json=VALID)
    r = client.patch("/contacts/1", json={"first_name": None})
    assert r.status_code == 200
    assert r.json()["first_name"] == "Ada"


def test_explicit_null_clears_optional_phone():
    client.post("/contacts/", json=VALID)
    r = client.patch("/contacts/1", json={"phone": None})
    assert r.status_code == 200
    assert r.json()["phone"] is None


def test_duplicate_email_rejected_case_insensitively():
    client.post("/contacts/", json=VALID)
    r = client.post("/contacts/", json={**VALID, "email": "ADA@example.com"})
    assert r.status_code == 409


def test_patch_to_own_email_is_allowed():
    """Re-sending your own email shouldn't collide with yourself."""
    client.post("/contacts/", json=VALID)
    assert client.patch("/contacts/1", json={"email": VALID["email"]}).status_code == 200


def test_ids_do_not_repeat():
    client.post("/contacts/", json=VALID)
    second = client.post("/contacts/", json={**VALID, "email": "bo@example.com"})
    assert second.json()["id"] == 2
