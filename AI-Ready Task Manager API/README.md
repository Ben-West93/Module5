# AI-Ready Task Manager API (Module 5 Final Project)

A task management REST API built with FastAPI, featuring:
- JWT authentication (register, login, protected endpoints)
- Task CRUD scoped to the authenticated user (other users' tasks return 404)
- A `/tasks/{id}/suggest` placeholder endpoint for future AI integration (returns a mock suggestion)
- SQLAlchemy 2.0 database with a one-to-many user → task relationship
- Custom exception classes in `auth.py` (`NotFoundException`, `DuplicateException`, `InvalidUpdateException`) with global handlers producing a consistent JSON error format
- A background task that logs each task creation
- CORS restricted to explicit origins configured via `.env`
- A pytest suite of 15 passing tests using an in-memory SQLite database

## Setup

```bash
# From the project root (the folder containing app/ and tests/)
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows

pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env — then edit SECRET_KEY

uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

`bcrypt` is pinned to 4.0.1 because newer bcrypt releases are incompatible with `passlib` 1.7.4.

## Environment variables (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | `dev-secret-key-change-in-production` | Signs JWTs |
| `DATABASE_URL` | `sqlite:///./taskmanager.db` | SQLAlchemy connection string |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Token lifetime |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | No | Health check |
| POST | `/auth/register` | No | Create a user (JSON: username, email, password) → 201 |
| POST | `/auth/token` | No | Log in (form data: username, password) → JWT |
| GET | `/auth/me` | Yes | Current user |
| POST | `/tasks/` | Yes | Create a task → 201 |
| GET | `/tasks/` | Yes | List your tasks |
| GET | `/tasks/{task_id}` | Yes | Get one of your tasks |
| PATCH | `/tasks/{task_id}` | Yes | Partially update a task |
| DELETE | `/tasks/{task_id}` | Yes | Delete a task |
| GET | `/tasks/{task_id}/suggest` | Yes | Mock AI suggestion for a task |

Authenticated requests send `Authorization: Bearer <token>`. In `/docs`, click **Authorize** and paste the token from `/auth/token`.

## Error responses

Custom exceptions return `{"detail": "<message>", "error": "<code>"}`, where `error` is `not_found`, `duplicate`, or `invalid_update`.

- `400` duplicate username/email, empty or invalid PATCH body
- `401` missing, invalid, or expired token; wrong login credentials
- `404` task does not exist or belongs to another user
- `422` validation error, returned as `{"detail": "Validation error", "errors": [{"field": ..., "message": ...}]}`
- `500` database error, returned as `{"detail": "A database error occurred"}` (details are logged, not exposed)

## Running tests

```bash
pytest tests/ -v
```

Run pytest from the project root. `tests/conftest.py` adds the project root to the import path.

## Project structure

```
app/
├── main.py          — FastAPI app with CORS
├── database.py      — SQLAlchemy engine + get_db
├── auth.py          — JWT utilities (also holds the custom exception classes)
├── models/
│   ├── user.py      — User ORM model
│   └── task.py      — Task ORM model (FK to User)
├── schemas/
│   ├── user.py      — UserCreate, UserResponse, TokenResponse
│   └── task.py      — TaskCreate, TaskPatch, TaskResponse
└── routers/
    ├── auth.py      — /auth/register, /auth/token, /auth/me
    └── tasks.py     — /tasks CRUD + /tasks/{id}/suggest
tests/
├── conftest.py      — test DB fixture
└── test_tasks.py    — test cases
```

The project root also contains `README.md`, `requirements.txt`, `.env.example`, and `.gitignore`.

## Implementation checklist

- [x] database.py — create_engine, SessionLocal, get_db
- [x] models/user.py — User model with tasks relationship
- [x] models/task.py — Task model with owner_id FK
- [x] schemas/user.py — UserCreate, UserResponse, TokenResponse
- [x] schemas/task.py — TaskCreate, TaskPatch, TaskResponse
- [x] auth.py — hash_password, verify_password, create_access_token, get_current_user
- [x] routers/auth.py — register, login, me
- [x] routers/tasks.py — CRUD + suggest endpoint
- [x] main.py — wire everything together
- [x] tests/conftest.py — test DB override
- [x] tests/test_tasks.py — 5+ passing tests (15 total)
