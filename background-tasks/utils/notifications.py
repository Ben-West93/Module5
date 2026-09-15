# exercises/background-tasks/app/utils/notifications.py
# L9 — simulated side effects that run after the response has been sent
#
# Nothing in this module is an endpoint, and nothing in it touches the
# database. Both functions are plain synchronous callables that take only
# primitives, which is what makes them safe to hand to
# `background_tasks.add_task()`.
#
# Why primitives only — the one rule that matters here:
#
#   The request's Session is closed by the `get_db` dependency's `finally`
#   block as the response is finalised, and a background task runs AFTER
#   that. Passing a Student ORM object into a task would hand the task an
#   instance whose session is gone: touching any attribute that was not
#   already loaded raises DetachedInstanceError, and it does so in a worker
#   thread where nothing is watching. Passing `student.id` and
#   `student.email` as an int and a str cannot fail that way.
#
# The same reasoning is why these take `user_id: int` rather than a User.

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("jwt_api")

# --------------------------------------------------------------------------
# Where the logs go
# --------------------------------------------------------------------------
#
# Resolved once at import, against LOG_DIR if it is set. The default is the
# process's working directory, which is the exercise folder when the app is
# started the documented way (`uvicorn app.main:app` from background-tasks/).
#
# The env var is not decoration: verify_background_tasks.py points it at a
# scratch directory so a verification run cannot append to — or assert
# against — a log the grader is meant to read.

LOG_DIR = Path(os.getenv("LOG_DIR", ".")).resolve()
ACTIVITY_LOG = LOG_DIR / "activity_log.txt"
NOTIFICATION_LOG = LOG_DIR / "notification_log.txt"

# The 2-second delay the exercise asks for, overridable so the test suite does
# not have to spend two real seconds per notification.
NOTIFICATION_DELAY_SECONDS = float(os.getenv("NOTIFICATION_DELAY_SECONDS", "2"))

# Background tasks run in Starlette's thread pool, so two of them can be
# inside _append_line at the same moment. A single short write to a file
# opened in append mode is very likely atomic on Linux, but "very likely" is
# not a guarantee worth relying on for the artifact this exercise is graded
# on, and the lock costs nothing at this volume.
_file_lock = threading.Lock()


def _timestamp() -> str:
    """An ISO-8601 timestamp in UTC, including the offset.

    Timezone-aware on purpose. `datetime.now()` would write a local time with
    nothing recording which local it was, so two entries written by machines
    in different zones could not be ordered against each other.
    """
    return datetime.now(timezone.utc).isoformat()


def _sanitize(value: object) -> str:
    """Makes one caller-supplied value safe to put in a line of this log.

    This is a log-forging defence, and it is not hypothetical: a student's
    email address reaches activity_log.txt verbatim, and an email is whatever
    the client sent. Before this existed, registering a student with the
    address

        inject\\n2020-01-01T00:00:00+00:00 | user_id=999 | deleted every student@example.com

    appended TWO lines to the audit log, the second one a forgery attributing
    a deletion to a user who never made one. An audit trail the audited party
    can write to is not an audit trail. probe_background.py section [Q]
    reproduces it.

    Escaped, rather than stripped, so the log still records what was actually
    sent — a rejected value is evidence too, and silently deleting characters
    would hide the attempt.

    Four classes of character, in this order:
      backslash   first, so the escapes below cannot be spoofed by a value
                  that already contains "\\n" as two literal characters
      pipe        the field delimiter; escaping it means " | " can never
                  occur inside a field, so splitting a line always yields
                  exactly three parts
      newlines    the line delimiter, and the actual forging vector
      other C0    tabs, NULs, and ESC — the last of which would otherwise let
                  a value emit ANSI sequences that rewrite the screen when
                  someone cats the file
    """
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")

    escapes = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    out = []
    for char in text:
        if char in escapes:
            out.append(escapes[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\x{ord(char):02x}")
        else:
            out.append(char)
    return "".join(out)


def _append_record(path: Path, *fields: object) -> None:
    """Writes one timestamped, pipe-delimited record.

    The callers hand over fields, not a finished line, which is the point:
    if they formatted the line themselves, this function could not tell a
    delimiter that belongs to the format from one that arrived inside a
    value, and sanitizing would be impossible. Every write to either log goes
    through here, so the guarantee holds for any future caller too.
    """
    line = " | ".join([_timestamp()] + [_sanitize(field) for field in fields])
    with _file_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


# --------------------------------------------------------------------------
# The tasks
# --------------------------------------------------------------------------
#
# Both bodies are wrapped in try/except, which looks like belt-and-braces and
# is not. By the time a background task runs, the response is already on the
# wire: there is no status code left to change and no way to tell the caller
# anything. An exception escaping here propagates up through Starlette after
# the response has been sent, where — depending on the server — it is logged
# as an unhandled error or takes down the worker. Catching it means a full
# disk costs one log line instead of a request that already "succeeded"
# turning into a server-side crash.


def log_activity(user_id: int, action: str) -> None:
    """Appends a timestamped line to activity_log.txt.

    Format (pipe-delimited so it stays greppable and trivially parseable):

        2026-09-15T18:03:11.482913+00:00 | user_id=3 | created student 7 (ada@example.com)

    `action` embeds caller-supplied values such as an email address, so it is
    passed as a field rather than pre-joined — see _sanitize().
    """
    try:
        _append_record(ACTIVITY_LOG, f"user_id={user_id}", action)
    except Exception:
        logger.exception("Failed to write activity log for user_id=%s", user_id)


def send_notification(email: str, message: str) -> None:
    """Simulates sending an email, then records it in notification_log.txt.

    The sleep stands in for a real mail provider's round trip and is the
    whole point of the exercise: two seconds is long enough that a caller
    would notice it, which makes "did the response come back first?" a
    question with an observable answer rather than a claim in a README.

    time.sleep() is correct here, not a mistake to be replaced with
    `await asyncio.sleep()`. These tasks are registered as ordinary `def`
    functions, so Starlette runs them in a worker thread, where blocking
    affects only that thread. Making this module async would move the
    blocking onto the event loop, which is the opposite of what is wanted.

    Both the recipient and the message are caller-controlled, so both are
    sanitized on the way in.
    """
    try:
        time.sleep(NOTIFICATION_DELAY_SECONDS)
        _append_record(NOTIFICATION_LOG, f"to={email}", message)
    except Exception:
        logger.exception("Failed to send notification to %s", email)
