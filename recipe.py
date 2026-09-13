# app/schemas/recipe.py
#
# Pydantic schemas for the recipe resource.
#
# Schemas describe what the API accepts and returns. They are deliberately
# separate from database models (which would live in app/models/) because the
# two shapes differ: the client never sends an `id` when creating a recipe,
# but every response includes one.

from pydantic import BaseModel, Field


class RecipeCreate(BaseModel):
    """
    The body POST /recipes expects.

    No `id` field — the server assigns that.
    """

    title: str = Field(
        ...,
        min_length=1,
        description="Name of the recipe.",
        examples=["Shakshuka"],
    )
    cuisine: str = Field(
        ...,
        min_length=1,
        description="Cuisine the recipe belongs to.",
        examples=["Middle Eastern"],
    )
    prep_time_minutes: int = Field(
        ...,
        gt=0,
        description="Preparation time in minutes. Must be positive.",
        examples=[25],
    )
    servings: int = Field(
        ...,
        gt=0,
        description="How many people the recipe serves. Must be positive.",
        examples=[4],
    )


class Recipe(RecipeCreate):
    """
    A stored recipe, as returned by the API.

    Inherits every field from RecipeCreate and adds the server-assigned id,
    so the two can never drift apart when a field is added.
    """

    id: int = Field(..., description="Server-assigned identifier.")

