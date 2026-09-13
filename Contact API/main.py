# exercises/validated-contacts/app/main.py
# L3 — Validated Contact Book
#
# Run with: uvicorn app.main:app --reload  (from validated-contacts/ folder)

from fastapi import FastAPI

from app.routers import contacts

app = FastAPI(title="Contact Book API")

app.include_router(contacts.router, prefix="/contacts", tags=["contacts"])


@app.get("/")
def root():
    return {"message": "Contact Book API — see /docs for Swagger UI"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
