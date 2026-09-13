# app/routers/recipes.py
#
# Endpoints for the recipe resource.
#
# NOTE ON PREFIX: this router is created bare — no prefix, no tags. Both are
# applied in app/main.py at include_router() time. Setting them in both places
# would stack, producing paths like /recipes/recipes.

from fastapi import APIRouter, HTTPException

from app.schemas.recipe import Recipe, RecipeCreate

router = APIRouter()


# In-memory storage, standing in for a database table.
#
# NOTE: module-level mutable state. It resets every time the server restarts
# (including on every --reload), and it is shared across all requests. Fine for
# this exercise; replace with a real database before it matters.
recipes: list[Recipe] = [
    Recipe(
        id=1,
        title="Shakshuka",
        cuisine="Middle Eastern",
        prep_time_minutes=25,
        servings=4,
    ),
    Recipe(
        id=2,
        title="Cacio e Pepe",
        cuisine="Italian",
        prep_time_minutes=20,
        servings=2,
    ),
]

# Tracks the next id to hand out. Using len(recipes) + 1 instead would reuse
# ids after a deletion, so keep a separate counter.
_next_id = 3


# NOTE ON THE EMPTY PATH: the collection routes below use "" rather than "/".
# With prefix="/recipes", a path of "/" produces the route /recipes/ (trailing
# slash), which is what the docs page would then advertise. "" produces exactly
# /recipes. Both work either way — FastAPI redirects between them — but this
# keeps the documented paths matching the spec.


@router.get("", response_model=list[Recipe])
def list_recipes():
    """
    List all recipes.
    """
    return recipes


@router.get("/{recipe_id}", response_model=Recipe)
def get_recipe(recipe_id: int):
    """
    Get a single recipe by id.

    Args:
        recipe_id: The integer id from the URL path.

    Raises:
        HTTPException: 404 if no recipe has that id.
    """
    for recipe in recipes:
        if recipe.id == recipe_id:
            return recipe

    raise HTTPException(status_code=404, detail=f"No recipe with id {recipe_id}")


@router.post("", response_model=Recipe, status_code=201)
def create_recipe(payload: RecipeCreate):
    """
    Create a recipe.

    Args:
        payload: The validated request body. FastAPI knows to read this from
            the body rather than the path because it is annotated with a
            Pydantic model.

    Returns 201 Created rather than the default 200, which is the conventional
    status for a request that created a new resource.
    """
    global _next_id

    recipe = Recipe(id=_next_id, **payload.model_dump())
    recipes.append(recipe)
    _next_id += 1

    return recipe

