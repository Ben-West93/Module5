# exercises/structured-recipe-api/app/main.py
# L2 — Structured Recipe API
#
# Wires the FastAPI app to the recipes and ingredients routers.
#
# Run with: uvicorn app.main:app --reload  (from the structured-recipe-api/ folder)
# Docs at:  http://127.0.0.1:8000/docs

from fastapi import FastAPI

from app.config import settings
from app.routers import ingredients, recipes

# Key concept: APIRouter lets you group related endpoints in a separate file,
# then "include" them in the main app with a prefix. This keeps main.py clean
# as your API grows — this file creates the app and connects the pieces, and
# holds no endpoint logic of its own.

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=settings.description,
)

# The prefix is applied here rather than inside the router files, so every
# route in recipes.py is served under /recipes without repeating it on each
# decorator. The `tags` argument is what groups them into their own labelled
# section in the Swagger UI.
app.include_router(recipes.router, prefix="/recipes", tags=["recipes"])
app.include_router(ingredients.router, prefix="/ingredients", tags=["ingredients"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

