# exercises/student-crud/app/main.py
# L6 — Student CRUD API
#
# Run with: uvicorn app.main:app --reload  (from student-crud/ folder)

from fastapi import FastAPI

from app.database import Base, engine
from app.models.student import Student  # noqa: F401 — registers the table on Base
from app.routers import students

app = FastAPI(
    title="Student CRUD API",
    description="L6 — full six-endpoint CRUD over a SQLite-backed students table.",
    version="1.0.0",
)

# Importing the model above is what puts `students` into Base.metadata;
# create_all() then issues CREATE TABLE IF NOT EXISTS for it.
Base.metadata.create_all(bind=engine)

# The router declares its routes as "" and "/{student_id}", so the prefix
# here produces exactly /students and /students/{id} — no trailing-slash
# redirect on the collection endpoints.
app.include_router(students.router, prefix="/students", tags=["students"])


@app.get("/", tags=["meta"])
def read_root():
    return {"message": "Student CRUD API — see /docs for the interactive UI"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
