# exercises/database-notes/app/main.py
# L5 — Database-Backed Notes API
#
# Run with: uvicorn app.main:app --reload  (from database-notes/ folder)

from fastapi import FastAPI
from app.database import Base, engine
from app.routers import notes

app = FastAPI(title="Notes API", version="1.0.0")

# Creates notes.db and the notes table if they don't exist yet, and leaves them
# alone if they do — which is why notes survive a restart.
#
# create_all only builds tables registered on Base.metadata, and a model
# registers itself when its module is imported. No explicit model import is
# needed here because importing the router pulls in app.models.note already.
Base.metadata.create_all(bind=engine)

app.include_router(notes.router, prefix="/notes", tags=["notes"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
