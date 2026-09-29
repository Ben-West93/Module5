# exercises/structured-recipe-api/app/schemas/recipe.py
# L2 — Pydantic schemas for recipes

from pydantic import BaseModel, Field


class RecipeCreate(BaseModel):
    """Request body for POST /recipes."""
    title: str = Field(..., min_length=1, examples=["Pasta Carbonara"])
    cuisine: str = Field(..., min_length=1, examples=["Italian"])
    prep_time_minutes: int = Field(..., ge=0, examples=[25])
    servings: int = Field(..., gt=0, examples=[2])


class Recipe(RecipeCreate):
    """Recipe as returned by the API (includes the server-assigned id)."""
    id: int
