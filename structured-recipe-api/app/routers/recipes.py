# exercises/structured-recipe-api/app/routers/recipes.py
# L2 — Recipe endpoints
#
# Implements the recipe endpoints using an in-memory list.
# Mounted in main.py with prefix="/recipes" and tags=["recipes"].

from fastapi import APIRouter, HTTPException, status

from app.schemas.recipe import Recipe, RecipeCreate

router = APIRouter()

# In-memory "database" — a simple list of dicts.
# No real database needed for this exercise.
recipes: list[dict] = [
    {"id": 1, "title": "Pasta Carbonara", "cuisine": "Italian",
     "prep_time_minutes": 25, "servings": 2},
    {"id": 2, "title": "Tacos al Pastor", "cuisine": "Mexican",
     "prep_time_minutes": 45, "servings": 4},
]

# Simple counter to generate new IDs
_next_id = 3


@router.get("", response_model=list[Recipe])
def list_recipes():
    """Returns all recipes."""
    return recipes


@router.get("/{recipe_id}", response_model=Recipe)
def get_recipe(recipe_id: int):
    """
    Returns a single recipe by ID.

    Args:
        recipe_id: The integer ID in the URL path.

    Raises:
        HTTPException(404) if no recipe has that ID.
    """
    for recipe in recipes:
        if recipe["id"] == recipe_id:
            return recipe
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Recipe with id {recipe_id} not found",
    )


@router.post("", response_model=Recipe, status_code=status.HTTP_201_CREATED)
def create_recipe(recipe: RecipeCreate):
    """Creates a new recipe and returns it with an assigned id (HTTP 201)."""
    global _next_id
    new_recipe = {"id": _next_id, **recipe.model_dump()}
    _next_id += 1
    recipes.append(new_recipe)
    return new_recipe
