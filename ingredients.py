# app/routers/ingredients.py
#
# Endpoints for the ingredient resource.
#
# Same shape as recipes.py — bare router, prefix and tags applied in main.py.

from fastapi import APIRouter

from app.schemas.ingredient import Ingredient, IngredientCreate

router = APIRouter()


# In-memory storage, standing in for a database table.
ingredients: list[Ingredient] = [
    Ingredient(id=1, name="Smoked paprika", category="spice"),
    Ingredient(id=2, name="Pecorino Romano", category="dairy"),
]

_next_id = 3


# NOTE ON THE EMPTY PATH: the collection routes below use "" rather than "/".
# With prefix="/recipes", a path of "/" produces the route /recipes/ (trailing
# slash), which is what the docs page would then advertise. "" produces exactly
# /recipes. Both work either way — FastAPI redirects between them — but this
# keeps the documented paths matching the spec.


@router.get("", response_model=list[Ingredient])
def list_ingredients():
    """
    List all ingredients.
    """
    return ingredients


@router.post("", response_model=Ingredient, status_code=201)
def create_ingredient(payload: IngredientCreate):
    """
    Create an ingredient.

    Args:
        payload: The validated request body.
    """
    global _next_id

    ingredient = Ingredient(id=_next_id, **payload.model_dump())
    ingredients.append(ingredient)
    _next_id += 1

    return ingredient

