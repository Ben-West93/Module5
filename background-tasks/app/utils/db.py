# exercises/background-tasks/app/utils/db.py
# L8 — shared commit helper

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.exceptions import BadRequestException, DuplicateException

logger = logging.getLogger("jwt_api")


def commit_or_conflict(db: Session, conflict_detail: str) -> None:
    """Commits, turning a unique-constraint violation into a 409.

    Checking for an existing row before inserting handles the ordinary case,
    but two requests can pass that check at the same moment and both proceed.
    Whichever loses the race hits the database constraint, and without this
    the loser surfaces as an unhandled 500.

    Only uniqueness violations become 409. Any other constraint failure is a
    400 naming nothing specific, because the raw message from the database
    names tables and columns — it is logged rather than returned.

    This lives in utils/ rather than in one router because every insert path
    needs the same behavior; auth.register() originally did a bare commit and
    returned a 500 under concurrent sign-ups.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        cause = str(getattr(exc, "orig", exc))
        if "unique" in cause.lower():
            raise DuplicateException(conflict_detail)

        logger.warning("Integrity error on commit: %s", cause)
        raise BadRequestException("The request violates a database constraint")
