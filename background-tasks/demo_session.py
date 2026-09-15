"""Drives one realistic session and leaves the log files behind as evidence.

Run from background-tasks/:

    python demo_session.py

Unlike verify_background_tasks.py, this asserts almost nothing — it exists to
produce activity_log.txt and notification_log.txt in the exercise folder and
print them, so the deliverable's log files come from a real run rather than
being written by hand. It uses the default LOG_DIR and the real 2-second
delay for the same reason.

It runs against a live uvicorn server, not TestClient, so the timings printed
below are what an actual HTTP client would see. See serve.py for why that
distinction matters.
"""

import time
from datetime import datetime
from pathlib import Path

import httpx

from app.database import Base, engine
from app.main import app
from utils.notifications import ACTIVITY_LOG, NOTIFICATION_LOG
from serve import live_server

# One session, start to finish: clear the tables and both logs so the
# artifacts are a single coherent story rather than an accumulation of runs.
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
for log in (ACTIVITY_LOG, NOTIFICATION_LOG):
    log.unlink(missing_ok=True)


def timed(label: str, call):
    """Runs a request, prints how long the caller waited, returns the response."""
    started = time.monotonic()
    response = call()
    print(f"  {label:<42} {response.status_code}  ({time.monotonic() - started:.3f}s)")
    return response


with live_server(app) as base_url:
    client = httpx.Client(base_url=base_url, timeout=30.0)

    print(f"\nServer running at {base_url}\n")
    print("Requests (the number in brackets is what the client waited):")

    registration = timed(
        "POST /auth/register",
        lambda: client.post(
            "/auth/register",
            json={
                "username": "registrar",
                "email": "registrar@school.example",
                "password": "correct-horse-battery",
            },
        ),
    )
    client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"

    ada = timed(
        "POST /students  (Ada)",
        lambda: client.post(
            "/students",
            json={
                "name": "Ada Lovelace",
                "email": "ada@school.example",
                "grade_level": 11,
                "major": "Mathematics",
                "gpa": 3.9,
            },
        ),
    ).json()

    alan = timed(
        "POST /students  (Alan)",
        lambda: client.post(
            "/students",
            json={
                "name": "Alan Turing",
                "email": "alan@school.example",
                "grade_level": 12,
                "major": "Computer Science",
                "gpa": 3.4,
                "is_enrolled": False,
            },
        ),
    ).json()

    timed(
        "DELETE /students  (Alan, unenrolled)",
        lambda: client.delete(f"/students/{alan['id']}"),
    )

    report = timed(
        "POST /reports  (enrollment, 500 rows)",
        lambda: client.post("/reports", json={"report_type": "enrollment", "rows": 500}),
    ).json()

    timed(
        "POST /reports/notifications",
        lambda: client.post(
            "/reports/notifications",
            json={
                "recipient": "principal@school.example",
                "message": "The enrollment report has been requested.",
            },
        ),
    )

    # Poll the report the way a real client would, and show the states it
    # passes through — this is the visible proof that the POST above came back
    # long before the work behind it was done.
    print("\nPolling the report:")
    last = None
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        status = client.get(f"/reports/{report['report_id']}").json()
        if status["status"] != last:
            last = status["status"]
            print(f"  {datetime.now().strftime('%H:%M:%S.%f')[:-3]}  {last}")
        if last in ("complete", "failed"):
            break
        time.sleep(0.05)

    print(f"\n  result: {status.get('result')}")

    # The notification queued above sleeps 2 seconds; wait for it so the log
    # file is complete before the server shuts down.
    print("\nWaiting for the queued notifications to finish...")
    time.sleep(3.0)

    client.close()


def show(path: Path) -> None:
    print(f"\n{'─' * 72}\n{path.name}\n{'─' * 72}")
    if not path.exists():
        print("  (not created)")
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        print(f"  {line}")


show(ACTIVITY_LOG)
show(NOTIFICATION_LOG)
print()
