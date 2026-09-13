# Structured Recipe API

L2 — a FastAPI project split into routers and schemas.

## Layout

```
structured-recipe-api/
├── app/
│   ├── __init__.py
│   ├── main.py              ← creates the app, includes the routers
│   ├── config.py            ← settings and environment variables
│   ├── schemas/             ← Pydantic request/response shapes
│   │   ├── __init__.py
│   │   ├── recipe.py
│   │   └── ingredient.py
│   └── routers/             ← endpoint definitions, one file per resource
│       ├── __init__.py
│       ├── recipes.py
│       └── ingredients.py
├── requirements.txt
├── .gitignore
└── README.md
```

`models/`, `database.py`, `utils/`, and `tests/` from the reference layout are
omitted: storage is in-memory for this exercise, so there are no SQLAlchemy
models or database session to set up yet.

## Running

From the project root (the folder containing `app/`):

```
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Docs at http://127.0.0.1:8000/docs

## Endpoints

| Method | Path            | Description          |
| ------ | --------------- | -------------------- |
| GET    | `/recipes`      | List all recipes     |
| GET    | `/recipes/{id}` | Get one recipe       |
| POST   | `/recipes`      | Create a recipe      |
| GET    | `/ingredients`  | List all ingredients |
| POST   | `/ingredients`  | Create an ingredient |
