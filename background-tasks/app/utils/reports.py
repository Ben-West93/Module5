# exercises/background-tasks/app/utils/reports.py
# L9 — the in-memory report store and the task that fills it
#
# This lives beside notifications.py rather than inside routers/reports.py
# for two reasons. The practical one is circular imports: the router needs
# generate_report, and generate_report needs the store, so if the store sat
# in the router the two modules would import each other. The structural one
# is that a router should contain endpoints — the work an endpoint schedules
# is a different kind of thing and belongs in utils/ with the rest of it.

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("jwt_api")

# Same override trick as the notification delay: real enough to observe,
# short enough that the test suite is not dominated by sleeps.
REPORT_DURATION_SECONDS = float(os.getenv("REPORT_DURATION_SECONDS", "2"))

# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------
#
# A module-level dict, which is exactly as durable as it sounds: a restart
# loses every report, and a second uvicorn worker would have its own copy and
# answer "404" for reports the first one is holding. That is fine for an
# exercise about BackgroundTasks and wrong for anything real, where this
# would be a table or Redis. It is called out here because the limitation is
# a property of the design, not an oversight to be discovered later.
#
# Unlike the students table, this store is never written from a request
# thread and a worker thread at the same time by accident — but it IS written
# from both (the endpoint seeds "pending", the task moves it on), so the lock
# is real. `dict[key] = value` is atomic under the GIL; the
# read-modify-write in _update() is not.

report_store: dict[str, dict[str, Any]] = {}
_store_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_record(report_id: str, user_id: int, report_type: str, rows: int) -> dict:
    """Seeds a report at status "pending" and returns a copy of it.

    Called from the endpoint, before the task is scheduled, so that a client
    that polls immediately gets "pending" rather than a 404 — the record has
    to exist by the time the response leaves.
    """
    record = {
        "report_id": report_id,
        "user_id": user_id,
        "report_type": report_type,
        "rows": rows,
        "status": "pending",
        "requested_at": _now(),
        "started_at": None,
        "completed_at": None,
        "result": None,
        "detail": None,
    }
    with _store_lock:
        report_store[report_id] = record
    return dict(record)


def get_record(report_id: str) -> Optional[dict]:
    """Returns a copy of a report record, or None.

    A copy, so a caller cannot mutate the store by holding onto the result,
    and so the dict it reads cannot change under it mid-serialisation while
    the background task is writing.
    """
    with _store_lock:
        record = report_store.get(report_id)
        return dict(record) if record is not None else None


def delete_record(report_id: str) -> bool:
    """Removes a report. Returns whether it was there."""
    with _store_lock:
        return report_store.pop(report_id, None) is not None


def _update(report_id: str, **fields: Any) -> None:
    """Merges `fields` into a record, if it still exists.

    The existence check matters: a report can be deleted while its task is
    mid-sleep, and the task must not resurrect it as a half-populated ghost
    record that no endpoint will ever clean up.
    """
    with _store_lock:
        record = report_store.get(report_id)
        if record is None:
            return
        record.update(fields)


# --------------------------------------------------------------------------
# The task
# --------------------------------------------------------------------------


def generate_report(report_id: str, report_type: str, rows: int) -> None:
    """Walks a report from pending through processing to complete.

    The three states are what make the delay observable. A client that polls
    GET /reports/{id} sees "pending" or "processing" for about two seconds
    and then "complete" — which is direct evidence that the POST returned
    before the work was done, without anyone having to time anything.

    A failure sets "failed" rather than leaving the record at "processing".
    A status that never changes is indistinguishable from a task that is
    still running, so a poller would wait forever on a job that is already
    dead.
    """
    try:
        _update(report_id, status="processing", started_at=_now())
        time.sleep(REPORT_DURATION_SECONDS)
        _update(
            report_id,
            status="complete",
            completed_at=_now(),
            result={
                "report_type": report_type,
                "rows_processed": rows,
                "filename": f"{report_type}_report_{report_id[:8]}.csv",
            },
        )
    except Exception:
        logger.exception("Report %s failed", report_id)
        _update(
            report_id,
            status="failed",
            completed_at=_now(),
            detail="Report generation failed. See the server log.",
        )
