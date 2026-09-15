"""End-to-end check of the auth system.

Run from the auth-system/ folder:  python verify.py

Every request is checked two ways: the status code first, then the returned
values against what was sent. A 200 that contains the wrong data is still a
failure, so nothing here trusts a status code on its own.
"""

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as database

# Swap in a throwaway database BEFORE importing app.main. main.py calls
# create_all(bind=engine) at import time, so importing it first would create an
# empty auth.db in the working directory as a side effect.
tmp_dir = tempfile.mkdtemp()
test_engine = create_engine(
    f"sqlite:///{Path(tmp_dir) / 'verify.db'}", connect_args={"check_same_thread": False}
)
database.engine = test_engine

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402,F401 - registers the table on Base

TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
Base.metadata.create_all(bind=test_engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}{' — ' + detail if detail else ''}")
        failures.append(label)


SENT = {"username": "ada", "email": "ada@example.com", "password": "hunter2hunter2"}

print("\n1. Register a new user")
r = client.post("/auth/register", json=SENT)
check("returns 201", r.status_code == 201, f"got {r.status_code}: {r.text}")
body = r.json() if r.status_code == 201 else {}
check("username round-trips", body.get("username") == SENT["username"], repr(body.get("username")))
check("email round-trips", body.get("email") == SENT["email"], repr(body.get("email")))
check("id assigned", isinstance(body.get("id"), int))
check("no password field in response", "password" not in body and "hashed_password" not in body, str(body))

print("\n2. Password is stored hashed, not in plain text")
with TestingSession() as db:
    stored = db.query(User).filter(User.username == SENT["username"]).one_or_none()
check("user row exists", stored is not None)
if stored is not None:
    check("stored value is not the plain password", stored.hashed_password != SENT["password"])
    check("stored value is a bcrypt hash", stored.hashed_password.startswith("$2"), stored.hashed_password[:10])

print("\n3. Duplicate registration is rejected")
r = client.post("/auth/register", json=SENT)
check("duplicate username returns 409", r.status_code == 409, f"got {r.status_code}")
r = client.post("/auth/register", json={**SENT, "username": "ada2"})
check("duplicate email returns 409", r.status_code == 409, f"got {r.status_code}")

print("\n4. Invalid registration input is rejected")
r = client.post("/auth/register", json={**SENT, "username": "bob", "email": "not-an-email"})
check("bad email returns 422", r.status_code == 422, f"got {r.status_code}")
r = client.post("/auth/register", json={"username": "bob", "email": "bob@example.com", "password": "short"})
check("short password returns 422", r.status_code == 422, f"got {r.status_code}")

print("\n5. Log in and receive a token")
r = client.post(
    "/auth/token",
    data={"username": SENT["username"], "password": SENT["password"]},
)
check("returns 200", r.status_code == 200, f"got {r.status_code}: {r.text}")
token_body = r.json() if r.status_code == 200 else {}
token = token_body.get("access_token", "")
check("token_type is bearer", token_body.get("token_type") == "bearer", repr(token_body.get("token_type")))
check("token looks like a JWT (three parts)", token.count(".") == 2, repr(token[:20]))

print("\n6. Bad credentials are rejected")
r = client.post("/auth/token", data={"username": SENT["username"], "password": "wrongpassword"})
check("wrong password returns 401", r.status_code == 401, f"got {r.status_code}")
r = client.post("/auth/token", data={"username": "ghost", "password": SENT["password"]})
check("unknown user returns 401", r.status_code == 401, f"got {r.status_code}")

print("\n7. Protected endpoints with a valid token")
auth_header = {"Authorization": f"Bearer {token}"}
r = client.get("/auth/me", headers=auth_header)
check("GET /me returns 200", r.status_code == 200, f"got {r.status_code}: {r.text}")
me = r.json() if r.status_code == 200 else {}
check("/me returns the user who logged in", me.get("username") == SENT["username"], repr(me.get("username")))
check("/me email matches registration", me.get("email") == SENT["email"], repr(me.get("email")))
check("/me id matches registration", me.get("id") == body.get("id"), f"{me.get('id')} vs {body.get('id')}")

r = client.get("/auth/dashboard", headers=auth_header)
check("GET /dashboard returns 200", r.status_code == 200, f"got {r.status_code}")
dash = r.json() if r.status_code == 200 else {}
check("/dashboard names the caller", SENT["username"] in dash.get("message", ""), repr(dash.get("message")))
check("/dashboard user_id matches", dash.get("user_id") == body.get("id"), repr(dash.get("user_id")))

print("\n8. Protected endpoints without a usable token")
r = client.get("/auth/me")
check("missing header returns 401/403", r.status_code in (401, 403), f"got {r.status_code}")
r = client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"})
check("malformed token returns 401", r.status_code == 401, f"got {r.status_code}")

from datetime import timedelta  # noqa: E402

from app.auth import create_access_token  # noqa: E402

expired = create_access_token({"sub": SENT["username"]}, expires_delta=timedelta(minutes=-5))
r = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
check("expired token returns 401", r.status_code == 401, f"got {r.status_code}")

forged = create_access_token({"sub": "ada"}).rsplit(".", 1)[0] + ".tampered_signature"
r = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
check("tampered signature returns 401", r.status_code == 401, f"got {r.status_code}")

ghost_token = create_access_token({"sub": "no-such-user"})
r = client.get("/auth/me", headers={"Authorization": f"Bearer {ghost_token}"})
check("valid token for a deleted user returns 401", r.status_code == 401, f"got {r.status_code}")

print("\n9. Regressions found by adversarial testing")
r = client.post("/auth/register", json={"username": "nullbyte", "email": "nb@example.com", "password": "pass\x00word"})
check("NULL byte in password returns 422, not 500", r.status_code == 422, f"got {r.status_code}")
r = client.post("/auth/register", json={"username": "multibyte", "email": "mb@example.com", "password": "\u00e9" * 40})
check("80-byte password returns 422 (byte length, not char length)", r.status_code == 422, f"got {r.status_code}")
r = client.post("/auth/register", json={"username": "  spaced  ", "email": "sp@example.com", "password": "password123"})
check("username is trimmed on the way in", r.status_code == 201 and r.json()["username"] == "spaced", r.text[:120])
r = client.post("/auth/register", json={"username": "ctrl\x00name", "email": "ct@example.com", "password": "password123"})
check("control character in username returns 422", r.status_code == 422, f"got {r.status_code}")
r = client.post("/auth/register", json={"username": "ADA", "email": "different@example.com", "password": "password123"})
check("username differing only in case returns 409", r.status_code == 409, f"got {r.status_code}")
r = client.post("/auth/register", json={"username": "adatwo", "email": "ADA@Example.com", "password": "password123"})
check("email differing only in case returns 409", r.status_code == 409, f"got {r.status_code}")
r = client.post("/auth/token", data={"username": "ADA", "password": SENT["password"]})
check("login is case-insensitive on username", r.status_code == 200, f"got {r.status_code}")

client.post("/auth/register", json={"username": "trunc", "email": "tr@example.com", "password": "a" * 72})
r = client.post("/auth/token", data={"username": "trunc", "password": "a" * 73})
check("73-byte password does not log into the 72-byte account", r.status_code == 401, f"got {r.status_code}")

from jose import jwt as _jwt  # noqa: E402

from app.auth import ALGORITHM, SECRET_KEY  # noqa: E402

no_exp = _jwt.encode({"sub": SENT["username"]}, SECRET_KEY, algorithm=ALGORITHM)
r = client.get("/auth/me", headers={"Authorization": f"Bearer {no_exp}"})
check("token with no exp claim returns 401", r.status_code == 401, f"got {r.status_code}")

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("All checks passed.")
