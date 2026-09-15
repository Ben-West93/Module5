# exercises/background-tasks/app/routers/reports.py
# L9 — Report and notification endpoints
#
# Every endpoint in this file follows the same three-line shape: decide what
# needs to happen, hand it to background_tasks, return. None of them do the
# work themselves — that lives in app/utils/reports.py and
# app/utils/notifications.py.
#
# Key concept — background task vs async endpoint:
#
#   Use a background task when the work can happen AFTER the response is
#   sent and the caller does not need its outcome: sending mail, writing an
#   audit line, firing a webhook, building a file the caller will fetch
#   later.
#
#   Use `async def` when the work must finish BEFORE the response, because
#   its result is IN the response. Awaiting concurrent I/O is still
#   synchronous from the caller's point of view — it just stops the process
#   blocking a thread while it waits.
#
#   The test is not "is this slow?" but "does the caller need the answer?".
#   POST /reports is slow and returns instantly, because the caller needs a
#   receipt, not a report.

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status

from app.exceptions import NotFoundException
from app.models.user import User
from app.schemas.errors import ErrorResponse
from app.schemas.report import (
    NotificationRequest,
    QueuedResponse,
    ReportRequest,
    ReportResponse,
)
from utils.notifications import log_activity, send_notification
from app.utils.reports import create_record, delete_record, generate_report, get_record
from app.utils.security import get_current_user

router = APIRouter()

NOT_FOUND = {404: {"model": ErrorResponse, "description": "No report with that ID"}}
UNAUTHORIZED = {401: {"model": ErrorResponse, "description": "Missing or invalid bearer token"}}


def get_owned_report_or_404(report_id: str, user_id: int) -> dict:
    """Fetches a report belonging to `user_id`, or raises 404.

    Someone else's report is a 404 rather than a 403, and that is the whole
    reason this helper exists. A 403 would confirm that the id is real and
    belongs to somebody — enough to enumerate which reports exist by walking
    ids and watching the status code change. A 404 for "not yours" and
    "not there" makes the two indistinguishable, which is the same reasoning
    that gives login one message for a bad username and a bad password.
    """
    record = get_record(report_id)
    if record is None or record["user_id"] != user_id:
        raise NotFoundException(f"Report {report_id} not found")
    return record


# --------------------------------------------------------------------------
# CREATE
# --------------------------------------------------------------------------


@router.post(
    "",
    response_model=ReportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**UNAUTHORIZED},
)
def create_report(
    background_tasks: BackgroundTasks,
    payload: ReportRequest | None = None,
    current_user: User = Depends(get_current_user),
):
    """Schedules a report and returns immediately with an id to poll.

    202, not 201. 201 Created promises a resource that now exists at a URL;
    what exists here is a job, and the report it will produce does not exist
    yet. 202 Accepted means exactly "understood, queued, not done" — which is
    the honest description of what just happened.

    The body is optional because both of its fields have defaults. Declaring
    it required would mean `{}` is accepted and no body at all is a 422,
    which is an odd line to draw: the two requests ask for exactly the same
    report.

    Two tasks are scheduled, and they run in the order they were added, one
    after the other, on the same worker thread. That ordering is worth
    knowing: `add_task` is a queue, not a fan-out, so a slow task delays
    every task added after it. Here the log line goes first precisely so a
    two-second report cannot hold up the audit trail.
    """
    payload = payload or ReportRequest()

    report_id = str(uuid.uuid4())
    record = create_record(report_id, current_user.id, payload.report_type, payload.rows)

    background_tasks.add_task(
        log_activity,
        current_user.id,
        f"requested {payload.report_type} report {report_id} ({payload.rows} rows)",
    )
    background_tasks.add_task(generate_report, report_id, payload.report_type, payload.rows)

    return record


# --------------------------------------------------------------------------
# NOTIFICATIONS
# --------------------------------------------------------------------------


# Declared before /{report_id} on purpose. These two do not actually collide
# — one is a POST and the other a GET — but Starlette matches routes in
# declaration order, so a future GET /reports/notifications added below the
# parameterised route would be silently swallowed as report_id="notifications".
# Putting the literal path first means that mistake cannot happen.
@router.post(
    "/notifications",
    response_model=QueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**UNAUTHORIZED},
)
def queue_notification(
    payload: NotificationRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Queues a notification and returns without waiting for it to send.

    The response beats the send by two full seconds. Confirming delivery
    would mean either blocking the caller for that long or lying about an
    outcome that has not happened — so the body says "queued" and nothing
    more.
    """
    background_tasks.add_task(
        log_activity, current_user.id, f"queued a notification to {payload.recipient}"
    )
    background_tasks.add_task(send_notification, payload.recipient, payload.message)

    return QueuedResponse(detail=f"Notification to {payload.recipient} has been queued")


# --------------------------------------------------------------------------
# READ
# --------------------------------------------------------------------------


@router.get(
    "/{report_id}",
    response_model=ReportResponse,
    responses={**UNAUTHORIZED, **NOT_FOUND},
)
def get_report_status(report_id: str, current_user: User = Depends(get_current_user)):
    """Returns a report's current status: pending, processing, complete or failed.

    This is the polling half of the pattern. The POST hands back an id
    instead of a result, and this is where the caller comes back for the
    result once there is one.
    """
    return get_owned_report_or_404(report_id, current_user.id)


# --------------------------------------------------------------------------
# DELETE
# --------------------------------------------------------------------------


@router.delete(
    "/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**UNAUTHORIZED, **NOT_FOUND},
)
def delete_report(
    report_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> Response:
    """Discards a report and logs the deletion in the background. 204, no body.

    Deleting a report whose task is still sleeping is allowed. The task wakes
    to find the record gone and quietly does nothing — see the existence
    check in utils/reports._update().
    """
    get_owned_report_or_404(report_id, current_user.id)
    delete_record(report_id)

    background_tasks.add_task(log_activity, current_user.id, f"deleted report {report_id}")

    return Response(status_code=status.HTTP_204_NO_CONTENT)
