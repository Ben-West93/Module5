# L6 — Student CRUD API

A database-backed CRUD API for a `students` resource: SQLAlchemy model, four
Pydantic schemas, six endpoints, and a shared `get_student_or_404()` helper.

## Files

```
student-crud/
├── README.md                     this file
├── verify_crud.py                scripted run of the full CRUD cycle (55 checks)
├── students.db                   SQLite file, created on first run
└── app/
    ├── __init__.py
    ├── main.py                   FastAPI app, table creation, router wiring
    ├── database.py               engine, SessionLocal, Base, get_db()
    ├── models/
    │   ├── __init__.py
    │   └── student.py            Student ORM model
    ├── schemas/
    │   ├── __init__.py
    │   └── student.py            StudentCreate / Update / Patch / Response
    └── routers/
        ├── __init__.py
        └── students.py           the six endpoints + helpers
```

## Running it

```bash
cd exercises/student-crud
pip install fastapi uvicorn sqlalchemy pydantic
uvicorn app.main:app --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>

To run the scripted verification instead (needs `httpx`):

```bash
pip install httpx
python verify_crud.py
```

It drops and recreates the table, then walks create → duplicate → validation →
filter → read → replace → patch → delete, asserting on each response rather
than just printing it. A final section covers regressions for three bugs found
while building this:

1. `?major=` (blank value) was read as "major equals empty string" rather than
   "no filter", so it returned nothing.
2. `PATCH {"email": null}` crashed the email validator with a `TypeError` (500).
   The validator now tolerates `None`.
3. `PATCH {"name": null}` (and `grade_level`, `is_enrolled`) reached the
   database, tripped a NOT NULL constraint, and was reported as *409 — that
   email already exists*: wrong status and an actively misleading message.
   These are now 422 at the schema, and `commit_or_409()` only converts genuine
   uniqueness violations into 409s.

Last run: 55/55 passed. The API was also exercised over real HTTP under
uvicorn, and data plus the unique constraint were confirmed to survive an app
restart.

## Endpoints

| Method | Path                   | Success | Notes                                            |
|--------|------------------------|---------|--------------------------------------------------|
| POST   | `/students`            | 201     | 409 on duplicate email                            |
| GET    | `/students`            | 200     | filters: `grade_level`, `is_enrolled`, `major`, `min_gpa` |
| GET    | `/students/{id}`       | 200     | 404 if missing                                    |
| PUT    | `/students/{id}`       | 200     | full replacement — all fields required            |
| PATCH  | `/students/{id}`       | 200     | partial — only fields sent are changed            |
| DELETE | `/students/{id}`       | 204     | empty body                                        |

Filters are additive and each is applied only when supplied, so `/students`
returns everything and `?grade_level=9&is_enrolled=false` narrows on both.

Two of the guards are deliberately different from each other:

- `is_enrolled` and `min_gpa` are tested with `is not None`. Truthiness would
  silently drop `is_enrolled=false` and `min_gpa=0.0`, both of which are valid
  filters.
- `major` is tested with plain truthiness. A query string of `?major=` arrives
  as `""` rather than `None`, and filtering for students whose major is the
  empty string is never what the caller meant.

## Model

| Column        | Type         | Constraints                        |
|---------------|--------------|------------------------------------|
| `id`          | Integer      | primary key                        |
| `name`        | String(100)  | required                           |
| `email`       | String(200)  | required, unique, indexed          |
| `grade_level` | Integer      | required, 1–12 (enforced in schema) |
| `major`       | String(100)  | optional                           |
| `gpa`         | Float        | optional, 0.0–4.0 (enforced in schema) |
| `is_enrolled` | Boolean      | required, defaults to `True`       |
| `created_at`  | DateTime     | set by the DB on insert            |

Range checks live in the Pydantic schemas, not the DB — SQLite does not
enforce `CHECK` constraints the way a production Postgres schema would, and
catching a bad value at the request boundary produces a 422 with a useful
message instead of a 500.

## PUT vs PATCH

`StudentUpdate` (PUT) declares nullable fields as `Field(...)` — required to
send, allowed to be null. `StudentPatch` declares them as `Field(None)` —
optional to send. That single difference is what makes PUT a true replacement:

- PUT with `"gpa": null` clears the stored GPA.
- PUT omitting `gpa` is a 422, not a silent no-op.
- PATCH omitting `gpa` leaves the stored value alone, via
  `model_dump(exclude_unset=True)`.

An empty PATCH body returns 400 rather than a pointless 200.

## Error handling

- `get_student_or_404()` is the single lookup path for GET-one, PUT, PATCH and
  DELETE, so the 404 message is defined once.
- `ensure_email_available()` checks for a conflicting email before writing, and
  takes an `exclude_id` so re-sending a student's own email during PUT/PATCH is
  not treated as a conflict.
- `commit_or_409()` wraps `db.commit()` and converts an `IntegrityError` from
  the unique index into a 409. This is the safety net for two concurrent
  requests that both pass the availability check — without it, the loser
  surfaces as an unhandled 500.

## Known limitations

Recorded rather than silently left as surprises.

**HEAD** is not served on any route (405). FastAPI does not auto-generate HEAD
for a GET route; noted because it is a contract detail, not a defect.

**Input is not normalized.** A whitespace-only `name` passes `min_length=1`,
and an email is stored exactly as sent, so `" a@b.com "` keeps its spaces.
Stripping on write would be the fix; the exercise does not ask for it.

**Email uniqueness is case-sensitive,** because that is how SQLite's unique
index behaves by default. `Case@example.com` and `case@example.com` can both be
stored. Fixing it properly means normalizing to lowercase on write, or a
case-insensitive collation on the column; neither is in scope for this
exercise.

## Brief vs. stub reconciliation

The project brief and the GitHub stub files specify different things. I have
asked student support which takes precedence; pending an answer, this
implementation satisfies both wherever that is possible. The differences:

1. **Fields.** The brief specifies `grade_level`, `is_enrolled` and
   `created_at`; the stubs specify `major`. All four are implemented, with
   `major` optional so a brief-shaped request never has to mention it.
2. **List filters.** The brief asks for `grade_level` and `is_enrolled`; the
   stubs ask for `major` and `min_gpa`. All four are accepted.
3. **`StudentUpdate`.** The stub's schema comment says all fields are optional,
   but the stub's own PUT docstring says all fields are required. Required is
   the only reading under which PUT differs from PATCH, and it matches the
   brief's "full replacement", so fields are required.
4. **DELETE.** The brief specifies 204; the stub says to return a confirmation
   dict. These cannot both hold — a 204 is defined as having no body — so this
   follows the brief and returns 204 empty.
5. **`get_student_or_404()`.** Required by the brief, absent from the stubs.
   Implemented, and used by all four single-student endpoints.
6. **Route paths.** The router declares `""` rather than `"/"` for the
   collection routes so that with `prefix="/students"` the URLs are exactly
   `/students` and `/students/{id}`, with no trailing-slash redirect.

If support confirms the stub spec instead, items 1–2 narrow to `major` and
`min_gpa` only, and item 4 becomes a 200 with a message body. Nothing else
changes.
