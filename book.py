# exercises/library-search/app/schemas/book.py
# L4 — Library Search — Book schema and enums

from enum import Enum

from pydantic import BaseModel, Field


class Genre(str, Enum):
    """
    Allowed genre values.

    Inheriting from `str` means FastAPI serializes these as plain strings and
    accepts them straight from the URL. Anything not listed here is rejected
    with a 422 before the handler ever runs -- that is the whole point of using
    an Enum instead of a plain `str` parameter.
    """

    fiction = "fiction"
    nonfiction = "nonfiction"
    science = "science"
    history = "history"


class SortField(str, Enum):
    """
    Restricts what values are accepted for the sort_by query parameter.

    Whitelisting sort fields this way also closes a small injection surface:
    the handler can only ever sort by a key that actually exists on a book.
    """

    title = "title"
    author = "author"
    rating = "rating"
    year = "year"


class Book(BaseModel):
    """
    Represents a book in the library catalog.

    These Field() constraints validate the RESPONSE too, not just input. If a
    dict in BOOKS is ever malformed -- a rating of 9.9, a blank title -- FastAPI
    raises a 500 instead of quietly serving bad data. That is a useful catch-all
    once the data comes from a real database.
    """

    id: int = Field(..., gt=0, description="Unique book identifier")
    title: str = Field(..., min_length=1, max_length=200, description="Book title")
    author: str = Field(..., min_length=1, max_length=100, description="Author name")
    # The exercise spec lists this field as `str`. The Genre enum still guards
    # the query and path PARAMETERS in the router -- that is where the brief
    # asks for it -- but the model field itself stays a plain string, as
    # specified.
    genre: str = Field(..., min_length=1, description="Genre name")
    rating: float = Field(..., ge=0.0, le=5.0, description="Average rating, 0-5")
    year: int = Field(..., gt=0, le=2100, description="Year of publication")
    available: bool = Field(True, description="Currently on the shelf")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 1,
                "title": "Nineteen Eighty-Four",
                "author": "George Orwell",
                "genre": "fiction",
                "rating": 4.7,
                "year": 1949,
                "available": True,
            }
        }
    }
