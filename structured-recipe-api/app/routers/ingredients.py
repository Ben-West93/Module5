# exercises/structured-recipe-api/app/routers/ingredients.py
# L2 — Ingredient endpoints
#
# Implements the ingredient endpoints using an in-memory list.
# Mounted in main.py with prefix="/ingredients" and tags=["ingredients"].

from fastapi import APIRouter, status

from app.schemas.ingredient import Ingredient, IngredientCreate

router = APIRouter()

# In-memory "database" — a simple list of dicts.
ingredients: list[dict] = [
    {"id": 1, "name": "Spaghetti", "category": "Pasta"},
    {"id": 2, "name": "Pineapple", "category": "Fruit"},
]

# Simple counter to generate new IDs
_next_id = 3


@router.get("", response_model=list[Ingredient])
def list_ingredients():
    """Returns all ingredients."""
    return ingredients


@router.post("", response_model=Ingredient, status_code=status.HTTP_201_CREATED)
def create_ingredient(ingredient: IngredientCreate):
    """Creates a new ingredient and returns it with an assigned id (HTTP 201)."""
    global _next_id
    new_ingredient = {"id": _next_id, **ingredient.model_dump()}
    _next_id += 1
    ingredients.append(new_ingredient)
    return new_ingredient
