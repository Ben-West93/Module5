# project/starter/app/routers/tasks.py
# Module 5 Project — Task endpoints (auth-scoped)
#
# Every endpoint requires get_current_user, and every query filters by
# current_user.id so users can only see and modify their own tasks.

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
import logging

from app.schemas.task import TaskCreate, TaskPatch, TaskResponse
from app.database import get_db
from app.models.task import Task
from app.models.user import User
from app.auth import get_current_user
from app.auth import NotFoundException, InvalidUpdateException

router = APIRouter()
logger = logging.getLogger("task_manager")


def _get_owned_task_or_404(task_id: int, db: Session, user: User) -> Task:
    """Fetch a task owned by the user. Other users' tasks return 404 (not 403)
    so we don't reveal that the task exists."""
    task = db.query(Task).filter(Task.id == task_id, Task.owner_id == user.id).first()
    if task is None:
        raise NotFoundException(f"Task {task_id} not found")
    return task


def _log_task_event(action: str, task_id: int, username: str) -> None:
    """Background job: record task activity without slowing the response."""
    logger.info("User %s %s task %s", username, action, task_id)


@router.post("/", response_model=TaskResponse, status_code=201)
def create_task(
    task: TaskCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a task owned by the authenticated user.

    Also schedules a background job that logs the creation.
    """
    db_task = Task(**task.model_dump(), owner_id=current_user.id)
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    background_tasks.add_task(_log_task_event, "created", db_task.id, current_user.username)
    return db_task


@router.get("/", response_model=list[TaskResponse])
def list_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all tasks belonging to the authenticated user, ordered by id."""
    return db.query(Task).filter(Task.owner_id == current_user.id).order_by(Task.id).all()


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get one task by id. Returns 404 if it doesn't exist or belongs to someone else."""
    return _get_owned_task_or_404(task_id, db, current_user)


@router.patch("/{task_id}", response_model=TaskResponse)
def patch_task(
    task_id: int,
    task_data: TaskPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Partially update a task. Only the fields sent in the body are changed."""
    task = _get_owned_task_or_404(task_id, db, current_user)
    updates = task_data.model_dump(exclude_unset=True)
    if not updates:
        raise InvalidUpdateException("No fields provided to update")
    for required in ("title", "completed"):
        if required in updates and updates[required] is None:
            raise InvalidUpdateException(f"{required} cannot be null")
    for field, value in updates.items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete one of the authenticated user's tasks."""
    task = _get_owned_task_or_404(task_id, db, current_user)
    db.delete(task)
    db.commit()
    return {"message": f"Task {task_id} deleted"}


@router.get("/{task_id}/suggest")
def suggest_task_action(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Placeholder endpoint for AI-powered task suggestions.
    Returns a mock suggestion; a real LLM call can replace the logic below later.
    """
    task = _get_owned_task_or_404(task_id, db, current_user)

    if task.completed:
        suggestion = "This task is done. Consider archiving it or creating a follow-up task."
    elif not task.description:
        suggestion = "Add a description with clear acceptance criteria before starting."
    else:
        suggestion = "Break this task into 2-3 smaller steps and tackle the first one today."

    return {
        "task_id": task.id,
        "title": task.title,
        "suggestion": suggestion,
        "source": "mock",
    }
