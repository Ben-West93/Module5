# exercises/structured-recipe-api/app/main.py
# L2 — Structured Recipe API
#
# Wires the FastAPI app to the recipes and ingredients routers.
#
# Run with: uvicorn app.main:app --reload  (from the structured-recipe-api/ folder)
# Docs at:  http://127.0.0.1:8000/docs

from fastapi import FastAPI

from app import config
from app.routers import ingredients, recipes

# Key concept: APIRouter lets you group related endpoints in a separate file,
# then "include" them in the main app with a prefix. This keeps main.py clean
# as your API grows.

app = FastAPI(
    title=config.APP_TITLE,
    description=config.APP_DESCRIPTION,
    version=config.APP_VERSION,
)

app.include_router(recipes.router, prefix="/recipes", tags=["recipes"])
app.include_router(ingredients.router, prefix="/ingredients", tags=["ingredients"])


@app.get("/", tags=["root"])
def root():
    """Simple health check / landing endpoint."""
    return {"message": "Structured Recipe API is running. See /docs."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
