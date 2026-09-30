# exercises/database-notes/app/routers/notes.py
# L5 — Notes endpoints with database

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.schemas.note import NoteCreate, NoteResponse
from app.models.note import Note
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# SQLite stores INTEGER as a signed 64-bit value. A larger id reaches the
# driver and raises OverflowError — a 500 — so the path parameter is bounded
# and an out-of-range id is rejected with a 422 before any query runs.
NoteId = Path(ge=1, le=9_223_372_036_854_775_807, description="Note ID")


def _not_found(note_id: int) -> HTTPException:
    """Shared 404 so every endpoint words it identically."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Note {note_id} not found",
    )


def _unavailable(action: str) -> HTTPException:
    """
    503 for a database that's reachable but failing.

    503 rather than 500 because the request itself was valid and a retry may
    succeed — the client is being told to try again, not that it was wrong.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Could not {action} right now. Please try again.",
    )


@router.post("/", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
def create_note(note: NoteCreate, db: Session = Depends(get_db)):
    """Creates a new note in the database."""
    db_note = Note(**note.model_dump())

    try:
        db.add(db_note)      # stage the insert
        db.commit()          # write to notes.db — this is what makes it persist
        db.refresh(db_note)  # reload the row so id and created_at are populated
    except SQLAlchemyError:
        # Without this the client gets a plain-text "Internal Server Error"
        # body and the driver message leaks into the response.
        db.rollback()
        logger.error("Database error creating note", exc_info=True)
        raise _unavailable("save the note")

    return db_note


@router.get("/", response_model=list[NoteResponse])
def list_notes(
    category: Optional[str] = Query(None, description="Filter by category"),
    is_pinned: Optional[bool] = Query(None, description="Filter by pinned status"),
    db: Session = Depends(get_db),
):
    """
    Returns notes, optionally filtered by category and/or pinned status.

    Both filters default to None meaning "not supplied", and each is applied
    only when the client sends it. That's why is_pinned is Optional[bool]
    rather than a plain bool: with a False default there'd be no way to tell
    ?is_pinned=false from the filter being left off entirely.
    """
    query = select(Note)

    # A truthy check rather than "is not None": an empty ?category= is then
    # treated as "no filter" instead of a search for the empty string, which
    # would match nothing. is_pinned still uses "is not None", because False
    # is a meaningful value there.
    if category:
        query = query.where(Note.category == category)

    if is_pinned is not None:
        query = query.where(Note.is_pinned == is_pinned)

    # Bonus: explicit ordering. Without an ORDER BY the database is free to
    # return rows in any order it likes. SQLite happens to hand back rowid
    # order today, but that's an implementation detail rather than a promise —
    # a different engine, or a query plan that uses an index, could reorder
    # them. Sorting by id gives callers a stable, predictable sequence.
    query = query.order_by(Note.id)

    try:
        return db.execute(query).scalars().all()
    except SQLAlchemyError:
        logger.error("Database error listing notes", exc_info=True)
        raise _unavailable("load notes")


@router.get("/{note_id}", response_model=NoteResponse)
def get_note(note_id: int = NoteId, db: Session = Depends(get_db)):
    """Returns a single note by ID."""
    try:
        note = db.get(Note, note_id)
    except SQLAlchemyError:
        logger.error("Database error fetching note id=%s", note_id, exc_info=True)
        raise _unavailable("load the note")

    if note is None:
        raise _not_found(note_id)

    return note


@router.delete("/{note_id}")
def delete_note(note_id: int = NoteId, db: Session = Depends(get_db)):
    """Deletes a note by ID."""
    try:
        note = db.get(Note, note_id)
    except SQLAlchemyError:
        logger.error("Database error fetching note id=%s", note_id, exc_info=True)
        raise _unavailable("delete the note")

    # Checked outside the try so the 404 can't be mistaken for a database
    # failure, and so a reader doesn't have to work out which raises the
    # except clause is meant to catch.
    if note is None:
        raise _not_found(note_id)

    try:
        db.delete(note)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.error("Database error deleting note id=%s", note_id, exc_info=True)
        raise _unavailable("delete the note")

    return {"message": f"Note {note_id} deleted successfully"}
