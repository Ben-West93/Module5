# exercises/validated-contacts/app/routers/contacts.py
# L3 — Contact book endpoints

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.contact import (
    ContactCategory,
    ContactCreate,
    ContactResponse,
    ContactUpdate,
)

router = APIRouter()

# In-memory storage. Resets every time the server restarts — that's fine for L3,
# and it's why the tests call reset_storage() between cases.
contacts: list[dict] = []
_next_id = 1

# Fields a client is allowed to blank out with an explicit null. Everything else
# is required by ContactResponse, so writing None there would break serialization.
NULLABLE_FIELDS = {"phone"}


def reset_storage() -> None:
    """Clear all contacts and restart IDs at 1. Used by the tests."""
    global _next_id
    contacts.clear()
    _next_id = 1


def _find_contact(contact_id: int) -> dict:
    """Return the stored dict for contact_id, or raise 404."""
    for contact in contacts:
        if contact["id"] == contact_id:
            return contact
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Contact {contact_id} not found",
    )


def _reject_duplicate_email(email: str, exclude_id: Optional[int] = None) -> None:
    """Raise 409 if another contact already uses this email (case-insensitive)."""
    for contact in contacts:
        if contact["id"] == exclude_id:
            continue
        if contact["email"].lower() == email.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A contact with email {email} already exists",
            )


@router.post("/", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
def create_contact(contact: ContactCreate):
    """Create a new contact. The body is validated by ContactCreate before we get here."""
    global _next_id

    _reject_duplicate_email(contact.email)

    stored = contact.model_dump()
    stored["id"] = _next_id
    stored["created_at"] = datetime.now(timezone.utc).isoformat()

    contacts.append(stored)
    _next_id += 1

    return stored


@router.get("/", response_model=list[ContactResponse])
def list_contacts(category: Optional[ContactCategory] = Query(None)):
    """Return all contacts, optionally filtered with ?category=work."""
    if category is None:
        return contacts
    return [c for c in contacts if c["category"] == category]


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(contact_id: int):
    """Return a single contact by ID, or 404."""
    return _find_contact(contact_id)


@router.patch("/{contact_id}", response_model=ContactResponse)
def update_contact(contact_id: int, contact_data: ContactUpdate):
    """
    Partially update a contact.

    model_dump(exclude_unset=True) returns only the keys the client actually sent,
    so omitted fields keep their stored values.

    Careful: "unset" is not the same as "null". A client who sends
    {"first_name": null} HAS set that key, so it survives exclude_unset and would
    write None into a field ContactResponse requires to be a string — a 500 on the
    way out, and a corrupted record that makes every later read of it fail too.
    So nulls are only honored for the fields listed in NULLABLE_FIELDS.
    """
    contact = _find_contact(contact_id)

    updates = {
        key: value
        for key, value in contact_data.model_dump(exclude_unset=True).items()
        if value is not None or key in NULLABLE_FIELDS
    }

    if "email" in updates:
        _reject_duplicate_email(updates["email"], exclude_id=contact_id)

    contact.update(updates)

    return contact
