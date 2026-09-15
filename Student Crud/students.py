# exercises/student-crud/app/routers/students.py
# L6 — Full CRUD for students

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.student import Student
from app.schemas.student import (
    StudentCreate,
    StudentPatch,
    StudentResponse,
    StudentUpdate,
)

router = APIRouter()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def get_student_or_404(student_id: int, db: Session) -> Student:
    """Fetches a student by ID or raises 404.

    Every endpoint that operates on a single student goes through here, so
    the lookup and the error message are defined in exactly one place.
    """
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student {student_id} not found",
        )
    return student


def ensure_email_available(
    email: str, db: Session, *, exclude_id: Optional[int] = None
) -> None:
    """Raises 409 if `email` already belongs to a different student.

    exclude_id lets PUT/PATCH skip the row being edited, so re-sending a
    student's own email is not treated as a conflict.
    """
    stmt = select(Student).where(Student.email == email)
    if exclude_id is not None:
        stmt = stmt.where(Student.id != exclude_id)

    if db.scalars(stmt).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A student with email {email!r} already exists",
        )


def commit_or_409(db: Session, email: str) -> None:
    """Commits, converting a unique-constraint violation into a 409.

    ensure_email_available() catches the ordinary case. This is the safety
    net for the race where two requests pass that check at the same time —
    without it, the loser surfaces as an unhandled 500.

    The check on the message matters: catching every IntegrityError and
    calling it a duplicate email is wrong. A NOT NULL violation would then be
    reported as "that email already exists", which sends whoever is debugging
    it in entirely the wrong direction. Anything that is not a uniqueness
    problem gets a 400 that says what it actually was.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        cause = str(getattr(exc, "orig", exc)).lower()
        if "unique" in cause:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A student with email {email!r} already exists",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request violates a database constraint: {exc.orig}",
        )


# --------------------------------------------------------------------------
# CREATE
# --------------------------------------------------------------------------


@router.post("", response_model=StudentResponse, status_code=status.HTTP_201_CREATED)
def create_student(student: StudentCreate, db: Session = Depends(get_db)):
    """Creates a new student. Duplicate email returns 409 Conflict."""
    ensure_email_available(student.email, db)

    db_student = Student(**student.model_dump())
    db.add(db_student)
    commit_or_409(db, student.email)
    db.refresh(db_student)  # pulls back the DB-generated id and created_at
    return db_student


# --------------------------------------------------------------------------
# READ
# --------------------------------------------------------------------------


@router.get("", response_model=list[StudentResponse])
def list_students(
    grade_level: Optional[int] = Query(None, ge=1, le=12),
    is_enrolled: Optional[bool] = Query(None),
    major: Optional[str] = Query(None),
    min_gpa: Optional[float] = Query(None, ge=0.0, le=4.0),
    db: Session = Depends(get_db),
):
    """Returns students, optionally filtered.

    Filters are additive: each one is applied only if it was supplied, so
    `/students` returns everything and `/students?grade_level=9&is_enrolled=true`
    narrows on both. `is_enrolled=false` is handled correctly — the check is
    `is not None`, not a truthiness test.
    """
    stmt = select(Student)

    if grade_level is not None:
        stmt = stmt.where(Student.grade_level == grade_level)
    if is_enrolled is not None:
        # `is not None`, not truthiness: `?is_enrolled=false` is a real filter,
        # and `if is_enrolled:` would silently ignore it.
        stmt = stmt.where(Student.is_enrolled == is_enrolled)
    if major:
        # Truthiness IS right here, unlike the boolean above. A query string
        # of `?major=` arrives as "" rather than None, and "find students whose
        # major is the empty string" is never what the caller meant.
        stmt = stmt.where(Student.major == major)
    if min_gpa is not None:
        # `is not None` again: min_gpa=0.0 is falsy but valid.
        stmt = stmt.where(Student.gpa >= min_gpa)

    return db.scalars(stmt.order_by(Student.id)).all()


@router.get("/{student_id}", response_model=StudentResponse)
def get_student(student_id: int, db: Session = Depends(get_db)):
    """Returns a single student by ID, or 404."""
    return get_student_or_404(student_id, db)


# --------------------------------------------------------------------------
# UPDATE
# --------------------------------------------------------------------------


@router.put("/{student_id}", response_model=StudentResponse)
def replace_student(
    student_id: int, student: StudentUpdate, db: Session = Depends(get_db)
):
    """Fully replaces a student record. Every field must be sent.

    Because StudentUpdate marks all fields required, model_dump() without
    exclude_unset is the right call here — an omitted field is a validation
    error, not a silent no-op, and a field sent as null really does null out
    the stored value.

    `id` and `created_at` are not replaceable: they identify the row rather
    than describe it.
    """
    db_student = get_student_or_404(student_id, db)
    ensure_email_available(student.email, db, exclude_id=student_id)

    for field, value in student.model_dump().items():
        setattr(db_student, field, value)

    commit_or_409(db, student.email)
    db.refresh(db_student)
    return db_student


@router.patch("/{student_id}", response_model=StudentResponse)
def patch_student(
    student_id: int, student: StudentPatch, db: Session = Depends(get_db)
):
    """Partially updates a student. Only the fields sent are changed.

    Key concept — model_dump(exclude_unset=True):
    Pydantic tracks which fields were explicitly present in the request body.
    exclude_unset=True drops the ones that were not sent, so unmentioned
    columns keep their current values instead of being overwritten with None.

    This is why PATCH cannot null a field out, while PUT can.
    """
    db_student = get_student_or_404(student_id, db)

    updates = student.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body contained no fields to update",
        )

    if "email" in updates:
        ensure_email_available(updates["email"], db, exclude_id=student_id)

    for field, value in updates.items():
        setattr(db_student, field, value)

    commit_or_409(db, updates.get("email", db_student.email))
    db.refresh(db_student)
    return db_student


# --------------------------------------------------------------------------
# DELETE
# --------------------------------------------------------------------------


@router.delete("/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(student_id: int, db: Session = Depends(get_db)) -> Response:
    """Deletes a student by ID. Returns 204 with an empty body.

    204 means "done, nothing to send back" — it is defined as having no
    body at all, which is why this returns a bare Response rather than a
    confirmation dict. Deleting a missing student is still a 404.
    """
    db_student = get_student_or_404(student_id, db)
    db.delete(db_student)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
