# Auth System

Module 5 — FastAPI Development, L8.

JWT-based authentication: user registration with bcrypt password hashing, login
that issues a signed token, and endpoints protected by a FastAPI dependency.

## Files

```
auth-system/
  README.md              — this file
  requirements.txt       — pinned dependencies
  verify.py              — end-to-end check of every endpoint (38 assertions)
  app/
    __init__.py
    main.py              — FastAPI app; creates tables, includes the auth router at /auth
    database.py          — engine, SessionLocal, Base, get_db (unchanged from the starter)
    auth.py              — pwd_context, hash_password, verify_password,
                           create_access_token, get_current_user
    models/
      __init__.py
      user.py            — User ORM model (id, username, email, hashed_password)
    schemas/
      __init__.py
      user.py            — UserCreate, UserResponse, TokenResponse
    routers/
      __init__.py
      auth.py            — /register, /token, /me, /dashboard
```

`auth.db` is created in the working directory on first run and is not committed.

## Setup

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Swagger UI: http://127.0.0.1:8000/docs

## Endpoints

| Method | Path              | Auth   | Purpose                                      |
|--------|-------------------|--------|----------------------------------------------|
| POST   | `/auth/register`  | none   | Create a user. 409 if username or email taken |
| POST   | `/auth/token`     | none   | Exchange username + password for a JWT       |
| GET    | `/auth/me`        | Bearer | The authenticated user                        |
| GET    | `/auth/dashboard` | Bearer | Greeting, the caller's id/email, user count   |
| GET    | `/`               | none   | Health check                                  |

### Using the Swagger "Authorize" button

The security scheme is `HTTPBearer`, so Authorize shows one field:

1. `POST /auth/register` to create an account.
2. `POST /auth/token` with the same username and password — copy `access_token`.
3. Click **Authorize**, paste the token, and the protected routes work.

`/auth/token` takes a form-encoded body (`username`, `password`) via
`OAuth2PasswordRequestForm`, which is why `python-multipart` is required.

### From the command line

```bash
curl -X POST localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"ada","email":"ada@example.com","password":"hunter2hunter2"}'

TOKEN=$(curl -s -X POST localhost:8000/auth/token \
  -d "username=ada&password=hunter2hunter2" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl localhost:8000/auth/me -H "Authorization: Bearer $TOKEN"
```

## Verification

```bash
python verify.py
```

Runs against a temporary database, so it never touches `auth.db`. Each request
is checked twice — status code, then the returned values against what was sent.
Covered: registration round-trip, the password being stored as a bcrypt hash and
absent from every response, duplicate username and email both returning 409,
invalid email and short password returning 422, login and token shape, wrong
password and unknown user returning 401, `/me` and `/dashboard` returning the
caller's own record, six rejected-token cases (missing, malformed, expired,
tampered signature, no `exp` claim, and a valid token for a user who no longer
exists), and the input edge cases listed under "Hardening" below.

`verify.py` replaces `app.database.engine` with a temporary one *before*
importing `app.main`, because `main.py` runs `create_all(bind=engine)` at import
time — importing it first would create an empty `auth.db` as a side effect.

## Notes on the implementation

- **Errors are deliberately vague.** Login returns the same 401 for an unknown
  username as for a wrong password, and `get_current_user` returns one shared
  401 for every token failure. Specific messages would let someone probe for
  valid usernames or learn why a forged token failed.
- **Registration checks duplicates twice.** The `SELECT` gives a clean 409, and
  the `IntegrityError` handler catches the race between that check and the
  commit. The unique indexes on `username` and `email` are the real guarantee.
- **`UserResponse` cannot leak the hash.** It declares only `id`, `username` and
  `email`, so `response_model` drops `hashed_password` even though the ORM
  object carries it.
- **Passwords are capped at 72 *bytes*** in `UserCreate`, measured with
  `len(pw.encode())` rather than `len(pw)` — 40 accented characters are 80
  bytes, and bcrypt would silently ignore everything past 72. `verify_password`
  rejects over-length input too, so a longer string sharing the first 72 bytes
  can't authenticate. NULL bytes are rejected as well: bcrypt raises on them,
  which would otherwise surface as a 500.
- **Usernames and emails are unique case-insensitively.** Emails are lower-cased
  on the way in; usernames keep their casing but are backed by a unique index on
  `lower(username)`, so "Ada" and "ada" can't both exist and login works with
  either. Usernames are also trimmed and control characters rejected.
- **`exp` is required when decoding.** `jwt.decode(..., options={"require_exp":
  True})` — otherwise a token minted without an expiry claim would be valid
  forever.
- **`SECRET_KEY` reads `AUTH_SECRET_KEY`** from the environment and falls back
  to the exercise default. A real deployment would have no fallback.
- **Token expiry** uses a timezone-aware `datetime.now(timezone.utc)` rather
  than the deprecated `datetime.utcnow()`.
