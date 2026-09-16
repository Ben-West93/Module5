# exercises/polished-docs/app/routers/items.py
# L12 — Documented item endpoints
#
# Merged with the L10 item router: the L12 starter kept items in a Python
# list, while L10 stored them in SQLite behind the sanitizer and the rate
# limiter. The merged router keeps the database (so items survive a restart
# and share the test database override with tasks) and adds what L12 asks
# for: docstrings, response models, a 201, documented 404/409/422 responses
# and GET /items/{item_id}.

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.ids import plain_id
from app.models.item import Item
from app.sanitization import sanitize_text
from app.schemas.error import ErrorResponse
from app.schemas.item import ItemCreate, ItemResponse

router = APIRouter()

DUPLICATE_NAME = "An item with that name already exists"
ItemId = plain_id("item_id", "The item's id")


@router.get("", response_model=list[ItemResponse], summary="List items")
def list_items(
    name: Optional[str] = Query(None, max_length=100, description="Exact item name to match (HTML tags are stripped first)"),
    category: Optional[str] = Query(None, max_length=50, description="Exact category to match"),
    db: Session = Depends(get_db),
):
    """
    List inventory items, optionally filtered.

    Returns every item ordered by `id` (oldest first). Filters combine with AND:

    - **name**: exact, case-sensitive match. It is sanitized the same way stored
      names are, so `<b>Mouse</b>` finds `Mouse`. A blank value is no filter.
    - **category**: exact, case-sensitive match. A blank value is no filter.

    Sorting is fixed to `id` and there is no pagination yet; both are planned
    as `sort` and `limit`/`offset` query parameters.

    **Security:** filters are bound parameters (`WHERE items.name = ?`), never
    text formatted into SQL. An f-string such as
    `f"SELECT * FROM items WHERE name = '{name}'"` would let `' OR '1'='1`
    return every row; `verify_security.py` section 5 demonstrates both.
    """
    query = select(Item).order_by(Item.id)
    if name is not None and (cleaned := sanitize_text(name)):
        query = query.where(Item.name == cleaned)
    if category is not None and (cleaned := sanitize_text(category)):
        query = query.where(Item.category == cleaned)
    return db.execute(query).scalars().all()


@router.post(
    "",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an item",
    responses={
        409: {"model": ErrorResponse, "description": "An item with that name already exists"},
        422: {"model": ErrorResponse, "description": "The body failed validation"},
    },
)
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    """
    Create an inventory item.

    The JSON request body must contain:

    - **name** (required, 1-100 characters, unique): HTML tags are stripped;
      must still contain visible text.
    - **description** (optional, up to 500 characters, defaults to `""`).
    - **price** (required): a number greater than 0 and at most 1,000,000.
    - **category** (required, 1-50 characters).

    Responses:

    - **201**: the stored item, with its `id` and `created_at`.
    - **409**: another item already has that name.
    - **422**: a missing, invalid or unknown field.
    """
    if db.execute(select(Item.id).where(Item.name == item.name)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_NAME)
    db_item = Item(**item.model_dump())
    db.add(db_item)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two requests with the same new name can both pass the check above.
        db.rollback()
        if "UNIQUE" in str(exc.orig).upper():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_NAME) from None
        raise
    db.refresh(db_item)
    return db_item


@router.get(
    "/{item_id}",
    response_model=ItemResponse,
    summary="Get one item",
    responses={
        404: {"model": ErrorResponse, "description": "Item not found"},
        422: {"model": ErrorResponse, "description": "The id is not a valid item id"},
    },
)
def get_item(item_id: ItemId, db: Session = Depends(get_db)):
    """
    Get a single item by `item_id`.

    The id must be plain digits between 1 and 2^63-1; anything else (`abc`,
    `0`, `+1`, `01`) is a **422**. Returns **404** if no item has that id.
    """
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Item {item_id} not found")
    return item
