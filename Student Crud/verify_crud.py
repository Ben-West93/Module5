"""Exercises the full CRUD cycle against the app and checks the results.

This mirrors the Swagger UI walkthrough the exercise asks for, but asserts
on what comes back instead of just printing it. Run from student-crud/:

    python verify_crud.py
"""

from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

# Start from a clean table so re-runs are repeatable.
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)
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

client.delete(f"/students/{rid}")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("All checks passed.")
