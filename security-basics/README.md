# Security Basics

**Module 5 — FastAPI Development, L10**

Hardens a small item API with three defences: CORS restricted to named
origins, per-endpoint rate limits with SlowAPI, and schema-level
sanitization that strips HTML tags from user-submitted text. `GET /items`
carries the SQL injection explanation the brief asks for, and runs against a
real SQLite table so that explanation describes code that actually executes.
A registration and login flow (bcrypt-hashed passwords, a signed JWT) gives
the strictest rate limit something real to protect.

---

## Changes made for the reviewer's feedback

| Feedback | What changed | Where |
|---|---|---|
| Add `http://localhost:8501` for Streamlit | Added to the allowed origins, with `http://127.0.0.1:8501` beside it | `app/main.py`, `DEFAULT_ALLOWED_ORIGINS` |
| Use SlowAPI with limits per endpoint type | The hand-written middleware is gone. Login 5/min, create 20/min, list 60/min | `app/limiter.py`, `app/config.py`, decorators on each route |
| Test the login endpoint: six requests, 429 on the sixth | New section [8] of `verify_security.py`, and the live-server evidence in `rate_limit_evidence.txt` | `verify_security.py`, `demo_rate_limit.py` |
| Security-summary comment block at the top of `main.py` | Added, before the imports | `app/main.py` |

The project had no login, so one was added: `POST /auth/register`,
`POST /auth/login` and `GET /auth/me`, backed by a `users` table.

### Where this now departs from the brief

The feedback and the brief cannot both be followed in two places:

- **The brief asks for "a simple rate-limiting middleware that tracks request
  counts per IP"** with one limit of 10 per minute, and the starter hints at
  a `dict[str, list[float]]`. That is what the first submission had. It has
  been replaced by SlowAPI, as the feedback requires. A single global limit
  of 10 would cap the 20 and 60 limits, so the two cannot run side by side.
- **The brief's test is "call any endpoint 11 times rapidly and verify you
  get a 429".** Under the feedback's limits that is only true of login
  (429 from the 6th request). `GET /items` now needs 61 requests and
  `POST /items` 21. Section [9] records this with a check.

Everything else in the brief is still met, including its sanitization test:
section [3] posts an item with `<script>` tags in the **name** and checks both
the response and the stored row.

---

## Starting point

The brief's starter code is a fresh app (`app/main.py` and
`app/routers/items.py` only), so this exercise is built from that starter
rather than on top of an earlier lesson's API. The L7 error envelope
(`{"error", "detail", "status_code"}`) is carried over.

Where the implementation departs from a comment in the starter, it is on
purpose:

| Starter comment | Here | Why |
|---|---|---|
| `allow_credentials=True` | `False` | Login returns a token sent in the `Authorization` header; there are no cookies, so no browser credentials to allow |
| `allow_methods` includes PUT, DELETE | `["GET", "POST"]` | Only the methods the API has; the others can only ever 405 |
| `allow_headers=["Authorization", "Content-Type"]` | the same | `Authorization` carries the login token to `GET /auth/me` |
| `"https://yourfrontend.com"` | React on `:3000` and Streamlit on `:8501`, each as `localhost` and `127.0.0.1` | Real defaults, overridable with `CORS_ALLOWED_ORIGINS` |
| rate limiting with a dict of timestamps, 10/minute | SlowAPI, per endpoint | The reviewer's feedback; see above |
| `@router.get("/")` | `@router.get("")` | `"/"` makes the path `/items/`, so `/items` costs a 307 redirect first |
| "return sample items list" | a SQLite `items` table | So the SQL injection note can be demonstrated against a real query |

---

## Files

| File | Contents |
|---|---|
| `app/main.py` | security summary, CORS configuration and middleware, error handlers, the 429 handler, router registration |
| `app/config.py` | settings read from the environment and validated at startup: the three rate limits, the JWT key and lifetime |
| `app/limiter.py` | the SlowAPI `Limiter` (moving window, per endpoint, keyed on the client's address) |
| `app/security.py` | bcrypt hashing and JWT creation and checking |
| `app/database.py` | engine, session factory, `get_db` dependency |
| `app/models/item.py` | the `Item` ORM model |
| `app/models/user.py` | the `User` ORM model |
| `app/routers/items.py` | `sanitize_text()`, `ItemCreate` with its `@field_validator`, `GET /items` with the SQL injection note, `POST /items` |
| `app/routers/auth.py` | `UserCreate`, `LoginRequest`, `POST /auth/register`, `POST /auth/login`, `GET /auth/me` |
| `app/__init__.py`, `app/routers/__init__.py`, `app/models/__init__.py` | empty package markers |
| `verify_security.py` | 389 checks across 16 sections, including the six-login test, concurrency and regression tests |
| `demo_rate_limit.py` | drives a live server past the limits and writes the transcript below |
| `rate_limit_evidence.txt` | the real output of `python demo_rate_limit.py` |
| `requirements.txt` | dependencies; `slowapi`, `bcrypt` and `PyJWT` are new |

`items.db` is created in the working directory the first time the app starts.
It is not part of the submission.

---

## Running

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload      # from this folder
```

Then open `http://127.0.0.1:8000/docs`. To try the login there: register
with `POST /auth/register`, log in with `POST /auth/login`, and send the
returned `access_token` to `GET /auth/me` as `Authorization: Bearer <token>`.

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | the four origins above | comma-separated origins allowed to read responses in a browser |
| `RATE_LIMIT_LOGIN` | `5/minute` | `POST /auth/login` |
| `RATE_LIMIT_CREATE` | `20/minute` | `POST /items`, `POST /auth/register` |
| `RATE_LIMIT_LIST` | `60/minute` | `GET /items`, `GET /auth/me`, `GET /` |
| `JWT_SECRET_KEY` | a random key per process | the token signing key; at least 32 bytes |
| `JWT_EXPIRE_MINUTES` | `30` | how long a login token is valid |
| `DATABASE_URL` | `sqlite:///./items.db` | where items and users are stored |

Every setting is validated at startup. `CORS_ALLOWED_ORIGINS=*`, an origin
with a trailing slash, `RATE_LIMIT_LOGIN=5/fortnight` or `0/minute`, or a
short `JWT_SECRET_KEY` stops the server with a message. The rate-limit check
matters more than it looks: SlowAPI only parses a limit on the first
request, and when it cannot, it logs an error and applies **no** limit.

With `JWT_SECRET_KEY` unset, tokens stop working when the server restarts,
and a warning says so at startup. Set it for anything but development.

---

## Endpoints

| Endpoint | Limit | Returns | Notes |
|---|---|---|---|
| `POST /auth/login` | 5/min | 200, `{access_token, token_type, expires_in}` | 401 for a wrong password or unknown user, with one message for both |
| `POST /auth/register` | 20/min | 201, `{id, username, created_at}` | 409 if the username is taken (case-insensitive) |
| `GET /auth/me` | 60/min | 200, the account | needs `Authorization: Bearer <token>`; 401 otherwise |
| `POST /items` | 20/min | 201, the stored item | body `{"name": str, "description": str}`; both fields sanitized |
| `GET /items` | 60/min | 200, list of items | optional `?name=` exact-match filter, sent as a bound parameter |
| `GET /` | 60/min | 200 | a pointer to `/docs` |

Each limit is per client IP and per endpoint: using up the login budget does
not affect `GET /items`. Over a limit, the response is **429** in the error
envelope with `Retry-After` in whole seconds. Every response from a limited
endpoint carries `X-RateLimit-Limit` and `X-RateLimit-Remaining`. The 429 is
published in the OpenAPI schema for every endpoint above.

---

## The defences

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

Every schema has length limits, including the new ones: usernames 3–30
characters of letters, digits, `.`, `_` and `-`; passwords 8 characters to
72 bytes. Passwords are deliberately **not** sanitized or stripped. Removing
`<b>` or a trailing space would silently change someone's password, and a
password is never displayed, so it cannot carry stored XSS.

### Rate limiting

SlowAPI, with a `@limiter.limit(...)` decorator on each endpoint. The
settings in `app/limiter.py`, each tested:

- **Moving window,** not SlowAPI's default fixed window. In SlowAPI's storage
  a fixed window starts at a client's first request and resets a minute
  later, so one login at 0:00, four at 0:58 and five more at 1:00 would all
  be allowed: nine guesses in two seconds. Section [10] shows the moving
  window allows one.
- **One count per endpoint** (`key_style="endpoint"`). The default keys on
  the URL path, which is `/items` for both GET and POST.
- **The socket peer address identifies the client.** `X-Forwarded-For` and
  friends are ignored, since any client can set them.
- **Only served requests are counted,** so hammering during a lockout does
  not extend it.
- **Every login attempt counts, right or wrong.** The limit exists to slow
  down password guessing; section [8] shows a correct password on the sixth
  try is still refused.

Two things are done in `app/main.py` rather than by SlowAPI:

- **The 429** is built by `handle_rate_limited()` in the standard error
  envelope. `Retry-After` is the first whole second strictly after a slot
  frees, so a client that waits exactly that long is never refused again.
- **The rate-limit headers** are added by middleware. SlowAPI's own header
  option needs a `response` argument on every endpoint and adds
  `Retry-After` to successful responses too.

A request refused before it reaches an endpoint is not counted: a 404, a
405, or a body that fails validation (422). This differs from the first
submission, which counted everything. Those requests are rejected early and
cheaply; a wrong password, which does reach bcrypt, is counted.

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
outermost, and CORS is added last, so it wraps everything:

1. A 429 still carries `Access-Control-Allow-Origin`, so a Streamlit or React
   page can read the status and `Retry-After`. The same goes for 401s and 500s.
2. CORS answers preflight `OPTIONS` requests itself, so preflights never
   reach an endpoint and do not spend any budget.

`expose_headers` lists `Retry-After` and the two rate-limit headers, because a
browser hides non-safelisted response headers from page JavaScript otherwise.

### Login

- Passwords are hashed with bcrypt at cost 12; only the hash is stored.
- A login for an unknown username still runs bcrypt against a dummy hash,
  so it takes as long as a wrong password and the response time does not
  reveal which usernames exist. The error message is the same for both.
- Tokens are HS256 JWTs carrying the user id, issue time and expiry. They
  are checked against a fixed algorithm list, so an unsigned (`alg: none`)
  or re-signed token is refused.
- A 422 on a register or login body never echoes the request back, because
  for a missing field Pydantic's rejected "input" is the whole body,
  password included. Item 422s still echo a bounded preview.

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
second statement. The login queries use the same bound parameters.

---

## Verification

```bash
python verify_security.py          # about 30 seconds
```

It uses a scratch database, never `items.db`, and leaves nothing behind. A
fake clock stands in for `time.time()` in SlowAPI's storage, so the
60-second windows are tested to the half-second without sleeping. Every HTTP
call goes through a shared `request()` helper that catches transport errors
and checks the status code before the body is read.

| Section | Establishes |
|---|---|
| 1 | `sanitize_text()` exact outputs, including cases that must be left alone |
| 2 | the starter's single regex lets markup through; `sanitize_text()` leaves all 19 payloads inert when rendered in a page |
| 3 | the brief's test: `<script>` tags in an item's **name** are stripped, in the response and in the stored row; other stored values match; clean input is unchanged |
| 4 | markup-only, whitespace-only, null, numeric and over-long input is 422 and stores nothing; non-JSON bodies are refused |
| 5 | SQL injection, as described above |
| 6 | CORS: all four origins echoed, Streamlit included; disallowed ones granted nothing; preflights refuse unlisted methods and headers; `*` never appears |
| 7 | origin parsing rules; Starlette's wildcard-plus-credentials behaviour, demonstrated; the limits in force; startup validation of every setting |
| 8 | **the reviewer's test:** five logins are 200, the sixth is 429 with envelope and headers, and a correct password does not get past it; wrong passwords and unknown usernames count too |
| 9 | 60 GETs then a 429; 20 POSTs then a 429 that wrote no row; 20 registrations then a 429; each endpoint's budget is separate; `/openapi.json` is not limited |
| 10 | the window slides: obeying `Retry-After` exactly works first time, partial expiry, and the fixed-window burst is refused |
| 11 | per-IP isolation; forged forwarding headers ignored; lockout not extended by hammering; 404/405/422 do not count |
| 12 | middleware order: a Streamlit page's 429 carries CORS headers; preflights are free; rate-limit headers on 200s and 401s, and no `Retry-After` outside a 429 |
| 13 | against a live uvicorn server: 20 simultaneous logins, exactly 5 reach the password check; 80 simultaneous GETs, exactly 60 served |
| 14 | error envelope on 401/404/405/409/422/500; the 429 published in OpenAPI for all five limited endpoints |
| 15 | registration and login: stored bcrypt hash, case-insensitive usernames, 409 on duplicates, 422 on bad usernames and passwords, token contents, equal timing for unknown users, eight kinds of bad token refused, passwords never echoed in a 422 |
| 16 | regressions for every bug found by earlier probing (listed below) |

To confirm the checks can fail, the code was deliberately broken six ways
and the script run against each: login limit raised to 10/min (19 checks
failed), fixed window instead of moving (2), the Streamlit origin removed
(8), passwords echoed in 422s (6), the dummy-hash timing defence removed (1),
the create limit removed (2).

Result on the last run: **All 389 checks passed**, on Python 3.12.3 with
FastAPI 0.142.2, Starlette 1.7.0, Pydantic 2.13.5, SQLAlchemy 2.1.1, uvicorn
0.54.0, httpx 0.28.1, slowapi 0.1.10, limits 5.8.0, bcrypt 5.0.0 and
PyJWT 2.15.1.

### Evidence of the 429

```bash
python demo_rate_limit.py           # about 65 seconds; regenerates rate_limit_evidence.txt
```

`rate_limit_evidence.txt` is copied from a real run against uvicorn with the
default limits, not written by hand. It records:

1. An account registered.
2. Five logins served, `X-RateLimit-Remaining` counting 4 → 0, each with a token.
3. The sixth login's full response: `HTTP/1.1 429 Too Many Requests`,
   `Retry-After`, `X-RateLimit-Limit: 5`, `X-RateLimit-Remaining: 0`, and the
   JSON body `{"error": "TooManyRequests", ...}` with no token.
4. A seventh login with the correct password and `Origin: http://localhost:8501`:
   still 429, and still carrying `Access-Control-Allow-Origin` with
   `Retry-After` exposed.
5. While login is locked out, `GET /items` serves 60 then refuses the 61st,
   and `POST /items` serves 20 then refuses the 21st. The row count, read
   straight from the database, rises by exactly 20.
6. uvicorn's own access log: five 200s and two 429s for login.
7. After sleeping the `Retry-After` the server gave, the same client logs in
   again.

The script checks each of those claims and exits non-zero if any one does not
hold.

### Bugs found along the way

**First submission, while building:**

1. The starter's sanitizer hint (one `re.sub(r"<[^>]*>", "", ...)`) cannot
   remove a tag with no closing `>`. `<img src=x onerror=alert(1)//` passes
   through, and a page's own `</div>` supplies the `>`. Section 2 shows it.
2. The first fix turned `3 < 5 and 5 > 3` into `3  3`. Tags must now start
   with a letter, `/`, `!` or `?`.
3. The 422 handler crashed on non-JSON bodies, whose rejected input is bytes.

**First submission, adversarial probing** (section 16 keeps each covered):

1. The 422 handler crashed on a lone surrogate, `NaN` or `Infinity`, and
   echoed rejected values unbounded (a 5 MB error for a 5 MB name).
2. 405 responses had no `Allow` header, and Starlette's would have named only
   the first matching route's methods.
3. 500 responses escaped past CORS, so a browser reported a CORS failure.
4. CORS entries such as `https://app.example.com:443` passed validation and
   could never match.
5. A 400-digit number setting crashed the startup check with `OverflowError`.
6. A name of only invisible characters (zero-width spaces, a BOM) passed the
   "must contain text" rule.
7. The scripts crashed printing some characters on a Windows console.

**This revision:**

1. SlowAPI silently applies no limit when it cannot parse a limit string.
   Limits are now parsed at startup.
2. SlowAPI's built-in headers add `Retry-After` to successful responses.
   The headers are added by the app's own middleware instead.
3. SlowAPI's moving window frees a slot only once the oldest request is
   *more* than a minute old, so a `Retry-After` rounded up to the exact
   boundary would earn a client obeying it a second 429. It is now the first
   whole second after the boundary.
4. A 422 on a login body echoed the whole request back, password included.

---

## Limitations

- **The rate limits live in one process's memory.** A restart forgets every
  client, and with several uvicorn workers each keeps its own counts, so the
  effective limit is multiplied by the worker count. SlowAPI supports Redis
  (`storage_uri="redis://..."`) for this.
- **Behind a reverse proxy, every client shares the proxy's IP.** Run uvicorn
  with `--proxy-headers --forwarded-allow-ips=<proxy address>` so the peer
  address is rewritten only for requests that really came through the proxy.
- **Per-IP login limits slow guessing from one address, not from many.** An
  attacker with many addresses gets 5 guesses a minute from each. A
  per-account limit or lockout would be the next step.
- **Requests that fail before reaching an endpoint are not limited:** 404s,
  405s, 422s, `/docs`, `/openapi.json` and preflights. They touch no
  database, but they are unbounded.
- **Per-address limits are weak against IPv6.** One subscriber often controls
  a whole /64, which is an effectively unlimited supply of addresses.
- **There is no logout or token revocation.** A token is valid until it
  expires (30 minutes by default).
- **Sanitizing is lossy.** `x<y` looks like the start of a tag and is cut to
  `x`. Escaping on output, which templates and React do by default, remains
  the primary defence.
- **Tag stripping does not make text safe in every context.** A quote
  character is not a tag, so `" onmouseover="alert(1)` is stored as sent. It
  is harmless as page text, but not if a template places it unescaped inside
  an HTML attribute.
- **`GET /items` has no pagination.** Fine for an exercise, not for a table
  that grows.
