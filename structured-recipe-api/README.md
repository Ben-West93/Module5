# Structured Recipe API

A FastAPI project organized with routers and Pydantic schemas. Data is stored in in-memory lists (no database yet), so it resets whenever the server restarts.

## Project structure

```
structured-recipe-api/
├── app/
│   ├── __init__.py
│   ├── main.py            # creates the app and includes both routers
│   ├── config.py          # app title, description, version
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── recipes.py     # /recipes endpoints
│   │   └── ingredients.py # /ingredients endpoints
│   └── schemas/
│       ├── __init__.py
│       ├── recipe.py      # RecipeCreate, Recipe
│       └── ingredient.py  # IngredientCreate, Ingredient
├── requirements.txt
├── README.md
└── .gitignore
```

## Setup and run

From the `structured-recipe-api/` folder:

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Swagger UI: http://127.0.0.1:8000/docs — "recipes" and "ingredients" appear as separate sections.

## Endpoints

| Method | Path | Description | Success |
|---|---|---|---|
| GET | /recipes | List all recipes | 200 |
| GET | /recipes/{id} | Get one recipe (404 if missing) | 200 |
| POST | /recipes | Create a recipe | 201 |
| GET | /ingredients | List all ingredients | 200 |
| POST | /ingredients | Create an ingredient | 201 |

Recipe body: `title` (str), `cuisine` (str), `prep_time_minutes` (int ≥ 0), `servings` (int > 0).
Ingredient body: `name` (str), `category` (str).
Invalid or missing fields return 422.

## Pushing to GitHub

Use git from the terminal (the web uploader flattens folders):

```
git add app/ requirements.txt README.md .gitignore
git commit -m "Add structured Recipe API"
git push
```
