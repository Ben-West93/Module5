# exercises/polished-docs/app/schemas/error.py
# L12 — The error envelope, as a schema, so Swagger documents it

from pydantic import BaseModel, ConfigDict


class ErrorResponse(BaseModel):
    """The body of every error response (400, 404, 405, 409, 413, 422, 429, 500)."""

    error: str
    detail: str
    status_code: int

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"error": "NotFound", "detail": "Item 42 not found", "status_code": 404}
        }
    )
