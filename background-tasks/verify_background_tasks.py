"""Checks the L9 background-task behaviour against a real running server.

Run from background-tasks/:

    python verify_background_tasks.py

What it sets out to establish, in the exercise's own words:

    A  the POST response returns instantly — it does not wait 2 seconds
    B  the log files are created, with entries matching what was sent
    C  the DELETE activity is logged

and the things that have to hold for those to mean anything:

    D  work is scheduled only when the request actually succeeded
    E  a report walks pending -> processing -> complete while the caller polls
    F  L6/L7/L8 behaviour still works

LOG_DIR is redirected to a scratch directory below, BEFORE app.utils.
notifications is imported, because that module resolves its paths at import
time. Two reasons: a verification run must not append noise to the
activity_log.txt the grader reads, and it must not accidentally pass by
matching a line an earlier run left behind.
"""

import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# --- must happen before the app is imported ---------------------------------
SCRATCH = Path(tempfile.mkdtemp(prefix="bgtasks-verify-"))
os.environ["LOG_DIR"] = str(SCRATCH)
# Left at their real values on purpose: the delay IS the thing being measured,
# and shortening it here would make the headline check meaningless.
os.environ.setdefault("NOTIFICATION_DELAY_SECONDS", "2")
os.environ.setdefault("REPORT_DURATION_SECONDS", "2")
# ----------------------------------------------------------------------------

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from utils.notifications import (  # noqa: E402
    ACTIVITY_LOG,
    NOTIFICATION_DELAY_SECONDS,
    NOTIFICATION_LOG,
)
from serve import live_server  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, actual, expected) -> None:
    global checks
    checks += 1
    if actual == expected:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
        failures.append(label)


def read_log(path: Path) -> list[str]:
    """Lines currently in a log file. A missing file is an empty log."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def find_line(path: Path, needle: str) -> str | None:
    for line in read_log(path):
        if needle in line:
            return line
    return None


def wait_for_line(path: Path, needle: str, timeout: float = 10.0) -> str | None:
    """Polls a log until a line containing `needle` shows up, or gives up.

    Polling rather than sleeping a fixed interval: the task finishes when it
    finishes, and a fixed sleep is either a flaky test or a slow one.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = find_line(path, needle)
        if line is not None:
            return line
        time.sleep(0.05)
    return None


def stamp_of(line: str) -> datetime:
    """Parses the leading ISO-8601 timestamp out of a log line."""
    return datetime.fromisoformat(line.split(" | ", 1)[0])


# Start from empty tables so the run is repeatable.
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

CREDENTIALS = {
    "username": "bgtester",
    "email": "bgtester@example.com",
    "password": "correct-horse-battery",
}

try:
    with live_server(app) as base_url:
        client = httpx.Client(base_url=base_url, timeout=30.0)
        anon = httpx.Client(base_url=base_url, timeout=30.0)

        registration = client.post("/auth/register", json=CREDENTIALS)
        assert registration.status_code == 201, (
            f"setup failed to register: {registration.status_code} {registration.text}"
        )
        client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"

        me = client.get("/users/me").json()
        USER_ID = me["id"]

        # ------------------------------------------------------------------
        print("\n[1] the logs start empty")
        # ------------------------------------------------------------------
        check("no activity log yet", ACTIVITY_LOG.exists(), False)
        check("no notification log yet", NOTIFICATION_LOG.exists(), False)
        check("the notification delay under test is 2s", NOTIFICATION_DELAY_SECONDS, 2.0)

        # ------------------------------------------------------------------
        print("\n[2] POST /students returns before the notification is sent")
        # ------------------------------------------------------------------
        payload = {
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "grade_level": 11,
            "major": "Mathematics",
            "gpa": 3.9,
            "is_enrolled": True,
        }

        started = time.monotonic()
        response = client.post("/students", json=payload)
        elapsed = time.monotonic() - started
        returned_at = datetime.now().astimezone()

        check("status is 201", response.status_code, 201)
        body = response.json()
        for field, sent in payload.items():
            check(f"returned {field} matches what was sent", body.get(field), sent)
        student_id = body["id"]

        print(f"        response took {elapsed:.3f}s; the notification sleeps 2.000s")
        check("the response beat the 2-second notification", elapsed < 1.0, True)

        # The timing number alone could be explained by a task that never ran.
        # This is the direct form of the claim: at the instant the response
        # arrived, the work had provably not happened yet.
        check(
            "nothing was in the notification log when the response arrived",
            find_line(NOTIFICATION_LOG, "ada@example.com"),
            None,
        )

        notification = wait_for_line(NOTIFICATION_LOG, "ada@example.com")
        check("the notification did arrive, a moment later", notification is not None, True)

        activity = wait_for_line(ACTIVITY_LOG, f"created student {student_id}")
        check("the creation was logged", activity is not None, True)

        if activity and notification:
            check("the activity line records who acted", f"user_id={USER_ID}" in activity, True)
            check("  and what they created", f"({payload['email']})" in activity, True)
            check("the notification is addressed to the student", f"to={payload['email']}" in notification, True)
            check("  and names them", payload["name"] in notification, True)
            check("  and quotes the grade level that was sent", f"grade {payload['grade_level']}" in notification, True)
            check("  and describes an enrolled student as enrolled", "You are enrolled in" in notification, True)

            # Ordering: add_task() is a queue, so the log line (added first)
            # must be written before the notification (added second), and the
            # gap between them should be about the length of the sleep.
            gap = (stamp_of(notification) - stamp_of(activity)).total_seconds()
            print(f"        activity -> notification gap: {gap:.3f}s")
            check("tasks ran in the order they were added", gap > 0, True)
            check("  with the sleep in between them", 1.5 < gap < 4.0, True)

            # Both log lines must be stamped after the client had its response.
            check(
                "the notification was written after the response was received",
                stamp_of(notification) > returned_at,
                True,
            )

        # ------------------------------------------------------------------
        print("\n[3] the same POST through TestClient — why the number above needs a real server")
        # ------------------------------------------------------------------
        # Starlette runs background tasks at the end of the response's ASGI
        # call. TestClient awaits that call, so it waits for them; a real HTTP
        # client does not. Measuring both makes the difference explicit rather
        # than a footnote someone has to take on trust.
        with TestClient(app) as test_client:
            test_client.headers["Authorization"] = client.headers["Authorization"]
            started = time.monotonic()
            tc_response = test_client.post(
                "/students", json=dict(payload, email="grace@example.com", name="Grace Hopper")
            )
            tc_elapsed = time.monotonic() - started

        check("TestClient got the same 201", tc_response.status_code, 201)
        print(f"        TestClient took {tc_elapsed:.3f}s vs {elapsed:.3f}s over a socket")
        check("TestClient blocks until the tasks finish", tc_elapsed > NOTIFICATION_DELAY_SECONDS, True)
        check("  which is why this script uses a real server", elapsed < tc_elapsed, True)

        # ------------------------------------------------------------------
        print("\n[4] DELETE /students/{id} logs the deletion")
        # ------------------------------------------------------------------
        # The business rule from L6 still applies: unenroll first.
        check(
            "unenrolling first",
            client.patch(f"/students/{student_id}", json={"is_enrolled": False}).status_code,
            200,
        )
        deletion = client.delete(f"/students/{student_id}")
        check("delete returns 204", deletion.status_code, 204)
        check("  with an empty body", deletion.content, b"")

        # A 204 carries no body, so a task that silently failed to run would
        # leave no trace in the response at all. This is the only place the
        # claim in delete_student()'s docstring can actually be tested: a bare
        # Response object still carries its BackgroundTasks.
        delete_line = wait_for_line(ACTIVITY_LOG, f"deleted student {student_id}")
        check("the deletion was logged despite the bare 204 Response", delete_line is not None, True)
        if delete_line:
            check("  naming the user who deleted", f"user_id={USER_ID}" in delete_line, True)
            check("  and the record that was removed", payload["email"] in delete_line, True)
        check("the student is really gone", client.get(f"/students/{student_id}").status_code, 404)

        # ------------------------------------------------------------------
        print("\n[5] the notification reflects what was actually stored")
        # ------------------------------------------------------------------
        # is_enrolled can be sent as False, and the welcome note has to say so
        # rather than congratulating the student on an enrollment that did not
        # happen. Caught by reading a real log rather than by a failing check.
        unenrolled = client.post(
            "/students",
            json={"name": "Mary Jackson", "email": "mary@example.com", "grade_level": 9, "is_enrolled": False},
        )
        check("created unenrolled", unenrolled.status_code, 201)
        check("  and stored as unenrolled", unenrolled.json()["is_enrolled"], False)
        mary = wait_for_line(NOTIFICATION_LOG, "mary@example.com")
        check("the unenrolled student was notified", mary is not None, True)
        if mary:
            check("  without claiming they are enrolled", "You are enrolled in" in mary, False)
            check("  saying their record is on file instead", "not currently enrolled" in mary, True)

        # ------------------------------------------------------------------
        print("\n[6] failed requests schedule nothing")
        # ------------------------------------------------------------------
        # Tasks are added after the commit succeeds. If they were added before
        # the write, a rejected request would still send a welcome email to a
        # student who does not exist.
        before_activity = len(read_log(ACTIVITY_LOG))
        before_notifications = len(read_log(NOTIFICATION_LOG))

        check(
            "duplicate email is still 409",
            client.post("/students", json=dict(payload, email="grace@example.com")).status_code,
            409,
        )
        check(
            "invalid body is still 422",
            client.post("/students", json={"name": "X", "email": "no-at-sign", "grade_level": 4}).status_code,
            422,
        )
        check(
            "an anonymous create is still 401",
            anon.post("/students", json=dict(payload, email="anon@example.com")).status_code,
            401,
        )
        check(
            "deleting a missing student is still 404",
            client.delete("/students/9999").status_code,
            404,
        )

        # Long enough that a wrongly-queued notification would have landed.
        time.sleep(NOTIFICATION_DELAY_SECONDS + 0.5)
        check("no activity was logged for any of them", len(read_log(ACTIVITY_LOG)), before_activity)
        check("and no notification was sent", len(read_log(NOTIFICATION_LOG)), before_notifications)

        # ------------------------------------------------------------------
        print("\n[7] POST /reports returns an id to poll, not a report")
        # ------------------------------------------------------------------
        started = time.monotonic()
        queued = client.post("/reports", json={"report_type": "enrollment", "rows": 250})
        elapsed = time.monotonic() - started

        check("status is 202 Accepted", queued.status_code, 202)
        report = queued.json()
        check("  reporting status pending", report["status"], "pending")
        check("  echoing the type that was sent", report["report_type"], "enrollment")
        check("  echoing the row count that was sent", report["rows"], 250)
        check("  with no result yet", report["result"], None)
        print(f"        response took {elapsed:.3f}s; the report takes 2.000s to build")
        check("the response beat the work", elapsed < 1.0, True)

        report_id = report["report_id"]

        # Poll the way a real client would, and record which states are seen.
        seen = set()
        final = None
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            polled = client.get(f"/reports/{report_id}").json()
            seen.add(polled["status"])
            if polled["status"] in ("complete", "failed"):
                final = polled
                break
            time.sleep(0.05)

        check("polling reached a terminal state", final is not None and final["status"], "complete")
        check("  having passed through processing", "processing" in seen, True)
        if final:
            check("  and the result matches the request", final["result"]["rows_processed"], 250)
            check("  naming the right report type", final["result"]["report_type"], "enrollment")
            check("  with a completion timestamp", bool(final["completed_at"]), True)
            check(
                "  stamped after it started",
                datetime.fromisoformat(final["completed_at"]) > datetime.fromisoformat(final["started_at"]),
                True,
            )
        check("the request was logged too", wait_for_line(ACTIVITY_LOG, f"requested enrollment report {report_id}") is not None, True)

        # ------------------------------------------------------------------
        print("\n[8] reports belong to the user who asked for them")
        # ------------------------------------------------------------------
        other = httpx.Client(base_url=base_url, timeout=30.0)
        other_reg = other.post(
            "/auth/register",
            json={"username": "intruder", "email": "intruder@example.com", "password": "correct-horse-battery"},
        )
        check("a second user can register", other_reg.status_code, 201)
        other.headers["Authorization"] = f"Bearer {other_reg.json()['access_token']}"

        check("someone else's report is a 404, not a 403", other.get(f"/reports/{report_id}").status_code, 404)
        check("  so its existence is not confirmed", other.get(f"/reports/{report_id}").json()["error"], "NotFound")
        check("and they cannot delete it either", other.delete(f"/reports/{report_id}").status_code, 404)
        check("the owner can still read it", client.get(f"/reports/{report_id}").status_code, 200)
        check("an unknown id is 404", client.get("/reports/does-not-exist").status_code, 404)
        check("an anonymous request is 401", anon.get(f"/reports/{report_id}").status_code, 401)
        other.close()

        # ------------------------------------------------------------------
        print("\n[9] DELETE /reports/{id}")
        # ------------------------------------------------------------------
        check("delete returns 204", client.delete(f"/reports/{report_id}").status_code, 204)
        check("  and the report is gone", client.get(f"/reports/{report_id}").status_code, 404)
        check("  and deleting it again is 404", client.delete(f"/reports/{report_id}").status_code, 404)
        check(
            "the deletion was logged",
            wait_for_line(ACTIVITY_LOG, f"deleted report {report_id}") is not None,
            True,
        )

        # A report deleted mid-flight must not be resurrected by its own task.
        inflight = client.post("/reports", json={"report_type": "gpa", "rows": 10}).json()["report_id"]
        check("a fresh report can be deleted while still running", client.delete(f"/reports/{inflight}").status_code, 204)
        time.sleep(3.0)
        check("  and its task does not recreate it", client.get(f"/reports/{inflight}").status_code, 404)

        # ------------------------------------------------------------------
        print("\n[10] POST /reports/notifications")
        # ------------------------------------------------------------------
        note = {"recipient": "Parent@Example.com", "message": "Term reports are ready."}
        started = time.monotonic()
        queued = client.post("/reports/notifications", json=note)
        elapsed = time.monotonic() - started

        check("status is 202", queued.status_code, 202)
        check("  reporting queued rather than sent", queued.json()["status"], "queued")
        check("the response beat the send", elapsed < 1.0, True)
        check(
            "nothing was written when the response arrived",
            find_line(NOTIFICATION_LOG, "parent@example.com"),
            None,
        )

        sent = wait_for_line(NOTIFICATION_LOG, "parent@example.com")
        check("the notification was delivered afterwards", sent is not None, True)
        if sent:
            check("  with the recipient normalized to lowercase", "to=parent@example.com" in sent, True)
            check("  and the message that was sent", note["message"] in sent, True)

        check("a recipient without @ is 422", client.post("/reports/notifications", json={"recipient": "nope", "message": "hi"}).status_code, 422)
        check("an empty message is 422", client.post("/reports/notifications", json={"recipient": "a@b.com", "message": ""}).status_code, 422)
        check("an unknown report type is 422", client.post("/reports", json={"report_type": "invented"}).status_code, 422)
        check("  listing the valid ones", "sales" in client.post("/reports", json={"report_type": "invented"}).text, True)
        check("rows=0 is 422", client.post("/reports", json={"report_type": "sales", "rows": 0}).status_code, 422)
        check("an anonymous report request is 401", anon.post("/reports", json={"report_type": "sales"}).status_code, 401)

        # ------------------------------------------------------------------
        print("\n[11] the log files themselves")
        # ------------------------------------------------------------------
        activity_lines = read_log(ACTIVITY_LOG)
        notification_lines = read_log(NOTIFICATION_LOG)
        check("activity_log.txt exists", ACTIVITY_LOG.exists(), True)
        check("notification_log.txt exists", NOTIFICATION_LOG.exists(), True)
        check("activity_log.txt has entries", len(activity_lines) > 0, True)
        check("notification_log.txt has entries", len(notification_lines) > 0, True)
        check(
            "every activity line is timestamped, attributed, and describes an action",
            all(len(line.split(" | ")) == 3 and line.split(" | ")[1].startswith("user_id=") for line in activity_lines),
            True,
        )
        check(
            "every activity timestamp parses as an aware datetime",
            all(stamp_of(line).tzinfo is not None for line in activity_lines),
            True,
        )
        check(
            "the activity log is in chronological order",
            [stamp_of(line) for line in activity_lines] == sorted(stamp_of(line) for line in activity_lines),
            True,
        )
        check(
            "every notification line names a recipient",
            all(line.split(" | ")[1].startswith("to=") for line in notification_lines),
            True,
        )
        check("no password reached either log", any("correct-horse-battery" in line for line in activity_lines + notification_lines), False)

        # ------------------------------------------------------------------
        print("\n[12] the error envelope still covers the new endpoints")
        # ------------------------------------------------------------------
        for label, resp in {
            "404 on an unknown report": client.get("/reports/nope"),
            "401 without a token": anon.post("/reports", json={"report_type": "sales"}),
            "422 on a bad report type": client.post("/reports", json={"report_type": "invented"}),
        }.items():
            envelope = resp.json()
            check(
                f"{label}: has error/detail/status_code",
                sorted(set(envelope) & {"error", "detail", "status_code"}),
                ["detail", "error", "status_code"],
            )
            check(f"{label}: status_code matches the HTTP status", envelope["status_code"], resp.status_code)

        spec = client.get("/openapi.json").json()
        check("POST /reports documents its 401", "401" in spec["paths"]["/reports"]["post"]["responses"], True)
        check("GET /reports/{id} documents its 404", "404" in spec["paths"]["/reports/{report_id}"]["get"]["responses"], True)
        check(
            "the four write endpoints that schedule work are all protected",
            sorted(
                f"{method.upper()} {path}"
                for path, ops in spec["paths"].items()
                for method, op in ops.items()
                if op.get("security") and ("reports" in path or path.startswith("/students"))
            ),
            [
                "DELETE /reports/{report_id}",
                "DELETE /students/{student_id}",
                "GET /reports/{report_id}",
                "PATCH /students/{student_id}",
                "POST /reports",
                "POST /reports/notifications",
                "POST /students",
                "PUT /students/{student_id}",
            ],
        )

        client.close()
        anon.close()

    print("\n" + "=" * 60)
    print(f"logs written during this run: {SCRATCH}")
    if failures:
        print(f"{len(failures)} of {checks} check(s) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)
    print(f"All {checks} checks passed.")

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)
