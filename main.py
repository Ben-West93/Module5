# exercises/hello-fastapi/main.py
# L1 — Hello FastAPI
#
# A simple FastAPI app demonstrating core concepts:
# decorators, path parameters, request bodies, and automatic documentation.
#
# Run with: uvicorn main:app --reload
# Docs at:  http://127.0.0.1:8000/docs

from fastapi import FastAPI
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class EchoRequest(BaseModel):
    """
    The JSON body POST /echo expects.

    FastAPI reads this class to validate incoming requests AND to build
    the schema you see in /docs. `shout` has a default, so it is optional
    in the request body; `message` has none, so it is required.
    """

    message: str = Field(
        ...,
        min_length=1,
        description="The text to echo back.",
    )
    shout: bool = Field(
        False,
        description="Uppercase the message if true.",
    )


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

# A tiny in-memory stand-in for a database.
#
# NOTE: This is a mutable module-level global. It is fine while every route
# only reads from it, but once a write endpoint exists (e.g. POST /items),
# changes will persist for the life of the process and leak between requests
# and between tests. Swap it for a real database or a per-request dependency
# before adding writes.
ITEMS = [
    {"id": 1, "name": "Widget", "description": "A basic widget."},
    {"id": 2, "name": "Gadget", "description": "A slightly fancier widget."},
    {"id": 3, "name": "Doohickey", "description": "Nobody is sure what it does."},
]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Hello FastAPI",
    version="0.1.0",
    description="Module 5 - my first FastAPI app.",
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    """
    Root endpoint — returns a welcome message.
    """
    return {"message": "Welcome to my first API"}


@app.get("/hello")
def say_hello():
    """
    Returns a simple string greeting.

    Returning a bare string (rather than a dict) is legal — FastAPI
    serializes it to a JSON string, so the response body is "Hello, FastAPI!"
    with the quotes included.
    """
    return "Hello, FastAPI!"


@app.get("/about")
def about():
    """
    Returns a little information about the author of this API.
    """
    return {
        "name": "Ben",
        "module": "Module 5",
        "fun_fact": "I like to play video games.",
    }


@app.get("/greet/{name}")
def greet(name: str):
    """
    Returns a personalized greeting built from the path parameter.

    Args:
        name: The name taken from the URL path.

    Note that `name` is annotated `str` rather than `int`, so FastAPI accepts
    any text here instead of coercing and rejecting non-numbers the way
    /items/{item_id} does.
    """
    return {"message": f"Hello, {name}! Welcome to my first API."}


@app.get("/info")
def get_info():
    """
    Returns app metadata as a dict.

    These values are read back off the app object rather than retyped, so
    the endpoint and the /docs page can never drift out of sync.
    """
    return {
        "name": app.title,
        "version": app.version,
        "description": app.description,
    }


@app.get("/items")
def list_items():
    """
    Returns a list of example items.
    """
    return ITEMS


# NOTE ON ROUTE ORDER: FastAPI matches routes top to bottom, first match wins.
# A dynamic route like /items/{item_id} will swallow any sibling path that sits
# below it — declare /items/count *above* this one, or requests to it get
# matched against item_id, fail the int coercion, and return 422 instead of
# ever reaching your function.
@app.get("/items/{item_id}")
def get_item(item_id: int):
    """
    Returns a single item by ID.

    Args:
        item_id: The integer ID from the URL path.
    """
    for item in ITEMS:
        if item["id"] == item_id:
            return item

    # PLACEHOLDER: echo the ID back so the path parameter is visible.
    # A real API should `raise HTTPException(status_code=404, ...)` here —
    # returning 200 with invented content tells the caller a missing item
    # exists. Left as-is to match the exercise skeleton's TODO.
    return {
        "id": item_id,
        "name": "Unknown",
        "description": f"No item found with id {item_id}.",
    }


@app.post("/echo")
def echo(payload: EchoRequest):
    """
    Echoes a message back, optionally uppercased.

    Args:
        payload: The parsed and validated request body.

    Because the argument is annotated with a BaseModel rather than a plain
    type, FastAPI knows to read it from the request body instead of the
    path or query string.
    """
    message = payload.message.upper() if payload.shout else payload.message
    return {"message": message, "shouted": payload.shout}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
