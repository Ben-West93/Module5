# exercises/polished-docs/app/ids.py
# Path id type shared by /tasks/{task_id} and /items/{item_id}
#
# Moved out of routers/tasks.py when L12 added GET /items/{item_id}, so both
# routers refuse the same malformed ids (bugs 4 and 11 in the README) instead
# of the item route regressing to Pydantic's lax integer parsing.

import re
from typing import Annotated

from fastapi import Path
from pydantic import BeforeValidator

# SQLite stores INTEGER as a signed 64-bit value. Ids start at 1.
SQLITE_MAX_INTEGER = 2**63 - 1

# At most 19 digits and no leading zero, so every id has exactly one spelling
# and an absurd 5,000-digit id is refused before it is turned into a number.
_PLAIN_ID = re.compile(r"[1-9][0-9]{0,18}")


def plain_id(name: str, description: str):
    """An Annotated int path parameter: plain digits, 1 to 2**63 - 1."""

    def _plain_digits_only(value):
        # Without this, Pydantic accepts "1_0", "+1" and " 1" as ints.
        if isinstance(value, str) and not _PLAIN_ID.fullmatch(value):
            raise ValueError(f"{name} must be written as plain digits, such as 42")
        return value

    return Annotated[
        int,
        BeforeValidator(_plain_digits_only),
        Path(ge=1, le=SQLITE_MAX_INTEGER, description=description, examples=[1]),
    ]
