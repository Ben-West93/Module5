# exercises/test-suite/tests/test_security.py
# L10 security behaviour, tested on the L11 task endpoints
#
# verify_security.py covers the L10 defences in depth, against /items. These
# tests cover what changed when the task endpoints joined the same app: that
# sanitizing, CORS, the rate limit and the 405 Allow header all apply to
# /tasks too, including its PATCH and DELETE methods, which L10 never had.
# They also cover the fixes from the second round of edge-case probing that
# live in the shared layers: invisible and look-alike titles, the request body
# size limit, and HEAD.

import unicodedata

import pytest

import app.main as server
from app.main import MAX_BODY_BYTES
from tests.helpers import (
    assert_error_envelope,
    call,
    create_task,
    json_of,
    stored_task,
    stored_titles,
)

ALLOWED_ORIGIN = "http://localhost:3000"
EVIL_ORIGIN = "https://evil.example"


# ============================================================================
# Sanitization
# ============================================================================


def test_create_task_strips_markup_before_storing(client, db_session):
    body = create_task(
        client,
        title="  <b>Buy</b> milk<img src=x onerror=alert(1)//",
        description="<script>steal()</script>2%, not whole",
    )
    expected = {"title": "Buy milk", "description": "steal()2%, not whole", "completed": False}

    assert {k: body[k] for k in expected} == expected
    assert stored_task(db_session, body["id"]) == {"id": body["id"], **expected}  # stored, not just returned


def test_patch_task_strips_markup_before_storing(client, db_session):
    created = create_task(client, title="Plain")
    changes = {"title": "<i>Fancy</i>", "description": "<a href='javascript:x()'>link</a> text"}

    body = json_of(call(client, "PATCH", f"/tasks/{created['id']}", json=changes, expect=200))

    assert (body["title"], body["description"]) == ("Fancy", "link text")
    assert stored_task(db_session, created["id"]) == body


def test_clean_text_is_stored_byte_for_byte(client):
    sent = {"title": "3 < 5 and 5 > 3", "description": "Zoë's 学生 🎓 &lt;b&gt;"}
    body = create_task(client, **sent)
    assert {k: body[k] for k in sent} == sent


@pytest.mark.parametrize(
    "title",
    [
        "<b></b>",
        "   ",
        "<img src=x onerror=alert(1)",
        "\u200b\u200b",
        # Regressions: each of these was stored as a blank-looking task.
        "\u0301",   # a combining accent with no letter under it
        "\ufe0f",   # a variation selector
        "\u034f",   # the combining grapheme joiner
        "\u20dd",   # a combining enclosing circle
    ],
    ids=[
        "markup-only", "whitespace-only", "unterminated-tag-only", "zero-width-only",
        "combining-accent-only", "variation-selector-only", "grapheme-joiner-only", "enclosing-mark-only",
    ],
)
def test_title_that_sanitizes_to_nothing_is_422(client, db_session, title):
    call(client, "POST", "/tasks", json={"title": title}, expect=422)
    created = create_task(client, title="Real title")
    call(client, "PATCH", f"/tasks/{created['id']}", json={"title": title}, expect=422)
    assert stored_titles(db_session) == ["Real title"]


def test_an_accent_on_a_real_letter_is_accepted(client, db_session):
    """Only titles made entirely of marks are refused. "e" + U+0301 is stored
    in its composed form, the way a keyboard would have typed it."""
    body = create_task(client, title="Cafe\u0301")
    assert body["title"] == "Caf\u00e9"
    assert stored_task(db_session, body["id"])["title"] == "Caf\u00e9"


@pytest.mark.parametrize(
    "first, second",
    [
        ("Caf\u00e9", "Cafe\u0301"),          # precomposed vs letter + combining accent
        ("Cafe\u0301", "Caf\u00e9"),
        ("Chores", "Cho\u200bres"),            # zero-width space inside
        ("Chores", "\u200dChores\ufeff"),      # zero-width joiner and BOM around it
        ("Chores", "Cho\u00adres"),            # soft hyphen
    ],
    ids=["nfd-after-nfc", "nfc-after-nfd", "zero-width-space", "joiner-and-bom", "soft-hyphen"],
)
def test_lookalike_titles_are_duplicates(client, db_session, first, second):
    """Regression: each pair was stored as two tasks that look identical."""
    original = create_task(client, title=first)

    call(client, "POST", "/tasks", json={"title": second}, expect=409)
    other = create_task(client, title="Other")
    call(client, "PATCH", f"/tasks/{other['id']}", json={"title": second}, expect=409)

    assert stored_titles(db_session) == [original["title"], "Other"]
    assert original["title"] == unicodedata.normalize("NFC", first).replace("\u200b", "")


def test_removing_invisible_characters_cannot_assemble_a_tag(client, db_session):
    """Titles lose their zero-width characters BEFORE sanitizing. Done after,
    "<" + zero-width space + "script>" would pass the sanitizer as text and
    then become a live <script> tag."""
    body = create_task(client, title="Hi <\u200bscript>alert(1)</\u200bscript>")
    stored = stored_task(db_session, body["id"])["title"]
    assert "<" not in stored
    assert stored == "Hi alert(1)"


def test_descriptions_keep_zero_width_joiners(client):
    """Only titles are normalized. A joiner inside an emoji sequence in a
    description is kept as typed."""
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    body = create_task(client, title="Family dinner", description=family)
    assert body["description"] == family


def test_uniqueness_is_checked_on_the_sanitized_title(client, db_session):
    create_task(client, title="Chores")
    response = call(client, "POST", "/tasks", json={"title": "<em>Chores</em>"}, expect=409)
    assert_error_envelope(response, "Conflict")
    assert stored_titles(db_session) == ["Chores"]


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_non_json_bodies_are_refused(client, db_session, content_type):
    """A browser sends these cross-origin with no preflight, so CORS never gets
    a say. Refusing them is what keeps a hostile page from writing tasks."""
    headers = {"Content-Type": content_type, "Origin": EVIL_ORIGIN}
    call(client, "POST", "/tasks", content=b'{"title": "Sneaky"}', headers=headers, expect=422)
    created = create_task(client, title="Target")
    call(client, "PATCH", f"/tasks/{created['id']}", content=b'{"title": "Sneaky"}', headers=headers, expect=422)
    assert stored_titles(db_session) == ["Target"]


# ============================================================================
# CORS
# ============================================================================


def preflight(client, method: str, origin: str = ALLOWED_ORIGIN, path: str = "/tasks/1"):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_preflight_allows_the_task_methods(client, method):
    """L10 allowed only GET and POST. Without PATCH and DELETE, a browser
    frontend could never update or delete a task."""
    response = preflight(client, method)
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-methods"] == "GET, POST, PATCH, DELETE"


def test_preflight_refuses_put_which_nothing_accepts(client):
    assert preflight(client, "PUT").status_code == 400


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_preflight_from_a_disallowed_origin_is_refused(client, method):
    response = preflight(client, method, origin=EVIL_ORIGIN)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_task_responses_grant_only_allowed_origins(client):
    allowed = call(client, "GET", "/tasks", headers={"Origin": ALLOWED_ORIGIN}, expect=200)
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    denied = call(client, "GET", "/tasks", headers={"Origin": EVIL_ORIGIN}, expect=200)
    assert "access-control-allow-origin" not in denied.headers


# ============================================================================
# Rate limiting
# ============================================================================


def test_each_test_starts_with_a_fresh_limiter(client):
    """The fixture resets the limiter, so no test inherits another's requests."""
    assert server.request_counts == {}
    assert server.RATE_LIMIT_REQUESTS == 10


def test_task_routes_are_rate_limited(client, db_session):
    created = create_task(client, title="Budget")  # request 1

    remaining = []
    for path in ["/tasks", f"/tasks/{created['id']}"] * 4 + ["/tasks"]:  # requests 2-10
        remaining.append(call(client, "GET", path, expect=200).headers["x-ratelimit-remaining"])
    assert remaining == [str(n) for n in range(8, -1, -1)]

    limited = call(client, "GET", "/tasks", expect=429)  # request 11
    body = assert_error_envelope(limited, "TooManyRequests")
    assert "10 requests per 60 seconds" in body["detail"]
    assert limited.headers["retry-after"].isdigit()

    # Refused before the endpoint runs, so nothing is written or removed.
    call(client, "POST", "/tasks", json={"title": "Too late"}, expect=429)
    call(client, "PATCH", f"/tasks/{created['id']}", json={"completed": True}, expect=429)
    call(client, "DELETE", f"/tasks/{created['id']}", expect=429)
    assert stored_task(db_session, created["id"]) == created
    assert stored_titles(db_session) == ["Budget"]


def test_items_and_tasks_share_one_budget(client):
    """The limit is per client, not per router."""
    for _ in range(5):
        call(client, "GET", "/items", expect=200)
    for _ in range(5):
        call(client, "GET", "/tasks", expect=200)
    call(client, "GET", "/items", expect=429)
    call(client, "GET", "/tasks", expect=429)


def test_a_limited_browser_request_can_still_be_read(client):
    for _ in range(10):
        call(client, "GET", "/tasks", expect=200)
    response = call(client, "PATCH", "/tasks/1", json={}, headers={"Origin": ALLOWED_ORIGIN}, expect=429)
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "retry-after" in response.headers["access-control-expose-headers"].lower()


def test_preflights_do_not_spend_the_budget(client):
    """CORS sits outside the limiter and answers preflights itself. A browser
    preflights every PATCH and DELETE, so counting them would halve a real
    user's allowance."""
    for method in ["PATCH", "DELETE"] * 10:
        assert preflight(client, method).status_code == 200
    assert server.request_counts == {}
    for _ in range(10):
        call(client, "GET", "/tasks", expect=200)


# ============================================================================
# 405 responses
# ============================================================================


@pytest.mark.parametrize(
    "method, path, allow",
    [
        ("PUT", "/tasks/1", "GET, HEAD, PATCH, DELETE"),
        ("POST", "/tasks/1", "GET, HEAD, PATCH, DELETE"),
        ("DELETE", "/tasks", "GET, HEAD, POST"),
        ("PATCH", "/tasks", "GET, HEAD, POST"),
    ],
)
def test_405_names_every_method_the_path_accepts(client, method, path, allow):
    """Starlette's own Allow header names only the first matching route's
    methods, which on /tasks/{id} would be just GET. HEAD is listed wherever
    GET is, because it is answered there."""
    response = call(client, method, path, expect=405)
    assert_error_envelope(response, "MethodNotAllowed")
    assert response.headers["allow"] == allow


def test_openapi_publishes_the_task_routes_with_their_429(client):
    spec = json_of(call(client, "GET", "/openapi.json", expect=200))
    paths = spec["paths"]
    assert sorted(paths["/tasks"]) == ["get", "post"]  # HEAD is answered but not published as an operation
    assert sorted(paths["/tasks/{task_id}"]) == ["delete", "get", "patch"]
    assert "/tasks/" not in paths
    for operation in [*paths["/tasks"].values(), *paths["/tasks/{task_id}"].values()]:
        assert "429" in operation["responses"]


# ============================================================================
# Request body size limit (regression: no limit existed)
# ============================================================================
#
# Before: the whole body was read into memory before validation rejected it.
# One 200 MB POST took the server from 79 MB to 879 MB of memory.

JSON = {"Content-Type": "application/json"}


def padded_body(size: int) -> bytes:
    """A valid task body padded with JSON whitespace to exactly `size` bytes."""
    body = b'{"title": "Padded"}'
    return body + b" " * (size - len(body))


def test_a_body_at_the_limit_is_accepted(client, db_session):
    body = padded_body(MAX_BODY_BYTES)
    assert len(body) == MAX_BODY_BYTES
    call(client, "POST", "/tasks", content=body, headers=JSON, expect=201)
    assert stored_titles(db_session) == ["Padded"]


def test_one_byte_over_the_limit_is_413(client, db_session):
    response = call(client, "POST", "/tasks", content=padded_body(MAX_BODY_BYTES + 1), headers=JSON, expect=413)
    body = assert_error_envelope(response, "PayloadTooLarge")
    assert "64 KB" in body["detail"]
    assert stored_titles(db_session) == []


def test_an_oversized_chunked_body_is_413(client, db_session):
    """No Content-Length to check up front, so the limit is enforced while reading."""

    def chunks():
        for _ in range(8):
            yield b" " * (16 * 1024)

    response = call(client, "POST", "/tasks", content=chunks(), headers=JSON, expect=413)
    assert "content-length" not in response.request.headers
    assert_error_envelope(response, "PayloadTooLarge")
    assert stored_titles(db_session) == []


def test_oversized_patch_is_413_and_changes_nothing(client, db_session):
    created = create_task(client, title="Unchanged")
    big = b'{"description": "' + b"d" * MAX_BODY_BYTES + b'"}'
    call(client, "PATCH", f"/tasks/{created['id']}", content=big, headers=JSON, expect=413)
    assert stored_task(db_session, created["id"]) == created


def test_a_413_carries_cors_and_rate_limit_headers_and_spends_a_request(client):
    """The limit sits inside CORS and the rate limiter, so a browser can read
    the 413, and a stream of oversized bodies is not free."""
    response = call(
        client, "POST", "/tasks",
        content=padded_body(MAX_BODY_BYTES + 1),
        headers={**JSON, "Origin": ALLOWED_ORIGIN},
        expect=413,
    )
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["x-ratelimit-remaining"] == "9"
    assert len(server.request_counts["testclient"]) == 1


# ============================================================================
# HEAD (regression: HEAD /tasks was 405)
# ============================================================================


@pytest.mark.parametrize("path", ["/tasks", "/tasks/{id}", "/items", "/"])
def test_head_matches_get_without_a_body(client, path):
    created = create_task(client, title="Headed")
    path = path.format(id=created["id"])

    get = call(client, "GET", path, expect=200)
    head = call(client, "HEAD", path, expect=200)

    assert head.content == b""
    assert head.headers["content-type"] == get.headers["content-type"]
    assert head.headers["content-length"] == get.headers["content-length"]


def test_head_on_a_missing_task_is_404_without_a_body(client):
    response = call(client, "HEAD", "/tasks/9999", expect=404)
    assert response.content == b""
