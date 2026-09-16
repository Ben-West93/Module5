# exercises/test-suite/app/routers/items.py
# L10 — Secure item endpoints

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.item import Item
# Moved to app/sanitization.py in L11 so the task schemas share it.
from app.sanitization import has_visible_text, sanitize_text

router = APIRouter()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class ItemCreate(BaseModel):
    """Body for POST /items. Both text fields are sanitized on the way in.

    max_length is checked against what the client SENT, before sanitizing.
    That is deliberate: it caps the work the regexes do per request, and a
    client that sends 5,000 characters of markup around a short name has sent
    5,000 characters.
    """

    # extra="forbid": an unknown or misspelled field is a 422. Pydantic's
    # default is to drop it silently, so {"nmae": "Widget"} would have been
    # reported as a missing name rather than as the typo it is, and a field
    # the client expected to set would be ignored without a word.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=100)
    description: str = Field("", max_length=500)

    # mode="after" (the default) means Pydantic has already confirmed these
    # are strings. A "before" validator would receive whatever JSON arrived —
    # an int, a list, null — and the regexes would crash on it as a 500.
    @field_validator("name", "description")
    @classmethod
    def _sanitize(cls, value: str, info: ValidationInfo) -> str:
        cleaned = sanitize_text(value)

        # The name is required, and a name that was nothing but markup,
        # whitespace or invisible characters was not really supplied. The
        # check has to run here, after sanitizing: Field(min_length=1) would
        # run before it and accept "<b></b>", storing an item with an empty
        # name. Only the check ignores invisible characters; a name that has
        # real text keeps them exactly as sent.
        if info.field_name == "name" and not has_visible_text(cleaned):
            raise ValueError("name must contain text once HTML tags and whitespace are removed")
        return cleaned


class ItemResponse(BaseModel):
    """What the item endpoints return."""

    id: int
    name: str
    description: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# READ
# --------------------------------------------------------------------------


# The routes are declared as "" rather than the starter's "/". With the
# "/items" prefix in main.py, "/" would make the real path /items/, and a
# request to /items would get a 307 redirect first. Under a 10-per-minute rate
# limit that redirect is not free: it spends a second request of the caller's
# budget on every call.
@router.get("", response_model=list[ItemResponse])
def list_items(
    name: Optional[str] = Query(None, max_length=100, description="Exact item name to match"),
    db: Session = Depends(get_db),
):
    """
    Returns all items, optionally filtered to an exact name.

    Security note: SQL injection prevention
    ----------------------------------------
    Vulnerable (NEVER do this):

        query = f"SELECT * FROM items WHERE name = '{user_input}'"
        db.execute(text(query))

    Safe (parameterized query):

        db.execute(select(Item).where(Item.name == user_input))

    Why the first is vulnerable: the user's input is pasted INTO the SQL text
    before the database parses it, so the database cannot tell which
    characters the programmer wrote and which the user wrote. Send the name

        ' OR '1'='1

    and the statement the database receives is

        SELECT * FROM items WHERE name = '' OR '1'='1'

    The quote in the input closed the string literal early, and everything
    after it is parsed as SQL. The WHERE clause is now always true and every
    row comes back. The same trick can append UNION SELECT to read other
    tables.

    Why the second is safe: SQLAlchemy compiles it to

        SELECT ... FROM items WHERE items.name = ?

    and sends the value separately, as a bound parameter. The database parses
    the statement while the placeholder is still empty, so its structure is
    fixed before the value exists. The value then fills a slot that can only
    ever hold data. A quote in it is just a quote character in a name that
    matches nothing. This is not escaping; nothing is escaped. The input
    simply never reaches the parser.

    The ORM is not what makes it safe; the placeholder is. SQLAlchemy code can
    still be vulnerable:

        db.execute(text(f"SELECT * FROM items WHERE name = '{user_input}'"))  # vulnerable
        db.execute(text("SELECT * FROM items WHERE name = :name"), {"name": user_input})  # safe

    verify_security.py section [5] runs the vulnerable pattern against a
    scratch database to show it leaking every row, then sends the same payload
    to this endpoint.

    The filter value goes through the same sanitize_text() as stored names,
    so a search matches the way the name was saved. Sanitizing it is about
    consistency, not SQL safety: the payload above contains no tags and passes
    through unchanged, and the bound parameter is what makes it harmless.
    """
    stmt = select(Item).order_by(Item.id)

    if name is not None:
        cleaned = sanitize_text(name)
        # A blank ?name= (or one that was only markup) is treated as no
        # filter, not as "find items whose name is the empty string" — no
        # item can have an empty name, so that filter could only ever
        # return [].
        if cleaned:
            stmt = stmt.where(Item.name == cleaned)

    return db.scalars(stmt).all()


# --------------------------------------------------------------------------
# CREATE
# --------------------------------------------------------------------------


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    """
    Creates an item. Input is sanitized by the schema validator.

    By the time this function runs, `item.name` and `item.description` have
    already been cleaned. There is no path from the request body to the
    database that skips ItemCreate, so there is no way to store an unsanitized
    value through this endpoint.

    The response is built from the stored row, not echoed from the request.
    What the client sees is what was actually saved.
    """
    db_item = Item(name=item.name, description=item.description)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)  # pulls back the generated id and created_at
    return db_item
