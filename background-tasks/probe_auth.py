"""Angles on the L8 auth layer that verify_crud.py does not cover.

  W  token forgery and algorithm confusion
  X  identity isolation — one token cannot become another user
  Y  credential storage
  Z  Authorization header parsing
  AA registration and login edge cases
  AB concurrency
  AC what the API exposes about its users
"""

import atexit
import base64
import logging
import json
import os
import shutil
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

# --- L9 adjustment, before the app is imported ------------------------------
#
# See the same block in verify_crud.py. TestClient waits for background tasks,
# so the 2-second notification delay would be added to every POST /students
# this probe makes, and its log lines would land in the activity log that
# ships as the exercise's evidence. Neither has anything to do with auth.
_SCRATCH = tempfile.mkdtemp(prefix="probe-auth-")
os.environ["LOG_DIR"] = _SCRATCH
os.environ["NOTIFICATION_DELAY_SECONDS"] = "0"
os.environ["REPORT_DURATION_SECONDS"] = "0"
atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
# ----------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.utils.security import ALGORITHM, SECRET_KEY, create_access_token  # noqa: E402

logging.getLogger("jwt_api").setLevel(logging.CRITICAL)

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

anon = TestClient(app)
# Returns a 500 response instead of re-raising, so a server-side crash shows
# up as a failed check rather than killing the probe.
quiet = TestClient(app, raise_server_exceptions=False)
findings: list[str] = []
notes: list[str] = []
n = 0


def check(label, actual, expected):
    global n
    n += 1
    if actual == expected:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
        findings.append(label)


def note(text):
    notes.append(text)
    print(f"  NOTE  {text}")


def register(username, email, password="correct-horse-battery"):
    return anon.post(
        "/auth/register", json={"username": username, "email": email, "password": password}
    )


def auth(token):
    return {"Authorization": f"Bearer {token}"}


alice_r = register("alice", "alice@example.com")
alice = alice_r.json()["access_token"]
bob = register("bob", "bob@example.com", "different-password-here").json()["access_token"]

print("\n[W] token forgery and algorithm confusion")
payload = jwt.get_unverified_claims(alice)
check("token carries sub/iat/exp", {"sub", "iat", "exp"} <= set(payload), True)
check("sub is a string, per the JWT spec", isinstance(payload["sub"], str), True)

forged_wrong_key = jwt.encode(payload, "attacker-key", algorithm="HS256")
check("token signed with a different key is rejected", anon.get("/users/me", headers=auth(forged_wrong_key)).status_code, 401)


def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


alg_none = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}."
check("alg=none token is rejected", anon.get("/users/me", headers=auth(alg_none)).status_code, 401)

alg_none_sig = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(payload)}.{alice.split('.')[2]}"
check("alg=none with a borrowed signature is rejected", anon.get("/users/me", headers=auth(alg_none_sig)).status_code, 401)

tampered = f"{alice.split('.')[0]}.{b64(dict(payload, sub='999'))}.{alice.split('.')[2]}"
check("payload edited under the original signature is rejected", anon.get("/users/me", headers=auth(tampered)).status_code, 401)

check("signature stripped is rejected", anon.get("/users/me", headers=auth(".".join(alice.split(".")[:2]) + ".")).status_code, 401)
check("truncated token is rejected", anon.get("/users/me", headers=auth(alice[:-10])).status_code, 401)

no_sub = jwt.encode({k: v for k, v in payload.items() if k != "sub"}, SECRET_KEY, algorithm=ALGORITHM)
check("validly signed token with no sub is rejected", anon.get("/users/me", headers=auth(no_sub)).status_code, 401)
non_numeric = jwt.encode(dict(payload, sub="not-an-id"), SECRET_KEY, algorithm=ALGORITHM)
check("sub that is not an id is rejected", anon.get("/users/me", headers=auth(non_numeric)).status_code, 401)
ghost = jwt.encode(dict(payload, sub="4242"), SECRET_KEY, algorithm=ALGORITHM)
check("valid signature for a nonexistent user is rejected", anon.get("/users/me", headers=auth(ghost)).status_code, 401)

print("\n[X] identity isolation")
check("alice's token resolves to alice", anon.get("/users/me", headers=auth(alice)).json()["username"], "alice")
check("bob's token resolves to bob", anon.get("/users/me", headers=auth(bob)).json()["username"], "bob")
check("/users/me takes no id, so no one else's profile is reachable", "/users/{user_id}" in anon.get("/openapi.json").json()["paths"], False)

db = SessionLocal()
doomed = User(username="doomed", email="doomed@example.com", hashed_password="x")
db.add(doomed)
db.commit()
db.refresh(doomed)
doomed_token = create_access_token(doomed)
check("a fresh token for a real user works", anon.get("/users/me", headers=auth(doomed_token)).status_code, 200)
db.delete(doomed)
db.commit()
db.close()
check("that token stops working the moment the user is deleted", anon.get("/users/me", headers=auth(doomed_token)).status_code, 401)

expired = create_access_token(
    SessionLocal().query(User).filter_by(username="alice").first(), expires_delta=timedelta(seconds=-1)
)
check("expired token is rejected", anon.get("/users/me", headers=auth(expired)).status_code, 401)

print("\n[Y] credential storage")
anon.post("/students", json={"name": "S", "email": "s@example.com", "grade_level": 5}, headers=auth(alice))
con = sqlite3.connect("students.db")
rows = con.execute("select username, hashed_password from users").fetchall()
stored = dict(rows)
check("password is not stored in plaintext", any("correct-horse-battery" in v for v in stored.values()), False)
check("stored value is a hash", stored["alice"].startswith(("$2b$", "$2a$", "$pbkdf2")), True)
cols = [c[1] for c in con.execute("PRAGMA table_info(users)")]
check("there is no plaintext password column", "password" in cols, False)
check("users table has the expected columns", sorted(cols), ["created_at", "email", "hashed_password", "id", "username"])

same_pw_a = register("samepw1", "sp1@example.com", "identical-password").json()
same_pw_b = register("samepw2", "sp2@example.com", "identical-password").json()
h = dict(con.execute("select username, hashed_password from users where username like 'samepw%'").fetchall())
check("identical passwords produce different hashes (salted)", h["samepw1"] != h["samepw2"], True)
con.close()

check("register response contains no password", "password" in json.dumps(alice_r.json()), False)
check("/users/me exposes no hash", "hashed_password" in json.dumps(anon.get("/users/me", headers=auth(alice)).json()), False)

print("\n[Z] Authorization header parsing")
cases = {
    "no header": {},
    "empty value": {"Authorization": ""},
    "scheme only": {"Authorization": "Bearer"},
    "scheme with no token": {"Authorization": "Bearer "},
    "wrong scheme": {"Authorization": f"Basic {alice}"},
    "token with no scheme": {"Authorization": alice},
    "two tokens": {"Authorization": f"Bearer {alice} {bob}"},
}
for label, headers in cases.items():
    check(f"{label} is 401", anon.get("/users/me", headers=headers).status_code, 401)

lower = anon.get("/users/me", headers={"Authorization": f"bearer {alice}"})
if lower.status_code == 200:
    note("lowercase 'bearer' is accepted — RFC 7235 says the scheme is case-insensitive, so this is correct, just worth knowing")
else:
    note(f"lowercase 'bearer' returns {lower.status_code}; RFC 7235 makes the scheme case-insensitive, so accepting it would also be valid")

print("\n[AA] registration and login edge cases")
check("SQL injection in username is harmless", anon.post("/auth/login", json={"username": "' OR 1=1 --", "password": "x"}).status_code, 401)
check("  and the users table survives", anon.get("/users/me", headers=auth(alice)).status_code, 200)
check("unicode username round-trips", register("zoë", "zoe@example.com").status_code, 201)
check("unicode password works", anon.post("/auth/login", json={"username": "zoë", "password": "correct-horse-battery"}).status_code, 200)
check("2-char username is 422", register("ab", "ab@example.com").status_code, 422)
check("email without @ is 422", register("noat", "nope").status_code, 422)
check("empty login body is 422", anon.post("/auth/login", json={}).status_code, 422)
check("GET on /auth/login is 405", anon.get("/auth/login").status_code, 405)
check("  in the standard envelope", sorted(set(anon.get("/auth/login").json()) & {"error", "detail", "status_code"}), ["detail", "error", "status_code"])

check("case-variant username cannot impersonate", register("ALICE", "ALICE@example.com").status_code, 409)
check("padded username cannot impersonate", register("  alice  ", "pad@example.com").status_code, 409)
check("case-variant email is caught too", register("alice2", "Alice@Example.com").status_code, 409)
check("login works with different case", anon.post("/auth/login", json={"username": "ALICE", "password": "correct-horse-battery"}).status_code, 200)
check("login works with padding", anon.post("/auth/login", json={"username": "  alice "}| {"password": "correct-horse-battery"}).status_code, 200)
check("passwords stay case-sensitive", anon.post("/auth/login", json={"username": "alice", "password": "CORRECT-HORSE-BATTERY"}).status_code, 401)

print("\n[AB] concurrency")
payload_reg = {"username": "racer", "email": "racer@example.com", "password": "correct-horse-battery"}
with ThreadPoolExecutor(max_workers=8) as pool:
    results = [f.result() for f in [pool.submit(quiet.post, "/auth/register", json=payload_reg) for _ in range(8)]]
codes = sorted(r.status_code for r in results)
print(f"        8 simultaneous registrations -> {codes}")
check("exactly one succeeds", codes.count(201), 1)
check("no 500s", [c for c in codes if c >= 500], [])
check("every loser is 409", set(codes) - {201}, {409})
con = sqlite3.connect("students.db")
check("exactly one row written", con.execute("select count(*) from users where username='racer'").fetchone()[0], 1)
con.close()

print("\n[AC] what the API exposes about its users")
spec = anon.get("/openapi.json").json()
check("HTTPBearer security scheme is published", "HTTPBearer" in spec["components"].get("securitySchemes", {}), True)
protected = sorted(f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m, o in ops.items() if o.get("security"))
# An exact list, not a subset check. A subset would pass just as happily if
# someone later shipped an endpoint with no `security` on it at all, which is
# the failure worth catching here. The cost is that the list has to be updated
# when the API gains a route — as it did at L9, when the four reports
# endpoints arrived. That is the check working, not the check being annoying.
#
# GET /reports/{report_id} is on this list while GET /students/{id} is not:
# browsing students is public, but a report belongs to the user who asked for
# it, so reading one requires proving which user you are.
check(
    "every write, profile and report operation is marked protected",
    protected,
    [
        "DELETE /reports/{report_id}",
        "DELETE /students/{student_id}",
        "GET /reports/{report_id}",
        "GET /users/me",
        "PATCH /students/{student_id}",
        "POST /reports",
        "POST /reports/notifications",
        "POST /students",
        "PUT /students/{student_id}",
    ],
)
# Check the declared properties, not the serialized JSON: the docstring says
# "no password field of any kind", so a substring search matches the
# reassurance rather than a leak.
user_props = set(spec["components"]["schemas"]["UserResponse"]["properties"])
check("UserResponse declares no password property", {"password", "hashed_password"} & user_props, set())
check("  and exposes only the expected fields", sorted(user_props), ["created_at", "email", "id", "username"])
check("no endpoint lists all users", [p for p in spec["paths"] if p.startswith("/users") and p != "/users/me"], [])
check("401 is documented on POST /students", "401" in spec["paths"]["/students"]["post"]["responses"], True)
note("there is no rate limiting on /auth/login — unlimited password guesses are possible; out of scope for the exercise, recorded in the README")

print("\n" + "=" * 60)
print(f"{n - len(findings)}/{n} checks passed, {len(notes)} note(s)")
if findings:
    print("Failures:")
    for f in findings:
        print(f"  - {f}")
    raise SystemExit(1)
