"""Checks the L10 security behaviour, and tries to break it.

Run from security-basics/:

    python verify_security.py

What the brief asks for, and what this establishes about each:

    CORS         specific origins, methods and headers; never "*"
    rate limit   429 once an IP exceeds 10 requests per minute
    sanitizing   name and description stripped of whitespace and HTML tags
    SQL note     parameterized queries, explained and demonstrated

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
for _var in ("CORS_ALLOWED_ORIGINS", "RATE_LIMIT_REQUESTS", "RATE_LIMIT_WINDOW_SECONDS"):
    os.environ.pop(_var, None)
# Newer Starlette warns that its TestClient will move off httpx. Harmless
# here, and it would otherwise print in the middle of the results.
warnings.filterwarnings("ignore", message=".*httpx.*")
# ----------------------------------------------------------------------------

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects import sqlite as sqlite_dialect  # noqa: E402

import app.main as server  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.models.item import Item  # noqa: E402
from app.routers.items import sanitize_text  # noqa: E402

failures: list[str] = []
checks = 0

ALLOWED = "http://localhost:3000"
ALSO_ALLOWED = "http://127.0.0.1:3000"
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
    """Lifts the rate limit for sections that are about something else.

    Sections [3] to [6] make more than ten requests each, and a 429 halfway
    through a sanitization check would be a failure of the test, not of the
    sanitizer. The limit itself is tested at its real value from [8] on.
    """
    original = server.RATE_LIMIT_REQUESTS
    server.RATE_LIMIT_REQUESTS = 10**9
    server.reset_rate_limiter()
    try:
        yield
    finally:
        server.RATE_LIMIT_REQUESTS = original
        server.reset_rate_limiter()


class FakeClock:
    """Stands in for time.monotonic() so a 60-second window takes no time."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@contextmanager
def fake_clock():
    clock = FakeClock()
    original = server.clock
    server.clock = clock
    server.reset_rate_limiter()
    try:
        yield clock
    finally:
        server.clock = original
        server.reset_rate_limiter()


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

        r = request(client, "GET", "/items", headers={"Origin": ALLOWED}, expect=200, label="a GET from an allowed origin")
        if r is not None:
            check("  echoes that exact origin, not *", r.headers.get("access-control-allow-origin"), ALLOWED)
            check("  varies the cache on Origin", "origin" in r.headers.get("vary", "").lower(), True)
            check("  does not allow credentials", r.headers.get("access-control-allow-credentials"), None)
            exposed = {h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",")}
            check("  exposes Retry-After and the rate-limit headers to page JS", {"retry-after", "x-ratelimit-limit", "x-ratelimit-remaining"} <= exposed, True)

        r = request(client, "GET", "/items", headers={"Origin": ALSO_ALLOWED}, expect=200, label="127.0.0.1:3000 is a separate allowed origin")
        if r is not None:
            check("  and is echoed as itself", r.headers.get("access-control-allow-origin"), ALSO_ALLOWED)

        for origin in (EVIL, "http://localhost:3001", "https://localhost:3000", "null", "http://localhost:3000.evil.example"):
            r = request(client, "GET", "/items", headers={"Origin": origin}, expect=200, label=f"a GET from {origin} is still served")
            if r is not None:
                check("  but the browser is given no permission to read it", r.headers.get("access-control-allow-origin"), None)
        print("        (CORS is enforced by browsers; it is not access control)")

        def preflight(origin: str, method: str, headers: Optional[str] = "content-type"):
            h = {"Origin": origin, "Access-Control-Request-Method": method}
            if headers:
                h["Access-Control-Request-Headers"] = headers
            return send(client, "OPTIONS", "/items", headers=h)

        r = preflight(ALLOWED, "POST")
        if r is not None:
            check("preflight: allowed origin, POST, Content-Type is 200", r.status_code, 200)
            check("  echoing the origin", r.headers.get("access-control-allow-origin"), ALLOWED)
            check("  listing exactly GET and POST", r.headers.get("access-control-allow-methods"), "GET, POST")
            check("  caching the answer for 600s", r.headers.get("access-control-max-age"), "600")

        for method in ("DELETE", "PUT", "PATCH"):
            r = preflight(ALLOWED, method)
            if r is not None:
                check(f"preflight: {method} is refused", r.status_code, 400)

        r = preflight(ALLOWED, "POST", headers="content-type, authorization")
        if r is not None:
            check("preflight: an unlisted Authorization header is refused", r.status_code, 400)
        r = preflight(ALLOWED, "POST", headers="x-custom-header")
        if r is not None:
            check("preflight: an arbitrary custom header is refused", r.status_code, 400)
        r = preflight(EVIL, "POST")
        if r is not None:
            check("preflight: a disallowed origin is refused", r.status_code, 400)
            check("  and granted nothing", r.headers.get("access-control-allow-origin"), None)

        wildcard_seen = []
        for origin in (ALLOWED, EVIL, "null"):
            for method, path in (("GET", "/items"), ("GET", "/nope"), ("POST", "/items")):
                resp = send(client, method, path, headers={"Origin": origin}, json={"name": "x"} if method == "POST" else None)
                if resp is not None and resp.headers.get("access-control-allow-origin") == "*":
                    wildcard_seen.append((origin, method, path))
        check("no response ever carries Access-Control-Allow-Origin: *", wildcard_seen, [])

    # ======================================================================
    print("\n[7] CORS configuration")
    # ======================================================================
    parse = server.parse_allowed_origins
    check("the configured origins are the two dev-server origins", server.ALLOWED_ORIGINS, [ALLOWED, ALSO_ALLOWED])
    check("unset uses the defaults", parse(None), [ALLOWED, ALSO_ALLOWED])
    check("blank uses the defaults", parse("   "), [ALLOWED, ALSO_ALLOWED])
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
        ("a trailing slash", "http://localhost:3000/"),
        ("a path", "https://app.example.com/api"),
        ("a missing scheme", "localhost:3000"),
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

    for name, raw, integer in [
        ("RATE_LIMIT_REQUESTS", "0", True),
        ("RATE_LIMIT_REQUESTS", "-5", True),
        ("RATE_LIMIT_REQUESTS", "1O", True),
        ("RATE_LIMIT_REQUESTS", "2.5", True),
        ("RATE_LIMIT_WINDOW_SECONDS", "nan", False),
        ("RATE_LIMIT_WINDOW_SECONDS", "inf", False),
    ]:
        os.environ[name] = raw
        try:
            server.read_positive_number(name, 1, integer=integer)
            outcome = "accepted"
        except ValueError:
            outcome = "refused"
        finally:
            os.environ.pop(name, None)
        check(f"{name}={raw} is refused at startup", outcome, "refused")
    os.environ["RATE_LIMIT_WINDOW_SECONDS"] = "1.5"
    check("a fractional window is allowed", server.read_positive_number("RATE_LIMIT_WINDOW_SECONDS", 60.0, integer=False), 1.5)
    os.environ.pop("RATE_LIMIT_WINDOW_SECONDS", None)

    # ======================================================================
    print("\n[8] rate limit — the eleventh request in a minute is 429")
    # ======================================================================
    check("the limit in force is 10 requests", server.RATE_LIMIT_REQUESTS, 10)
    check("  per 60 seconds", server.RATE_LIMIT_WINDOW_SECONDS, 60.0)

    with fake_clock():
        client = client_from("10.0.0.8")
        remaining_seen = []
        statuses = []
        for _ in range(10):
            resp = send(client, "GET", "/items")
            if resp is not None:
                statuses.append(resp.status_code)
                remaining_seen.append(resp.headers.get("x-ratelimit-remaining"))
        check("the first 10 requests are all 200", statuses, [200] * 10)
        check("  counting X-RateLimit-Remaining down from 9 to 0", remaining_seen, [str(n) for n in range(9, -1, -1)])

        r = request(client, "GET", "/items", expect=429, label="the 11th request is 429")
        if r is not None:
            envelope = json_of(r, "429 body")
            if envelope:
                check("  labelled TooManyRequests", envelope.get("error"), "TooManyRequests")
                check("  with status_code matching", envelope.get("status_code"), 429)
                check("  and a detail naming the limit", "10 requests per 60 seconds" in envelope.get("detail", ""), True)
            check("  telling the client to wait the full 60s (no time has passed)", r.headers.get("retry-after"), "60")
            check("  with X-RateLimit-Remaining 0", r.headers.get("x-ratelimit-remaining"), "0")

        # The 429 must be decided BEFORE the endpoint runs, or a limited POST
        # would still write a row and just report failure afterwards.
        with unmetered():
            count_client = client_from("10.0.0.80")
            got = request(count_client, "GET", "/items", expect=200, label="count items before a limited POST")
            before = len(json_of(got, "before") or []) if got is not None else None
        server.request_counts["10.0.0.8"] = [server.clock()] * 10  # still limited after unmetered() reset it
        request(client, "POST", "/items", json={"name": "Should not exist"}, expect=429, label="a POST while limited is 429")
        with unmetered():
            got = request(count_client, "GET", "/items", expect=200, label="count items after it")
            after = len(json_of(got, "after") or []) if got is not None else None
        check("  and created nothing", after, before)

    # ======================================================================
    print("\n[9] rate limit — the window slides")
    # ======================================================================
    with fake_clock() as clock:
        client = client_from("10.0.0.9")
        for _ in range(10):
            send(client, "GET", "/items")

        clock.advance(59.5)
        r = request(client, "GET", "/items", expect=429, label="59.5s later: still limited")
        if r is not None:
            check("  Retry-After rounds the remaining 0.5s UP to 1", r.headers.get("retry-after"), "1")

        clock.advance(0.5)
        request(client, "GET", "/items", expect=200, label="exactly 60s later: allowed again")

    with fake_clock() as clock:
        client = client_from("10.0.0.19")
        # 5 requests at t=0 and 5 at t=30: the limit is reached at t=30.
        for _ in range(5):
            send(client, "GET", "/items")
        clock.advance(30)
        for _ in range(5):
            send(client, "GET", "/items")
        r = request(client, "GET", "/items", expect=429, label="5 at t=0 and 5 at t=30: the 11th at t=30 is 429")
        if r is not None:
            check("  waiting only until the t=0 batch expires", r.headers.get("retry-after"), "30")

        clock.advance(30)
        statuses = [resp.status_code for resp in (send(client, "GET", "/items") for _ in range(6)) if resp is not None]
        check("at t=60 the first batch has left: exactly 5 slots free", statuses, [200] * 5 + [429])

    with fake_clock() as clock:
        # The fixed-window loophole: a burst either side of a minute boundary.
        client = client_from("10.0.0.29")
        clock.advance(59)
        for _ in range(10):
            send(client, "GET", "/items")
        clock.advance(1)
        request(client, "GET", "/items", expect=429, label="10 at 0:59 then one at 1:00 is still 429 (no fixed-window burst)")

    # ======================================================================
    print("\n[10] rate limit — who is limited, and by what")
    # ======================================================================
    with fake_clock() as clock:
        hammer = client_from("10.0.0.10")
        bystander = client_from("10.0.0.11")

        for _ in range(10):
            send(hammer, "GET", "/items")
        request(hammer, "GET", "/items", expect=429, label="one IP exhausts its budget")
        request(bystander, "GET", "/items", expect=200, label="  and a different IP is unaffected")

        spoofed = {
            "X-Forwarded-For": "203.0.113.99",
            "X-Real-IP": "203.0.113.98",
            "Forwarded": "for=203.0.113.97",
        }
        request(hammer, "GET", "/items", headers=spoofed, expect=429, label="forged forwarding headers do not buy a fresh budget")

        # Hammering during the lockout must not extend it.
        clock.advance(30)
        rejected = [resp.status_code for resp in (send(hammer, "GET", "/items") for _ in range(50)) if resp is not None]
        check("50 more requests during the lockout are all 429", set(rejected), {429})
        check("  and none of them were recorded", len(server.request_counts.get("10.0.0.10", [])), 10)
        clock.advance(30)
        request(hammer, "GET", "/items", expect=200, label="  so the lockout still ends on time")

        other = client_from("10.0.0.12")
        mixed = [
            send(other, "GET", "/does-not-exist"),
            send(other, "POST", "/items", json={"name": ""}),
            send(other, "DELETE", "/items"),
        ]
        check("404, 422 and 405 responses are produced normally", [m.status_code for m in mixed if m is not None], [404, 422, 405])
        check("  and each one spent a request", len(server.request_counts.get("10.0.0.12", [])), 3)

    # ======================================================================
    print("\n[11] rate limit and CORS together — the middleware order")
    # ======================================================================
    with fake_clock():
        browser = client_from("10.0.0.13")
        for _ in range(10):
            send(browser, "GET", "/items", headers={"Origin": ALLOWED})

        r = request(browser, "GET", "/items", headers={"Origin": ALLOWED}, expect=429, label="a browser's 11th request is 429")
        if r is not None:
            check("  and still carries Access-Control-Allow-Origin, so page JS can read it", r.headers.get("access-control-allow-origin"), ALLOWED)
            check("  including the exposed Retry-After", "retry-after" in r.headers.get("access-control-expose-headers", "").lower(), True)

        server.reset_rate_limiter()
        for _ in range(25):
            send(browser, "OPTIONS", "/items", headers={"Origin": ALLOWED, "Access-Control-Request-Method": "POST"})
        check("25 preflights spent none of the budget", len(server.request_counts.get("10.0.0.13", [])), 0)
        statuses = [resp.status_code for resp in (send(browser, "GET", "/items") for _ in range(10)) if resp is not None]
        check("  so all 10 real requests still go through", statuses, [200] * 10)

        # An OPTIONS that is not a CORS preflight passes through CORS to the
        # limiter, and is counted like any other request.
        server.reset_rate_limiter()
        send(browser, "OPTIONS", "/items")
        check("a bare OPTIONS (not a preflight) is counted", len(server.request_counts.get("10.0.0.13", [])), 1)

    # ======================================================================
    print("\n[12] rate limit — memory stays bounded")
    # ======================================================================
    with fake_clock() as clock:
        for n in range(200):
            send(client_from(f"10.1.{n // 250}.{n % 250}"), "GET", "/")
        check("200 distinct IPs are tracked", len(server.request_counts), 200)
        check("  each holding one timestamp", {len(v) for v in server.request_counts.values()}, {1})

        clock.advance(61)
        send(client_from("10.2.0.1"), "GET", "/")
        check("once they go quiet for a window, the next request sweeps them", list(server.request_counts), ["10.2.0.1"])

        swept_at = server._last_sweep
        clock.advance(10)
        send(client_from("10.2.0.2"), "GET", "/")
        check("  and the sweep is not re-run on every request", server._last_sweep, swept_at)

        busy = client_from("10.2.0.3")
        for _ in range(500):
            send(busy, "GET", "/")
        check("500 requests from one IP store at most 10 timestamps", len(server.request_counts["10.2.0.3"]), 10)

    # ======================================================================
    print("\n[13] rate limit under real concurrency")
    # ======================================================================
    server.reset_rate_limiter()
    with live_server() as base_url:
        with httpx.Client(base_url=base_url, timeout=30.0) as live:
            with ThreadPoolExecutor(max_workers=30) as pool:
                responses = list(pool.map(lambda _: send(live, "GET", "/items"), range(30)))
            codes = sorted(r.status_code for r in responses if r is not None)
            print(f"        30 simultaneous requests -> {codes.count(200)} x 200, {codes.count(429)} x 429")
            check("all 30 got a response", len(codes), 30)
            check("exactly 10 were served", codes.count(200), 10)
            check("  and the other 20 were 429, not errors", codes.count(429), 20)
            check("the limiter saw the real socket address", list(server.request_counts), ["127.0.0.1"])
            check("  and recorded exactly 10 timestamps for it", len(server.request_counts["127.0.0.1"]), 10)
    server.reset_rate_limiter()

    # ======================================================================
    print("\n[14] the error envelope and the published schema")
    # ======================================================================
    with unmetered():
        client = client_from("10.0.0.14")
        quiet = TestClient(server.app, raise_server_exceptions=False, client=("10.0.0.14", 50000))

        @server.app.get("/_selftest/boom", include_in_schema=False)
        def _boom():
            return 1 / 0

        for label, (method, path, kwargs, status, error) in {
            "unknown path": ("GET", "/nope", {}, 404, "NotFound"),
            "wrong method": ("DELETE", "/items", {}, 405, "MethodNotAllowed"),
            "bad body": ("POST", "/items", {"json": {"name": "<p></p>"}}, 422, "ValidationError"),
        }.items():
            r = request(client, method, path, expect=status, label=f"{label} is {status}", **kwargs)
            body = json_of(r, label) if r is not None else None
            if body:
                check(f"  {label}: has error/detail/status_code", sorted({"error", "detail", "status_code"} & set(body)), ["detail", "error", "status_code"])
                check(f"  {label}: error is {error}", body.get("error"), error)

        # The handler logs the traceback on purpose; silenced here so the
        # expected ZeroDivisionError does not look like a failure of the run.
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
            item_ops = spec.get("paths", {}).get("/items", {})
            check("GET /items and POST /items are published at /items", sorted(item_ops), ["get", "post"])
            check("  /items/ (trailing slash) is not a separate path", "/items/" in spec.get("paths", {}), False)
            check("GET /items documents its 429", "429" in item_ops.get("get", {}).get("responses", {}), True)
            check("POST /items documents its 429", "429" in item_ops.get("post", {}).get("responses", {}), True)

    # ======================================================================
    print("\n[15] regressions — bugs found by probing, kept covered here")
    # ======================================================================
    with unmetered():
        client = client_from("10.0.0.15")
        quiet = TestClient(server.app, raise_server_exceptions=False, client=("10.0.0.15", 50000))
        JSON = {"Content-Type": "application/json"}

        def count_now() -> Optional[int]:
            got = request(client, "GET", "/items", expect=200, label="count items")
            data = json_of(got, "count") if got is not None else None
            return len(data) if isinstance(data, list) else None

        # Bug 1: values the stdlib JSON parser accepts but JSON output cannot
        # carry made the 422 handler crash inside itself (a 500).
        before = count_now()
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
        check("  and none of them stored anything", count_now(), before)

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
        for method, path, allow in [("DELETE", "/items", "GET, POST"), ("PUT", "/items", "GET, POST"), ("POST", "/", "GET")]:
            r = request(client, method, path, expect=405, label=f"{method} {path} is 405")
            if r is not None:
                check(f"  with Allow: {allow} (every method the path accepts)", r.headers.get("allow"), allow)

        # Bug 3: a 500 left the middleware stack outside CORS, so a browser
        # would have reported it as a CORS failure. (Route from section [14].)
        api_logger = logging.getLogger("security_api")
        api_logger.disabled = True
        try:
            r = request(quiet, "GET", "/_selftest/boom", headers={"Origin": ALLOWED}, expect=500, label="a 500 requested from an allowed origin")
        finally:
            api_logger.disabled = False
        if r is not None:
            check("  carries Access-Control-Allow-Origin, so the browser shows the 500", r.headers.get("access-control-allow-origin"), ALLOWED)
            check("  and the rate-limit headers", "x-ratelimit-remaining" in r.headers, True)
            check("  still in the envelope", (json_of(r, "boom") or {}).get("error"), "InternalServerError")

        # Bug 4: a name made only of invisible characters passed the
        # "must contain text" rule and was stored as a blank-looking row.
        before = count_now()
        for label, name in [
            ("zero-width spaces", "\u200b\u200b"),
            ("a byte-order mark", "\ufeff"),
            ("a soft hyphen", "\u00ad"),
            ("a NUL character", "\x00"),
            ("a Hangul filler", "\u3164"),
            ("a Braille blank inside markup", "<b>\u2800</b>"),
        ]:
            request(client, "POST", "/items", json={"name": name}, expect=422, label=f"a name of only {label} is 422")
        check("  none of them stored", count_now(), before)
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
        ("http://[0:0:0:0:0:0:0:1]:3000", ["http://[::1]:3000"]),
    ]:
        check(f"{raw} is normalized to what a browser sends", parse(raw), expected)
    for label, raw in [
        ("an empty port", "http://localhost:"),
        ("port 0", "http://localhost:0"),
        ("a space in the host", "http://local host:3000"),
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
    os.environ["RATE_LIMIT_REQUESTS"] = "1" + "0" * 400
    try:
        outcome = type(server.read_positive_number("RATE_LIMIT_REQUESTS", 10, integer=True)).__name__
    except ValueError:
        outcome = "refused cleanly"
    except Exception as exc:
        outcome = f"crashed with {type(exc).__name__}"
    finally:
        os.environ.pop("RATE_LIMIT_REQUESTS", None)
    check("a 401-digit RATE_LIMIT_REQUESTS does not crash the startup check", outcome, "int")

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
