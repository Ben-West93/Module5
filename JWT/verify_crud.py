"""Exercises the full CRUD cycle against the app and checks the results.

This mirrors the Swagger UI walkthrough the exercise asks for, but asserts
on what comes back instead of just printing it. Run from jwt/:

    python verify_crud.py
"""

import json

from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

# Start from a clean table so re-runs are repeatable.
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)

# `anon` never carries a token: it is what an unauthenticated caller sees.
anon = TestClient(app)

# Writing to /students now requires a login, so register once and attach the
# token to `client` for the rest of the script. Everything below this line is
# an authenticated request unless it explicitly uses `anon`.
_REG = client.post(
    "/auth/register",
    json={"username": "tester", "email": "tester@example.com", "password": "correct-horse-battery"},
)
assert _REG.status_code == 201, f"test setup failed to register: {_REG.status_code} {_REG.text}"
TOKEN = _REG.json()["access_token"]
client.headers.update({"Authorization": f"Bearer {TOKEN}"})

failures: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
        failures.append(label)


print("\n[1] POST /students — create")
payload = {
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "grade_level": 11,
    "major": "Mathematics",
    "gpa": 3.9,
    "is_enrolled": True,
}
r = client.post("/students", json=payload)
check("status is 201", r.status_code, 201)
body = r.json()
for field, sent in payload.items():
    check(f"returned {field} matches what was sent", body.get(field), sent)
check("id was assigned", isinstance(body.get("id"), int), True)
check("created_at was generated", bool(body.get("created_at")), True)
student_id = body["id"]

print("\n[2] POST /students — duplicate email")
r = client.post("/students", json=payload)
check("duplicate email is 409", r.status_code, 409)

print("\n[3] POST /students — validation")
bad = dict(payload, email="not-an-email", grade_level=11)
bad["email"] = "nope"
check("email without @ is 422", client.post("/students", json=bad).status_code, 422)
check(
    "grade_level 13 is 422",
    client.post(
        "/students", json=dict(payload, email="b@example.com", grade_level=13)
    ).status_code,
    422,
)
check(
    "gpa 4.5 is 422",
    client.post(
        "/students", json=dict(payload, email="c@example.com", gpa=4.5)
    ).status_code,
    422,
)

print("\n[4] GET /students — filters")
client.post(
    "/students",
    json={
        "name": "Alan Turing",
        "email": "alan@example.com",
        "grade_level": 12,
        "major": "Computer Science",
        "gpa": 3.4,
        "is_enrolled": False,
    },
)
check("no filter returns both", len(client.get("/students").json()), 2)
check(
    "grade_level=11 returns one",
    len(client.get("/students", params={"grade_level": 11}).json()),
    1,
)
check(
    "is_enrolled=false returns one",
    len(client.get("/students", params={"is_enrolled": "false"}).json()),
    1,
)
check(
    "major filter returns one",
    len(client.get("/students", params={"major": "Mathematics"}).json()),
    1,
)
check(
    "min_gpa=3.5 returns one",
    len(client.get("/students", params={"min_gpa": 3.5}).json()),
    1,
)
check(
    "combined filters returning nothing gives []",
    client.get("/students", params={"grade_level": 11, "is_enrolled": "false"}).json(),
    [],
)

print("\n[5] GET /students/{id}")
r = client.get(f"/students/{student_id}")
check("status is 200", r.status_code, 200)
check("returns the right student", r.json()["email"], "ada@example.com")
check("missing id is 404", client.get("/students/9999").status_code, 404)

print("\n[6] PUT /students/{id} — full replace")
replacement = {
    "name": "Ada Byron",
    "email": "ada.byron@example.com",
    "grade_level": 12,
    "major": None,
    "gpa": None,
    "is_enrolled": False,
}
r = client.put(f"/students/{student_id}", json=replacement)
check("status is 200", r.status_code, 200)
body = r.json()
for field, sent in replacement.items():
    check(f"{field} was replaced", body.get(field), sent)
check("id is unchanged", body["id"], student_id)
check(
    "PUT with a missing field is 422",
    client.put(
        f"/students/{student_id}", json={"name": "x", "email": "x@y.com"}
    ).status_code,
    422,
)
check(
    "PUT onto a taken email is 409",
    client.put(
        f"/students/{student_id}", json=dict(replacement, email="alan@example.com")
    ).status_code,
    409,
)
check("PUT on missing id is 404", client.put("/students/9999", json=replacement).status_code, 404)

print("\n[7] PATCH /students/{id} — partial update")
r = client.patch(f"/students/{student_id}", json={"gpa": 3.75})
check("status is 200", r.status_code, 200)
body = r.json()
check("gpa was updated", body["gpa"], 3.75)
check("name was left alone", body["name"], "Ada Byron")
check("grade_level was left alone", body["grade_level"], 12)
check("is_enrolled was left alone", body["is_enrolled"], False)
check(
    "empty PATCH body is 400",
    client.patch(f"/students/{student_id}", json={}).status_code,
    400,
)
check(
    "PATCH onto a taken email is 409",
    client.patch(f"/students/{student_id}", json={"email": "alan@example.com"}).status_code,
    409,
)
check("PATCH on missing id is 404", client.patch("/students/9999", json={"gpa": 3.0}).status_code, 404)

print("\n[8] DELETE /students/{id}")
# Business rule: an enrolled student cannot be deleted. Create one that is
# still enrolled and confirm the refusal before testing the happy path.
enrolled = client.post(
    "/students",
    json={"name": "Enrolled", "email": "enrolled@example.com", "grade_level": 3},
).json()
check("new students are enrolled by default", enrolled["is_enrolled"], True)
r_blocked = client.delete(f"/students/{enrolled['id']}")
check("deleting an enrolled student is 400", r_blocked.status_code, 400)
check("  labelled BadRequest", r_blocked.json()["error"], "BadRequest")
check("  and the student still exists", client.get(f"/students/{enrolled['id']}").status_code, 200)
check(
    "unenrolling then deleting succeeds",
    (
        client.patch(f"/students/{enrolled['id']}", json={"is_enrolled": False}).status_code,
        client.delete(f"/students/{enrolled['id']}").status_code,
    ),
    (200, 204),
)
check("404 beats the enrollment rule for a missing id", client.delete("/students/9999").status_code, 404)

# student_id was set is_enrolled=False by the PUT in section 6.
r = client.delete(f"/students/{student_id}")
check("status is 204", r.status_code, 204)
check("body is empty", r.content, b"")
check("student is gone", client.get(f"/students/{student_id}").status_code, 404)
check("deleting again is 404", client.delete(f"/students/{student_id}").status_code, 404)
check("one student remains", len(client.get("/students").json()), 1)

print("\n[9] regressions — bugs found during testing, kept covered here")
keep = client.post(
    "/students",
    json={"name": "Reg", "email": "reg@example.com", "grade_level": 7, "gpa": 3.0},
).json()
rid = keep["id"]

# Bug 1: `?major=` arrived as "" and was read as a filter, matching nothing.
check(
    "blank ?major= is treated as no filter",
    len(client.get("/students", params={"major": ""}).json()) > 0,
    True,
)

# Bug 2: `"email": null` crashed the email validator with a TypeError (500).
# Bug 3: nulls on NOT NULL columns reached the DB and were mislabelled 409.
for field in ("name", "email", "grade_level", "is_enrolled"):
    check(
        f"PATCH {field}=null is a 422, not a 409 or a crash",
        client.patch(f"/students/{rid}", json={field: None}).status_code,
        422,
    )
check("record survived the null attempts", client.get(f"/students/{rid}").json(), keep)
check(
    "PATCH gpa=null is still allowed (that column is nullable)",
    client.patch(f"/students/{rid}", json={"gpa": None}).json()["gpa"],
    None,
)

# Falsy values must not be dropped by truthiness checks.
check(
    "PATCH gpa=0.0 stores zero rather than no-op",
    client.patch(f"/students/{rid}", json={"gpa": 0.0}).json()["gpa"],
    0.0,
)
check(
    "min_gpa=0.0 is applied rather than ignored",
    len(client.get("/students", params={"min_gpa": 0.0}).json()) > 0,
    True,
)

check(
    "cleanup delete needs unenrolling first",
    (
        client.patch(f"/students/{rid}", json={"is_enrolled": False}).status_code,
        client.delete(f"/students/{rid}").status_code,
    ),
    (200, 204),
)

print("\n[10] error responses share one format")
# Every error the API can emit should carry the same three top-level keys.
cases = {
    "404 on a missing student": client.get("/students/9999"),
    "404 on an unknown path": client.get("/nope"),
    "405 on a wrong method": client.delete("/students"),
    "409 on a duplicate email": client.post(
        "/students",
        json={"name": "Dup", "email": "alan@example.com", "grade_level": 4},
    ),
    "422 on schema validation": client.post(
        "/students", json={"name": "X", "email": "no-at-sign", "grade_level": 4}
    ),
    "400 on an empty PATCH body": client.patch("/students/2", json={}),
}
expected_labels = {
    "404 on a missing student": ("NotFound", 404),
    "404 on an unknown path": ("NotFound", 404),
    "405 on a wrong method": ("MethodNotAllowed", 405),
    "409 on a duplicate email": ("Duplicate", 409),
    "422 on schema validation": ("ValidationError", 422),
    "400 on an empty PATCH body": ("BadRequest", 400),
}
for label, resp in cases.items():
    body = resp.json()
    check(f"{label}: has error/detail/status_code", sorted(set(body) & {"error", "detail", "status_code"}), ["detail", "error", "status_code"])
    want_error, want_status = expected_labels[label]
    check(f"{label}: error is {want_error}", body.get("error"), want_error)
    check(f"{label}: status_code matches the HTTP status", (body.get("status_code"), resp.status_code), (want_status, want_status))
    check(f"{label}: detail is a non-empty string", isinstance(body.get("detail"), str) and len(body["detail"]) > 0, True)

validation_body = cases["422 on schema validation"].json()
check("422 adds a per-field errors list", isinstance(validation_body.get("errors"), list), True)
check("  which is non-empty", len(validation_body["errors"]) > 0, True)
check(
    "the enrollment refusal explains how to proceed",
    "is_enrolled" in client.delete(f"/students/{client.post('/students', json={'name': 'E2', 'email': 'e2@example.com', 'grade_level': 2}).json()['id']}").json()["detail"],
    True,
)

print("\n[11] error-handling regressions")
# A bug in the code must not escape the envelope, and must not leak internals.
quiet = TestClient(app, raise_server_exceptions=False)


@app.get("/_selftest/boom")
def _boom():
    return 1 / 0


r = quiet.get("/_selftest/boom")
body = r.json()
check("an unhandled bug returns 500", r.status_code, 500)
check("  in the same envelope", sorted(set(body) & {"error", "detail", "status_code"}), ["detail", "error", "status_code"])
check("  labelled InternalServerError", body["error"], "InternalServerError")
check("  with no traceback in the body", "Traceback" in json.dumps(body), False)
check("  and no file paths", "/home/" in json.dumps(body), False)

# Swagger must list the errors each endpoint can return, not just 204/422.
spec = client.get("/openapi.json").json()
item = spec["paths"]["/students/{student_id}"]
check("DELETE documents its 400 and 404", {"400", "404"} <= set(item["delete"]["responses"]), True)
check("POST documents its 409", "409" in spec["paths"]["/students"]["post"]["responses"], True)
check("ErrorResponse is published in the schema", "ErrorResponse" in spec["components"]["schemas"], True)

print("\n[12] auth — register, login, token")
check("register returned 201", _REG.status_code, 201)
check("  with a bearer token", _REG.json()["token_type"], "bearer")
check("  and an expiry in seconds", isinstance(_REG.json()["expires_in"], int), True)
check("  and no password field anywhere", "password" in json.dumps(_REG.json()), False)
check("duplicate username is 409", anon.post("/auth/register", json={"username": "tester", "email": "other@example.com", "password": "correct-horse-battery"}).status_code, 409)
check("duplicate email is 409", anon.post("/auth/register", json={"username": "other", "email": "tester@example.com", "password": "correct-horse-battery"}).status_code, 409)
check("short password is 422", anon.post("/auth/register", json={"username": "shorty", "email": "s@example.com", "password": "abc"}).status_code, 422)
check("password over 72 bytes is 422", anon.post("/auth/register", json={"username": "longy", "email": "l@example.com", "password": "a" * 73}).status_code, 422)

login = anon.post("/auth/login", json={"username": "tester", "password": "correct-horse-battery"})
check("login returned 200", login.status_code, 200)
check("  with a usable token", isinstance(login.json()["access_token"], str) and login.json()["access_token"].count(".") == 2, True)
check("wrong password is 401", anon.post("/auth/login", json={"username": "tester", "password": "nope"}).status_code, 401)
check("unknown username is 401", anon.post("/auth/login", json={"username": "ghost", "password": "nope"}).status_code, 401)
check(
    "both failures give the same message (no user enumeration)",
    anon.post("/auth/login", json={"username": "tester", "password": "nope"}).json()["detail"]
    == anon.post("/auth/login", json={"username": "ghost", "password": "nope"}).json()["detail"],
    True,
)

print("\n[13] GET /users/me")
me = client.get("/users/me")
check("returns 200 with a token", me.status_code, 200)
check("  is the registered user", me.json()["username"], "tester")
check("  exposes no password or hash", any(k in me.json() for k in ("password", "hashed_password")), False)
check("401 without a token", anon.get("/users/me").status_code, 401)
check("  labelled Unauthorized", anon.get("/users/me").json()["error"], "Unauthorized")
check("  in the standard envelope", sorted(set(anon.get("/users/me").json()) & {"error", "detail", "status_code"}), ["detail", "error", "status_code"])
check("  with a WWW-Authenticate header", anon.get("/users/me").headers.get("www-authenticate"), "Bearer")

print("\n[14] which endpoints are protected")
probe = client.post("/students", json={"name": "Probe", "email": "probe@example.com", "grade_level": 6}).json()
pid = probe["id"]
public = {
    "GET /students": anon.get("/students"),
    "GET /students/{id}": anon.get(f"/students/{pid}"),
}
for label, resp in public.items():
    check(f"{label} is public", resp.status_code, 200)

protected = {
    "POST /students": anon.post("/students", json={"name": "N", "email": "n@example.com", "grade_level": 6}),
    "PUT /students/{id}": anon.put(f"/students/{pid}", json={"name": "N", "email": "n@example.com", "grade_level": 6, "major": None, "gpa": None, "is_enrolled": False}),
    "PATCH /students/{id}": anon.patch(f"/students/{pid}", json={"gpa": 1.0}),
    "DELETE /students/{id}": anon.delete(f"/students/{pid}"),
}
for label, resp in protected.items():
    check(f"{label} rejects an anonymous caller", resp.status_code, 401)

check("an anonymous write changed nothing", client.get(f"/students/{pid}").json(), probe)

bad_tokens = {
    "garbage": "not-a-token",
    "wrong signature": TOKEN[:-4] + "AAAA",
    "empty": "",
}
for label, tok in bad_tokens.items():
    r = anon.post("/students", json={"name": "N", "email": "n2@example.com", "grade_level": 6}, headers={"Authorization": f"Bearer {tok}"})
    check(f"{label} token is rejected", r.status_code, 401)

r = anon.post("/students", json={"name": "N", "email": "n3@example.com", "grade_level": 6}, headers={"Authorization": TOKEN})
check("token without the Bearer prefix is rejected", r.status_code, 401)

# An expired token must fail even though it is correctly signed.
from datetime import timedelta  # noqa: E402

from app.models.user import User  # noqa: E402
from app.utils.security import create_access_token  # noqa: E402
from app.database import SessionLocal  # noqa: E402

_db = SessionLocal()
_user = _db.query(User).first()
expired = create_access_token(_user, expires_delta=timedelta(seconds=-60))
_db.close()
check("an expired token is rejected", anon.get("/users/me", headers={"Authorization": f"Bearer {expired}"}).status_code, 401)
check("  and says nothing about why", anon.get("/users/me", headers={"Authorization": f"Bearer {expired}"}).json()["detail"], "Invalid or expired token")

client.patch(f"/students/{pid}", json={"is_enrolled": False})
client.delete(f"/students/{pid}")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("All checks passed.")
