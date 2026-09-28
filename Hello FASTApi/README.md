# L1 — Hello FastAPI

My first FastAPI project for Module 5. It demonstrates route decorators, path parameters, request-body validation with Pydantic, and FastAPI's automatic Swagger docs.

## Files

| File | Purpose |
|---|---|
| `main.py` | The FastAPI app with all endpoints |
| `SWAGGER_OUTPUT.md` | Copy-paste of the Swagger UI endpoints, responses, and the validation error (deliverable) |

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install fastapi uvicorn
```

## Run

```bash
uvicorn main:app --reload
```

Swagger UI: http://127.0.0.1:8000/docs

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Returns `{"message": "Welcome to my first API"}` |
| GET | `/about` | My name, the current module, and a fun fact |
| GET | `/greet/{name}` | Personalized greeting using the path parameter |
| POST | `/echo` | Body `{"message": str, "shout": bool = false}`; returns the message, UPPERCASE if `shout` is true |
| GET | `/hello` | Plain string greeting |
| GET | `/info` | App name, version, description |
| GET | `/items` | List of example items |
| GET | `/items/{item_id}` | One item by integer ID; 404 if not found, 422 if the ID isn't an integer |

## Validation example

Sending `{"message": "hi", "shout": "maybe"}` to `POST /echo` returns **422** with a `bool_parsing` error at `["body", "shout"]`, generated automatically by Pydantic. See `SWAGGER_OUTPUT.md`.

## Testing

With the server running, open http://127.0.0.1:8000/docs and use **Try it out** on each endpoint. Results, including the validation error, are recorded in `SWAGGER_OUTPUT.md`.
