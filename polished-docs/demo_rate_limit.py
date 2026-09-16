"""Produces evidence of the 429 response from a real running server.

Run from polished-docs/:

    python demo_rate_limit.py

It starts the app on a real uvicorn socket, with the real limit of 10
requests per 60 seconds, and writes rate_limit_evidence.txt beside this file.
The transcript is copied from the responses the client actually received, so
the evidence comes from a run rather than being written by hand.

It shows, in order:

  1. ten requests served, X-RateLimit-Remaining counting down to 0
  2. the eleventh request refused with 429, the full response as received
  3. a POST to /items and a POST to /tasks made while limited are refused AND
     create no row (checked in the database directly, since asking the API
     would spend another request). The budget is per client, not per router.
  4. a browser-style request's 429 still carries CORS headers
  5. uvicorn's own access log recording the 429s, independently of the client
  6. after waiting out Retry-After, the same client is served again

Step 6 waits about a minute. That wait is the point: it shows the lockout
really ends when Retry-After says it will.

The database is a scratch file, so running this leaves no app.db behind.
"""

import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Windows consoles, and output redirected to a file on Windows, often use a
# legacy encoding that cannot print every character these checks use. Without
# this, a failing check about unicode would crash the script while printing
# the failure — the one moment its output matters. Unprintable characters are
# shown as escapes instead.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="backslashreplace")

# --- before the app is imported ---------------------------------------------
SCRATCH = Path(tempfile.mkdtemp(prefix="rate-limit-demo-"))
os.environ["DATABASE_URL"] = f"sqlite:///{SCRATCH / 'demo.db'}"
for _var in ("CORS_ALLOWED_ORIGINS", "RATE_LIMIT_REQUESTS", "RATE_LIMIT_WINDOW_SECONDS"):
    os.environ.pop(_var, None)  # the evidence must use the real defaults
# ----------------------------------------------------------------------------

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import app.main as server  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models.item import Item  # noqa: E402
from app.models.task import Task  # noqa: E402

EVIDENCE = Path(__file__).resolve().parent / "rate_limit_evidence.txt"
ALLOWED_ORIGIN = "http://localhost:3000"

lines: list[str] = []
problems: list[str] = []


def out(text: str = "") -> None:
    print(text)
    lines.append(text)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def expect(label: str, actual, expected) -> bool:
    """Records whether the evidence shows what it claims to show."""
    ok = actual == expected
    out(f"  [{'ok' if ok else 'UNEXPECTED'}] {label}: {actual!r}")
    if not ok:
        problems.append(f"{label}: expected {expected!r}, got {actual!r}")
    return ok


def send(client: httpx.Client, method: str, path: str, **kwargs):
    """Sends a request, returning None (and recording why) if it never arrived."""
    try:
        return client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        problems.append(f"{method} {path} failed to send: {type(exc).__name__}: {exc}")
        out(f"  [UNEXPECTED] {method} {path} failed to send: {exc}")
        return None


def raw_response(method: str, path: str, response: httpx.Response, extra_request_headers: dict | None = None) -> None:
    """Writes a request and its response the way they looked on the wire."""
    out(f"> {method} {path} HTTP/1.1")
    for name, value in (extra_request_headers or {}).items():
        out(f"> {name}: {value}")
    out(">")
    out(f"< HTTP/1.1 {response.status_code} {response.reason_phrase}")
    for name, value in response.headers.items():
        out(f"< {name}: {value}")
    out("<")
    try:
        body = json.dumps(response.json(), indent=2, ensure_ascii=False)
    except ValueError:
        body = response.text
    for body_line in body.splitlines():
        out(f"  {body_line}")


def row_counts() -> dict[str, int]:
    with SessionLocal() as db:
        return {
            "items": db.scalar(select(func.count()).select_from(Item)),
            "tasks": db.scalar(select(func.count()).select_from(Task)),
        }


class AccessLogCapture(logging.Handler):
    """Keeps uvicorn's access-log lines so the server's own view is recorded."""

    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(record.getMessage())


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


access_log = AccessLogCapture()

port = free_port()
uv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="info"))
thread = threading.Thread(target=uv.run, daemon=True)

try:
    thread.start()
    deadline = time.monotonic() + 15
    while not uv.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)

    # Attached only once the server is up. uvicorn applies its logging
    # config during startup, which replaces any handler added before it; an
    # earlier version of this script attached it first and captured nothing.
    logging.getLogger("uvicorn.access").addHandler(access_log)

    base_url = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base_url, timeout=30.0)

    out("Rate limit evidence — Tasks and Items API (Module 5, L10 + L11 + L12)")
    out("=" * 72)
    out(f"Generated by demo_rate_limit.py at {stamp()}")
    out(f"Server: uvicorn on {base_url} (a real socket, not TestClient)")
    out(f"Limit in force: {server.RATE_LIMIT_REQUESTS} requests per {server.RATE_LIMIT_WINDOW_SECONDS:g} seconds per IP")

    # ----------------------------------------------------------------------
    out("\n[1] Ten requests inside the limit")
    out("-" * 72)
    out(f"  {'#':>2}  {'time (UTC)':<29} {'status':<7} X-RateLimit-Remaining")
    statuses = []
    for n in range(1, 11):
        r = send(client, "GET", "/items")
        if r is None:
            continue
        statuses.append(r.status_code)
        out(f"  {n:>2}  {stamp():<29} {r.status_code:<7} {r.headers.get('x-ratelimit-remaining')}")
    expect("all ten served", statuses, [200] * 10)

    # ----------------------------------------------------------------------
    out("\n[2] The eleventh request — the 429, exactly as received")
    out("-" * 72)
    out(f"  sent at {stamp()}")
    limited = send(client, "GET", "/items")
    if limited is not None:
        raw_response("GET", "/items", limited)
        out()
        if expect("status", limited.status_code, 429):
            try:
                body = limited.json()
            except ValueError:
                body = {}
                problems.append("429 body was not JSON")
            expect("error label", body.get("error"), "TooManyRequests")
            expect("status_code in body", body.get("status_code"), 429)
            expect("X-RateLimit-Remaining", limited.headers.get("x-ratelimit-remaining"), "0")
            retry_after = limited.headers.get("retry-after", "")
            expect("Retry-After is a whole number of seconds", retry_after.isdigit(), True)

    # ----------------------------------------------------------------------
    out("\n[3] POSTs to /items and /tasks while limited are refused and write nothing")
    out("-" * 72)
    before = row_counts()
    blocked_item = send(client, "POST", "/items", json={"name": "Should not be created", "price": 1.0, "category": "demo"})
    blocked_task = send(client, "POST", "/tasks", json={"title": "Should not be created"})
    after = row_counts()
    for path, blocked in (("/items", blocked_item), ("/tasks", blocked_task)):
        if blocked is not None:
            raw_response("POST", path, blocked, {"Content-Type": "application/json"})
            out()
            expect(f"POST {path} status", blocked.status_code, 429)
            out()
    out("  rows in each table, read from the database directly:")
    out(f"    before the POSTs: {before}")
    out(f"    after the POSTs:  {after}")
    expect("no row was created in either table", after, before)

    # ----------------------------------------------------------------------
    out("\n[4] A browser request from an allowed origin still gets readable CORS headers")
    out("-" * 72)
    browser = send(client, "GET", "/items", headers={"Origin": ALLOWED_ORIGIN})
    if browser is not None:
        raw_response("GET", "/items", browser, {"Origin": ALLOWED_ORIGIN})
        out()
        expect("status", browser.status_code, 429)
        expect("Access-Control-Allow-Origin", browser.headers.get("access-control-allow-origin"), ALLOWED_ORIGIN)
        expect(
            "Retry-After is exposed to page JavaScript",
            "retry-after" in browser.headers.get("access-control-expose-headers", "").lower(),
            True,
        )

    # ----------------------------------------------------------------------
    out("\n[5] uvicorn's own access log for everything above")
    out("-" * 72)
    time.sleep(0.2)  # let the last access-log record land
    for record in access_log.records:
        out(f"  {record}")
    logged = [record for record in access_log.records if " 429" in record]
    expect("the server logged four 429 responses", len(logged), 4)

    # ----------------------------------------------------------------------
    out("\n[6] Waiting out Retry-After, then trying again")
    out("-" * 72)
    wait = int(browser.headers.get("retry-after", "60")) if browser is not None else 60
    out(f"  Retry-After said {wait}s; sleeping {wait}s from {stamp()}")
    time.sleep(wait)
    recovered = send(client, "GET", "/items")
    out(f"  retried at {stamp()}")
    if recovered is not None:
        out(f"  -> {recovered.status_code} {recovered.reason_phrase}, X-RateLimit-Remaining: {recovered.headers.get('x-ratelimit-remaining')}")
        expect("served again once the window moved on", recovered.status_code, 200)

    client.close()

    out("\n" + "=" * 72)
    if problems:
        out(f"{len(problems)} thing(s) did not match what this evidence claims:")
        for problem in problems:
            out(f"  - {problem}")
    else:
        out("Every expectation above was met.")

finally:
    uv.should_exit = True
    thread.join(timeout=15)
    engine.dispose()
    shutil.rmtree(SCRATCH, ignore_errors=True)
    if lines:
        EVIDENCE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nWrote {EVIDENCE.name}")

sys.exit(1 if problems else 0)
