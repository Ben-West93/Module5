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

# In-memory storage
contacts: list[dict] = []
_next_id = 1


def _find_contact(contact_id: int) -> dict:
    """Return the stored contact with this id, or raise a 404."""
    for contact in contacts:
        if contact["id"] == contact_id:
            return contact
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Contact with id {contact_id} not found",
    )


@router.post("/", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
def create_contact(contact: ContactCreate):
    """Creates a new contact."""
    global _next_id
    new_contact = contact.model_dump()
    new_contact["id"] = _next_id
    new_contact["created_at"] = datetime.now(timezone.utc).isoformat()
    contacts.append(new_contact)
    _next_id += 1
    return new_contact


@router.get("/", response_model=list[ContactResponse])
def list_contacts(category: Optional[ContactCategory] = Query(None)):
    """Returns all contacts. Optionally filter by category using ?category=work"""
    if category is None:
        return contacts
    return [c for c in contacts if c["category"] == category]


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(contact_id: int):
    """Returns a single contact by ID."""
    return _find_contact(contact_id)


@router.patch("/{contact_id}", response_model=ContactResponse)
def update_contact(contact_id: int, contact_data: ContactUpdate):
    """
    Partially updates a contact.

    model_dump(exclude_unset=True) returns only the fields the client actually
    sent, so fields they didn't include are left unchanged.
    """
    stored = _find_contact(contact_id)
    updates = contact_data.model_dump(exclude_unset=True)

    # first_name, last_name, email and category are required on a contact,
    # so an explicit null for any of them would leave the record invalid.
    required = {"first_name", "last_name", "email", "category"}
    nulled = sorted(k for k, v in updates.items() if v is None and k in required)
    if nulled:
        raise HTTPException(
            status_code=422,
            detail=f"These fields cannot be null: {', '.join(nulled)}",
        )

    stored.update(updates)
    return stored
