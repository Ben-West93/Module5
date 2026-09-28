# exercises/hello-fastapi/main.py
# L1 — Hello FastAPI
#
# A simple FastAPI app demonstrating core concepts: decorators, path
# parameters, request-body validation, and automatic documentation.
#
# Run with: uvicorn main:app --reload
# Docs at:  http://127.0.0.1:8000/docs

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="Hello FastAPI",
    version="1.0.0",
    description="Module 5, Lesson 1 — my first FastAPI project.",
)

# In-memory example data used by the /items endpoints
ITEMS = [
    {"id": 1, "name": "Widget", "description": "A small, useful widget."},
    {"id": 2, "name": "Gadget", "description": "A clever little gadget."},
    {"id": 3, "name": "Doohickey", "description": "Nobody knows what it does."},
]


class EchoRequest(BaseModel):
    """Request body for POST /echo."""
    message: str
    shout: bool = False


class EchoResponse(BaseModel):
    """Response body for POST /echo."""
    message: str
    shout: bool


# Key concept: The @app.get("/") decorator tells FastAPI to call this
# function whenever a GET request is made to the "/" path.
@app.get("/")
def root():
    """Root endpoint — returns a welcome message."""
    return {"message": "Welcome to my first API"}


@app.get("/hello")
def say_hello():
    """Returns a simple string greeting."""
    return "Hello, FastAPI!"


@app.get("/info")
def get_info():
    """Returns app metadata as a dict."""
    return {
        "name": app.title,
        "version": app.version,
        "description": app.description,
    }


@app.get("/about")
def about():
    """Returns my name, the current module, and a fun fact about me."""
    return {
        "name": "Ben",
        "module": "Module 5 — FastAPI Development",
        "fun_fact": "I live in Iowa, where there are more pigs than people.",
    }


@app.get("/greet/{name}")
def greet(name: str):
    """Returns a personalized greeting using the `name` path parameter."""
    return {"greeting": f"Hello, {name}! Welcome to my first API."}


@app.post("/echo", response_model=EchoResponse)
def echo(body: EchoRequest):
    """
    Echoes the message back. If `shout` is true, the message is returned
    in UPPERCASE. Invalid bodies (e.g. shout="maybe") get an automatic 422.
    """
    text = body.message.upper() if body.shout else body.message
    return EchoResponse(message=text, shout=body.shout)


@app.get("/items")
def list_items():
    """Returns a list of example items."""
    return ITEMS


# Key concept: Path parameters are defined with {curly_braces} in the path
# and appear as function arguments with matching names. The `int` type hint
# means FastAPI validates and converts it (e.g. /items/abc -> 422).
@app.get("/items/{item_id}")
def get_item(item_id: int):
    """Returns a single item by ID, or 404 if it doesn't exist."""
    for item in ITEMS:
        if item["id"] == item_id:
            return item
    raise HTTPException(status_code=404, detail=f"Item {item_id} not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
