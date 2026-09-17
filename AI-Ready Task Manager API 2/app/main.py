# project/starter/app/main.py
# Module 5 Project — AI-Ready Task Manager API
#
# Run with: uvicorn app.main:app --reload  (from the project root folder)
# Docs at:  http://127.0.0.1:8000/docs

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv
import logging
import os

from app.database import Base, engine
from app.auth import AppException
from app.models.user import User  # noqa: F401 — registers tables on Base.metadata
from app.models.task import Task  # noqa: F401
from app.routers import auth, tasks

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("task_manager")

tags_metadata = [
    {"name": "health", "description": "Check that the API is running."},
    {"name": "auth", "description": "Register, log in to get a JWT, and view the current user."},
    {"name": "tasks", "description": "Create, read, update, and delete your own tasks, plus AI suggestions. Requires a Bearer token."},
]

app = FastAPI(
    openapi_tags=tags_metadata,
    title="AI-Ready Task Manager",
    description="A task management API with JWT auth and an AI suggestion endpoint.",
    version="1.0.0",
)

# CORS: explicit origins only (never "*"). Set CORS_ORIGINS in .env as a comma-separated list.
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------- Custom error handling ----------

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """Handle all custom exceptions (NotFoundException, DuplicateException, ...)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error": exc.error},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return validation errors in a consistent, readable shape."""
    errors = [
        {
            "field": ".".join(str(loc) for loc in err["loc"] if loc != "body"),
            "message": err["msg"],
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": "Validation error", "errors": errors},
    )


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(request: Request, exc: SQLAlchemyError):
    """Never leak raw database errors to clients."""
    logger.exception("Database error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "A database error occurred"},
    )


# ---------- Database + routers ----------

Base.metadata.create_all(bind=engine)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])


@app.get("/", tags=["health"])
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "message": "Task Manager API is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
