# exercises/background-tasks/app/exceptions.py
# L7 — Custom exceptions
#
# These deliberately do NOT subclass fastapi.HTTPException. If they did,
# FastAPI's own built-in handler would catch them first and the global
# handlers registered in main.py would never run — which is the whole point
# of the lesson. Subclassing plain Exception forces the app to define how
# its own errors are rendered.
#
# Each class carries the two things a handler needs: the HTTP status to send
# and a short machine-readable label for the error type. The human-readable
# message is per-instance.


class AppException(Exception):
    """Base for every error this application raises deliberately.

    Registering a handler for this one class would cover all three
    subclasses at once. main.py registers all three explicitly instead,
    because the exercise asks for three handlers and because separate
    registrations leave room for the responses to diverge later.
    """

    status_code: int = 500
    error: str = "Error"
    headers: dict[str, str] | None = None

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status_code={self.status_code}, detail={self.detail!r})"


class NotFoundException(AppException):
    """The requested resource does not exist. Rendered as 404."""

    status_code = 404
    error = "NotFound"


class DuplicateException(AppException):
    """A unique constraint would be violated. Rendered as 409."""

    status_code = 409
    error = "Duplicate"


class UnauthorizedException(AppException):
    """The caller is not authenticated. Rendered as 401.

    401 versus 403: 401 means "I do not know who you are" — no token, an
    expired one, a bad signature. 403 would mean "I know who you are and you
    still may not do this", which this API has no rules for yet.

    The WWW-Authenticate header is required by the HTTP spec on a 401 and
    tells the client what kind of credential to present.
    """

    status_code = 401
    error = "Unauthorized"
    headers = {"WWW-Authenticate": "Bearer"}


class BadRequestException(AppException):
    """The request is well-formed but not allowed by business rules.

    Rendered as 400. Note the distinction from FastAPI's automatic 422:
    422 means the request body failed schema validation, while this means
    the body was valid and the application still refused it — deleting a
    student who is still enrolled, for example.
    """

    status_code = 400
    error = "BadRequest"
