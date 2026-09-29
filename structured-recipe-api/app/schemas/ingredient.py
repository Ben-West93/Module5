# exercises/structured-recipe-api/app/schemas/ingredient.py
# L2 — Pydantic schemas for ingredients

from pydantic import BaseModel, Field


class IngredientCreate(BaseModel):
    """Request body for POST /ingredients."""
    name: str = Field(..., min_length=1, examples=["Garlic"])
    category: str = Field(..., min_length=1, examples=["Vegetable"])


class Ingredient(IngredientCreate):
    """Ingredient as returned by the API (includes the server-assigned id)."""
    id: int
