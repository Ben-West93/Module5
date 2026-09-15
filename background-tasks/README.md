# Background Tasks

**Module 5 — FastAPI Development, L9**

Adds post-response processing to the L8 JWT API: creating or deleting a
student now writes an audit line and sends a notification *after* the response
has gone back to the client, and report generation is a queued job the caller
polls rather than waits for.

---

## Which brief this implements

Two versions of the L9 brief were in circulation and this repo implements
both, because they are two halves of one lesson rather than competing designs.

| | Starter brief (`instructions.md`) | Second brief |
|---|---|---|
| Work simulated | report generation | activity logging + notifications |
| Side effect lands in | an in-memory dict | real files on disk |
| Status polling | `GET /reports/{id}`, pending → processing → complete | none |
| Tasks per request | one | two |
| Built on | a fresh standalone app | "your existing endpoints" |
| Task functions live in | `routers/reports.py` | `utils/notifications.py` |

Where they disagreed:

- **File layout** — resolved in favour of the second brief. Task functions
  live in a `utils/` package; routers contain endpoints and nothing else.
  `notifications.py` sits at the repo root as `utils/notifications.py`, the
  path the brief names. `reports.py` stays in `app/utils/` beside the L8
  helpers, since no brief asked for it.
- **Store vs. files** — kept both, because they do different jobs. The
  in-memory dict is the *state machine* that `GET /reports/{id}` reads. The
  log files are the *audit trail* the deliverable asks for. Neither
  substitutes for the other.
- **"Your existing endpoints"** — the existing API is the L8 JWT app
  (`exercises/jwt/`), copied here and extended. That is what gives
  `log_activity` a real `user_id` to record: `current_user.id` from the
  bearer token.

---

## What was added to the L8 app

Everything else in `app/` is L6–L8 code, unchanged except for the header
comment path.

**New files**

| File | Contents |
|---|---|
| `utils/notifications.py` | `log_activity()` and `send_notification()` — the two task functions from the brief |
| `utils/__init__.py` | makes the above importable as `utils.notifications` |
| `app/utils/reports.py` | the in-memory `report_store` and `generate_report()` |
| `app/schemas/report.py` | request/response models for the report and notification endpoints |
| `app/routers/reports.py` | `POST /reports`, `GET /reports/{id}`, `DELETE /reports/{id}`, `POST /reports/notifications` |
| `serve.py` | runs the app on a real uvicorn socket, shared by the two scripts below |
| `verify_background_tasks.py` | 101 checks covering the L9 behaviour |
| `probe_background.py` | 47 adversarial checks — concurrency, log forging, task isolation |
| `demo_session.py` | one realistic session; produces the log files in this folder |

**Changed files**

| File | Change |
|---|---|
| `app/routers/students.py` | `POST` schedules a log line and a notification; `DELETE` schedules a log line |
| `app/main.py` | includes the reports router at `/reports` |
| `verify_crud.py` | sets `NOTIFICATION_DELAY_SECONDS=0` and redirects `LOG_DIR` before importing the app — see "The TestClient trap" |
| `probe_auth.py` | same adjustment; its exact list of protected endpoints now includes the four reports routes |
| `requirements.txt` | no new packages; notes the second job `uvicorn` and `httpx` now do |

---

## Running

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload      # from this folder
```

Run it from this folder. Both `app` and `utils` are resolved off the working
directory, so starting the server from anywhere else fails on `import app`
before it ever reaches `utils`.

Then open `http://127.0.0.1:8000/docs`. Every endpoint below except the two
public `GET /students` routes needs a bearer token — register at
`POST /auth/register` and paste the `access_token` into Swagger's **Authorize**
box.

`activity_log.txt` and `notification_log.txt` are written to the process's
working directory. Set `LOG_DIR` to put them elsewhere.

| Variable | Default | Purpose |
|---|---|---|
| `LOG_DIR` | `.` | where the two log files are written |
| `NOTIFICATION_DELAY_SECONDS` | `2` | the simulated send delay |
| `REPORT_DURATION_SECONDS` | `2` | the simulated report build time |
| `SECRET_KEY` | dev fallback | JWT signing key (L8) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | token lifetime (L8) |

---

## Endpoints that schedule work

| Endpoint | Returns | Tasks queued, in order |
|---|---|---|
| `POST /students` | 201 + the student | `log_activity`, then `send_notification` |
| `DELETE /students/{id}` | 204, empty | `log_activity` |
| `POST /reports` | 202 + a `report_id` | `log_activity`, then `generate_report` |
| `DELETE /reports/{id}` | 204, empty | `log_activity` |
| `POST /reports/notifications` | 202 + `"queued"` | `log_activity`, then `send_notification` |
| `GET /reports/{id}` | 200 + current status | none |

`POST /reports` returns **202 Accepted**, not 201. 201 promises a resource
that now exists at a URL; what exists is a job, and the report does not exist
yet. 202 means "understood, queued, not done", which is what actually
happened.

---

## Log format

Pipe-delimited, one line per event, timestamps ISO-8601 in UTC with the
offset attached — `datetime.now()` would write a local time with no record of
which local it was.

```
activity_log.txt
2026-09-15T18:59:42.156746+00:00 | user_id=1 | created student 1 (ada@school.example)
2026-09-15T18:59:42.172368+00:00 | user_id=1 | deleted student 2 (Alan Turing, alan@school.example)

notification_log.txt
2026-09-15T18:59:44.157238+00:00 | to=ada@school.example | Welcome, Ada Lovelace. You are enrolled in grade 11.
```

Caller-supplied values are escaped before they are written: `|` becomes
`\|`, newlines become `\n`, and other control characters become `\xNN`. So a
field can never contain the delimiter or end the line, and a record always
splits into exactly three parts. See the log-forging note below for why.

The two files in this folder are the real output of `python demo_session.py`.
Reading them across, the evidence is in the timestamps: all five activity
lines land inside 22ms at `18:59:42`, all three notifications land two full
seconds later at `18:59:44`, and the client had every response back before the
first notification was written.

One quirk visible in the shipped logs: Alan is sent a welcome note at
`:44.163` having been deleted at `:42.172`. That is the demo creating and
deleting him five milliseconds apart, not a bug — but it is a fair
illustration of what fire-and-forget means. A queued task has no idea the
world moved on.

---

## Verification

```bash
python verify_background_tasks.py   # 101 checks — L9 behaviour
python probe_background.py          #  47 checks — L9 under attack and under load
python verify_crud.py               # L6–L8 CRUD and error handling
python probe_auth.py                # L8 auth
python demo_session.py              # regenerates the two log files
```

All five are green. `verify_background_tasks.py` establishes, in order:

1. Both logs start absent.
2. `POST /students` returns in **0.017s** while its notification sleeps
   **2.000s** — and, more directly than a timing number can show, the
   notification log provably contains nothing about that student at the
   instant the response arrives. The log line and the notification are then
   2.001s apart, in the order they were added.
3. The same POST through `TestClient` takes **2.007s** — see below.
4. `DELETE` logs the deletion despite returning a bare 204 `Response`.
5. An unenrolled student's notification does not claim they are enrolled.
6. A 409, a 422, a 401 and a 404 schedule nothing at all.
7. `POST /reports` returns in 0.009s; polling observes `processing` and then
   `complete`, with a result matching the type and row count that were sent.
8. Another user gets 404 — not 403 — for a report that is not theirs.
9. A report deleted mid-flight is not resurrected by its own task.
10. Recipients are normalized; invalid types, empty messages and `rows=0` are
    422s.
11. Every log line parses: three fields, an aware timestamp, chronological
    order, no password anywhere in either file.
12. The L7 error envelope covers the new endpoints, and all four are
    documented as protected in the OpenAPI schema.

### Adversarial probe

`probe_background.py` asks a different question from the suite above — not
"does it work?" but "what happens when someone is trying to break it, or when
twenty-four requests arrive at once?". It covers concurrency (P), hostile
input (Q), unicode and size limits (R), task isolation (S), the report store
under parallel load (T), and protocol edges (U).

It found a real vulnerability, since fixed.

**Log forging via a caller-controlled field.** The activity line embeds a
student's email address, and an email is whatever the client sent. Creating a
student with the address

```
inject\n2020-01-01T00:00:00+00:00 | user_id=999 | deleted every student@example.com
```

appended **two** lines to `activity_log.txt` — the second a forgery
attributing a deletion to a user who never made one. The same worked through
the student name into `notification_log.txt`, through a notification message,
and with a bare carriage return.

An audit trail the audited party can write to is not an audit trail.

The fix is `_sanitize()` in `utils/notifications.py`, applied at the sink
rather than at the schema. Task functions now hand `_append_record()` a list
of *fields* rather than a finished line — if they formatted the line
themselves, the writer could not tell a delimiter belonging to the format from
one that arrived inside a value. Because every write to either log goes
through that one function, the guarantee covers any future caller too.

Values are escaped rather than stripped: the attempt stays on the record,
where it belongs, instead of being silently deleted. Section Q asserts both —
that one line is written, and that `user_id=999` is still visible in it, safely
escaped.

Rejecting control characters at the Pydantic layer would also be reasonable
and is not mutually exclusive. It is not done here because it would change L6
schemas that L6's own tests cover, and because a schema rule only protects the
callers that go through that schema, while the sink protects all of them.

Two smaller findings, both fixed:

- `POST /reports` with `{}` was accepted but with no body at all was a 422,
  even though both requests ask for exactly the same report. The body is now
  optional, since every field has a default.
- Twenty-four concurrent creates produced twenty-four well-formed,
  non-interleaved, uniquely-attributed log lines — the `threading.Lock` in
  `_append_record()` is doing its job. Eight simultaneous creates of the same
  email still yield one 201 and seven 409s, with exactly one log line.

One open note, not fixed: `POST /reports/` (trailing slash) answers with a 307
redirect to `/reports`. Harmless for any normal client, but a client that
drops the body across a redirect would see a 422 instead of a 202.

### The TestClient trap

`TestClient` **cannot** show that a response beats its background tasks,
because it waits for them.

A Starlette `Response` runs `await self.background()` at the end of its ASGI
call, after the body has been sent. Over a real socket that is invisible — the
bytes are already gone. `TestClient` has no socket; it awaits the application
coroutine directly, so the call does not return until the tasks have finished.
Time a POST through it and you measure 2 seconds and go hunting for a bug that
is not there.

This is why `serve.py` exists and why the two scripts that care about timing
run against real uvicorn. `verify_background_tasks.py` measures the same
request both ways — 0.017s over a socket, 2.007s through `TestClient` — so the
difference is demonstrated rather than claimed.

It is also why `verify_crud.py` and `probe_auth.py` set
`NOTIFICATION_DELAY_SECONDS=0`. They create students about six times; left
alone, the L9 sleep turned a 3-second regression run into a 15-second one
while testing nothing about notifications.

---

## Notes on the implementation

**Tasks take primitives, never ORM objects or the session.** The request's
`Session` is closed by `get_db`'s `finally` block as the response is
finalised, and a task runs after that. Passing a `Student` would hand the task
a detached instance whose attribute access raises — in a worker thread, where
nothing is watching. `delete_student()` reads `name` and `email` into locals
*before* `db.delete()` for the same reason.

**Tasks are added after the commit succeeds.** `add_task()` only queues, so
adding them earlier would still run them — which means a duplicate-email 409
would leave a queued welcome email for a student that was never created.
Section 6 of the verification asserts that failed requests schedule nothing.

**`add_task` is a queue, not a fan-out.** Tasks run sequentially on one
worker thread in the order added, so a slow task delays every task behind it.
`log_activity` is always added first, so a 2-second notification cannot hold
up the audit line.

**Both task functions catch their own exceptions.** By the time a task runs
the response is sent: there is no status code left to change and no way to
tell the caller anything. An escaping exception propagates through Starlette
after the fact. Catching means a full disk costs one log line instead of a
request that already "succeeded" turning into a server-side crash.
`generate_report` sets `status: "failed"` rather than leaving a record stuck
at `processing`, which a poller could not distinguish from work still running.

**Returning a `Response` directly does not discard queued tasks.** FastAPI
checks whether the returned response carries a `background` attribute and,
finding none, attaches the `BackgroundTasks` it injected. Worth stating
because the opposite is a reasonable assumption, and because a 204 has no body
in which a silently-dropped task would ever show up.

**Everything caller-supplied is escaped on the way into a log.** See the
log-forging finding above. `_append_record()` is the single choke point, and
it takes fields rather than a formatted line so that it can tell the
delimiter from the data.

**Someone else's report is a 404, not a 403.** A 403 confirms the id is real
and belongs to somebody — enough to enumerate reports by walking ids and
watching the status code. Same reasoning as L8's single message for a bad
username and a bad password.

**`time.sleep()` is correct in these tasks, not a mistake.** They are
registered as ordinary `def` functions, so Starlette runs them in a worker
thread where blocking affects only that thread. Making the module `async`
would move the blocking onto the event loop.

---

## Limitations

- **`report_store` is a module-level dict.** A restart loses every report,
  and a second uvicorn worker would keep its own copy and return 404 for
  reports the first one holds. Real code would use a table or Redis. This is
  a property of the design, not an oversight.
- **No retries and no dead-letter queue.** A notification that fails is
  logged and gone. Real delivery needs a broker (Celery, RQ, arq); FastAPI's
  `BackgroundTasks` is for work that is cheap to lose.
- **Nothing prunes `report_store`.** Completed reports sit there until
  deleted or the process restarts.
- **Notifications are simulated.** `time.sleep()` and a file append stand in
  for a mail provider.
- **No rate limiting on `/auth/login`** — carried over from L8, still out of
  scope.
