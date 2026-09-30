"""Checks the L10 security behaviour, and tries to break it.

Run from security-basics/:

    python verify_security.py

What the brief and the reviewer ask for, and what this establishes:

    CORS         specific origins (React :3000, Streamlit :8501), methods and
                 headers; never "*"
    rate limit   SlowAPI, per endpoint: login 5/min, create 20/min, list
                 60/min; the SIXTH login in a minute is 429 (section [8])
    sanitizing   name and description stripped of whitespace and HTML tags,
                 including a <script> tag in the name (section [3])
    SQL note     parameterized queries, explained and demonstrated
    login        registration, bcrypt-hashed passwords, a signed JWT

Wherever a claim in a code comment can be shown rather than asserted, it is
shown here: the naive tag-stripping regex is run and caught letting a payload
through, the vulnerable SQL pattern is run and caught leaking rows, and
Starlette's wildcard-plus-credentials behaviour is run and caught reflecting
an attacker's origin.

Every HTTP call goes through request() or send() below, which catch transport
errors and check the status code before anything reads the body.

The database is redirected to a scratch file BEFORE the app is imported,
because app.database reads DATABASE_URL at import time. A verification run
should not write rows into the items.db a developer is using.
"""

import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

# Windows consoles, and output redirected to a file on Windows, often use a
# legacy encoding that cannot print every character these checks use. Without
# this, a failing check about unicode would crash the script while printing
# the failure — the one moment its output matters. Unprintable characters are
# shown as escapes instead.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="backslashreplace")

# --- must happen before the app is imported ---------------------------------
SCRATCH = Path(tempfile.mkdtemp(prefix="security-verify-"))
os.environ["DATABASE_URL"] = f"sqlite:///{SCRATCH / 'verify.db'}"
# The defaults are what is under test, so make sure nothing in the shell
# environment has overridden them.
for _var in (
    "CORS_ALLOWED_ORIGINS",
    "RATE_LIMIT_LOGIN",
    "RATE_LIMIT_CREATE",
    "RATE_LIMIT_LIST",
    "JWT_EXPIRE_MINUTES",
):
    os.environ.pop(_var, None)
# A fixed key, so this script can check token signatures itself.
os.environ["JWT_SECRET_KEY"] = "verify-security-" + "k" * 32
# Newer Starlette warns that its TestClient will move off httpx. Harmless
# here, and it would otherwise print in the middle of the results.
warnings.filterwarnings("ignore", message=".*httpx.*")
# ----------------------------------------------------------------------------

import httpx  # noqa: E402
import jwt  # noqa: E402
import limits.storage.memory  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.dialects import sqlite as sqlite_dialect  # noqa: E402

import app.config as config  # noqa: E402
import app.main as server  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.limiter import limiter  # noqa: E402
from app.models.item import Item  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routers.items import sanitize_text  # noqa: E402

failures: list[str] = []
checks = 0

ALLOWED = "http://localhost:3000"
ALSO_ALLOWED = "http://127.0.0.1:3000"
STREAMLIT = "http://localhost:8501"
ALSO_STREAMLIT = "http://127.0.0.1:8501"
EVIL = "https://evil.example"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def check(label: str, actual, expected) -> bool:
    global checks
    checks += 1
    if actual == expected:
        print(f"  PASS  {label}")
        return True
    print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    failures.append(label)
    return False


def send(client, method: str, path: str, **kwargs) -> Optional[httpx.Response]:
    """Sends a request and returns the response, or None if it never arrived.

    No assertion about the status: for the rate-limit sections, where the
    status IS the thing being counted.
    """
    try:
        return client.request(method, path, **kwargs)
    except Exception as exc:  # a crash, a refused connection, a timeout
        global checks
        checks += 1
        label = f"{method} {path} could be sent"
        print(f"  FAIL  {label}: {type(exc).__name__}: {exc}")
        failures.append(label)
        return None


def request(client, method: str, path: str, *, expect: int, label: str, **kwargs) -> Optional[httpx.Response]:
    """Sends a request and checks its status before anyone uses the body.

    Returns the response only when the status matched, so a caller written as
    `if r is not None:` can never parse the JSON of an error it did not
    expect and fail with a confusing KeyError three lines later.
    """
    response = send(client, method, path, **kwargs)
    if response is None:
        return None
    if not check(label, response.status_code, expect):
        print(f"        body: {response.text[:200]}")
        return None
    return response


def json_of(response: httpx.Response, label: str) -> Any:
    """Parses a JSON body, recording a failure instead of raising."""
    try:
        return response.json()
    except ValueError:
        global checks
        checks += 1
        print(f"  FAIL  {label}: body is not JSON: {response.text[:200]!r}")
        failures.append(label)
        return None


def client_from(ip: str) -> TestClient:
    """A TestClient whose requests appear to come from `ip`."""
    return TestClient(server.app, client=(ip, 50000))


@contextmanager
def unmetered():
    """Switches the rate limits off for sections that are about something else.

    Sections [3] to [6] make many requests each, and a 429 halfway through a
    sanitization check would be a failure of the test, not of the sanitizer.
    The limits themselves are tested at their real values from [8] on.
    """
    limiter.enabled = False
    limiter.reset()
    try:
        yield
    finally:
        limiter.enabled = True
        limiter.reset()


class FakeTime:
    """Stands in for the `time` module so a 60-second window takes no time.

    SlowAPI's in-memory storage (the `limits` package) and main.py's 429
    handler both call time.time(). Everything else is passed to the real
    module.
    """

    def __init__(self):
        self.now = time.time()

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __getattr__(self, name):
        return getattr(time, name)


@contextmanager
def fake_clock():
    clock = FakeTime()
    patched = (limits.storage.memory, server)
    for module in patched:
        module.time = clock
    limiter.reset()
    try:
        yield clock
    finally:
        for module in patched:
            module.time = time
        limiter.reset()


def item_count() -> int:
    """Rows in the items table, read directly: asking the API would be limited."""
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Item))


PASSWORD = "correct horse battery"


def bcrypt_ok(password: str, password_hash: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def register(client, username: str, password: str = PASSWORD) -> None:
    """Creates an account, with the limits off so it spends no budget."""
    was = limiter.enabled
    limiter.enabled = False
    try:
        request(client, "POST", "/auth/register", json={"username": username, "password": password}, expect=201, label=f"register {username!r}")
    finally:
        limiter.enabled = was


def login(client, username: str, password: str = PASSWORD, **kwargs) -> Optional[httpx.Response]:
    return send(client, "POST", "/auth/login", json={"username": username, "password": password}, **kwargs)


class TagCollector(HTMLParser):
    """Records every start tag an HTML parser finds."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, list]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    handle_startendtag = handle_starttag


def tags_when_rendered(value: str) -> list[tuple[str, list]]:
    """Places `value` into a page the careless way and parses the result.

    The markup around it matters. `<p>after</p>` supplies the ">" that an
    unterminated tag in the value would borrow, which is how that attack
    works in a real page. Python's parser is not a browser, but it follows the
    HTML spec on the cases used here.
    """
    parser = TagCollector()
    parser.feed(f'<div class="item">{value}</div><p>after</p>')
    parser.close()
    return parser.tags


def is_inert(value: str) -> bool:
    """True if rendering `value` produces no tags beyond the template's own."""
    return [tag for tag, _ in tags_when_rendered(value)] == ["div", "p"]


@contextmanager
def live_server():
    """Runs the app on a real uvicorn socket in a background thread.

    Used only by section [13]. TestClient drives the ASGI app directly, and
    the question there is what happens when requests genuinely arrive at the
    same time over real connections.
    """
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    uv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not uv.started:
        if not thread.is_alive():
            raise RuntimeError("the server thread died during startup")
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start within 15s")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        uv.should_exit = True
        thread.join(timeout=15)


# Start from an empty table so the run is repeatable.
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

try:
    # ======================================================================
    print("\n[1] sanitize_text() — exact outputs")
    # ======================================================================
    cases = [
        ("plain text is untouched", "Widget", "Widget"),
        ("surrounding whitespace is stripped", "   Widget \n\t", "Widget"),
        ("a simple tag pair is removed", "<b>Widget</b>", "Widget"),
        ("whitespace left behind by tags is stripped too", "<b> </b>Widget<i> </i>", "Widget"),
        ("a script tag is removed, its text left inert", "<script>alert(1)</script>", "alert(1)"),
        ("an event-handler tag is removed whole", "Hi<img src=x onerror=alert(1)>", "Hi"),
        ("an UNTERMINATED tag is removed to the end", "Hi <img src=x onerror=alert(1)//", "Hi"),
        ("a nested-tag trick leaves no tag", "<scr<script>ipt>alert(1)</script>", "ipt>alert(1)"),
        ("a tag rebuilt by removing another is removed too", "<<b>script>alert(1)", "alert(1)"),
        ("text between two stray brackets is not a tag", "a < b > c", "a < b > c"),
        ("a tag spanning lines is removed", "A<img\nsrc=x\nonerror=alert(1)>B", "AB"),
        ("a comment is removed", "A<!-- hidden -->B", "AB"),
        ("a less-than that is not markup survives", "3 < 5 and 5 > 3", "3 < 5 and 5 > 3"),
        ("entities are NOT decoded into tags", "&lt;script&gt;", "&lt;script&gt;"),
        ("unicode survives", "  Zoë's 学生 🎓 ", "Zoë's 学生 🎓"),
        ("markup-only input becomes empty", "<b></b>", ""),
    ]
    for label, raw, expected in cases:
        check(label, sanitize_text(raw), expected)

    # ======================================================================
    print("\n[2] the starter's single regex lets a payload through; this does not")
    # ======================================================================
    def naive(value: str) -> str:
        """The starter's hint, taken literally: strip, then one re.sub."""
        return re.sub(r"<[^>]*>", "", value.strip())

    unterminated = "<img src=x onerror=alert(1)//"
    naive_tags = tags_when_rendered(naive(unterminated))
    check(
        "naive: the unterminated payload survives the regex unchanged",
        naive(unterminated),
        unterminated,
    )
    check(
        "naive: rendered in a page, it becomes a real <img> tag",
        "img" in [tag for tag, _ in naive_tags],
        True,
    )
    img_attrs = next((dict(attrs) for tag, attrs in naive_tags if tag == "img"), {})
    check("naive:   with a live onerror handler", "onerror" in img_attrs, True)
    check("sanitize_text: the same payload renders as no tag at all", is_inert(sanitize_text(unterminated)), True)

    corpus = [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<img src=x onerror=alert(1)//",
        "<IMG SRC=x OnErRoR=alert(1)>",
        "<svg/onload=alert(1)>",
        "<body onload=alert(1)",
        '<a href="javascript:alert(1)">click</a>',
        '"><script>alert(1)</script>',
        "</title><script>alert(1)</script>",
        "<scr<script>ipt>alert(1)</script>",
        "<<script>script>alert(1)<</script>/script>",
        '<img src="x>" onerror=alert(1)>',
        '<iframe srcdoc="<script>alert(1)</script>">',
        "<!--<img src=x onerror=alert(1)>-->",
        "<a\nhref=javascript:alert(1)",
        "x<y and z",
        "</ bogus comment that swallows markup",
        "<?php echo 1 ?>",
        "&lt;script&gt;alert(1)&lt;/script&gt;",
    ]
    naive_failures = [p for p in corpus if not is_inert(naive(p))]
    sanitized_failures = [p for p in corpus if not is_inert(sanitize_text(p))]
    print(f"        naive regex let {len(naive_failures)} of {len(corpus)} payloads render as markup")
    check("the naive regex is caught failing on at least one payload", len(naive_failures) > 0, True)
    check(f"sanitize_text leaves all {len(corpus)} payloads inert", sanitized_failures, [])

    # ======================================================================
    print("\n[3] POST /items — what is stored is what was expected")
    # ======================================================================
    with unmetered():
        client = client_from("10.0.0.3")

        # Clean input first: sanitizing must not alter a value that needed
        # nothing, so every field that was sent comes back byte-for-byte.
        clean = {"name": "Widget", "description": "A plain, ordinary widget. 3 < 5."}
        r = request(client, "POST", "/items", json=clean, expect=201, label="clean item is 201")
        clean_body = json_of(r, "clean item body") if r is not None else None
        if clean_body:
            for field, sent in clean.items():
                check(f"  returned {field} matches what was sent", clean_body.get(field), sent)
            check("  an integer id was assigned", isinstance(clean_body.get("id"), int), True)
            check("  created_at was generated", bool(clean_body.get("created_at")), True)

        # The brief's own test, word for word: "submit an item with <script>
        # tags in the name and verify they're stripped".
        sent = {"name": "<script>alert('xss')</script>Widget Pro"}
        r = request(client, "POST", "/items", json=sent, expect=201, label="brief's test: an item with <script> tags in the NAME is 201")
        body = json_of(r, "script-name body") if r is not None else None
        if body:
            check("  the returned name has no <script> tags", "<script" in body.get("name", "") or "</script" in body.get("name", ""), False)
            check("  and is exactly the text between them", body.get("name"), "alert('xss')Widget Pro")
            with SessionLocal() as db:
                stored_name = db.scalar(select(Item.name).where(Item.id == body.get("id")))
            check("  the row in the database is stored without them", stored_name, "alert('xss')Widget Pro")
            check("  and renders as no tag at all", is_inert(stored_name or ""), True)

        hostile_cases = [
            (
                {"name": "  <b>Gadget</b>  ", "description": "<script>steal()</script>Useful"},
                {"name": "Gadget", "description": "steal()Useful"},
            ),
            (
                {"name": "Gizmo<img src=x onerror=alert(1)//", "description": "  "},
                {"name": "Gizmo", "description": ""},
            ),
            (
                {"name": "&lt;b&gt;Literal&lt;/b&gt;"},
                {"name": "&lt;b&gt;Literal&lt;/b&gt;", "description": ""},
            ),
        ]
        created: list[tuple[int, dict]] = []
        for sent, expected in hostile_cases:
            r = request(client, "POST", "/items", json=sent, expect=201, label=f"hostile item {sent['name'][:20]!r} is 201")
            body = json_of(r, "hostile item body") if r is not None else None
            if body:
                for field, want in expected.items():
                    check(f"  returned {field} is sanitized to {want!r}", body.get(field), want)
                created.append((body["id"], expected))

        # The response could be right while the row is wrong, if the endpoint
        # ever echoed the request instead of the stored object. Read it back.
        r = request(client, "GET", "/items", expect=200, label="GET /items after creating")
        listing = json_of(r, "listing body") if r is not None else None
        if listing is not None:
            stored = {item["id"]: item for item in listing}
            check("every created item is in the listing", all(item_id in stored for item_id, _ in created), True)
            for item_id, expected in created:
                if item_id in stored:
                    check(
                        f"  item {item_id} was STORED sanitized, not just returned that way",
                        {k: stored[item_id][k] for k in expected},
                        expected,
                    )
            check("no stored name or description renders as markup", all(is_inert(i["name"]) and is_inert(i["description"]) for i in listing), True)

        r = request(client, "GET", "/items", expect=200, label="GET /items answers without a redirect", follow_redirects=False)
        if r is not None:
            check("  (the route is /items, not /items/)", r.headers.get("location"), None)

    # ======================================================================
    print("\n[4] POST /items — what is refused")
    # ======================================================================
    with unmetered():
        client = client_from("10.0.0.4")

        def count_items() -> Optional[int]:
            got = request(client, "GET", "/items", expect=200, label="count items")
            data = json_of(got, "count body") if got is not None else None
            return len(data) if isinstance(data, list) else None

        before = count_items()
        refusals = [
            ("a name that is only markup", {"name": "<b></b><i></i>"}),
            ("a name that is only whitespace", {"name": "   \n  "}),
            ("an empty name", {"name": ""}),
            ("a name that is only an unterminated tag", {"name": "<img src=x onerror=alert(1)"}),
            ("a missing name", {"description": "no name"}),
            ("a null name", {"name": None}),
            ("a numeric name (no silent str() coercion)", {"name": 12345}),
            ("a 101-character name", {"name": "x" * 101}),
            ("a 501-character description", {"name": "ok", "description": "d" * 501}),
            ("a null description", {"name": "ok", "description": None}),
        ]
        for label, body in refusals:
            r = request(client, "POST", "/items", json=body, expect=422, label=f"{label} is 422")
            if r is not None:
                envelope = json_of(r, f"{label} envelope")
                if envelope:
                    check("  in the error envelope", (envelope.get("error"), envelope.get("status_code")), ("ValidationError", 422))

        r = request(client, "POST", "/items", json={"name": "x" * 100}, expect=201, label="a 100-character name is accepted")
        check("only the 100-character item was stored; every refusal wrote nothing", count_items(), None if before is None else before + 1)

        # Where the name's length limit applies: to what was sent. 100
        # characters of tag around a short name is still over the limit.
        r = request(
            client, "POST", "/items",
            json={"name": "<b>" + "x" * 95 + "</b>"},
            expect=422,
            label="max_length applies to the raw input, before sanitizing",
        )

        # A cross-origin form or text/plain POST reaches the server without a
        # preflight, so CORS never gets a say. Only JSON bodies are accepted,
        # which is what closes that route.
        for content_type in ("text/plain", "application/x-www-form-urlencoded"):
            r = request(
                client, "POST", "/items",
                content=b'{"name": "Sneaky"}',
                headers={"Content-Type": content_type, "Origin": EVIL},
                expect=422,
                label=f"a JSON body sent as {content_type} is refused",
            )
            if r is not None:
                envelope = json_of(r, f"{content_type} envelope")
                if envelope:
                    check("  in the error envelope, not a crash", envelope.get("error"), "ValidationError")
        check("  so a no-preflight cross-origin POST created nothing", count_items(), None if before is None else before + 1)

    # ======================================================================
    print("\n[5] SQL injection — the vulnerable pattern, then the endpoint")
    # ======================================================================
    payload = "' OR '1'='1"

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO items (name) VALUES (?)", [("Widget",), ("Gadget",), ("Secret",)])

    vulnerable_rows = con.execute(f"SELECT * FROM items WHERE name = '{payload}'").fetchall()
    check("VULNERABLE f-string query: the payload returns every row", len(vulnerable_rows), 3)

    union_payload = "' UNION SELECT 1, sqlite_version() --"
    union_rows = con.execute(f"SELECT * FROM items WHERE name = '{union_payload}'").fetchall()
    check("VULNERABLE: a UNION payload reads data from outside the table", union_rows, [(1, sqlite3.sqlite_version)])

    safe_rows = con.execute("SELECT * FROM items WHERE name = ?", (payload,)).fetchall()
    check("SAFE parameterized query: the same payload matches nothing", safe_rows, [])

    try:
        con.execute("SELECT * FROM items WHERE name = 'x'; DROP TABLE items; --'")
        stacked = "ran"
    except (sqlite3.ProgrammingError, sqlite3.Warning):
        stacked = "refused by the driver"
    check("the sqlite3 driver refuses stacked statements", stacked, "refused by the driver")
    print("        (not a defence: the OR and UNION payloads above needed no second statement)")
    con.close()

    compiled = str(select(Item).where(Item.name == payload).compile(dialect=sqlite_dialect.dialect()))
    check("SQLAlchemy compiles the filter with a placeholder", "items.name = ?" in compiled, True)
    check("  and the payload is nowhere in the SQL text", "OR '1'" in compiled, False)

    with unmetered():
        client = client_from("10.0.0.5")
        r = request(client, "GET", "/items", expect=200, label="GET /items before the injection attempts")
        everything = json_of(r, "baseline") if r is not None else None
        total = len(everything) if isinstance(everything, list) else None

        injections = {
            "OR-always-true": payload,
            "UNION read": union_payload,
            "stacked DROP TABLE": "x'; DROP TABLE items; --",
            "comment-out": "Widget' --",
            "LIKE wildcard": "%",
        }
        for label, value in injections.items():
            r = request(client, "GET", "/items", params={"name": value}, expect=200, label=f"{label} payload is a normal 200")
            if r is not None:
                check("  matching no rows", json_of(r, label), [])

        r = request(client, "GET", "/items", expect=200, label="the table is intact afterwards")
        if r is not None:
            check("  with every row still there", len(json_of(r, "after") or []), total)

        r = request(client, "GET", "/items", params={"name": "Widget"}, expect=200, label="an exact name filter still works")
        matched = json_of(r, "exact") if r is not None else None
        if isinstance(matched, list):
            check("  returning exactly the matching item", [i["name"] for i in matched], ["Widget"])

        r = request(client, "GET", "/items", params={"name": "<b>Gadget</b>"}, expect=200, label="the filter is sanitized like stored names")
        if r is not None:
            check("  so a marked-up search finds the stored item", [i["name"] for i in json_of(r, "sanitized filter") or []], ["Gadget"])

        r = request(client, "GET", "/items", params={"name": ""}, expect=200, label="a blank ?name= is no filter, not a match on ''")
        if r is not None:
            check("  returning everything", len(json_of(r, "blank") or []), total)
        request(client, "GET", "/items", params={"name": "n" * 101}, expect=422, label="an over-long ?name= is 422")

        # Undecodable bytes in a body that is not JSON: the 422 handler has to
        # report them without crashing on them.
        r = request(
            client, "POST", "/items",
            content=b"\xff\xfe not utf-8 \x00",
            headers={"Content-Type": "text/plain"},
            expect=422,
            label="a body of invalid UTF-8 bytes is a 422, not a 500",
        )

    # ======================================================================
    print("\n[6] CORS over HTTP")
    # ======================================================================
    with unmetered():
        client = client_from("10.0.0.6")

        for origin in (ALLOWED, ALSO_ALLOWED, STREAMLIT, ALSO_STREAMLIT):
            r = request(client, "GET", "/items", headers={"Origin": origin}, expect=200, label=f"a GET from {origin}")
            if r is not None:
                check("  echoes that exact origin, not *", r.headers.get("access-control-allow-origin"), origin)

        r = request(client, "GET", "/items", headers={"Origin": STREAMLIT}, expect=200, label="the Streamlit origin's response")
        if r is not None:
            check("  varies the cache on Origin", "origin" in r.headers.get("vary", "").lower(), True)
            check("  does not allow credentials", r.headers.get("access-control-allow-credentials"), None)
            exposed = {h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",")}
            check("  exposes Retry-After and the rate-limit headers to page JS", {"retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"} <= exposed, True)

        for origin in (EVIL, "http://localhost:3001", "http://localhost:8502", "https://localhost:8501", "null", "http://localhost:8501.evil.example"):
            r = request(client, "GET", "/items", headers={"Origin": origin}, expect=200, label=f"a GET from {origin} is still served")
            if r is not None:
                check("  but the browser is given no permission to read it", r.headers.get("access-control-allow-origin"), None)
        print("        (CORS is enforced by browsers; it is not access control)")

        def preflight(origin: str, method: str, headers: Optional[str] = "content-type", path: str = "/items"):
            h = {"Origin": origin, "Access-Control-Request-Method": method}
            if headers:
                h["Access-Control-Request-Headers"] = headers
            return send(client, "OPTIONS", path, headers=h)

        r = preflight(STREAMLIT, "POST", path="/auth/login")
        if r is not None:
            check("preflight: Streamlit, POST /auth/login, Content-Type is 200", r.status_code, 200)
            check("  echoing the origin", r.headers.get("access-control-allow-origin"), STREAMLIT)
            check("  listing exactly GET and POST", r.headers.get("access-control-allow-methods"), "GET, POST")
            check("  caching the answer for 600s", r.headers.get("access-control-max-age"), "600")

        for method in ("DELETE", "PUT", "PATCH"):
            r = preflight(ALLOWED, method)
            if r is not None:
                check(f"preflight: {method} is refused", r.status_code, 400)

        r = preflight(ALLOWED, "GET", headers="authorization", path="/auth/me")
        if r is not None:
            check("preflight: Authorization is allowed (the token for GET /auth/me)", r.status_code, 200)
        r = preflight(ALLOWED, "POST", headers="x-custom-header")
        if r is not None:
            check("preflight: an arbitrary custom header is refused", r.status_code, 400)
        r = preflight(EVIL, "POST", path="/auth/login")
        if r is not None:
            check("preflight: a disallowed origin is refused", r.status_code, 400)
            check("  and granted nothing", r.headers.get("access-control-allow-origin"), None)

        wildcard_seen = []
        for origin in (ALLOWED, STREAMLIT, EVIL, "null"):
            for method, path, body in (
                ("GET", "/items", None),
                ("GET", "/nope", None),
                ("POST", "/items", {"name": "x"}),
                ("POST", "/auth/login", {"username": "x", "password": "y"}),
            ):
                resp = send(client, method, path, headers={"Origin": origin}, json=body)
                if resp is not None and resp.headers.get("access-control-allow-origin") == "*":
                    wildcard_seen.append((origin, method, path))
        check("no response ever carries Access-Control-Allow-Origin: *", wildcard_seen, [])

    # ======================================================================
    print("\n[7] configuration")
    # ======================================================================
    parse = server.parse_allowed_origins
    check(
        "the configured origins are React and Streamlit, each as localhost and 127.0.0.1",
        server.ALLOWED_ORIGINS,
        [ALLOWED, ALSO_ALLOWED, STREAMLIT, ALSO_STREAMLIT],
    )
    check("unset uses the defaults", parse(None), [ALLOWED, ALSO_ALLOWED, STREAMLIT, ALSO_STREAMLIT])
    check("blank uses the defaults", parse("   "), [ALLOWED, ALSO_ALLOWED, STREAMLIT, ALSO_STREAMLIT])
    check(
        "a list is split, trimmed and de-duplicated",
        parse(" https://app.example.com , http://localhost:5173,https://app.example.com,"),
        ["https://app.example.com", "http://localhost:5173"],
    )
    check("host and scheme are lowercased", parse("HTTPS://App.Example.COM"), ["https://app.example.com"])

    def rejects(raw: str) -> bool:
        try:
            parse(raw)
        except ValueError:
            return True
        return False

    for label, raw in [
        ("a bare *", "*"),
        ("* alongside real origins", "http://localhost:3000, *"),
        ("a wildcard subdomain", "https://*.example.com"),
        ("the null origin", "null"),
        ("a trailing slash", "http://localhost:8501/"),
        ("a path", "https://app.example.com/api"),
        ("a missing scheme", "localhost:8501"),
        ("a non-web scheme", "file:///home"),
        ("an invalid port", "http://localhost:abc"),
        ("userinfo", "http://user@localhost:3000"),
        ("only commas", ",,,"),
    ]:
        check(f"refuses {label}", rejects(raw), True)

    # Why "*" is refused rather than merely discouraged: in Starlette, the
    # starter's commented-out pairing of a wildcard with credentials reflects
    # the caller's origin back, whatever it is.
    demo = FastAPI()
    demo.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True)
    demo.get("/")(lambda: {"ok": True})
    with TestClient(demo) as demo_client:
        r = request(demo_client, "GET", "/", headers={"Origin": EVIL, "Cookie": "session=abc"}, expect=200, label="wildcard + credentials demo app responds")
        if r is not None:
            check("  and it grants the ATTACKER'S origin by name", r.headers.get("access-control-allow-origin"), EVIL)
            check("  with credentials allowed", r.headers.get("access-control-allow-credentials"), "true")

    check("the login limit in force is 5/minute", config.LOGIN_LIMIT, "5/minute")
    check("the create limit in force is 20/minute", config.CREATE_LIMIT, "20/minute")
    check("the list limit in force is 60/minute", config.LIST_LIMIT, "60/minute")

    def outcome_of(func, name: str, raw: str):
        """Runs `func` with the variable `name` set to `raw`, then restores it."""
        previous = os.environ.get(name)
        os.environ[name] = raw
        try:
            return func()
        except ValueError:
            return "refused"
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

    # SlowAPI parses a limit string on the first request and, if it cannot,
    # logs an error and applies NO limit. These must fail at startup instead.
    for raw in ("5/fortnight", "five per minute", "0/minute", "/minute"):
        got = outcome_of(lambda: config.read_rate_limit("RATE_LIMIT_LOGIN", "5/minute"), "RATE_LIMIT_LOGIN", raw)
        check(f"RATE_LIMIT_LOGIN={raw!r} is refused at startup", got, "refused")
    check(
        "a valid override is accepted as written",
        outcome_of(lambda: config.read_rate_limit("RATE_LIMIT_LOGIN", "5/minute"), "RATE_LIMIT_LOGIN", " 3/minute "),
        "3/minute",
    )

    for raw in ("0", "-5", "3O", "2.5"):
        got = outcome_of(lambda: config.read_positive_number("JWT_EXPIRE_MINUTES", 30, integer=True), "JWT_EXPIRE_MINUTES", raw)
        check(f"JWT_EXPIRE_MINUTES={raw} is refused at startup", got, "refused")
    for raw in ("nan", "inf"):
        got = outcome_of(lambda: config.read_positive_number("SOME_FLOAT_SETTING", 1.0, integer=False), "SOME_FLOAT_SETTING", raw)
        check(f"a float setting of {raw} is refused at startup", got, "refused")
    check(
        "a JWT_SECRET_KEY shorter than 32 bytes is refused at startup",
        outcome_of(config.read_jwt_secret, "JWT_SECRET_KEY", "too-short"),
        "refused",
    )
    saved_secret = os.environ.pop("JWT_SECRET_KEY")
    try:
        first, second = config.read_jwt_secret(), config.read_jwt_secret()
    finally:
        os.environ["JWT_SECRET_KEY"] = saved_secret
    check("an unset JWT_SECRET_KEY gets a random key of at least 32 bytes", len(first) >= 32, True)
    check("  that is different every time", first != second, True)

    # ======================================================================
    print("\n[8] rate limit on LOGIN — six requests, the sixth is 429")
    # ======================================================================
    # The reviewer's test: 5 login attempts per minute are allowed, and the
    # sixth is refused.
    with fake_clock() as clock:
        client = client_from("10.0.0.8")
        register(client, "ben")

        statuses, remaining, tokens = [], [], []
        for _ in range(5):
            r = login(client, "ben")
            if r is not None:
                statuses.append(r.status_code)
                remaining.append(r.headers.get("x-ratelimit-remaining"))
                tokens.append("access_token" in (json_of(r, "login body") or {}))
        check("logins 1-5 with the correct password are all 200", statuses, [200] * 5)
        check("  each one returning a token", tokens, [True] * 5)
        check("  counting X-RateLimit-Remaining down from 4 to 0", remaining, ["4", "3", "2", "1", "0"])

        clock.advance(0.5)
        r = login(client, "ben")
        check("login 6 is 429 Too Many Requests", r.status_code if r is not None else None, 429)
        if r is not None and r.status_code == 429:
            envelope = json_of(r, "429 body")
            if envelope:
                check("  labelled TooManyRequests", envelope.get("error"), "TooManyRequests")
                check("  with status_code matching", envelope.get("status_code"), 429)
                check("  and a detail naming the limit", "5 per 1 minute" in envelope.get("detail", ""), True)
                check("  and no token", "access_token" in envelope, False)
            check("  Retry-After is 60 (the first login was half a second ago)", r.headers.get("retry-after"), "60")
            check("  X-RateLimit-Limit is 5", r.headers.get("x-ratelimit-limit"), "5")
            check("  X-RateLimit-Remaining is 0", r.headers.get("x-ratelimit-remaining"), "0")

        request(client, "POST", "/auth/login", json={"username": "ben", "password": PASSWORD}, expect=429, label="login 7, with the correct password, is still 429")

        # Wrong passwords count too: the limit is on GUESSES.
        guesser = client_from("10.0.0.81")
        guesses = [r.status_code for r in (login(guesser, "ben", "wrong guess %d" % n) for n in range(5)) if r is not None]
        check("5 wrong-password guesses are each 401", guesses, [401] * 5)
        request(guesser, "POST", "/auth/login", json={"username": "ben", "password": PASSWORD}, expect=429, label="  so a 6th try with the RIGHT password is 429")

        prober = client_from("10.0.0.82")
        probes = [r.status_code for r in (login(prober, f"nobody{n}") for n in range(5)) if r is not None]
        check("5 attempts at usernames that do not exist are each 401", probes, [401] * 5)
        request(prober, "POST", "/auth/login", json={"username": "nobody5", "password": "x"}, expect=429, label="  and the 6th is 429")

    # ======================================================================
    print("\n[9] rate limit — a different limit per kind of endpoint")
    # ======================================================================
    with fake_clock():
        client = client_from("10.0.0.9")

        codes = [r.status_code for r in (send(client, "GET", "/items") for _ in range(60)) if r is not None]
        check("list: 60 GET /items in a minute are all 200", codes, [200] * 60)
        check("  so 11 rapid requests no longer produce a 429 on this endpoint", codes[:11], [200] * 11)
        r = request(client, "GET", "/items", expect=429, label="list: the 61st GET /items is 429")
        if r is not None:
            check("  with X-RateLimit-Limit 60", r.headers.get("x-ratelimit-limit"), "60")

        before = item_count()
        codes = [r.status_code for r in (send(client, "POST", "/items", json={"name": f"Bulk {n}"}) for n in range(20)) if r is not None]
        check("create: 20 POST /items in a minute are all 201, from the IP whose list budget is spent", codes, [201] * 20)
        r = request(client, "POST", "/items", json={"name": "One too many"}, expect=429, label="create: the 21st POST /items is 429")
        if r is not None:
            check("  with X-RateLimit-Limit 20", r.headers.get("x-ratelimit-limit"), "20")
        check("  and exactly 20 rows were written, none by the refused POST", item_count(), before + 20)

        register(client, "listuser")
        r = login(client, "listuser")
        check("login still works from the same IP: each endpoint has its own count", r.status_code if r is not None else None, 200)
        r = request(client, "GET", "/", expect=200, label="GET / is limited as a read")
        if r is not None:
            check("  at 60 per minute", r.headers.get("x-ratelimit-limit"), "60")

        signup = client_from("10.0.0.90")
        with SessionLocal() as db:
            users_before = db.scalar(select(func.count()).select_from(User))
        codes = [
            r.status_code
            for r in (send(signup, "POST", "/auth/register", json={"username": f"signup{n}", "password": PASSWORD}) for n in range(20))
            if r is not None
        ]
        check("create: 20 registrations in a minute are all 201", codes, [201] * 20)
        request(signup, "POST", "/auth/register", json={"username": "signup20", "password": PASSWORD}, expect=429, label="create: the 21st registration is 429")
        with SessionLocal() as db:
            users_after = db.scalar(select(func.count()).select_from(User))
        check("  and it created no account", users_after, users_before + 20)

        docs = client_from("10.0.0.91")
        codes = {r.status_code for r in (send(docs, "GET", "/openapi.json") for _ in range(70)) if r is not None}
        check("/openapi.json is not rate limited (loading /docs costs no budget)", codes, {200})

    # ======================================================================
    print("\n[10] rate limit — the window slides")
    # ======================================================================
    with fake_clock() as clock:
        client = client_from("10.0.0.10")
        register(client, "slider")
        for _ in range(5):
            login(client, "slider")

        clock.advance(59.5)
        r = request(client, "POST", "/auth/login", json={"username": "slider", "password": PASSWORD}, expect=429, label="59.5s later: still limited")
        wait = r.headers.get("retry-after") if r is not None else None
        check("  Retry-After is the whole second after the slot frees: 1", wait, "1")
        clock.advance(float(wait or 1))
        r = login(client, "slider")
        check("waiting exactly Retry-After gets the login through, first time", r.status_code if r is not None else None, 200)

    with fake_clock() as clock:
        client = client_from("10.0.0.11")
        register(client, "halves")
        for _ in range(3):
            login(client, "halves")
        clock.advance(30)
        for _ in range(2):
            login(client, "halves")
        r = request(client, "POST", "/auth/login", json={"username": "halves", "password": PASSWORD}, expect=429, label="3 at t=0 and 2 at t=30: the 6th at t=30 is 429")
        if r is not None:
            check("  waiting only until the t=0 batch expires", r.headers.get("retry-after"), "31")
        clock.advance(31)
        codes = [r.status_code for r in (login(client, "halves") for _ in range(4)) if r is not None]
        check("at t=61 the first batch has left: exactly 3 slots free", codes, [200, 200, 200, 429])

    with fake_clock() as clock:
        # The fixed-window burst, which SlowAPI's default strategy allows. In
        # its storage a fixed window starts at a client's FIRST request and
        # resets a minute later, whatever happened just before the reset.
        client = client_from("10.0.0.12")
        register(client, "boundary")
        login(client, "boundary")                   # t=0: opens the window
        clock.advance(58)
        for _ in range(4):                          # t=58: 4 more, limit reached
            login(client, "boundary")
        clock.advance(2.5)                          # t=60.5
        codes = [r.status_code for r in (login(client, "boundary") for _ in range(5)) if r is not None]
        print("        (a fixed window would reset at t=60 and allow all 5: 9 logins in 2.5 seconds)")
        check("at t=60.5 only the one slot freed by the t=0 login is available", codes, [200] + [429] * 4)

    # ======================================================================
    print("\n[11] rate limit — who is limited, and by what")
    # ======================================================================
    with fake_clock() as clock:
        hammer = client_from("10.0.0.13")
        bystander = client_from("10.0.0.14")
        register(hammer, "hammer")

        for _ in range(5):
            login(hammer, "hammer")
        request(hammer, "POST", "/auth/login", json={"username": "hammer", "password": PASSWORD}, expect=429, label="one IP exhausts its login budget")
        request(bystander, "POST", "/auth/login", json={"username": "hammer", "password": PASSWORD}, expect=200, label="  and a different IP is unaffected")

        spoofed = {"X-Forwarded-For": "203.0.113.99", "X-Real-IP": "203.0.113.98", "Forwarded": "for=203.0.113.97"}
        request(hammer, "POST", "/auth/login", json={"username": "hammer", "password": PASSWORD}, headers=spoofed, expect=429, label="forged forwarding headers do not buy a fresh budget")

        clock.advance(30)
        rejected = {r.status_code for r in (login(hammer, "hammer") for _ in range(50)) if r is not None}
        check("50 more attempts during the lockout are all 429", rejected, {429})
        clock.advance(30.5)
        request(hammer, "POST", "/auth/login", json={"username": "hammer", "password": PASSWORD}, expect=200, label="  and did not extend it: it still ends on time")

        # Requests refused before they reach the endpoint are not counted.
        other = client_from("10.0.0.15")
        register(other, "other")
        mixed = [
            send(other, "GET", "/does-not-exist"),
            send(other, "GET", "/auth/login"),
            send(other, "POST", "/auth/login", json={}),
            send(other, "POST", "/auth/login", json={"username": "other"}),
        ]
        check("404, 405 and 422 responses are produced normally", [m.status_code for m in mixed if m is not None], [404, 405, 422, 422])
        codes = [r.status_code for r in (login(other, "other") for _ in range(6)) if r is not None]
        check("  and spent none of the login budget: 5 logins then a 429", codes, [200] * 5 + [429])

    # ======================================================================
    print("\n[12] rate limit and CORS together — the middleware order")
    # ======================================================================
    with fake_clock():
        browser = client_from("10.0.0.16")
        register(browser, "streamlit")
        for _ in range(5):
            login(browser, "streamlit", headers={"Origin": STREAMLIT})

        r = request(browser, "POST", "/auth/login", json={"username": "streamlit", "password": PASSWORD}, headers={"Origin": STREAMLIT}, expect=429, label="a Streamlit page's 6th login is 429")
        if r is not None:
            check("  and still carries Access-Control-Allow-Origin, so the page can read it", r.headers.get("access-control-allow-origin"), STREAMLIT)
            check("  including the exposed Retry-After", "retry-after" in r.headers.get("access-control-expose-headers", "").lower(), True)

        limiter.reset()
        for _ in range(25):
            send(browser, "OPTIONS", "/auth/login", headers={"Origin": STREAMLIT, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        codes = [r.status_code for r in (login(browser, "streamlit", headers={"Origin": STREAMLIT}) for _ in range(5)) if r is not None]
        check("25 preflights spent none of the budget: all 5 logins still go through", codes, [200] * 5)

        limiter.reset()
        r = send(browser, "OPTIONS", "/auth/login")
        check("a bare OPTIONS (not a preflight) is 405", r.status_code if r is not None else None, 405)
        codes = [r.status_code for r in (login(browser, "streamlit") for _ in range(5)) if r is not None]
        check("  and is not counted", codes, [200] * 5)

        limiter.reset()
        r = request(browser, "GET", "/items", expect=200, label="a 200 from a limited endpoint")
        if r is not None:
            check("  carries X-RateLimit-Limit and X-RateLimit-Remaining", (r.headers.get("x-ratelimit-limit"), r.headers.get("x-ratelimit-remaining")), ("60", "59"))
            check("  and no Retry-After, which belongs on a 429 only", r.headers.get("retry-after"), None)
        r = request(browser, "POST", "/auth/login", json={"username": "streamlit", "password": "wrong"}, expect=401, label="a 401 from a wrong password")
        if r is not None:
            check("  also shows the login budget left", r.headers.get("x-ratelimit-remaining"), "4")

    # ======================================================================
    print("\n[13] rate limit under real concurrency")
    # ======================================================================
    limiter.reset()
    register(client_from("10.0.0.17"), "concurrent")
    limiter.reset()
    with live_server() as base_url:
        with httpx.Client(base_url=base_url, timeout=60.0) as live:
            with ThreadPoolExecutor(max_workers=20) as pool:
                responses = list(pool.map(lambda n: login(live, "concurrent", f"wrong {n}"), range(20)))
            codes = sorted(r.status_code for r in responses if r is not None)
            print(f"        20 simultaneous logins -> {codes.count(401)} x 401, {codes.count(429)} x 429")
            check("all 20 got a response", len(codes), 20)
            check("exactly 5 reached the password check", codes.count(401), 5)
            check("  and the other 15 were 429, not errors", codes.count(429), 15)

            limiter.reset()
            with ThreadPoolExecutor(max_workers=40) as pool:
                responses = list(pool.map(lambda _: send(live, "GET", "/items"), range(80)))
            codes = sorted(r.status_code for r in responses if r is not None)
            print(f"        80 simultaneous GET /items -> {codes.count(200)} x 200, {codes.count(429)} x 429")
            check("exactly 60 of 80 simultaneous list requests were served", codes.count(200), 60)
            check("  and the other 20 were 429", codes.count(429), 20)
    limiter.reset()

    # ======================================================================
    print("\n[14] the error envelope and the published schema")
    # ======================================================================
    with unmetered():
        client = client_from("10.0.0.18")
        quiet = TestClient(server.app, raise_server_exceptions=False, client=("10.0.0.18", 50000))

        @server.app.get("/_selftest/boom", include_in_schema=False)
        def _boom():
            return 1 / 0

        register(client, "envelope")
        for label, (method, path, kwargs, status, error) in {
            "unknown path": ("GET", "/nope", {}, 404, "NotFound"),
            "wrong method": ("DELETE", "/items", {}, 405, "MethodNotAllowed"),
            "bad body": ("POST", "/items", {"json": {"name": "<p></p>"}}, 422, "ValidationError"),
            "wrong password": ("POST", "/auth/login", {"json": {"username": "envelope", "password": "nope"}}, 401, "Unauthorized"),
            "taken username": ("POST", "/auth/register", {"json": {"username": "envelope", "password": PASSWORD}}, 409, "Conflict"),
        }.items():
            r = request(client, method, path, expect=status, label=f"{label} is {status}", **kwargs)
            body = json_of(r, label) if r is not None else None
            if body:
                check(f"  {label}: has error/detail/status_code", sorted({"error", "detail", "status_code"} & set(body)), ["detail", "error", "status_code"])
                check(f"  {label}: error is {error}", body.get("error"), error)

        api_logger = logging.getLogger("security_api")
        api_logger.disabled = True
        try:
            r = request(quiet, "GET", "/_selftest/boom", expect=500, label="an unhandled bug is 500")
        finally:
            api_logger.disabled = False
        body = json_of(r, "500") if r is not None else None
        if body:
            check("  in the envelope", body.get("error"), "InternalServerError")
            check("  with no exception text leaked", "ZeroDivision" in r.text or "division" in r.text, False)

        r = request(client, "GET", "/openapi.json", expect=200, label="the OpenAPI schema is served")
        spec = json_of(r, "spec") if r is not None else None
        if spec:
            paths = spec.get("paths", {})
            check("GET /items and POST /items are published at /items", sorted(paths.get("/items", {})), ["get", "post"])
            check("  /items/ (trailing slash) is not a separate path", "/items/" in paths, False)
            for method, path in (("get", "/items"), ("post", "/items"), ("post", "/auth/register"), ("post", "/auth/login"), ("get", "/auth/me")):
                check(f"{method.upper()} {path} documents its 429", "429" in paths.get(path, {}).get(method, {}).get("responses", {}), True)

    # ======================================================================
    print("\n[15] registration and login")
    # ======================================================================
    with unmetered():
        client = client_from("10.0.0.19")

        r = request(client, "POST", "/auth/register", json={"username": "Alice", "password": PASSWORD}, expect=201, label="registering Alice is 201")
        account = json_of(r, "register") if r is not None else None
        if account:
            check("  the username is stored lowercased", account.get("username"), "alice")
            check("  the response has id, username and created_at only", sorted(account), ["created_at", "id", "username"])
            check("  the password appears nowhere in it", PASSWORD in r.text, False)
        with SessionLocal() as db:
            stored = db.scalar(select(User).where(User.username == "alice"))
            stored_hash = stored.password_hash if stored else ""
        check("the database holds a bcrypt hash, cost 12", stored_hash[:7], "$2b$12$")
        check("  60 characters long", len(stored_hash), 60)
        check("  not the password", PASSWORD in stored_hash, False)
        check("  and it verifies against the password", bcrypt_ok(PASSWORD, stored_hash), True)

        with SessionLocal() as db:
            users_before = db.scalar(select(func.count()).select_from(User))
        request(client, "POST", "/auth/register", json={"username": "ALICE", "password": "another password"}, expect=409, label="registering ALICE again is 409 (usernames are case-insensitive)")
        with SessionLocal() as db:
            check("  and creates nothing", db.scalar(select(func.count()).select_from(User)), users_before)

        for label, body in [
            ("a 2-character username", {"username": "ab", "password": PASSWORD}),
            ("a 31-character username", {"username": "a" * 31, "password": PASSWORD}),
            ("markup in a username", {"username": "<script>", "password": PASSWORD}),
            ("a space in a username", {"username": "has space", "password": PASSWORD}),
            ("a non-ASCII username", {"username": "zoë", "password": PASSWORD}),
            ("a 7-character password", {"username": "shortpw", "password": "1234567"}),
            ("a password over 72 bytes (37 é = 74 bytes)", {"username": "longpw", "password": "é" * 37}),
            ("an unknown field", {"username": "admin2", "password": PASSWORD, "is_admin": True}),
        ]:
            request(client, "POST", "/auth/register", json=body, expect=422, label=f"register: {label} is 422")
        request(client, "POST", "/auth/register", json={"username": "maxpw", "password": "a" * 72}, expect=201, label="register: a password of exactly 72 bytes is accepted")

        r = request(client, "POST", "/auth/login", json={"username": "alice", "password": PASSWORD}, expect=200, label="login with the right password is 200")
        token_body = json_of(r, "token") if r is not None else None
        token = (token_body or {}).get("access_token", "")
        if token_body:
            check("  token_type is bearer", token_body.get("token_type"), "bearer")
            check("  expires_in is 30 minutes", token_body.get("expires_in"), 1800)
            try:
                claims = jwt.decode(token, os.environ["JWT_SECRET_KEY"], algorithms=["HS256"])
            except jwt.InvalidTokenError as exc:
                claims = {"error": str(exc)}
            check("  the token is signed with the server's key and names Alice's id", claims.get("sub"), str(account.get("id") if account else None))
            check("  and expires 30 minutes after it was issued", claims.get("exp", 0) - claims.get("iat", 0), 1800)

        request(client, "POST", "/auth/login", json={"username": "ALICE", "password": PASSWORD}, expect=200, label="login is case-insensitive on the username")

        wrong = request(client, "POST", "/auth/login", json={"username": "alice", "password": "wrong password"}, expect=401, label="a wrong password is 401")
        unknown = request(client, "POST", "/auth/login", json={"username": "mallory", "password": PASSWORD}, expect=401, label="an unknown username is 401")
        if wrong is not None and unknown is not None:
            check("  with the same message, so usernames cannot be discovered", json_of(wrong, "w").get("detail"), json_of(unknown, "u").get("detail"))
            check("  and a WWW-Authenticate: Bearer header", wrong.headers.get("www-authenticate"), "Bearer")

        def timed_login(username: str, password: str) -> float:
            started = time.perf_counter()
            login(client, username, password)
            return time.perf_counter() - started

        wrong_time = min(timed_login("alice", "wrong password") for _ in range(3))
        unknown_time = min(timed_login("nobody-at-all", "wrong password") for _ in range(3))
        print(f"        wrong password {wrong_time * 1000:.0f} ms, unknown user {unknown_time * 1000:.0f} ms")
        check("an unknown user takes as long as a wrong password (bcrypt runs for both)", unknown_time > wrong_time * 0.5, True)

        request(client, "POST", "/auth/login", json={"username": "alice", "password": PASSWORD + " "}, expect=401, label="passwords are not stripped: a trailing space is a different password")
        request(client, "POST", "/auth/register", json={"username": "markup", "password": "<b>secret</b>pw"}, expect=201, label="a password containing markup is accepted")
        request(client, "POST", "/auth/login", json={"username": "markup", "password": "<b>secret</b>pw"}, expect=200, label="  and logs in exactly as typed")
        request(client, "POST", "/auth/login", json={"username": "markup", "password": "secretpw"}, expect=401, label="  because passwords are not sanitized (the tags are part of it)")

        def me(auth: Optional[str]) -> Optional[httpx.Response]:
            return send(client, "GET", "/auth/me", headers={"Authorization": auth} if auth else {})

        r = me(f"Bearer {token}")
        check("GET /auth/me with the token is 200", r.status_code if r is not None else None, 200)
        if r is not None and r.status_code == 200:
            check("  and names the account", json_of(r, "me").get("username"), "alice")

        now = int(time.time())
        secret = os.environ["JWT_SECRET_KEY"]
        user_id = str(account.get("id")) if account else "1"
        forged = {
            "no Authorization header": None,
            "a token that is not a JWT": "Bearer not-a-token",
            "a token with its signature altered": f"Bearer {token[:-4]}{'AAAA' if not token.endswith('AAAA') else 'BBBB'}",
            "an expired token": "Bearer " + jwt.encode({"sub": user_id, "iat": now - 7200, "exp": now - 3600}, secret, algorithm="HS256"),
            "a token signed with a different key": "Bearer " + jwt.encode({"sub": user_id, "iat": now, "exp": now + 600}, "x" * 40, algorithm="HS256"),
            "an unsigned token (alg none)": "Bearer " + jwt.encode({"sub": user_id, "iat": now, "exp": now + 600}, None, algorithm="none"),
            "a token for an account that does not exist": "Bearer " + jwt.encode({"sub": "999999", "iat": now, "exp": now + 600}, secret, algorithm="HS256"),
            "a token with no expiry": "Bearer " + jwt.encode({"sub": user_id, "iat": now}, secret, algorithm="HS256"),
        }
        for label, auth in forged.items():
            r = me(auth)
            check(f"GET /auth/me with {label} is 401", r.status_code if r is not None else None, 401)

        # A 422 on a credentials body must not repeat the password back.
        secret_pw = "Sup3rSecretValue!"
        for label, kwargs in [
            ("a too-long password", {"json": {"username": "alice", "password": secret_pw * 6}}),
            ("a missing username", {"json": {"password": secret_pw}}),
            ("an unknown field", {"json": {"username": "alice", "password": secret_pw, "extra": 1}}),
            ("a text/plain body", {"content": ('{"username":"a","password":"%s"}' % secret_pw).encode(), "headers": {"Content-Type": "text/plain"}}),
        ]:
            for path in ("/auth/login", "/auth/register"):
                r = request(client, "POST", path, expect=422, label=f"{path}: {label} is 422", **kwargs)
                if r is not None:
                    check("  and the password is not in the response", secret_pw in r.text, False)
        r = request(client, "POST", "/items", json={"name": 12345}, expect=422, label="an item 422 still echoes the rejected value")
        if r is not None:
            check("  (the redaction is only for credential bodies)", "input" in (json_of(r, "item 422") or {}).get("errors", [{}])[0], True)

    # ======================================================================
    print("\n[16] regressions — bugs found by probing, kept covered here")
    # ======================================================================
    with unmetered():
        client = client_from("10.0.0.20")
        quiet = TestClient(server.app, raise_server_exceptions=False, client=("10.0.0.20", 50000))
        JSON = {"Content-Type": "application/json"}

        # Bug 1: values the stdlib JSON parser accepts but JSON output cannot
        # carry made the 422 handler crash inside itself (a 500).
        before = item_count()
        unencodable = {
            "a lone surrogate in name": b'{"name": "\\ud800x"}',
            "a lone surrogate beside another error": b'{"name": "\\ud800", "description": 5}',
            "NaN as name": b'{"name": NaN}',
            "Infinity as description": b'{"name": "ok", "description": Infinity}',
            "-Infinity inside a list": b'{"name": [1, -Infinity]}',
        }
        for label, body in unencodable.items():
            r = request(quiet, "POST", "/items", content=body, headers=JSON, expect=422, label=f"{label} is 422, not 500")
            if r is not None:
                envelope = json_of(r, label)
                check("  with a body that is valid JSON, in the envelope", (envelope or {}).get("error"), "ValidationError")
        request(quiet, "POST", "/auth/login", content=b'{"username": "\\ud800", "password": NaN}', headers=JSON, expect=422, label="the same on the login endpoint is 422, not 500")
        check("  and none of them stored anything", item_count(), before)

        # Bug 1, second half: the rejected value was echoed back unbounded.
        huge = b'{"name": "' + b"x" * 5_000_000 + b'"}'
        r = request(client, "POST", "/items", content=huge, headers=JSON, expect=422, label="a 5 MB invalid name is 422")
        if r is not None:
            check("  answered in under 2 KB, not 5 MB", len(r.content) < 2000, True)
            echoed = ((json_of(r, "huge") or {}).get("errors") or [{}])[0].get("input", "")
            check("  echoing a 200-character preview", (len(echoed), echoed.endswith("…")), (201, True))
        nested = b'{"name": ' + b"[" * 900 + b"]" * 900 + b"}"
        r = request(client, "POST", "/items", content=nested, headers=JSON, expect=422, label="a list nested 900 deep is 422")
        if r is not None:
            check("  also with a bounded response", len(r.content) < 2000, True)

        # Bug 2: 405 responses dropped the Allow header, and Starlette's own
        # version of it named only the first matching route's methods.
        for method, path, allow in [("DELETE", "/items", "GET, POST"), ("PUT", "/items", "GET, POST"), ("GET", "/auth/login", "POST"), ("POST", "/", "GET")]:
            r = request(client, method, path, expect=405, label=f"{method} {path} is 405")
            if r is not None:
                check(f"  with Allow: {allow} (every method the path accepts)", r.headers.get("allow"), allow)

        # Bug 3: a 500 left the middleware stack outside CORS, so a browser
        # would have reported it as a CORS failure. (Route from section [14].)
        api_logger = logging.getLogger("security_api")
        api_logger.disabled = True
        try:
            r = request(quiet, "GET", "/_selftest/boom", headers={"Origin": STREAMLIT}, expect=500, label="a 500 requested from an allowed origin")
        finally:
            api_logger.disabled = False
        if r is not None:
            check("  carries Access-Control-Allow-Origin, so the browser shows the 500", r.headers.get("access-control-allow-origin"), STREAMLIT)
            check("  still in the envelope", (json_of(r, "boom") or {}).get("error"), "InternalServerError")

        # Bug 4: a name made only of invisible characters passed the
        # "must contain text" rule and was stored as a blank-looking row.
        before = item_count()
        for label, name in [
            ("zero-width spaces", "\u200b\u200b"),
            ("a byte-order mark", "\ufeff"),
            ("a soft hyphen", "\u00ad"),
            ("a NUL character", "\x00"),
            ("a Hangul filler", "\u3164"),
            ("a Braille blank inside markup", "<b>\u2800</b>"),
        ]:
            request(client, "POST", "/items", json={"name": name}, expect=422, label=f"a name of only {label} is 422")
        check("  none of them stored", item_count(), before)
        r = request(client, "POST", "/items", json={"name": "Wid\u200bget"}, expect=201, label="a real name containing a zero-width space is accepted")
        if r is not None:
            check("  and stored exactly as sent", (json_of(r, "zwsp") or {}).get("name"), "Wid\u200bget")

    # Bug 5: CORS entries that passed validation but could never match the
    # Origin a browser sends.
    parse = server.parse_allowed_origins
    for raw, expected in [
        ("https://app.example.com:443", ["https://app.example.com"]),
        ("http://localhost:80", ["http://localhost"]),
        ("https://app.example.com:8443", ["https://app.example.com:8443"]),
        ("https://bücher.example", ["https://xn--bcher-kva.example"]),
        ("http://[0:0:0:0:0:0:0:1]:8501", ["http://[::1]:8501"]),
    ]:
        check(f"{raw} is normalized to what a browser sends", parse(raw), expected)
    for label, raw in [
        ("an empty port", "http://localhost:"),
        ("port 0", "http://localhost:0"),
        ("a space in the host", "http://local host:8501"),
        ("an empty domain label", "https://app..example.com"),
        ("an invalid IPv6 address", "http://[::zz]:3000"),
    ]:
        check(f"refuses {label}", rejects(raw), True)

    default_port_app = FastAPI()
    default_port_app.add_middleware(CORSMiddleware, allow_origins=parse("https://app.example.com:443"))
    default_port_app.get("/")(lambda: {"ok": True})
    with TestClient(default_port_app) as port_client:
        r = request(port_client, "GET", "/", headers={"Origin": "https://app.example.com"}, expect=200, label="a config written with :443")
        if r is not None:
            check("  grants the origin a browser actually sends", r.headers.get("access-control-allow-origin"), "https://app.example.com")

    # Bug 6: a very large integer crashed the startup check with OverflowError.
    os.environ["JWT_EXPIRE_MINUTES"] = "1" + "0" * 400
    try:
        outcome = type(config.read_positive_number("JWT_EXPIRE_MINUTES", 30, integer=True)).__name__
    except ValueError:
        outcome = "refused cleanly"
    except Exception as exc:
        outcome = f"crashed with {type(exc).__name__}"
    finally:
        os.environ.pop("JWT_EXPIRE_MINUTES", None)
    check("a 401-digit number setting does not crash the startup check", outcome, "int")

    # Bug 7: printing a failed unicode check crashed on legacy consoles.
    check("this script's output replaces unprintable characters instead of crashing", sys.stdout.errors, "backslashreplace")

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} of {checks} check(s) FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)
    print(f"All {checks} checks passed.")

finally:
    engine.dispose()
    shutil.rmtree(SCRATCH, ignore_errors=True)
