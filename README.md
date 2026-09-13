# Library Search API — L4

FastAPI router exercise: path parameters, query parameters, and validation.

## Run

```bash
cd library-search
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

Run from `library-search/`, not from `app/` — the imports are absolute.

## Test

```bash
python test_api.py
```

57 checks covering happy paths, validation failures, boundaries, and edge cases.
Exits non-zero on any failure.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/books` | Search and filter the catalog |
| GET | `/books/{book_id}` | One book by ID |
| GET | `/books/genre/{genre}` | All books in one genre |

### `GET /books` query parameters

| Param | Type | Default | Constraint |
|---|---|---|---|
| `genre` | enum | none | fiction, nonfiction, science, history |
| `min_year` | int | none | > 0 |
| `max_year` | int | none | > 0, and >= `min_year` |
| `search` | str | none | min 1 char, matched against title |
| `min_rating` | float | 0.0 | 0.0–5.0 |
| `sort_by` | enum | title | title, author, rating, year |
| `skip` | int | 0 | >= 0 |
| `limit` | int | 10 | 1–25 |

## Status codes

| Code | Raised by | Meaning |
|---|---|---|
| 422 | FastAPI, automatically | Malformed input — bad type, bad enum, out of bounds |
| 400 | Handler | Well-formed but incoherent — `min_year > max_year`, blank search |
| 404 | Handler | Valid input, no such record |

`/books/-5` is a 422; `/books/999` is a 404. Different kinds of wrong.

## Notes

- **Route order matters.** `/{book_id}` is greedy and will swallow the literal
  `genre` segment if declared first. It is last in `books.py` on purpose.
- **Genre casing.** Enum matching is case-sensitive, so `?genre=FICTION` returns
  422. This is standard FastAPI enum behavior and is left as-is: the brief calls
  for an Enum, and an Enum's job is to restrict what's accepted.
- **Trailing slash.** `/books` and `/books/` both return 200 directly, no 307.
