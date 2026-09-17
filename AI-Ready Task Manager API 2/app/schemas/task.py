# project/starter/app/schemas/task.py
# Module 5 Project — Task schemas

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional


class TaskCreate(BaseModel):
    """Input for creating a task."""
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    completed: bool = False


class TaskPatch(BaseModel):
    """Input for partial task update."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    completed: Optional[bool] = None


class TaskResponse(TaskCreate):
    """Returned to clients."""
    id: int
    owner_id: int

    model_config = ConfigDict(from_attributes=True)
