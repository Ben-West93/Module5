"""
Validation checks for the Library Search API.

Plain script, not a pytest module -- run it directly:
    python test_api.py

Exits 1 if anything fails, so it works as a pre-submit gate.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

passed = failed = 0


def check(label, response, expected_status, predicate=None):
    """Assert a status code, and optionally a property of the response body."""
    global passed, failed
    ok = response.status_code == expected_status
    detail = f"got {response.status_code}, expected {expected_status}"

    if ok and predicate is not None:
        body = response.json()
        ok = predicate(body)
        if not ok:
            detail = f"status fine but body check failed: {body}"

    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} -- {detail}")


print("\n--- Happy paths ---")
check("GET /books returns a list", client.get("/books"), 200,
      lambda b: isinstance(b, list) and len(b) > 0)
check("genre=science returns only science", client.get("/books?genre=science"), 200,
      lambda b: all(x["genre"] == "science" for x in b))
check("min_year=2000 filters old books", client.get("/books?min_year=2000"), 200,
      lambda b: all(x["year"] >= 2000 for x in b))
check("max_year=1980 filters new books", client.get("/books?max_year=1980"), 200,
      lambda b: all(x["year"] <= 1980 for x in b))
check("search=cosmos matches title", client.get("/books?search=cosmos"), 200,
      lambda b: len(b) == 1 and b[0]["title"] == "Cosmos")
check("filters stack (science + 1980+)", client.get("/books?genre=science&min_year=1980"), 200,
      lambda b: all(x["genre"] == "science" and x["year"] >= 1980 for x in b))
check("sort_by=year is ascending", client.get("/books?sort_by=year&limit=25"), 200,
      lambda b: [x["year"] for x in b] == sorted(x["year"] for x in b))
check("skip=2&limit=3 pages correctly", client.get("/books?skip=2&limit=3"), 200,
      lambda b: len(b) == 3)
check("GET /books/3 returns Sapiens", client.get("/books/3"), 200,
      lambda b: b["title"] == "Sapiens")
check("GET /books/genre/history", client.get("/books/genre/history"), 200,
      lambda b: all(x["genre"] == "history" for x in b))
check("genre path + sort_by=year", client.get("/books/genre/fiction?sort_by=year"), 200,
      lambda b: [x["year"] for x in b] == sorted(x["year"] for x in b))

print("\n--- Invalid enum values (422) ---")
check("unknown genre, query param", client.get("/books?genre=poetry"), 422)
check("unknown genre, path param", client.get("/books/genre/poetry"), 422)
check("unknown sort_by", client.get("/books?sort_by=publisher"), 422)
check("empty genre value", client.get("/books?genre="), 422)
check("numeric genre value", client.get("/books?genre=123"), 422)

print("\n--- Enum matching is case-sensitive (standard FastAPI behavior) ---")
check("genre=FICTION rejected", client.get("/books?genre=FICTION"), 422)
check("genre=Science rejected", client.get("/books?genre=Science"), 422)
check("path /genre/History rejected", client.get("/books/genre/History"), 422)
check("sort_by=YEAR rejected", client.get("/books?sort_by=YEAR"), 422)
check("exact lowercase still works", client.get("/books?genre=fiction"), 200,
      lambda b: len(b) == 3 and all(x["genre"] == "fiction" for x in b))

print("\n--- Negative / zero IDs (422) ---")
check("negative book_id", client.get("/books/-5"), 422)
check("zero book_id", client.get("/books/0"), 422)
check("min_year=0 (must be > 0)", client.get("/books?min_year=0"), 422)
check("negative min_year", client.get("/books?min_year=-100"), 422)
check("negative max_year", client.get("/books?max_year=-5"), 422)
check("negative skip", client.get("/books?skip=-1"), 422)
check("non-integer book_id", client.get("/books/abc"), 422)
check("float book_id", client.get("/books/1.5"), 422)

print("\n--- Limit cap of 25 (422) ---")
check("limit=50 above cap", client.get("/books?limit=50"), 422)
check("limit=26 one past cap", client.get("/books?limit=26"), 422)
check("limit=0 below floor", client.get("/books?limit=0"), 422)
check("negative limit", client.get("/books?limit=-1"), 422)
check("non-integer limit", client.get("/books?limit=many"), 422)

print("\n--- Other invalid input (422) ---")
check("empty search string", client.get("/books?search="), 422)
check("min_rating above 5", client.get("/books?min_rating=6"), 422)
check("negative min_rating", client.get("/books?min_rating=-1"), 422)

print("\n--- Application errors ---")
check("unknown book -> 404", client.get("/books/999"), 404)
check("min_year > max_year -> 400", client.get("/books?min_year=2020&max_year=1990"), 400)
check("whitespace-only search -> 400", client.get("/books?search=%20%20"), 400)

print("\n--- Edge cases ---")
check("/books resolves without a redirect", client.get("/books", follow_redirects=False), 200)
check("/books/ also resolves directly", client.get("/books/", follow_redirects=False), 200)
check("search is trimmed before matching", client.get("/books?search=%20cosmos%20"), 200,
      lambda b: len(b) == 1 and b[0]["title"] == "Cosmos")
check("skip past the end -> empty list, not 404", client.get("/books?skip=500"), 200,
      lambda b: b == [])
check("min_year == max_year is allowed", client.get("/books?min_year=1949&max_year=1949"), 200,
      lambda b: len(b) == 1 and b[0]["year"] == 1949)
check("impossible range -> empty list", client.get("/books?min_year=2050&max_year=2060"), 200,
      lambda b: b == [])
check("no title matches -> empty list", client.get("/books?search=zzzznope"), 200,
      lambda b: b == [])
check("no duplicate IDs in a full page", client.get("/books?limit=25"), 200,
      lambda b: len({x["id"] for x in b}) == len(b))

print("\n--- Boundaries ---")
check("limit=25 accepted (at cap)", client.get("/books?limit=25"), 200)
check("limit=1 accepted (at floor)", client.get("/books?limit=1"), 200,
      lambda b: len(b) == 1)
check("min_year=1 accepted", client.get("/books?min_year=1"), 200)
check("skip=0 accepted", client.get("/books?skip=0"), 200)
check("single-char search accepted", client.get("/books?search=a"), 200)
check("min_rating=0.0 accepted", client.get("/books?min_rating=0.0"), 200,
      lambda b: len(b) > 0)
check("min_rating=5.0 accepted, matches nothing", client.get("/books?min_rating=5.0"), 200,
      lambda b: b == [])
check("book_id=1 accepted (lowest valid)", client.get("/books/1"), 200,
      lambda b: b["id"] == 1)

print("\n--- Pagination consistency across pages ---")
page1 = client.get("/books?limit=4&skip=0&sort_by=year").json()
page2 = client.get("/books?limit=4&skip=4&sort_by=year").json()
page3 = client.get("/books?limit=4&skip=8&sort_by=year").json()
all_ids = [b["id"] for b in page1 + page2 + page3]
check("three pages cover all 10 books, no repeats",
      client.get("/books?limit=25"), 200,
      lambda _: len(all_ids) == 10 and len(set(all_ids)) == 10)

print(f"\n{passed} passed, {failed} failed\n")
raise SystemExit(1 if failed else 0)
