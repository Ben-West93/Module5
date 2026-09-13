# exercises/library-search/app/routers/books.py
# L4 — Library Search endpoints
#
# ---------------------------------------------------------------------------
# KEY CONCEPT: Path vs Query parameters
#
# PATH params (/books/{id}) are used when:
#   - The value uniquely identifies a resource
#   - The resource doesn't "make sense" without it
#   - It forms part of a clean, RESTful URL hierarchy
#
# QUERY params (/books?genre=fiction) are used when:
#   - Filtering, sorting, or paginating a collection
#   - The value is optional or has a default
#   - Multiple independent filters make sense together
#
# Note that `genre` appears as BOTH in this file, and that is deliberate:
#   /books?genre=fiction        -> genre narrows an existing collection
#   /books/genre/fiction        -> genre names the collection being requested
# ---------------------------------------------------------------------------
#
# ---------------------------------------------------------------------------
# KEY CONCEPT: which status code, and who raises it
#
#   422  FastAPI, automatically. The input is malformed -- wrong type, bad enum
#        member, outside a ge/le/gt bound. Your handler never runs.
#   400  You, via HTTPException. The input is individually well-formed but the
#        COMBINATION is nonsense (min_year > max_year). Query() cannot catch
#        this because it validates each parameter in isolation.
#   404  You, via HTTPException. The input is perfectly valid; the thing just
#        isn't there.
#
# Collapsing these into one code is the most common way to lose points on this
# exercise. "-5" and "999" are different kinds of wrong.
# ---------------------------------------------------------------------------

from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from app.schemas.book import Book, Genre, SortField

router = APIRouter()

# Sample in-memory library — 10 books across all four genres.
# The data is deliberately varied so the filters have something to bite on:
# years span 1945-2018, ratings span 4.0-4.8, two books share an author, and
# three books are unavailable. Uniform sample data makes broken filters look
# like working ones.
BOOKS: list[dict] = [
    {"id": 1, "title": "Nineteen Eighty-Four", "author": "George Orwell",
     "genre": "fiction", "rating": 4.7, "year": 1949, "available": True},
    {"id": 2, "title": "Animal Farm", "author": "George Orwell",
     "genre": "fiction", "rating": 4.3, "year": 1945, "available": False},
    {"id": 3, "title": "Sapiens", "author": "Yuval Noah Harari",
     "genre": "history", "rating": 4.5, "year": 2011, "available": True},
    {"id": 4, "title": "A Brief History of Time", "author": "Stephen Hawking",
     "genre": "science", "rating": 4.6, "year": 1988, "available": True},
    {"id": 5, "title": "The Selfish Gene", "author": "Richard Dawkins",
     "genre": "science", "rating": 4.2, "year": 1976, "available": True},
    {"id": 6, "title": "Educated", "author": "Tara Westover",
     "genre": "nonfiction", "rating": 4.4, "year": 2018, "available": False},
    {"id": 7, "title": "The Immortal Life of Henrietta Lacks", "author": "Rebecca Skloot",
     "genre": "nonfiction", "rating": 4.1, "year": 2010, "available": True},
    {"id": 8, "title": "Guns, Germs, and Steel", "author": "Jared Diamond",
     "genre": "history", "rating": 4.0, "year": 1997, "available": True},
    {"id": 9, "title": "The Road", "author": "Cormac McCarthy",
     "genre": "fiction", "rating": 4.5, "year": 2006, "available": True},
    {"id": 10, "title": "Cosmos", "author": "Carl Sagan",
     "genre": "science", "rating": 4.8, "year": 1980, "available": False},
]

MAX_LIMIT = 25  # Hard cap on page size, per the spec.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sort_books(books: list[dict], sort_by: SortField) -> list[dict]:
    """
    Sort by the requested field, with title as a tiebreaker.

    The secondary key matters for pagination: if two science books share a
    year and the order between them can drift, a client paging through with
    skip/limit can see one book twice and miss another entirely. Sorting on a
    unique-enough secondary key makes the ordering total and stable.
    """
    key = sort_by.value

    if key == "title":
        return sorted(books, key=lambda b: b["title"].lower())
    if key == "author":
        return sorted(books, key=lambda b: (b["author"].lower(), b["title"].lower()))
    # rating and year are numeric; fall back to title for ties.
    return sorted(books, key=lambda b: (b[key], b["title"].lower()))


# ---------------------------------------------------------------------------
# ROUTE ORDER IS LOAD-BEARING.
#
# FastAPI matches routes top to bottom and takes the first hit. "/{book_id}"
# is a wildcard that will happily swallow the literal string "genre", then fail
# to parse it as an int and return 422. So "/genre/{genre}" MUST be declared
# above it. Moving get_book() to the top of this file silently breaks
# /books/genre/{genre}.
# ---------------------------------------------------------------------------


@router.get("", response_model=list[Book])          # /books
@router.get("/", response_model=list[Book],         # /books/
            include_in_schema=False)
def search_books(
    genre: Optional[Genre] = Query(
        None, description="Filter by genre (fiction, nonfiction, science, history)"
    ),
    min_year: Optional[int] = Query(
        None, gt=0, description="Only books published in or after this year"
    ),
    max_year: Optional[int] = Query(
        None, gt=0, description="Only books published in or before this year"
    ),
    search: Optional[str] = Query(
        None, min_length=1, description="Case-insensitive substring match on title"
    ),
    min_rating: float = Query(0.0, ge=0.0, le=5.0, description="Minimum rating"),
    sort_by: SortField = Query(SortField.title, description="Sort field"),
    skip: int = Query(0, ge=0, description="Records to skip (pagination offset)"),
    limit: int = Query(
        10, ge=1, le=MAX_LIMIT, description=f"Max results, capped at {MAX_LIMIT}"
    ),
):
    """
    Search and filter the catalog.

    Every parameter is optional and the filters stack, so
    `?genre=science&min_year=1980&sort_by=year&limit=2` narrows on all three.

    Registered at both "" and "/" on purpose. With only "/" declared, a request
    to `/books` gets a 307 redirect to `/books/` -- fine in a browser, but it
    trips up curl without -L and any HTTP client configured not to follow
    redirects. Declaring both means each returns 200 directly. The "/" variant
    is hidden from the docs so the endpoint isn't listed twice.
    """
    # Cross-field check. Query() validated each bound in isolation and both
    # passed, so this contradiction can only be caught here. 400, not 422:
    # the request is well-formed, it just asks for something incoherent.
    if min_year is not None and max_year is not None and min_year > max_year:
        raise HTTPException(
            status_code=400,
            detail=f"min_year ({min_year}) cannot be greater than max_year ({max_year}).",
        )

    results = BOOKS

    # `genre` is already a validated Genre member by this point -- FastAPI
    # rejected anything else with a 422 before the handler ran. Enum matching
    # is case-sensitive, so "FICTION" never reaches here either. No .lower()
    # needed on the incoming value; compare against the canonical member.
    if genre is not None:
        results = [b for b in results if b["genre"] == genre.value]

    if min_year is not None:
        results = [b for b in results if b["year"] >= min_year]

    if max_year is not None:
        results = [b for b in results if b["year"] <= max_year]

    if search is not None:
        # min_length=1 accepts a single space, and " " is a substring of nearly
        # every title -- so an all-whitespace search would silently return the
        # whole catalog while looking like a real query. Strip first, then
        # reject what's left if it's empty.
        needle = search.strip().lower()
        if not needle:
            raise HTTPException(
                status_code=400,
                detail="search cannot be empty or whitespace only.",
            )
        # Stripping also means "?search= cosmos " matches, which is what a user
        # who copy-pasted a title with trailing spaces expects.
        results = [b for b in results if needle in b["title"].lower()]

    results = [b for b in results if b["rating"] >= min_rating]
    results = _sort_books(results, sort_by)

    # A skip past the end yields [] rather than an error. That is correct: an
    # empty page is a valid answer to "show me records 500-510", not a failure.
    return results[skip : skip + limit]


@router.get("/genre/{genre}", response_model=list[Book])
def get_books_by_genre(
    genre: Genre = Path(..., description="Genre to list"),
    sort_by: SortField = Query(SortField.title, description="Sort by title or year"),
):
    """
    All books in one genre.

    `genre` is a PATH param here because it identifies which collection is
    being requested, rather than filtering one you already have. An unknown
    genre is a 422 from the enum, not a 404 -- "poetry" isn't a missing
    collection, it's not a valid genre at all.

    A valid genre with no books (none here, but easy to imagine) correctly
    returns an empty list, not a 404.
    """
    matches = [b for b in BOOKS if b["genre"] == genre.value]
    return _sort_books(matches, sort_by)


@router.get("/{book_id}", response_model=Book)
def get_book(book_id: int = Path(..., gt=0, description="Book ID, must be > 0")):
    """
    One book by ID.

    `gt=0` rather than `ge=0` because 0 is not a valid ID either -- IDs start at
    1. That distinction is why /books/0 returns 422 and not 404.

    Declared LAST because it is the greediest route in this file. See the
    ordering note above.
    """
    for book in BOOKS:
        if book["id"] == book_id:
            return book

    raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
