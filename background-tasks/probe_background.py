"""Angles on the L9 background-task layer that the other scripts do not cover.

  P  concurrency — many tasks writing one file at once
  Q  hostile input reaching a log file
  R  unicode and size limits
  S  task isolation — one failing task must not silence the next
  T  the report store under parallel load
  U  protocol edges on the new endpoints

verify_background_tasks.py asks "does it work?". This asks "what happens when
someone is trying to break it, or when twenty requests arrive at once?".

Timing is deliberately NOT the subject here — the delays are turned down so
the concurrency sections are about correctness under load rather than about
sleeping. Whether the response beats the work is measured in
verify_background_tasks.py, against a real server, where the question means
something.
"""

import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# --- before the app is imported ---------------------------------------------
SCRATCH = Path(tempfile.mkdtemp(prefix="bgtasks-probe-"))
os.environ["LOG_DIR"] = str(SCRATCH)
os.environ["NOTIFICATION_DELAY_SECONDS"] = "0.05"
os.environ["REPORT_DURATION_SECONDS"] = "0.05"
# ----------------------------------------------------------------------------

import httpx  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from utils.notifications import ACTIVITY_LOG, NOTIFICATION_LOG  # noqa: E402
from serve import live_server  # noqa: E402

failures: list[str] = []
notes: list[str] = []
checks = 0


def check(label: str, actual, expected) -> None:
    global checks
    checks += 1
    if actual == expected:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
        failures.append(label)


def note(text: str) -> None:
    notes.append(text)
    print(f"  NOTE  {text}")


def read_log(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def settle(seconds: float = 1.5) -> None:
    """Gives queued tasks time to finish before the logs are inspected."""
    time.sleep(seconds)


def stamp_of(line: str) -> datetime:
    return datetime.fromisoformat(line.split(" | ", 1)[0])


Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

try:
    with live_server(app) as base_url:
        client = httpx.Client(base_url=base_url, timeout=30.0)
        anon = httpx.Client(base_url=base_url, timeout=30.0)

        registration = client.post(
            "/auth/register",
            json={"username": "prober", "email": "prober@example.com", "password": "correct-horse-battery"},
        )
        assert registration.status_code == 201, registration.text
        client.headers["Authorization"] = f"Bearer {registration.json()['access_token']}"
        USER_ID = client.get("/users/me").json()["id"]

        # ------------------------------------------------------------------
        print("\n[P] concurrency — many tasks writing one file at once")
        # ------------------------------------------------------------------
        BURST = 24

        def create(n: int):
            return client.post(
                "/students",
                json={"name": f"Student {n}", "email": f"burst{n}@example.com", "grade_level": 7},
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = [f.result() for f in [pool.submit(create, n) for n in range(BURST)]]

        check("every concurrent create succeeded", [r.status_code for r in results].count(201), BURST)
        settle(3.0)

        activity = read_log(ACTIVITY_LOG)
        notifications = read_log(NOTIFICATION_LOG)
        check("one activity line per create", len(activity), BURST)
        check("one notification per create", len(notifications), BURST)

        # The failure mode a lock exists to prevent: two appends interleaving
        # inside one line, so a line has the wrong shape rather than being
        # merely out of order.
        check(
            "no line was mangled by an interleaved write",
            all(len(line.split(" | ")) == 3 for line in activity + notifications),
            True,
        )
        check(
            "every line still starts with a parseable timestamp",
            all(stamp_of(line).tzinfo is not None for line in activity + notifications),
            True,
        )
        check(
            "no create was logged twice",
            len({line.split(" | ")[2] for line in activity}),
            BURST,
        )
        check(
            "every student that was created is in the log",
            sorted(int(r.json()["id"]) for r in results),
            sorted(int(line.split("created student ")[1].split(" ")[0]) for line in activity),
        )

        stamps = [stamp_of(line) for line in activity]
        if stamps != sorted(stamps):
            out_of_order = sum(1 for a, b in zip(stamps, stamps[1:]) if b < a)
            note(
                f"{out_of_order} activity line(s) are stamped out of order under load — "
                "the timestamp is taken before the lock is acquired, so a thread can be "
                "overtaken between stamping and writing"
            )
        else:
            check("the activity log stayed in chronological order under load", True, True)

        # Concurrent duplicates: the 409 losers must schedule nothing.
        before = len(read_log(ACTIVITY_LOG))
        same = {"name": "Racer", "email": "racer@example.com", "grade_level": 7}
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = sorted(f.result().status_code for f in [pool.submit(client.post, "/students", json=same) for _ in range(8)])
        check("exactly one of eight identical creates won", codes.count(201), 1)
        check("  and the rest were 409, not 500", set(codes) - {201}, {409})
        settle()
        check("  and only the winner was logged", len(read_log(ACTIVITY_LOG)) - before, 1)

        # ------------------------------------------------------------------
        print("\n[Q] hostile input reaching a log file")
        # ------------------------------------------------------------------
        # The activity line is built as "created student {id} ({email})" and the
        # notification embeds the student's name. Both are caller-controlled
        # strings written verbatim into a line-delimited file, which is the
        # classic setup for log forging: put a newline in the value and the
        # attacker writes their own log entry.
        forged_tail = "2020-01-01T00:00:00+00:00 | user_id=999 | deleted every student"
        injected_email = f"inject\n{forged_tail}@example.com"

        before_activity = len(read_log(ACTIVITY_LOG))
        response = client.post(
            "/students",
            json={"name": "Injector", "email": injected_email, "grade_level": 7},
        )
        settle()
        added = len(read_log(ACTIVITY_LOG)) - before_activity

        if response.status_code == 201:
            check("a newline in the email added exactly one log line", added, 1)
            check(
                "  and no forged entry appears in the log",
                any(line.strip() == forged_tail for line in read_log(ACTIVITY_LOG)),
                False,
            )
            injected_line = next((line for line in read_log(ACTIVITY_LOG) if "inject" in line), "")
            check(
                "  the attempt is escaped, not deleted — it stays on the record",
                "\\n" in injected_line and "user_id=999" in injected_line,
                True,
            )
            check(
                "  and the forged pipes are escaped, so the line still has 3 fields",
                len(injected_line.split(" | ")),
                3,
            )
        else:
            check("a newline in the email was rejected outright", response.status_code, 422)
            check("  so nothing was logged", added, 0)

        # Same attack through the name, which reaches the notification log.
        forged_note = "2020-01-01T00:00:00+00:00 | to=victim@example.com | Your account is closed"
        before_notifications = len(read_log(NOTIFICATION_LOG))
        response = client.post(
            "/students",
            json={"name": f"Nice Person\n{forged_note}", "email": "namehack@example.com", "grade_level": 7},
        )
        settle()
        added = len(read_log(NOTIFICATION_LOG)) - before_notifications

        if response.status_code == 201:
            check("a newline in the name added exactly one notification line", added, 1)
            check(
                "  and no forged notification appears",
                any(line.strip() == forged_note for line in read_log(NOTIFICATION_LOG)),
                False,
            )
        else:
            check("a newline in the name was rejected outright", response.status_code, 422)
            check("  so nothing was sent", added, 0)

        # The notification endpoint takes a free-text message directly.
        before_notifications = len(read_log(NOTIFICATION_LOG))
        response = client.post(
            "/reports/notifications",
            json={"recipient": "target@example.com", "message": f"hello\n{forged_note}"},
        )
        settle()
        added = len(read_log(NOTIFICATION_LOG)) - before_notifications
        if response.status_code == 202:
            check("a newline in a notification message adds exactly one line", added, 1)
        else:
            check("a newline in a notification message was rejected", response.status_code, 422)

        # A carriage return alone also starts a new line in most log viewers.
        before_activity = len(read_log(ACTIVITY_LOG))
        response = client.post(
            "/students",
            json={"name": "CR", "email": "cr\r2020-01-01 | user_id=1 | forged@example.com", "grade_level": 7},
        )
        settle()
        if response.status_code == 201:
            check("a carriage return adds exactly one line", len(read_log(ACTIVITY_LOG)) - before_activity, 1)
            check(
                "  and leaves no bare CR in the file",
                "\r" in ACTIVITY_LOG.read_text(encoding="utf-8"),
                False,
            )
        else:
            check("a carriage return was rejected outright", response.status_code, 422)

        # ------------------------------------------------------------------
        print("\n[R] unicode and size limits")
        # ------------------------------------------------------------------
        unicode_name = "Zoë Ωmega 学生 🎓"
        created = client.post(
            "/students",
            json={"name": unicode_name, "email": "zoë@example.com", "grade_level": 7},
        )
        check("a unicode name and email are accepted", created.status_code, 201)
        settle()
        check(
            "the name round-trips into the notification log intact",
            any(unicode_name in line for line in read_log(NOTIFICATION_LOG)),
            True,
        )
        check(
            "the log file is still valid UTF-8",
            isinstance(NOTIFICATION_LOG.read_text(encoding="utf-8"), str),
            True,
        )

        check(
            "a 100-character name is accepted",
            client.post("/students", json={"name": "x" * 100, "email": "long@example.com", "grade_level": 7}).status_code,
            201,
        )
        check(
            "a 101-character name is 422",
            client.post("/students", json={"name": "x" * 101, "email": "toolong@example.com", "grade_level": 7}).status_code,
            422,
        )
        check(
            "a 1000-character notification message is accepted",
            client.post("/reports/notifications", json={"recipient": "a@b.com", "message": "m" * 1000}).status_code,
            202,
        )
        check(
            "a 1001-character message is 422",
            client.post("/reports/notifications", json={"recipient": "a@b.com", "message": "m" * 1001}).status_code,
            422,
        )

        # ------------------------------------------------------------------
        print("\n[S] task isolation")
        # ------------------------------------------------------------------
        # Starlette runs queued tasks sequentially and does NOT catch their
        # exceptions: if one raises, the ones behind it never run. That is why
        # both task functions catch internally. Simulating a failure directly
        # is awkward from the client side, so this checks the property that
        # matters — a task whose write fails must not take the next one down.
        import utils.notifications as notifications_module

        original = notifications_module._append_record
        calls = {"count": 0}

        def exploding_append(path, *fields):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("simulated disk failure")
            return original(path, *fields)

        notifications_module._append_record = exploding_append
        try:
            response = client.post(
                "/students",
                json={"name": "Isolation", "email": "isolation@example.com", "grade_level": 7},
            )
            check("the request still succeeded", response.status_code, 201)
            settle()
            check("the failing task did not stop the next one", calls["count"] >= 2, True)
            check(
                "the notification behind the failed log line still went out",
                any("isolation@example.com" in line for line in read_log(NOTIFICATION_LOG)),
                True,
            )
        finally:
            notifications_module._append_record = original

        # ------------------------------------------------------------------
        print("\n[T] the report store under parallel load")
        # ------------------------------------------------------------------
        with ThreadPoolExecutor(max_workers=10) as pool:
            reports = [
                f.result().json()
                for f in [
                    pool.submit(client.post, "/reports", json={"report_type": "sales", "rows": n + 1})
                    for n in range(10)
                ]
            ]
        ids = [r["report_id"] for r in reports]
        check("ten concurrent reports got ten distinct ids", len(set(ids)), 10)
        settle(2.0)

        finals = [client.get(f"/reports/{rid}").json() for rid in ids]
        check("all ten completed", [f["status"] for f in finals].count("complete"), 10)
        check(
            "each result carries its own row count, not another report's",
            sorted(f["result"]["rows_processed"] for f in finals),
            list(range(1, 11)),
        )
        check(
            "each report kept its own id",
            all(f["report_id"] == rid for f, rid in zip(finals, ids)),
            True,
        )

        # ------------------------------------------------------------------
        print("\n[U] protocol edges on the new endpoints")
        # ------------------------------------------------------------------
        check("GET on /reports (collection) is 405", client.get("/reports").status_code, 405)
        check("  in the standard envelope", client.get("/reports").json()["error"], "MethodNotAllowed")
        check("PUT on a report is 405", client.put("/reports/abc", json={}).status_code, 405)
        check("a missing body on POST /reports is accepted (all fields default)", client.post("/reports").status_code, 202)
        check("a non-JSON body is 422", client.post("/reports", content="not json", headers={"Content-Type": "application/json"}).status_code, 422)
        check("a JSON array instead of an object is 422", client.post("/reports", json=[1, 2, 3]).status_code, 422)
        check("rows as a numeric string is coerced", client.post("/reports", json={"report_type": "sales", "rows": "5"}).json()["rows"], 5)
        check("rows as a float is 422", client.post("/reports", json={"report_type": "sales", "rows": 1.5}).status_code, 422)
        check("report_type is case-sensitive", client.post("/reports", json={"report_type": "Sales"}).status_code, 422)
        check("an over-long report id is 404, not a crash", client.get(f"/reports/{'x' * 500}").status_code, 404)
        check("a path-traversal-shaped id is 404", client.get("/reports/..%2F..%2Fetc%2Fpasswd").status_code, 404)

        trailing = client.post("/reports/", json={"report_type": "sales"}, follow_redirects=False)
        if trailing.status_code in (307, 308):
            note(f"POST /reports/ returns a {trailing.status_code} redirect to /reports — harmless, but a client that drops the body on redirect would see a 422")
        else:
            check("POST /reports/ is handled without a redirect", trailing.status_code, 202)

        settle(2.0)
        check(
            "after everything above, every activity line is still well-formed",
            all(len(line.split(" | ")) == 3 and line.split(" | ")[1].startswith("user_id=") for line in read_log(ACTIVITY_LOG)),
            True,
        )
        check(
            "and every activity line is attributed to a real user",
            {line.split(" | ")[1] for line in read_log(ACTIVITY_LOG)},
            {f"user_id={USER_ID}"},
        )

        client.close()
        anon.close()

    print("\n" + "=" * 60)
    print(f"{checks - len(failures)}/{checks} checks passed, {len(notes)} note(s)")
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)

finally:
    shutil.rmtree(SCRATCH, ignore_errors=True)
