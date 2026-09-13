# app/schemas/ingredient.py
#
# Pydantic schemas for the ingredient resource.

from pydantic import BaseModel, Field


class IngredientCreate(BaseModel):
    """
    The body POST /ingredients expects.
    """

    name: str = Field(
        ...,
        min_length=1,
        description="Name of the ingredient.",
        examples=["Smoked paprika"],
    )
    category: str = Field(
        ...,
        min_length=1,
        description="Rough grouping, e.g. produce, dairy, spice.",
        examples=["spice"],
    )


class Ingredient(IngredientCreate):
    """
    A stored ingredient, as returned by the API.
    """

    id: int = Field(..., description="Server-assigned identifier.")

