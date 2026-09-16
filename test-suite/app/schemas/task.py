# exercises/test-suite/app/schemas/task.py
# L11 — Task schemas
#
# The starter called these "already complete". Probing them against the real
# database found three ways a valid-looking request crashed the server, and one
# of them broke GET /tasks for every client afterwards. The changes below are
# the fixes, plus the L10 sanitizer now that the two lessons share one API.
# tests/test_tasks.py has a regression test for each.
#
#   1. TaskPatch.description had no max_length. SQLite does not enforce
#      String(500), so a 600-character PATCH was saved. TaskResponse then
#      failed to validate that stored row, so the PATCH was a 500, and so was
#      every later GET /tasks and GET /tasks/{id} that included it. One request
#      permanently broke the list endpoint.
#
#   2. {"title": null} or {"completed": null} passed TaskPatch, because the
#      fields are Optional so that they can be left out. The router then wrote
#      NULL into NOT NULL columns and the commit raised IntegrityError (500).
#      Optional here means "may be omitted", not "may be null", so an explicit
#      null is now a 422.
#
#   3. TaskResponse inherited from TaskCreate, which gave the RESPONSE model
#      the request's input rules (and, once added, would have run the
#      sanitizer on every value being sent back). It is now its own model.
#
# A second round of edge-case probing added two more:
#
#   4. Unknown fields were silently dropped. PATCH {"complete": true} (a typo
#      for "completed") returned 200 and changed nothing, so the client
#      believed the update worked. Both request models now forbid extras.
#
#   5. Titles that look identical were not duplicates. "Café" typed as one
#      precomposed character and as "e" plus a combining accent are different
#      strings, and so are "Chores" and "Cho<zero-width space>res". Titles are
#      now normalized before the uniqueness check; see _clean_title().

import unicodedata
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.sanitization import has_visible_text, sanitize_text

TITLE_MAX = 200
DESCRIPTION_MAX = 500


def _normalize_title(value: str) -> str:
    """The canonical form of a title, so look-alikes compare equal.

    - NFC composes "e" + U+0301 into "é", the form nearly every keyboard
      produces, so the two spellings of "Café" become the same string.
    - Format characters (Unicode category Cf: zero-width space, zero-width
      joiner, BOM, soft hyphen, direction marks) are removed. They change no
      visible letter, so they could only ever make one title look like another.

    This runs BEFORE sanitize_text(), and that order is a security property.
    The sanitizer only treats "<" as a tag when a letter follows it, so
    "<" + zero-width space + "script>" is left alone as text. Removing the
    zero-width space afterwards would turn it into a live <script> tag after
    the check had already passed. tests/test_security.py sends exactly that.
    """
    composed = unicodedata.normalize("NFC", value)
    return "".join(char for char in composed if unicodedata.category(char) != "Cf")


def _clean_title(value: Optional[str]) -> str:
    """Normalizes and sanitizes a title, and refuses one that is null or empty.

    The empty check has to run last. min_length=1 runs before this validator,
    so on its own it would accept "<b></b>" and store a task with an empty
    title.
    """
    if value is None:
        raise ValueError("title may be omitted but not null")
    cleaned = sanitize_text(_normalize_title(value))
    if not has_visible_text(cleaned):
        raise ValueError("title must contain text once HTML tags and whitespace are removed")
    return cleaned


def _clean_description(value: Optional[str]) -> Optional[str]:
    """Sanitizes a description. null stays null: the column allows it.

    Descriptions are not normalized like titles: they carry no uniqueness
    rule, so there is nothing for a look-alike to get around, and a zero-width
    joiner inside an emoji sequence is kept as typed.
    """
    return None if value is None else sanitize_text(value)


class TaskCreate(BaseModel):
    """Body for POST /tasks.

    max_length applies to what the client sent, before sanitizing, as in L10.
    Sanitizing only shortens text, so the stored value always fits the column.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: Optional[str] = Field(None, max_length=DESCRIPTION_MAX)
    completed: bool = False

    # mode="after" (the default): Pydantic has already confirmed the type, so
    # the regexes never see an int or a list.
    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        return _clean_title(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value: Optional[str]) -> Optional[str]:
        return _clean_description(value)


class TaskPatch(BaseModel):
    """Body for PATCH /tasks/{id}. Every field may be omitted.

    Field validators do not run on a default, only on a value the client
    actually sent. So each `value is None` check below fires for an explicit
    null and never for a field that was left out.
    """

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(None, min_length=1, max_length=TITLE_MAX)
    description: Optional[str] = Field(None, max_length=DESCRIPTION_MAX)
    completed: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: Optional[str]) -> str:
        return _clean_title(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value: Optional[str]) -> Optional[str]:
        return _clean_description(value)

    @field_validator("completed")
    @classmethod
    def _completed(cls, value: Optional[bool]) -> bool:
        if value is None:
            raise ValueError("completed may be omitted but not null")
        return value


class TaskResponse(BaseModel):
    """What the task endpoints return. Describes stored rows; enforces no input rules."""

    id: int
    title: str
    description: Optional[str]
    completed: bool

    model_config = ConfigDict(from_attributes=True)
