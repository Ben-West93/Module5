# exercises/auth-system/app/main.py
# L8 — Auth System
#
# Run with: uvicorn app.main:app --reload  (from auth-system/ folder)

from fastapi import FastAPI

from app.database import Base, engine
from app.models import user as _user_model  # noqa: F401 - registers User on Base
from app.routers import auth

app = FastAPI(title="Auth System API")

# The model module must be imported before this runs, otherwise Base has no
# tables registered and the users table is never created.
Base.metadata.create_all(bind=engine)

app.include_router(auth.router, prefix="/auth", tags=["auth"])


@app.get("/")
def root():
    return {"status": "ok", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
