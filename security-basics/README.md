# Security Basics

**Module 5 — FastAPI Development, L10**

Hardens a small item API with three defences: CORS restricted to named
origins, a per-IP rate limit of 10 requests per minute, and schema-level
sanitization that strips HTML tags from user-submitted text. `GET /items`
carries the SQL injection explanation the brief asks for, and runs against a
real SQLite table so that explanation describes code that actually executes.

---

## Starting point

The brief's starter code is a fresh app (`app/main.py` and
`app/routers/items.py` only), so this exercise is built from that starter
rather than on top of the L9 background-tasks API. Two patterns from earlier
lessons are carried over because they apply here too: the L7 error envelope
(`{"error", "detail", "status_code"}`) and routes declared as `""` rather
than `"/"`.

Where the implementation departs from a comment in the starter, it is on
purpose:

| Starter comment | Here | Why |
|---|---|---|
| `allow_credentials=True` | `False` | The API has no cookies or login, so there are no credentials to allow |
| `allow_methods` includes PUT, DELETE | `["GET", "POST"]` | Only the methods the API has; the others can only ever 405 |
| `allow_headers` includes Authorization | `["Content-Type"]` | There is no auth header to read |
| `"https://yourfrontend.com"` | `http://localhost:3000`, `http://127.0.0.1:3000` | Real defaults, overridable with `CORS_ALLOWED_ORIGINS` |
| `@router.get("/")` | `@router.get("")` | `"/"` makes the path `/items/`, so `/items` costs a 307 redirect, which spends a second request of the rate-limit budget |
| "return sample items list" | a SQLite `items` table | So the SQL injection note can be demonstrated against a real query |

---

## Files

| File | Contents |
|---|---|
| `app/main.py` | CORS middleware, rate-limit middleware, error handlers, router registration |
| `app/routers/items.py` | `sanitize_text()`, `ItemCreate` with its `@field_validator`, `ItemResponse`, `GET /items`, `POST /items` |
| `app/database.py` | engine, session factory, `get_db` dependency |
| `app/models/item.py` | the `Item` ORM model |
| `app/__init__.py`, `app/routers/__init__.py`, `app/models/__init__.py` | empty package markers |
| `verify_security.py` | 272 checks across 15 sections, including adversarial, concurrency and regression tests |
| `demo_rate_limit.py` | drives a live server past the limit and writes the transcript below |
| `rate_limit_evidence.txt` | the real output of `python demo_rate_limit.py`: the 429 response as received |
| `requirements.txt` | dependencies; no new packages beyond L6–L9 |

`items.db` is created in the working directory the first time the app starts.
It is not part of the submission.

---

## Running

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload      # from this folder
```

Then open `http://127.0.0.1:8000/docs`.

Loading `/docs` makes two requests (the page and `/openapi.json`), and both
count toward the 10-per-minute limit. After a few "Try it out" calls, a 429 is
the limiter working, not a bug. Set `RATE_LIMIT_REQUESTS` higher while
exploring.

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | comma-separated origins allowed to read responses in a browser |
| `RATE_LIMIT_REQUESTS` | `10` | requests allowed per IP per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | the window length |
| `DATABASE_URL` | `sqlite:///./items.db` | where items are stored |

All three security settings are validated at startup. `CORS_ALLOWED_ORIGINS=*`,
an origin with a trailing slash, or `RATE_LIMIT_REQUESTS=0` stops the server
with a message rather than running with a setting that silently does not do
what it says.

---

## Endpoints

| Endpoint | Returns | Notes |
|---|---|---|
| `GET /items` | 200, list of items | optional `?name=` exact-match filter, sent as a bound parameter |
| `POST /items` | 201, the stored item | body `{"name": str, "description": str}`; both fields sanitized |
| `GET /` | 200 | a pointer to `/docs` |

Any endpoint can return **429** once the caller's IP has used its budget. The
response carries `Retry-After` (whole seconds, rounded up) and is published in
the OpenAPI schema. Every response carries `X-RateLimit-Limit` and
`X-RateLimit-Remaining`.

---

## The three defences

### Input sanitization

`ItemCreate` runs `sanitize_text()` on `name` and `description` in an
`@field_validator`, so there is no path from a request body to the database
that skips it. It removes complete tags (repeating until nothing changes,
since removing one tag can rebuild another: `<<b>script>` → `<script>`),
then removes an unterminated tag through to the end of the string, then
strips whitespace.

A name that is empty once sanitized is a 422. `Field(min_length=1)` could not
catch that, because it runs before the validator and would accept `<b></b>`.
`max_length` applies to the raw input, which caps the work per request.

Entities are not decoded (`&lt;script&gt;` is stored as typed), and text
between tags is kept as plain text (`<script>alert(1)</script>` becomes
`alert(1)`, which is inert).

### Rate limiting

A sliding log per client IP in `request_counts: dict[str, list[float]]`,
as the starter suggests. Key decisions, each tested:

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

Configured for named origins only. CORSMiddleware compares origins as exact
strings, so `parse_allowed_origins()` rewrites each entry the way a browser
writes it, or refuses it with a message:

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
   spend the budget. A browser sends one before every JSON POST, so counting
   them would halve a real user's allowance.

`expose_headers` lists `Retry-After` and the two rate-limit headers, because a
browser hides non-safelisted response headers from page JavaScript otherwise.

---

## SQL injection

The docstring on `list_items()` explains the vulnerable and safe patterns.
Section 5 of the verification demonstrates them instead of asserting them:

- The vulnerable f-string query, run against a scratch SQLite table, returns
  every row for `' OR '1'='1` and reads `sqlite_version()` for a `UNION`
  payload.
- The parameterized version returns nothing for the same input.
- SQLAlchemy's compiled statement for the endpoint's filter contains
  `items.name = ?` and not the payload.
- Five injection payloads sent to `GET /items?name=` each return `[]`, and the
  table is intact afterwards.

The sqlite3 driver refuses stacked statements (`'; DROP TABLE items; --`).
That is recorded but not relied on: the `OR` and `UNION` payloads need no
second statement.

---

## Verification

```bash
python verify_security.py
```

It uses a scratch database, never `items.db`, and leaves nothing behind.
A fake clock stands in for `time.monotonic()` so the 60-second window is
tested to the millisecond without sleeping. Every HTTP call goes through a
shared `request()` helper that catches transport errors and checks the status
code before the body is read.

| Section | Establishes |
|---|---|
| 1 | `sanitize_text()` exact outputs, including cases that must be left alone |
| 2 | the starter's single regex lets markup through; `sanitize_text()` leaves all 19 payloads inert when rendered in a page |
| 3 | stored values, read back from the database, match the expected sanitized values; clean input is unchanged |
| 4 | markup-only, whitespace-only, null, numeric and over-long input is 422 and stores nothing; non-JSON bodies are refused |
| 5 | SQL injection, as described above |
| 6 | CORS: allowed origins echoed, disallowed ones granted nothing, preflights refuse unlisted methods and headers, `*` never appears |
| 7 | origin parsing rules; Starlette's wildcard-plus-credentials behaviour, demonstrated; startup validation of the rate-limit variables |
| 8 | the 11th request is 429 with envelope and headers; a limited POST creates nothing |
| 9 | the window slides: exact expiry at 60.0s, `Retry-After` rounding, no fixed-window burst |
| 10 | per-IP isolation; forged forwarding headers ignored; lockout not extended by hammering; 404/405/422 count |
| 11 | middleware order: 429 carries CORS headers; preflights are free; a bare OPTIONS is counted |
| 12 | idle IPs are swept; per-IP storage stays at 10 entries |
| 13 | 30 concurrent requests to a live uvicorn server: exactly 10 served |
| 14 | error envelope on 404/405/422/500; 429 published in OpenAPI |
| 15 | regressions for every bug found by probing (listed below); run against the earlier code, 29 of these checks fail |

Result on the last run: **All 272 checks passed**, on Python 3.12.3 with
FastAPI 0.141.1, Starlette 1.6.0, Pydantic 2.13.5, SQLAlchemy 2.0.54, uvicorn
0.53.0 and httpx 0.28.1.

### Evidence of the 429

```bash
python demo_rate_limit.py           # about 65 seconds; regenerates rate_limit_evidence.txt
```

`rate_limit_evidence.txt` is copied from a real run against uvicorn with the
default limit, not written by hand. It records:

1. Ten `GET /items` requests served, `X-RateLimit-Remaining` counting 9 → 0.
2. The eleventh request's full response: `HTTP/1.1 429 Too Many Requests`,
   `Retry-After: 60`, `X-RateLimit-Remaining: 0`, and the JSON body
   `{"error": "TooManyRequests", ...}`.
3. A `POST /items` while limited is also 429, and the row count read straight
   from the database is the same before and after, so the endpoint never ran.
4. A request with `Origin: http://localhost:3000` gets a 429 that still carries
   `Access-Control-Allow-Origin` and exposes `Retry-After`.
5. uvicorn's own access log shows ten 200s followed by three 429s. That is the
   server's record, independent of what the client printed.
6. After sleeping the 60 seconds `Retry-After` gave, the same client is served
   again with 9 requests remaining.

The script checks each of those claims and exits non-zero if any one does not
hold. Its first version captured no access-log lines: uvicorn replaces logging
handlers during startup, so the capture handler is now attached after the
server is up.

### What the verification found

Three real problems while building, all fixed:

**The starter's sanitizer hint is bypassable.** One `re.sub(r"<[^>]*>", "", ...)`
cannot remove a tag with no closing `>`. The name
`<img src=x onerror=alert(1)//` passes through unchanged, and rendered inside
`<div>{name}</div>` the page's own `</div>` supplies the `>`, producing an
`<img>` with a live `onerror`. Section 2 renders it through an HTML parser to
show it. 4 of the 19 payloads in the corpus get through the single regex.

**The first version of the fix mangled ordinary text.** Matching any `<` up to
the next `>` turned `3 < 5 and 5 > 3` into `3  3`. Tags now must start with a
letter, `/`, `!` or `?`, which is when an HTML parser actually leaves text
mode. Section 1 keeps both the math string and `a < b > c` covered.

**The 422 handler crashed on non-JSON bodies.** A POST sent as `text/plain`
fails validation with the raw body in `input` as *bytes*, which the error
handler could not serialize, so the client got a 500. The handler now decodes
bytes (replacing invalid UTF-8) before encoding. Section 4 sends a
`text/plain` JSON body and section 5 a body of invalid UTF-8.

### What adversarial probing found

A later round of probing attacked the finished code from angles the suite did
not cover: malformed and hostile request bodies, protocol edges, CORS
configuration against what browsers really send, sanitizer fuzzing (200,000
random markup-heavy inputs, worst-case timing, idempotence), a live server
under concurrent writes, abandoned connections and junk bytes, and running
the scripts from another directory with a non-UTF-8 console. Seven problems
turned up, all fixed inside the existing files. Section 15 covers each one.

1. **Crash: the 422 handler could not encode some rejected values.** A lone
   surrogate (`"\ud800"`), `NaN` or `Infinity` in a body is accepted by
   Python's JSON parser and correctly rejected by Pydantic. The handler then
   failed to echo it back as JSON, and the client got a 500. It also echoed
   values unbounded: a 5 MB invalid name produced a 5 MB error. The echoed
   `input` is now a JSON-safe preview of at most 200 characters.
2. **405 responses had no `Allow` header**, which HTTP requires. Starlette's
   own header would also have been wrong: it names only the first matching
   route's methods, so `/items` would advertise `GET` but not `POST`. The
   handler now asks the router which methods would have matched, using only
   the public `route.matches()`.
3. **500 responses carried no CORS headers.** An unhandled error escaped
   past the CORS middleware, so a browser would report a CORS failure instead
   of the server error. The rate-limit middleware now turns it into the
   standard 500 inside CORS.
4. **CORS entries could pass validation and never match.** Examples were
   `https://app.example.com:443`, `http://localhost:`, port `0`,
   `https://bücher.example`, and a host containing a space.
5. **Crash: a 400-digit `RATE_LIMIT_REQUESTS` raised `OverflowError`** in the
   startup check, which only handled floats.
6. **A name of only invisible characters passed the "must contain text"
   rule.** Examples: zero-width spaces, a byte-order mark, a NUL. Such a name
   stored a blank-looking row. A name that has real text keeps those
   characters exactly as sent.
7. **Crash: the two scripts could not print every character they use** on a
   Windows console or redirected output. A failing unicode check would have
   crashed the script while reporting the failure. Output now escapes what
   the console cannot print.

What held up: the sanitizer produced no markup across the fuzzing, stayed
under half a millisecond at the 500-character limit, and never changed an
already-sanitized value. 200 concurrent POSTs to a live server all stored
without a locking error. The server survived abandoned connections, invalid
HTTP, TLS bytes and a 70 KB URL, and answered requests with no client address
from a shared `"unknown"` bucket.

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
  `x`. This is the trade-off of cleaning input without knowing where it will
  be displayed; escaping on output, which templates and React do by default,
  remains the primary defence.
- **Tag stripping does not make text safe in every context.** A quote
  character is not a tag, so `" onmouseover="alert(1)` is stored as sent. It
  is harmless as page text, but not if a template places it unescaped inside
  an HTML attribute. The same applies to a `javascript:` value placed in an
  `href`. Output escaping is what covers those contexts.
- **Control characters inside a real name are kept.** `a\x00b` is stored as
  sent. Only names made *entirely* of invisible characters are refused.
- **`/items/` redirects to `/items` using the request's `Host` header** to
  build the `Location` URL. That is Starlette's default behaviour, and the
  redirect also spends a request of the caller's budget.
- **`GET /items` has no pagination.** Fine for an exercise, not for a table
  that grows.
