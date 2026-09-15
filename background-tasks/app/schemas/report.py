# exercises/background-tasks/app/schemas/report.py
# L9 — Pydantic schemas for the report and notification endpoints

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Constrained rather than a free-form str so that a typo is a 422 naming the
# three valid values, instead of a "complete" report of a type that does not
# exist. The endpoint has no way to reject it later — by the time the task
# runs, the 202 has already been sent.
ReportType = Literal["sales", "enrollment", "gpa"]


class ReportRequest(BaseModel):
    """Body for POST /reports."""

    report_type: ReportType = "sales"

    # Upper bound because `rows` is echoed back in the result and would
    # otherwise let a caller store an arbitrarily large int per report.
    rows: int = Field(100, ge=1, le=1_000_000)


class ReportResponse(BaseModel):
    """What POST /reports returns, and what GET /reports/{id} polls.

    One schema for both: a client that polls is looking at the same record it
    was handed at creation, so giving the two endpoints different shapes
    would mean writing the parsing twice.
    """

    report_id: str
    status: Literal["pending", "processing", "complete", "failed"]
    report_type: str
    rows: int
    requested_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    detail: Optional[str] = None


class NotificationRequest(BaseModel):
    """Body for POST /reports/notifications."""

    recipient: str = Field(..., max_length=200)
    message: str = Field(..., min_length=1, max_length=1000)

    @field_validator("recipient")
    @classmethod
    def _validate_recipient(cls, value: str) -> str:
        # Same minimal check the student schemas use — consistency matters
        # more here than rigour, since neither is a real address validator.
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("recipient must contain @")
        return value


class QueuedResponse(BaseModel):
    """Acknowledgement for work that has been scheduled but not done.

    Deliberately not shaped like a result. The endpoint cannot report an
    outcome it does not have yet, and a body that looked like one would be
    a lie the client has no way to detect.
    """

    status: Literal["queued"] = "queued"
    detail: str
