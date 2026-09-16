# exercises/security-basics/app/routers/items.py
# L10 — Secure item endpoints

import re
import unicodedata
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.item import Item

router = APIRouter()


# --------------------------------------------------------------------------
# Sanitization
# --------------------------------------------------------------------------
#
# Key concept: input sanitization prevents stored XSS. If user-submitted text
# is later placed into a page without escaping, any markup in it runs as
# markup. Stripping tags before the value is stored means a careless template
# later on cannot turn stored text into a script.
#
# The starter's hint — one re.sub() over <...> — is not enough on its own, and
# verify_security.py section [2] proves it. A tag that is never closed has no
# ">" for the pattern to find, so it survives untouched:
#
#     <img src=x onerror=alert(1)//
#
# Stored like that and rendered inside <div>{name}</div>, the browser uses
# the ">" from the closing </div> to finish the img tag, and onerror fires.
# The payload does not need its own ">" because the page supplies one.
#
# So sanitize_text() makes two passes:
#
#   1. Remove every complete tag: "<", a character that can start a tag, then
#      anything up to ">". Repeated until nothing changes, because removing
#      one tag can join the text on either side of it into a new one:
#
#          <<b>script>   ->   <script>
#
#      Each round makes the string shorter, so the loop always ends.
#
#   2. Remove an unterminated tag: a "<" that starts a tag, through to the end
#      of the string. That is exactly how a browser reads it, as a tag whose
#      attributes run to the end of the text, so it is removed the same way
#      a complete tag is.
#
# "Can start a tag" means an ASCII letter, "/", "!" or "?" — the characters
# after which the HTML parser leaves text mode. That condition is what keeps
# ordinary text intact. Without it, "3 < 5 and 5 > 3" looks like one long tag
# from the first "<" to the first ">" and is reduced to "3  3", which
# verify_security.py section [1] caught in an earlier version of this file.
#
# Two things this deliberately does not do:
#
#   - Decode HTML entities. "&lt;script&gt;" stays as those literal
#     characters. Decoding AFTER stripping would manufacture a real <script>
#     tag out of text that had just passed the check.
#   - Remove the text between tags. "<script>alert(1)</script>" becomes the
#     plain text "alert(1)", which is inert: it is only dangerous as markup.
#
# Sanitizing input is a second line of defence. The first is escaping on
# output (Jinja2 and React both do it by default), because only the output
# side knows the context: HTML body, attribute, URL, or JavaScript.

_COMPLETE_TAG = re.compile(r"<[A-Za-z/!?][^>]*>")
_UNTERMINATED_TAG = re.compile(r"<[A-Za-z/!?][\s\S]*")


def sanitize_text(value: str) -> str:
    """Removes HTML tags and surrounding whitespace from user-submitted text.

    Whitespace is stripped LAST. Stripping first would leave the space in
    "<b> </b>Widget" behind once the tags were gone, and the stored name
    would be " Widget".
    """
    without_tags = value
    while True:
        stripped = _COMPLETE_TAG.sub("", without_tags)
        if stripped == without_tags:
            break
        without_tags = stripped

    without_unterminated = _UNTERMINATED_TAG.sub("", without_tags)
    return without_unterminated.strip()


# Characters that occupy a position in a string but display as nothing. Not
# whitespace to str.strip(), so without this a name of two zero-width spaces
# would pass the "name must contain text" rule and show up as a blank row.
# Categories Cf (format: zero-width space, BOM, soft hyphen) and Cc (control:
# NUL and friends), plus the few letters and symbols that render blank and
# are known for being used as invisible names.
_BLANK_LOOKING = {"\u115f", "\u1160", "\u2800", "\u3164", "\uffa0"}


def has_visible_text(text: str) -> bool:
    """True if `text` contains at least one character that displays."""
    return any(
        not char.isspace()
        and unicodedata.category(char) not in ("Cf", "Cc")
        and char not in _BLANK_LOOKING
        for char in text
    )


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
