# exercises/background-tasks/app/schemas/errors.py
# L7 — the shape every error response takes
#
# This exists so the error format appears in the OpenAPI schema and therefore
# in Swagger UI. Without it, FastAPI only documents the success code and the
# automatic 422, so someone reading /docs would have no idea that DELETE can
# return a 400 or that POST can return a 409.

from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error body returned by every failing request."""

    error: str = Field(..., description="Stable label a client can branch on", examples=["NotFound"])
    detail: str = Field(..., description="Human-readable message; wording may change", examples=["Student 9999 not found"])
    status_code: int = Field(..., description="Repeats the HTTP status so the body is self-describing", examples=[404])
    errors: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Present only on 422: FastAPI's per-field validation breakdown",
    )
