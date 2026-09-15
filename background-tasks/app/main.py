# exercises/background-tasks/app/main.py
# L6/L7/L8/L9 — Background Tasks
#
# Run with: uvicorn app.main:app --reload  (from background-tasks/ folder)

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import Base, engine
from app.exceptions import (
    AppException,
    BadRequestException,
    DuplicateException,
    NotFoundException,
    UnauthorizedException,
)
from app.models.student import Student  # noqa: F401 — registers the table on Base
from app.models.user import User  # noqa: F401 — registers the users table on Base
from app.routers import auth, reports, students, users

logger = logging.getLogger("jwt_api")

app = FastAPI(
    title="Background Tasks API",
    description=(
        "Student CRUD over SQLite with JWT auth, centralized error handling, "
        "and post-response background work: activity logging, notifications, "
        "and pollable report generation."
    ),
    version="3.0.0",
)

# Importing the model above is what puts `students` into Base.metadata;
# create_all() then issues CREATE TABLE IF NOT EXISTS for it.
Base.metadata.create_all(bind=engine)


# --------------------------------------------------------------------------
# Error response format
# --------------------------------------------------------------------------
#
# Every error this API returns has the same three top-level keys:
#
#     {"error": "NotFound", "detail": "Student 9999 not found", "status_code": 404}
#
# `error` is a stable label a client can branch on; `detail` is for humans and
# may be reworded at any time; `status_code` repeats the HTTP status so the
# body is self-describing when it gets logged away from its response.
#
# Schema-validation failures add a fourth key, `errors`, holding FastAPI's
# per-field breakdown. That is additive — the three keys above are still there
# — so a client written against the common shape keeps working.
#
# None of these handlers can help a background task. By the time a task runs,
# the status code and body are already sent; a task that fails has to handle
# itself, which is why both functions in utils/notifications.py catch and log
# rather than raise.


def build_error_response(
    status_code: int,
    error: str,
    detail: str,
    headers: dict[str, str] | None = None,
    **extra,
) -> JSONResponse:
    """Single place where the error body is assembled.

    Every handler below goes through here. If the shape ever needs to change,
    it changes once rather than in five handlers that have quietly drifted
    apart.
    """
    payload = {"error": error, "detail": detail, "status_code": status_code}
    payload.update(extra)
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


# --------------------------------------------------------------------------
# Handlers for the custom exceptions
# --------------------------------------------------------------------------


@app.exception_handler(NotFoundException)
def handle_not_found(request: Request, exc: NotFoundException) -> JSONResponse:
    return build_error_response(exc.status_code, exc.error, exc.detail)


@app.exception_handler(DuplicateException)
def handle_duplicate(request: Request, exc: DuplicateException) -> JSONResponse:
    return build_error_response(exc.status_code, exc.error, exc.detail)


@app.exception_handler(BadRequestException)
def handle_bad_request(request: Request, exc: BadRequestException) -> JSONResponse:
    return build_error_response(exc.status_code, exc.error, exc.detail)


@app.exception_handler(UnauthorizedException)
def handle_unauthorized(request: Request, exc: UnauthorizedException) -> JSONResponse:
    # Passes exc.headers so the 401 carries WWW-Authenticate: Bearer.
    return build_error_response(exc.status_code, exc.error, exc.detail, headers=exc.headers)


# --------------------------------------------------------------------------
# Handlers that keep the format consistent across the whole API
# --------------------------------------------------------------------------
#
# The three handlers above only cover errors this code raises on purpose.
# Without the ones below, a 422 from schema validation and a 405 from a wrong
# HTTP method would still come back in FastAPI's default `{"detail": ...}`
# shape, so the API would return two different error formats depending on
# which layer failed. These close that gap.


@app.exception_handler(AppException)
def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
    """Catch-all for any future AppException subclass that has no handler."""
    return build_error_response(exc.status_code, exc.error, exc.detail, headers=exc.headers)


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Schema-validation failures (FastAPI's automatic 422)."""
    errors = exc.errors()
    count = len(errors)
    noun = "error" if count == 1 else "errors"
    return build_error_response(
        422,
        "ValidationError",
        f"Request failed validation with {count} {noun}",
        # Drop the "ctx" key: Pydantic attaches the original exception object
        # there, which is not JSON-serializable.
        errors=[{k: v for k, v in item.items() if k != "ctx"} for item in errors],
    )


@app.exception_handler(StarletteHTTPException)
def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Framework-level errors such as 404 on an unknown path or 405."""
    labels = {400: "BadRequest", 404: "NotFound", 405: "MethodNotAllowed", 409: "Duplicate"}
    return build_error_response(
        exc.status_code,
        labels.get(exc.status_code, "Error"),
        str(exc.detail),
    )


@app.exception_handler(Exception)
def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Anything not raised on purpose — an actual bug in this code.

    Without this, an unhandled exception returns Starlette's plain-text
    "Internal Server Error", which is the one response that would not match
    the format every other error uses.

    Two deliberate choices here:

    - The body says nothing specific. A traceback or a raw exception message
      in an HTTP response leaks file paths and internals to the caller.
    - The traceback is logged instead, and Starlette re-raises after this
      handler runs, so `--reload` still prints it in the terminal. The
      response is sanitized; the developer's view is not.
    """
    # exc_info=exc, not logger.exception(): this handler runs outside the
    # `except` block, so sys.exc_info() is empty and logger.exception() would
    # log the useless line "NoneType: None" instead of the traceback.
    logger.error(
        "Unhandled error on %s %s", request.method, request.url.path, exc_info=exc
    )
    return build_error_response(
        500,
        "InternalServerError",
        "An unexpected error occurred. Please try again or contact support.",
    )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

# The routers declare their collection routes as "" and their item routes as
# "/{id}", so the prefix here produces exactly /students and /students/{id} —
# no trailing-slash redirect on the collection endpoints.
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(students.router, prefix="/students", tags=["students"])
app.include_router(reports.router, prefix="/reports", tags=["reports"])


@app.get("/", tags=["meta"])
def read_root():
    return {"message": "Background Tasks API — see /docs for the interactive UI"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
