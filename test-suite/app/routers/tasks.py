# exercises/test-suite/app/routers/tasks.py
# L11 — Task endpoints
#
# The endpoints behave as the starter specified. Changes, each with a
# regression test in tests/test_tasks.py:
#
# - Routes are "" rather than "/", as L10 established for /items. With the
#   "/tasks" prefix, "/" makes the real path /tasks/, and a request to /tasks
#   first gets a 307 redirect. Under the 10-per-minute rate limit that redirect
#   spends a second request of the caller's budget on every call.
#
# - A unique-title clash in PATCH was a 500. The starter checked for duplicate
#   titles in POST but not in PATCH, so renaming a task to an existing title
#   hit the database's UNIQUE constraint as an unhandled IntegrityError. Both
#   now turn that error into a 409.
#
# - An id too large for SQLite (GET /tasks/99999999999999999999) was a 500:
#   Python accepts the integer, the sqlite3 driver raises OverflowError. Ids
#   are now bounded to what the column can hold, so that is a 422.
#
# - GET /tasks orders by id, so the list comes back in a stable order.
#
# A second round of edge-case probing found three more:
#
# - A PATCH racing a DELETE was a 500. The starter loaded the task, changed it
#   in Python, then committed. If another request deleted the row in between,
#   the UPDATE matched nothing (StaleDataError) or the refresh after it found
#   nothing ("Could not refresh instance"). PATCH now updates with a single
#   statement and checks how many rows it matched.
#
# - Simultaneous DELETEs of one task all returned 200. An ORM delete that
#   matches no row only emits a warning, so every request reported
#   "Task N deleted". DELETE now checks the row count too, and only the one
#   that removed the row says so.
#
# - One task answered at several URLs. Pydantic's lax integer parsing turned
#   "1_0" into 10 and "+1" or " 1" into 1. Ids must now be written as plain
#   digits.

import re
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BeforeValidator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task
from app.schemas.task import TaskCreate, TaskPatch, TaskResponse

router = APIRouter()

DUPLICATE_TITLE = "A task with that title already exists"

# SQLite stores INTEGER as a signed 64-bit value. Ids start at 1.
SQLITE_MAX_INTEGER = 2**63 - 1

# At most 19 digits (the length of SQLITE_MAX_INTEGER) and no leading zero, so
# every id has exactly one spelling. The length cap also means an absurd
# 5,000-digit id is refused before anything tries to turn it into a number.
_PLAIN_ID = re.compile(r"[1-9][0-9]{0,18}")


def _plain_digits_only(value):
    """Refuses any spelling of an id other than plain digits.

    Path values arrive as strings. Without this, Pydantic would accept "1_0"
    (Python's digit separator), "+1" and " 1", so /tasks/1_0 served task 10.
    """
    if isinstance(value, str) and not _PLAIN_ID.fullmatch(value):
        raise ValueError("task_id must be written as plain digits, such as 42")
    return value


TaskId = Annotated[
    int,
    BeforeValidator(_plain_digits_only),
    Path(ge=1, le=SQLITE_MAX_INTEGER, description="The task's id"),
]


def not_found(task_id: int) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task {task_id} not found")


def get_task_or_404(db: Session, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise not_found(task_id)
    return task


def raise_conflict_or_reraise(db: Session, exc: IntegrityError) -> NoReturn:
    """Reports a unique-title clash as 409; any other IntegrityError is a real bug.

    Title is the only unique column on tasks, and the schemas stop NULL from
    reaching the NOT NULL columns, so a UNIQUE failure is the only
    IntegrityError a valid request can meet.
    """
    db.rollback()
    if "UNIQUE" in str(exc.orig).upper():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_TITLE) from None
    raise exc


def commit_or_conflict(db: Session) -> None:
    """Commits, reporting a unique-title clash as 409 instead of a 500.

    The duplicate check in create_task() gives the common case a clear answer
    without touching the constraint. It cannot close the gap on its own: two
    requests with the same new title can both pass the check before either
    commits. The UNIQUE constraint on the column is what actually guarantees
    uniqueness, so its error is handled here too.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        raise_conflict_or_reraise(db, exc)


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    # task.title is already normalized and sanitized, so "<b>Chores</b>"
    # clashes with "Chores".
    existing = db.execute(select(Task).where(Task.title == task.title)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_TITLE)
    db_task = Task(**task.model_dump())
    db.add(db_task)
    commit_or_conflict(db)
    db.refresh(db_task)
    return db_task


@router.get("", response_model=list[TaskResponse])
def list_tasks(db: Session = Depends(get_db)):
    return db.execute(select(Task).order_by(Task.id)).scalars().all()


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: TaskId, db: Session = Depends(get_db)):
    return get_task_or_404(db, task_id)


@router.patch("/{task_id}", response_model=TaskResponse)
def patch_task(task_id: TaskId, task_data: TaskPatch, db: Session = Depends(get_db)):
    # exclude_unset: only fields the client actually sent are applied.
    changes = task_data.model_dump(exclude_unset=True)
    if not changes:
        return get_task_or_404(db, task_id)

    # One UPDATE ... WHERE id = ?, rather than load-modify-commit. There is no
    # gap between finding the row and changing it for a DELETE to fall into:
    # either the row is still there and is updated, or it is gone and the
    # statement matches nothing. A nonexistent id is simply the second case.
    try:
        result = db.execute(update(Task).where(Task.id == task_id).values(**changes))
    except IntegrityError as exc:
        raise_conflict_or_reraise(db, exc)
    if result.rowcount == 0:
        db.rollback()
        raise not_found(task_id)
    commit_or_conflict(db)

    # Read back what was stored, so the response is the row, not the request.
    # A DELETE can still land between the commit and this read; the task then
    # no longer exists, and 404 says exactly that.
    task = db.get(Task, task_id)
    if task is None:
        raise not_found(task_id)
    return task


@router.delete("/{task_id}")
def delete_task(task_id: TaskId, db: Session = Depends(get_db)):
    # The row count, not an earlier lookup, decides the answer. When several
    # DELETEs arrive together, only the one that actually removed the row
    # reports success; the rest get 404, the same as deleting twice in a row.
    result = db.execute(delete(Task).where(Task.id == task_id))
    if result.rowcount == 0:
        db.rollback()
        raise not_found(task_id)
    db.commit()
    return {"message": f"Task {task_id} deleted"}
