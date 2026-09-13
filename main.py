# exercises/library-search/app/main.py
# L4 — Library Search API
#
# Preferred entry point, run from the library-search/ folder:
#     uvicorn app.main:app --reload
#
# The imports below are absolute ("app.routers"), so Python needs the project
# root -- library-search/ -- on sys.path to resolve them.

import sys
from pathlib import Path

# Running `python app/main.py` directly puts app/ on sys.path instead of the
# project root, so the `from app.routers import books` line below would die with
# ModuleNotFoundError: No module named 'app'. Prepending the project root makes
# both entry points work. Under `uvicorn app.main:app` the root is already on
# the path, so this is a no-op there.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI  # noqa: E402  (must follow the sys.path fix above)

from app.routers import books  # noqa: E402

app = FastAPI(
    title="Library Search API",
    description="Path and query parameter practice: filtering, sorting, pagination.",
    version="1.0.0",
)

# prefix="/books" mounts every route in the books router one level down, so the
# router's "/{book_id}" becomes "/books/{book_id}". Keeping the prefix here
# rather than repeating "/books" in each decorator means the whole resource can
# be remounted elsewhere by editing this one line.
#
# tags=["books"] groups these endpoints under one heading in the /docs page.
app.include_router(books.router, prefix="/books", tags=["books"])


@app.get("/", tags=["root"])
def read_root():
    """Sanity-check endpoint. The interactive docs live at /docs."""
    return {"message": "Library Search API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
