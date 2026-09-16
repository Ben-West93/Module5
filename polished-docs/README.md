# Polished Docs

**Module 5 — FastAPI Development, L12** (built on L11, Test Suite, and L10, Security Basics)

A documented task and inventory API with a pytest suite. L12 supplied an items
router, an `Item` schema and a bare `FastAPI()` app, and asked for API
metadata, tag descriptions, endpoint docstrings, request examples and
documented response codes. The module's exercises roll into one project, so
this is the L11 project with L12 merged in: the L10 security layer covers every
endpoint, one database holds both tables, and the Swagger page at `/docs`
describes all of it.

---

## Running

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload      # from this folder
pytest tests/ -v                   # the test suite (L11 + L12)
python verify_security.py          # the L10 security verification
python demo_rate_limit.py          # about 65 seconds; regenerates rate_limit_evidence.txt
```

The server creates `app.db` in the working directory on first start. It is not
part of the submission. None of the three test commands creates it or touches
it. If you have an `app.db` left over from before this version, delete it once:
tables are only created when missing, so an old file keeps the old table
definition and would still reuse deleted task ids (bug 7 below).

Loading `/docs` makes two requests (the page and `/openapi.json`), and both
count toward the 10-per-minute limit. After a few "Try it out" calls, a 429 is
the limiter working, not a bug. Set `RATE_LIMIT_REQUESTS` higher while
exploring; the tests ignore that variable and always use the real defaults.

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | comma-separated origins allowed to read responses in a browser |
| `RATE_LIMIT_REQUESTS` | `10` | requests allowed per IP per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | the window length |
| `DATABASE_URL` | `sqlite:///./app.db` | where tasks and items are stored |

All three security settings are validated at startup. `CORS_ALLOWED_ORIGINS=*`,
an origin with a trailing slash, or `RATE_LIMIT_REQUESTS=0` stops the server
with a message rather than running with a setting that silently does not do
what it says.

---

## Files

| File | Contents |
|---|---|
| `app/main.py` | L12 metadata (title, markdown description, version, contact, license, `openapi_tags`); CORS, rate-limit, body-size-limit, HEAD and 500 middleware; error handlers; both routers |
| `app/database.py` | the one engine, session factory and `get_db` dependency |
| `app/sanitization.py` | `sanitize_text()` and `has_visible_text()`, shared by both APIs |
| `app/models/task.py` | the `Task` ORM model (L11 starter, plus `AUTOINCREMENT`) |
| `app/models/item.py` | the `Item` ORM model (L10, plus L12's `price` and `category`) |
| `app/schemas/task.py` | `TaskCreate`, `TaskPatch`, `TaskResponse`, with Swagger examples |
| `app/schemas/item.py` | `Item`, `ItemCreate`, `ItemResponse` (L12), with Swagger examples |
| `app/schemas/error.py` | `ErrorResponse`, the error envelope as a documented schema |
| `app/ids.py` | the plain-digits path id type shared by `/tasks/{task_id}` and `/items/{item_id}` |
| `app/routers/tasks.py` | `/tasks` CRUD endpoints, each with a summary, docstring and documented responses |
| `app/routers/items.py` | `GET /items`, `POST /items`, `GET /items/{item_id}` with docstrings and documented responses (L12 on the L10 router) |
| `tests/conftest.py` | in-memory test database; `client`, `db_session` and `deleted_mid_request` fixtures |
| `tests/helpers.py` | `call()` request helper, error-envelope and database read-back helpers for tasks and items |
| `tests/test_tasks.py` | 71 tests: the brief's 8, edge cases, and regressions |
| `tests/test_security.py` | 48 tests: the L10 defences applied to `/tasks`, body size limit, HEAD |
| `tests/test_items.py` | 51 tests: item success paths, filters, 422s, 404, 409, ids, HEAD |
| `tests/test_docs.py` | 14 tests: the published OpenAPI metadata, tags, docstrings, examples and response codes |
| `verify_security.py` | 297 checks across 15 sections for the L10 defences |
| `demo_rate_limit.py` | drives a live server past the limit and writes the evidence file |
| `rate_limit_evidence.txt` | the real output of `python demo_rate_limit.py` |
| `requirements.txt` | dependencies; `pytest` is the only addition since L10, and L12 needs none |
| `app/__init__.py`, `app/models/__init__.py`, `app/schemas/__init__.py`, `app/routers/__init__.py`, `tests/__init__.py` | empty package markers |

---

## Endpoints

| Endpoint | Success | Errors |
|---|---|---|
| `POST /tasks` | 201, the stored task | 409 duplicate title, 422 invalid body or unknown field |
| `GET /tasks` | 200, every task in id order | |
| `GET /tasks/{task_id}` | 200, the task | 404, 422 invalid id |
| `PATCH /tasks/{task_id}` | 200, the updated task | 404, 409, 422 |
| `DELETE /tasks/{task_id}` | 200, `{"message": "Task N deleted"}` | 404, 422 invalid id |
| `GET /items` | 200, list of items; optional `?name=` and `?category=` exact filters | 422 |
| `POST /items` | 201, the stored item | 409 duplicate name, 422 invalid body or unknown field |
| `GET /items/{item_id}` | 200, the item | 404, 422 invalid id |
| `GET /` | 200, a pointer to `/docs` | |

Every `GET` endpoint also answers `HEAD` with the same status and headers and
no body. Any request whose body is over 64 KB gets **413**; the largest valid
body is under 9 KB. Any endpoint can return **429** once the caller's IP has used its budget; the
budget is shared across `/tasks` and `/items`. The 429 carries `Retry-After`
and is published in the OpenAPI schema. Every response carries
`X-RateLimit-Limit` and `X-RateLimit-Remaining`. Every error, including the
task router's 404 and 409, uses one envelope:

```json
{"error": "Conflict", "detail": "A task with that title already exists", "status_code": 409}
```

A task id must be written as plain digits (no sign, spaces, underscores or
leading zeros) and be between 1 and 2⁶³−1, the range an SQLite `INTEGER` can
hold. Ids are never reused after a delete.

---

## L12: the documentation

Open `/docs` (Swagger) or `/redoc` after starting the server.

- **App metadata** in `app/main.py`: title, a markdown description of the
  rate limit, error envelope, sanitizing and size limit, a version, contact and
  license. `openapi_tags` describes the `tasks`, `items` and `meta` groups, and
  each router is included with its tag.
- **Endpoint descriptions** come from each function's docstring, in markdown,
  with a short `summary` for the sidebar.
- **Documented responses.** Every endpoint lists its non-default codes through
  `responses={}` (404, 409, 422) with the `ErrorResponse` envelope as their
  schema, and every router adds 429 with its `Retry-After` header. The 422
  entries replace FastAPI's default validation schema, which does not match the
  envelope this API actually returns.
- **Examples.** `json_schema_extra` puts a pre-filled example on every request
  and response schema. `test_item_create_example_is_itself_a_valid_request`
  sends the `ItemCreate` example to `POST /items`, so "Try it out" works as shown.

### Merging the L12 starter

| L12 starter | Here | Why |
|---|---|---|
| items in a Python list | the L10 SQLite table | items survive restarts, and the test database override covers them |
| `Item` has name, description, price, category | the same, plus L10's sanitizer and `extra="forbid"` on `ItemCreate` | the L10 defences apply to the new fields; `category` must contain visible text like `name` |
| `description` optional | optional, defaults to `""`, null is 422 | L10 behaviour, which `verify_security.py` checks |
| `price > 0` | `> 0`, at most 1,000,000, NaN and Infinity refused | Pydantic allows NaN and Infinity by default, and Infinity would pass `> 0` and then fail to serialize |
| `ItemResponse(ItemCreate)` | `ItemResponse(Item)` | stored rows are not re-run through the input sanitizer on the way out |
| 409 documented but no rule for it | item names are unique (UNIQUE column plus a 409 check) | a documented code should be one the endpoint can return |
| `GET /{item_id}` with `item_id: int` | the shared plain-digits id type in `app/ids.py` | bugs 4 and 11 below would otherwise return on the new route |
| `@router.get("/")` | `@router.get("")` | same redirect fix as `/tasks` |
| `list_items` "document the intent" | a real `?category=` filter; sorting and pagination documented as planned | |

`verify_security.py` and `demo_rate_limit.py` now send `price` and `category`
with every item body, so each check is refused or accepted for the reason its
label gives, and section 4 gained five L12 refusals (zero, negative and
non-numeric price, markup-only and null category).

---

## How L10 and L11 were combined


L10 and L11 each shipped `app/main.py` and `app/database.py`. Those are
merged: one engine and one `Base`, so a single `get_db` override moves the
whole API onto the test database. Starting from L10's `main.py`, the task
router was added and three things had to change for it:

- **CORS methods.** L10 allowed only `GET` and `POST`, the methods it had.
  The task endpoints add `PATCH` and `DELETE`, and without them a browser
  frontend could create tasks but never update or delete one; the preflight
  would be refused before the request was sent. `PUT` is still refused.
- **The 409 label.** The error handler had no label for 409, so the task
  router's conflict would have been reported as `"error": "Error"`.
- **The sanitizer moved** from `routers/items.py` to `app/sanitization.py`
  so task titles and descriptions get the same cleaning as item names.

Departures from the L11 starter, each covered by a test:

| Starter | Here | Why |
|---|---|---|
| `@router.get("/")` etc. | `@router.get("")` | `"/"` makes the path `/tasks/`, so `/tasks` costs a 307 redirect, which spends a second slot of the rate-limit budget. Same fix L10 made for `/items` |
| `TaskPatch.description` unbounded | `max_length=500` | see bug 1 below |
| `null` accepted for `title`, `completed` in PATCH | 422 | see bug 2 |
| `TaskResponse(TaskCreate)` | a separate model | a response model should describe stored rows, not enforce input rules or run the sanitizer on the way out |
| PATCH commits unguarded | `commit_or_conflict()` | see bug 3 |
| `task_id: int` | bounded 1 to 2⁶³−1 | see bug 4 |
| no sanitization | `sanitize_text()` on title and description | the L10 defence, applied to the new text fields |
| `list_tasks` unordered | ordered by id | a stable order to test against |
| PATCH loads, modifies, commits | one `UPDATE ... WHERE id` with a row-count check | see bug 5 |
| DELETE via the ORM | one `DELETE ... WHERE id` with a row-count check | see bug 6 |
| ids reused after a delete | `sqlite_autoincrement` on the model | see bug 7 |
| unknown fields ignored | `extra="forbid"` | see bug 8 |
| titles compared as typed | NFC-normalized, format characters removed | see bug 9 |
| `1_0`, `+1`, ` 1` accepted as ids | plain digits only | see bug 11 |

The endpoints' behaviour for every case the brief describes is unchanged, and
the brief's 8 tests pass against the original starter code as well.

---

## The test suite

```bash
pytest tests/ -v
```

**184 passed** on the last run, in about three seconds, on Python 3.12.3 with
FastAPI 0.141.1, Starlette 1.6.0, Pydantic 2.13.5, SQLAlchemy 2.0.54, pytest
9.1.1 and httpx 0.28.1.

### The fixture

`tests/conftest.py` follows the brief's pattern: an in-memory SQLite engine
with `StaticPool`, an `override_get_db()` yielding a test session, and a
`client` fixture that creates the tables, installs the override, yields
`TestClient(app)`, and then drops the tables and removes the override. Four
additions, each needed:

- **`DATABASE_URL` is set to memory before the app is imported.**
  `app/main.py` calls `create_all()` at import time, so without this, merely
  importing the app would create `app.db` before any override could apply.
- **The rate limiter is reset around every test.** Its state lives in
  `app.main`, not in the client, and every `TestClient` request comes from
  the same address. Without the reset, the whole suite shares one budget of
  10 requests; removing it makes most of the suite fail with 429.
- **A `db_session` fixture** on the same test database, so tests can read
  rows back directly instead of trusting what the API said it stored.
- **A `deleted_mid_request` fixture** for the race-condition regressions.
  Sending two requests at once and hoping they collide would make a flaky
  test, so it hooks the test database and deletes a task, committed, at an
  exact point inside the next request: just before its UPDATE, just before
  its DELETE, or inside its commit. Those are the moments a racing DELETE
  broke the original code.

Cleanup is in `finally`, so a failing test cannot leak rows into the next.
`test_isolation_first` and `test_isolation_second` both create the same
title; whichever runs second would get a 409 if isolation broke.

### How the tests are written

Every request goes through `call()` in `tests/helpers.py`, which asserts the
status code before anything reads the body. An unexpected error therefore
fails with its status and body in the message, not with a `KeyError` three
lines later. It also refuses to follow redirects, so a route that regresses
to `/tasks/` fails loudly instead of quietly costing an extra request.

Tests compare what came back with what was sent, and then read the database:
`test_create_task` checks that the stored row equals the response, and
`test_patch_task` checks that the fields it did not send are unchanged in the
table, not just in the response.

### What the tests cover

| Group | Tests |
|---|---|
| The brief's 8 | create, list, get by id, 404, patch, delete, 422 on empty title, 409 on duplicate |
| Fixture | the app runs on the in-memory database; rows never leak between tests |
| Create and read | every field round-trips; no redirect; 6 invalid bodies are 422 and store nothing; 200/500-character limits accepted |
| Update and delete | 404s for PATCH and DELETE; delete twice; multi-field patch; empty patch changes nothing; re-sending a task's own title is not a conflict; description can be cleared to null |
| Regressions | one or more per bug below |
| `test_items.py` | every item field round-trips and is read back from the table; `description` defaults to `""`; list order; `name` and `category` filters (sanitized, exact, combined, blank, SQL injection); markup stripped from name, description and category; limits accepted; 18 invalid bodies and each missing required field are 422 and store nothing; NaN and Infinity prices and malformed JSON are 422; 7 malformed ids are 422; 404; duplicate name 409; ids never reused; tasks and items share one database; HEAD and 405 `Allow` on `/items/{item_id}` |
| `test_docs.py` | title, version, markdown description, contact, license; every tag described and used; every operation has a summary and description; items and tasks document 404/409/422/429; errors use the `ErrorResponse` schema; five schemas carry examples; the `ItemCreate` example is itself accepted by `POST /items`; field rules appear in the schema; `/docs` and `/redoc` served |
| `test_security.py` | sanitizing on POST and PATCH, stored values read back; clean text byte-for-byte; 8 kinds of empty-after-sanitizing title; an accent on a real letter accepted; 5 look-alike title pairs are duplicates; removing invisible characters cannot assemble a tag; descriptions keep emoji joiners; duplicate check on the sanitized title; non-JSON bodies refused; CORS preflights for PATCH and DELETE, PUT refused, disallowed origins refused; the 11th request is 429 and a limited POST, PATCH or DELETE changes nothing; `/items` and `/tasks` share one budget; a limited browser request still carries CORS headers; preflights spend none of the budget; 405 `Allow` lists every method; OpenAPI publishes the 429; a body at the 64 KB limit is accepted and one byte over is 413, including chunked uploads and PATCH; a 413 carries CORS and rate-limit headers; HEAD matches GET without a body on four paths |

### Checking that the tests can fail

A test that cannot fail proves nothing, so the suite was run against
deliberately broken versions of the code:

| Breakage | Result |
|---|---|
| the original starter schemas and router | 57 failed. The brief's 8 pass, as they should |
| PATCH never commits | 5 failed |
| PATCH applies fields that were not sent | 11 failed |
| DELETE does not delete | 3 failed |
| fixture does not drop tables | 57 failed |
| fixture never resets the rate limiter | 108 failed |
| CORS back to `GET`/`POST` only | 3 failed |
| rate limiter registered after CORS, making it outermost | 2 failed |
| sanitizer removed from the task schemas | 6 failed |
| PATCH back to load-modify-commit (bug 5) | 2 failed |
| DELETE back to an ORM delete (bug 6) | 1 failed |
| no `AUTOINCREMENT` (bug 7) | 1 failed |
| unknown fields allowed (bug 8) | 4 failed |
| title normalization removed (bug 9) | 7 failed |
| title normalized *after* sanitizing instead of before (bug 9) | 1 failed |
| combining marks counted as visible (bug 10) | 4 failed |
| lax id parsing (bug 11) | 15 failed |
| no body size limit (bug 12) | 4 failed |
| no HEAD support (bug 13) | 5 failed |
| `create_task()`'s duplicate check removed | 0 failed |

The last one is correct, not a gap. The database's UNIQUE constraint plus
`commit_or_conflict()` produces the identical 409, so that check is a fast
path for the common case. The constraint is what guarantees uniqueness.

---

## Bugs found in the L11 starter

The starter was described as "already complete". Probing it found four ways
a well-formed request produced a 500. Each is fixed, and each has a
regression test that fails against the starter.

**1. One PATCH could break `GET /tasks` for everyone.** `TaskPatch.description`
had no length limit, and SQLite does not enforce `String(500)`, so a
600-character description was saved. `TaskResponse` then rejected the stored
row, so the PATCH returned 500, and so did every later `GET /tasks` and
`GET /tasks/{id}` that included it. Test:
`test_overlong_patch_description_is_rejected_and_the_list_survives`.

**2. `null` in a PATCH crashed the commit.** `{"title": null}` or
`{"completed": null}` passed validation, because the fields are `Optional` so
that they can be omitted. The router wrote NULL into NOT NULL columns and the
commit raised `IntegrityError`. An explicit null is now 422; `description`
may still be null because its column allows it. Test:
`test_patch_null_into_required_field_returns_422`.

**3. Renaming a task to an existing title was a 500, and so was a race on
create.** POST checked for duplicates; PATCH did not, so the UNIQUE
constraint raised unhandled. POST's check has a gap too: two requests with the
same new title can both pass it before either commits. Against a live uvicorn
server, 20 rounds of 40 simultaneous identical POSTs produced 16 responses of
500 without a commit-time handler, and none with it; exactly one task per
title was stored either way. That probe was a one-off and is not kept as a
script. Tests: `test_patch_to_another_tasks_title_returns_409`, and
`test_unique_clash_at_commit_is_409_not_500`, which pushes a duplicate row
past the check to reach the handler directly.

**4. A very large id crashed the database driver.**
`GET /tasks/99999999999999999999` is a valid Python int, but the sqlite3
driver raised `OverflowError`. Ids are now bounded to the column's range, so
it is a 422, and ids 0 and -1 are refused with it. Test:
`test_out_of_range_id_returns_422`.

---

## Bugs found by edge-case probing

A second round attacked the finished project: hostile request bodies and ids,
Unicode tricks, concurrent requests against a live uvicorn server run in its
own process, oversized uploads with the server's memory measured, and the
test tooling run from other folders, in reverse order and with conflicting
environment variables. The tooling held up. Nine problems were found, all
fixed, each with a regression test that fails when its fix is removed (see
the table above). The live-server probes were one-offs and are not kept as
scripts; the regression tests reproduce each failure deterministically.

**5. A PATCH racing a DELETE crashed with 500.** PATCH loaded the task,
changed it, then committed. If a DELETE landed in between, the save raised
`StaleDataError` or "Could not refresh instance": 4 of 3,000 racing requests
on a live server. PATCH is now a single `UPDATE ... WHERE id` whose row count
decides between 200 and 404. After the fix, the same 3,000 requests produced
no 500s. Test: `test_patch_racing_a_delete_returns_404_not_500`.

**6. Simultaneous DELETEs all reported success.** An ORM delete that matches
no row only warns, so 61 DELETEs answered "Task N deleted" for 30 tasks.
DELETE now checks its row count: exactly one success per task afterwards.
Test: `test_only_one_of_two_racing_deletes_reports_success`.

**7. Deleted ids were reused.** Without `AUTOINCREMENT`, SQLite gives a new
row the highest id plus one, so deleting the newest task handed its id to the
next one, and a stale link or delayed PATCH reached the wrong task. Both
tables now use `AUTOINCREMENT`. Tests: `test_deleted_ids_are_never_reused`,
and section 15 of `verify_security.py` for items.

**8. Unknown fields were silently ignored.** `PATCH {"complete": true}`, a
typo for `completed`, answered 200 and changed nothing. `TaskCreate`,
`TaskPatch` and `ItemCreate` now refuse extra fields with 422. Tests:
`test_unknown_fields_are_422`, and section 4 for items.

**9. Look-alike titles bypassed uniqueness.** "Café" written as one character
and as "e" plus a combining accent were two tasks, and so were "Chores" and
"Chores" with a zero-width space inside. Titles are now NFC-normalized and
lose their format characters (zero-width spaces and joiners, BOM, soft
hyphen) before the duplicate check. That happens *before* sanitizing, because
removing a zero-width space afterwards could turn `<`, zero-width space,
`script>` into a live tag. Tests: `test_lookalike_titles_are_duplicates`,
`test_removing_invisible_characters_cannot_assemble_a_tag`.

**10. Some invisible titles were accepted.** A title made only of a combining
accent (U+0301), a variation selector (U+FE0F), a grapheme joiner (U+034F) or
an enclosing mark was stored as a blank-looking task. `has_visible_text()`
now treats combining marks (categories Mn and Me) as invisible on their own.
Tests: `test_title_that_sanitizes_to_nothing_is_422`, and section 15 for
item names.

**11. One task answered at several URLs.** Pydantic's lax integer parsing
served task 10 at `/tasks/1_0` and task 1 at `/tasks/+1` and `/tasks/%201`.
Ids must now be plain digits. Test: `test_ids_must_be_plain_digits`.

**12. Request bodies had no size limit.** The whole body was read into memory
before validation: one 200 MB POST took the server from 79 MB to 879 MB, and
the rate limit allowed ten a minute. `BodySizeLimitMiddleware` now refuses
anything over 64 KB with 413, up front when `Content-Length` declares it and
while reading when it does not. After the fix, a 200 MB upload either way got
a clean 413 with the server staying at 81 MB. The 413 sits inside CORS and the
rate limiter, so a browser can read it and it spends a request. Tests: five in
`test_security.py`, and section 15.

**13. `HEAD` returned 405.** HTTP expects HEAD wherever GET works, and uptime
monitors use it. `HeadAsGetMiddleware` answers it with GET's status and
headers and no body, verified on a raw socket as well, and 405 `Allow`
headers now list HEAD. It is middleware rather than extra routes so the
OpenAPI schema does not gain a "head" operation per endpoint. Tests:
`test_head_matches_get_without_a_body`, and section 15 for items.

---

## The L10 security layer

### Input sanitization

`sanitize_text()` in `app/sanitization.py` runs in an `@field_validator` on
`ItemCreate.name`, `ItemCreate.description`, and the title and description of
`TaskCreate` and `TaskPatch`, so there is no path from a request body to the
database that skips it. It removes complete tags (repeating until nothing
changes, since removing one tag can rebuild another: `<<b>script>` →
`<script>`), then removes an unterminated tag through to the end of the
string, then strips whitespace.

A name or title that is empty once sanitized, or made only of invisible
characters (format and control characters, and combining marks with no
letter to sit on), is a 422. `Field(min_length=1)` could not catch that, because it
runs before the validator and would accept `<b></b>`. `max_length` applies to
the raw input, which caps the work per request. Task title uniqueness is
checked on the normalized, sanitized value, so `<em>Chores</em>`, `Cho` +
zero-width space + `res`, and `Chores` all clash.

Entities are not decoded (`&lt;script&gt;` is stored as typed), and text
between tags is kept as plain text (`<script>alert(1)</script>` becomes
`alert(1)`, which is inert).

### Rate limiting

A sliding log per client IP in `request_counts: dict[str, list[float]]`. Key
decisions, each tested in `verify_security.py`:

- **Sliding, not fixed, window.** Ten requests at 0:59 and ten more at 1:00
  would satisfy a per-minute counter; here the second burst is refused.
- **Only served requests are recorded.** Hammering during a lockout does not
  extend it, and no IP's list can exceed 10 entries.
- **`time.monotonic()`,** not `time.time()`, so an NTP clock adjustment cannot
  free or lock out every client at once.
- **The socket peer address identifies the client.** `X-Forwarded-For` and
  friends are ignored, since any client can set them.
- **No lock needed.** The middleware is `async` and its check-and-record step
  has no `await`, so nothing interleaves with it. Section 13 sends 30
  simultaneous requests to a live uvicorn server and exactly 10 get through.
- **Idle IPs are swept** once per window, so the dict does not grow with every
  address that ever connected.

### CORS

Configured for named origins only, with `GET`, `POST`, `PATCH` and `DELETE`,
the `Content-Type` header, and credentials off (the API has no cookies or
login). CORSMiddleware compares origins as exact strings, so
`parse_allowed_origins()` rewrites each entry the way a browser writes it, or
refuses it with a message:

- **Refused:** `*` (on its own or inside an origin), `null`, a trailing slash or
  path, an empty port, port `0`, an invalid port, and a host no domain could have.
- **Rewritten:** a default port is dropped (`:443`, `:80`), a non-ASCII domain
  becomes punycode, an IPv6 address is compressed, and scheme and host are
  lowercased.

**Middleware order matters.** Starlette makes the middleware added *last* the
outermost. CORS is added after the rate limiter, which means:

1. A 429 still carries `Access-Control-Allow-Origin`, so a frontend can read
   the status and `Retry-After`. Reversed, the browser would report a CORS
   failure and the page would see only a network error.
2. CORS answers preflight `OPTIONS` requests itself, so preflights do not
   spend the budget. A browser sends one before every JSON POST, PATCH and
   DELETE, so counting them would halve a real user's allowance.

`expose_headers` lists `Retry-After` and the two rate-limit headers, because a
browser hides non-safelisted response headers from page JavaScript otherwise.

### SQL injection

The docstring on `list_items()` in `app/routers/items.py` explains the
vulnerable and safe patterns. Section 5 of `verify_security.py` demonstrates
them instead of asserting them:

- The vulnerable f-string query, run against a scratch SQLite table, returns
  every row for `' OR '1'='1` and reads `sqlite_version()` for a `UNION`
  payload.
- The parameterized version returns nothing for the same input.
- SQLAlchemy's compiled statement for the endpoint's filter contains
  `items.name = ?` and not the payload.
- Five injection payloads sent to `GET /items?name=` each return `[]`, and the
  table is intact afterwards.

The task router builds every statement with `select()`, `update()`,
`delete()` and `db.get()`, so its values are bound parameters in the same way.

### Verification

```bash
python verify_security.py
```

**All 297 checks passed** on the last run. It uses a scratch database and
leaves nothing behind. A fake clock stands in for `time.monotonic()` so the
60-second window is tested to the millisecond without sleeping. Every HTTP
call goes through a shared `request()` helper that catches transport errors
and checks the status code before the body is read.

| Section | Establishes |
|---|---|
| 1 | `sanitize_text()` exact outputs, including cases that must be left alone |
| 2 | the L10 starter's single regex lets markup through; `sanitize_text()` leaves all 19 payloads inert when rendered in a page |
| 3 | stored item values, read back, match the expected sanitized values; clean input is unchanged |
| 4 | markup-only, whitespace-only, null, numeric, over-long, unknown and misspelled fields are 422 and store nothing; non-JSON bodies are refused |
| 5 | SQL injection, as described above |
| 6 | CORS: allowed origins echoed, disallowed ones granted nothing, preflights allow PATCH and DELETE but refuse PUT and unlisted headers, `*` never appears |
| 7 | origin parsing rules; Starlette's wildcard-plus-credentials behaviour, demonstrated; startup validation of the rate-limit variables |
| 8 | the 11th request is 429 with envelope and headers; a limited POST creates nothing |
| 9 | the window slides: exact expiry at 60.0s, `Retry-After` rounding, no fixed-window burst |
| 10 | per-IP isolation; forged forwarding headers ignored; lockout not extended by hammering; 404/405/422 count |
| 11 | middleware order: 429 carries CORS headers; preflights are free; a bare OPTIONS is counted |
| 12 | idle IPs are swept; per-IP storage stays at 10 entries |
| 13 | 30 concurrent requests to a live uvicorn server: exactly 10 served |
| 14 | error envelope on 404/405/422/500; 429 published in OpenAPI |
| 15 | regressions for every bug found by probing in L10, plus the edge-case fixes that apply to items: combining-mark names, 5 MB body refused with 413, `Allow` lists HEAD, ids not reused, HEAD answered like GET |

The script focuses on `/items`, where L10 built the defences.
`tests/test_security.py` covers the same defences on `/tasks`.

### Evidence of the 429

`rate_limit_evidence.txt` is copied from a real run of `demo_rate_limit.py`
against uvicorn with the default limit, regenerated for the combined app. It
records:

1. Ten `GET /items` requests served, `X-RateLimit-Remaining` counting 9 → 0.
2. The eleventh request's full response: `HTTP/1.1 429 Too Many Requests`,
   `Retry-After: 60`, `X-RateLimit-Remaining: 0`, and the JSON envelope.
3. A `POST /items` and a `POST /tasks` while limited are both 429, and the row
   counts read straight from the database are the same before and after, so
   neither endpoint ran and the budget is shared across routers.
4. A request with `Origin: http://localhost:3000` gets a 429 that still carries
   `Access-Control-Allow-Origin` and exposes `Retry-After`.
5. uvicorn's own access log shows ten 200s followed by four 429s, the
   server's record independent of what the client printed.
6. After sleeping the 60 seconds `Retry-After` gave, the same client is served
   again with 9 requests remaining.

The script checks each claim and exits non-zero if any one does not hold.

### Problems found and fixed in L10

Found while building: the L10 starter's single sanitizer regex let
unterminated tags through (4 of 19 payloads); the first fix mangled `3 < 5 and
5 > 3`; and the 422 handler crashed on non-JSON bodies.

Found by later adversarial probing: the 422 handler crashed on lone
surrogates, `NaN` and `Infinity` and echoed invalid input unbounded; 405
responses lacked a correct `Allow` header; 500 responses carried no CORS
headers; CORS entries could pass validation and never match a browser; a
400-digit `RATE_LIMIT_REQUESTS` crashed startup; names of only invisible
characters were accepted; and both scripts could crash printing unicode on a
Windows console. Section 15 of `verify_security.py` covers each.

---

## Limitations

- **The rate limiter lives in one process.** A restart forgets every client,
  and with several uvicorn workers each keeps its own dict, so the effective
  limit becomes 10 per worker. Production limiters use Redis or the gateway.
- **Behind a reverse proxy, every client shares the proxy's IP.** Run uvicorn
  with `--proxy-headers --forwarded-allow-ips=<proxy address>` so the peer
  address is rewritten only for requests that really came through the proxy.
- **Preflight requests are not rate limited**, as a consequence of CORS being
  outermost. They touch no database, but they are unbounded.
- **Per-address limits are weak against IPv6.** One subscriber often controls
  a whole /64, which is an effectively unlimited supply of addresses.
- **Sanitizing is lossy.** `x<y` looks like the start of a tag and is cut to
  `x`. Escaping on output, which templates and React do by default, remains
  the primary defence.
- **Tag stripping does not make text safe in every context.** A quote
  character is not a tag, so `" onmouseover="alert(1)` is stored as sent. It
  is harmless as page text, but not if a template places it unescaped inside
  an HTML attribute, and the same applies to a `javascript:` value in an
  `href`.
- **Task title uniqueness is case-sensitive.** `Chores` and `chores` are two
  different tasks; only invisible characters and the two Unicode spellings of
  the same letter are treated as the same.
- **Zero-width joiners are removed from titles.** An emoji sequence joined
  with them, such as a family emoji, is stored as its separate emoji.
  Descriptions keep them.
- **The 64 KB body limit is fixed in code** (`MAX_BODY_BYTES` in
  `app/main.py`), not an environment variable. It is far above any valid
  request.
- **`completed` accepts Pydantic's lax booleans**, such as `"true"` or `1`.
  Values that are not boolean-like, such as `"maybe"`, are 422.
- **Neither list endpoint is paginated.** Fine for an exercise, not for a
  table that grows. The `GET /items` docstring documents pagination as planned.
- **Item names are unique and case-sensitive,** and are not normalized the
  way task titles are.
- **`/tasks/` and `/items/` redirect** to the paths without the slash, using
  the request's `Host` header. That is Starlette's default behaviour, and the
  redirect spends a request of the caller's budget.
