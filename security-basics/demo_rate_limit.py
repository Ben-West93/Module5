"""Produces evidence of the rate limits from a real running server.

Run from security-basics/:

    python demo_rate_limit.py

It starts the app on a real uvicorn socket, with the real SlowAPI limits
(login 5/min, create 20/min, list 60/min), and writes rate_limit_evidence.txt
beside this file. The transcript is copied from the responses the client
actually received, so the evidence comes from a run rather than being written
by hand.

It shows, in order:

  1. an account is registered
  2. five logins served, X-RateLimit-Remaining counting down to 0
  3. the SIXTH login refused with 429: the full response as received
  4. a seventh login, with the correct password, from a Streamlit page's
     origin: still 429, and still carrying CORS headers the page can read
  5. the other endpoints have their own budgets: 60 GET /items then a 429,
     20 POST /items then a 429 that wrote no row (checked in the database)
  6. uvicorn's own access log for the login requests, independently of the
     client
  7. after waiting out Retry-After, the same client can log in again

Step 7 waits about a minute. That wait is the point: it shows the lockout
really ends when Retry-After says it will.

The database is a scratch file, so running this leaves no items.db behind.
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
for _var in ("CORS_ALLOWED_ORIGINS", "RATE_LIMIT_LOGIN", "RATE_LIMIT_CREATE", "RATE_LIMIT_LIST"):
    os.environ.pop(_var, None)  # the evidence must use the real defaults
# ----------------------------------------------------------------------------

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import app.config as config  # noqa: E402
import app.main as server  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.models.item import Item  # noqa: E402

EVIDENCE = Path(__file__).resolve().parent / "rate_limit_evidence.txt"
STREAMLIT_ORIGIN = "http://localhost:8501"
USERNAME = "evidence"
PASSWORD = "correct horse battery"

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


def item_count() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Item))


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

    out("Rate limit evidence — Security Basics (Module 5, L10)")
    out("=" * 72)
    out(f"Generated by demo_rate_limit.py at {stamp()}")
    out(f"Server: uvicorn on {base_url} (a real socket, not TestClient)")
    out("Limits in force (SlowAPI, per IP, per endpoint, moving window):")
    out(f"  POST /auth/login     {config.LOGIN_LIMIT}")
    out(f"  POST /items          {config.CREATE_LIMIT}   (and POST /auth/register)")
    out(f"  GET  /items          {config.LIST_LIMIT}   (and GET /auth/me, GET /)")

    credentials = {"username": USERNAME, "password": PASSWORD}

    # ----------------------------------------------------------------------
    out("\n[1] Register an account to log in with")
    out("-" * 72)
    registered = send(client, "POST", "/auth/register", json=credentials)
    if registered is not None:
        out(f"  POST /auth/register -> {registered.status_code} {registered.text}")
        expect("account created", registered.status_code, 201)

    # ----------------------------------------------------------------------
    out("\n[2] Five logins inside the limit")
    out("-" * 72)
    out(f"  {'#':>2}  {'answered at (UTC)':<29} {'status':<7} {'X-RateLimit-Remaining':<22} token")
    statuses = []
    for n in range(1, 6):
        r = send(client, "POST", "/auth/login", json=credentials)
        if r is None:
            continue
        statuses.append(r.status_code)
        has_token = "yes" if r.status_code == 200 and "access_token" in r.json() else "no"
        out(f"  {n:>2}  {stamp():<29} {r.status_code:<7} {r.headers.get('x-ratelimit-remaining'):<22} {has_token}")
    expect("all five logged in", statuses, [200] * 5)

    # ----------------------------------------------------------------------
    out("\n[3] The sixth login — the 429, exactly as received")
    out("-" * 72)
    out(f"  sent at {stamp()}")
    limited = send(client, "POST", "/auth/login", json=credentials)
    if limited is not None:
        raw_response("POST", "/auth/login", limited, {"Content-Type": "application/json"})
        out()
        if expect("status", limited.status_code, 429):
            try:
                body = limited.json()
            except ValueError:
                body = {}
                problems.append("429 body was not JSON")
            expect("error label", body.get("error"), "TooManyRequests")
            expect("status_code in body", body.get("status_code"), 429)
            expect("access_token in the body", "access_token" in body, False)
            expect("X-RateLimit-Limit", limited.headers.get("x-ratelimit-limit"), "5")
            expect("X-RateLimit-Remaining", limited.headers.get("x-ratelimit-remaining"), "0")
            retry_after = limited.headers.get("retry-after", "")
            expect("Retry-After is a whole number of seconds", retry_after.isdigit(), True)

    # ----------------------------------------------------------------------
    out("\n[4] A seventh login from a Streamlit page, correct password: still refused, CORS intact")
    out("-" * 72)
    browser = send(client, "POST", "/auth/login", json=credentials, headers={"Origin": STREAMLIT_ORIGIN})
    if browser is not None:
        raw_response("POST", "/auth/login", browser, {"Origin": STREAMLIT_ORIGIN, "Content-Type": "application/json"})
        out()
        expect("status", browser.status_code, 429)
        expect("Access-Control-Allow-Origin", browser.headers.get("access-control-allow-origin"), STREAMLIT_ORIGIN)
        expect(
            "Retry-After is exposed to page JavaScript",
            "retry-after" in browser.headers.get("access-control-expose-headers", "").lower(),
            True,
        )

    # ----------------------------------------------------------------------
    out("\n[5] The other endpoints keep their own budgets")
    out("-" * 72)
    listed = [send(client, "GET", "/items") for _ in range(61)]
    list_codes = [r.status_code for r in listed if r is not None]
    out(f"  61 x GET /items  -> {list_codes.count(200)} x 200, then {list_codes[-1] if list_codes else None}")
    expect("the first 60 list requests were served, although login is locked out", list_codes[:60], [200] * 60)
    expect("the 61st list request is 429", list_codes[-1:], [429])
    if listed and listed[-1] is not None:
        out(f"    61st: X-RateLimit-Limit {listed[-1].headers.get('x-ratelimit-limit')}, Retry-After {listed[-1].headers.get('retry-after')}")

    before = item_count()
    created = [send(client, "POST", "/items", json={"name": f"Evidence item {n}"}) for n in range(21)]
    create_codes = [r.status_code for r in created if r is not None]
    after = item_count()
    out(f"  21 x POST /items -> {create_codes.count(201)} x 201, then {create_codes[-1] if create_codes else None}")
    if created and created[-1] is not None:
        out(f"    21st: X-RateLimit-Limit {created[-1].headers.get('x-ratelimit-limit')}, Retry-After {created[-1].headers.get('retry-after')}")
    expect("the first 20 creates were served", create_codes[:20], [201] * 20)
    expect("the 21st create is 429", create_codes[-1:], [429])
    out("  rows in the items table, read from the database directly:")
    out(f"    before: {before}")
    out(f"    after:  {after}")
    expect("exactly 20 rows were written; the refused POST wrote nothing", after - before, 20)

    # ----------------------------------------------------------------------
    out("\n[6] uvicorn's own access log for the login requests")
    out("-" * 72)
    time.sleep(0.2)  # let the last access-log record land
    login_lines = [record for record in access_log.records if "/auth/login" in record]
    for record in login_lines:
        out(f"  {record}")
    expect("the server logged five 200s for login", sum(" 200" in r for r in login_lines), 5)
    expect("and then two 429s", sum(" 429" in r for r in login_lines), 2)
    items_lines = [record for record in access_log.records if "/items" in record]
    out(f"  (plus {len(items_lines)} /items lines: {sum(' 200' in r for r in items_lines)} x 200, "
        f"{sum(' 201' in r for r in items_lines)} x 201, {sum(' 429' in r for r in items_lines)} x 429)")

    # ----------------------------------------------------------------------
    out("\n[7] Waiting out Retry-After, then logging in again")
    out("-" * 72)
    wait = int(browser.headers.get("retry-after", "60")) if browser is not None else 60
    out(f"  the last login 429 said Retry-After: {wait}; sleeping {wait}s from {stamp()}")
    time.sleep(wait)
    recovered = send(client, "POST", "/auth/login", json=credentials)
    out(f"  retried at {stamp()}")
    if recovered is not None:
        out(f"  -> {recovered.status_code} {recovered.reason_phrase}, X-RateLimit-Remaining: {recovered.headers.get('x-ratelimit-remaining')}")
        expect("logged in again once the window moved on", recovered.status_code, 200)

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
