# exercises/polished-docs/tests/test_tasks.py
# L11 — Task API test suite
#
# Run with: pytest tests/ -v  (from the polished-docs/ folder)
#
# Key concept: test structure — AAA (Arrange / Act / Assert)
#   Arrange: set up any prerequisite state (e.g., create a task first)
#   Act:     make the HTTP request via the TestClient
#   Assert:  check the response status code and body
#
# The first eight tests are the ones the brief lists, in its order. Each does
# what the brief asks, and then also checks the returned values against what
# was sent and reads the row back from the database, because a response can
# look right while the stored data is wrong.
#
# After those come edge cases, and a regression test for every bug found by
# probing the starter API and by a second round of edge-case probing (see the
# README). Run against the code before each fix, its regression tests fail.

import pytest
from fastapi import HTTPException

from app.models.task import Task
from app.routers.tasks import SQLITE_MAX_INTEGER, commit_or_conflict
from tests.helpers import (
    assert_error_envelope,
    call,
    create_task,
    json_of,
    stored_task,
    stored_titles,
)


# ============================================================================
# The eight tests from the brief
# ============================================================================


def test_create_task(client, db_session):
    """POST /tasks creates a task and returns 201."""
    # Arrange
    sent = {"title": "Buy groceries", "completed": False}

    # Act
    response = call(client, "POST", "/tasks", json=sent, expect=201)

    # Assert
    body = json_of(response)
    assert body["title"] == "Buy groceries"
    assert "id" in body
    assert isinstance(body["id"], int)
    assert body == {"id": body["id"], "title": "Buy groceries", "description": None, "completed": False}
    assert stored_task(db_session, body["id"]) == body


def test_list_tasks(client):
    """GET /tasks returns 200 and a list."""
    response = call(client, "GET", "/tasks", expect=200)
    assert isinstance(json_of(response), list)
    assert json_of(response) == []  # a fresh test database

    first = create_task(client, title="First", description="one")
    second = create_task(client, title="Second", completed=True)

    listing = json_of(call(client, "GET", "/tasks", expect=200))
    assert listing == [first, second]  # every created task, as created, in id order


def test_get_task_by_id(client):
    """GET /tasks/{id} returns the created task."""
    # Arrange
    created = create_task(client, title="Read a book", description="Chapter 3")

    # Act
    response = call(client, "GET", f"/tasks/{created['id']}", expect=200)

    # Assert
    body = json_of(response)
    assert body["title"] == "Read a book"
    assert body == created


def test_get_nonexistent_task_returns_404(client):
    """GET /tasks/9999 returns 404."""
    response = call(client, "GET", "/tasks/9999", expect=404)
    body = assert_error_envelope(response, "NotFound")
    assert body["detail"] == "Task 9999 not found"


def test_patch_task(client, db_session):
    """PATCH /tasks/{id} updates only the specified fields."""
    # Arrange
    created = create_task(client, title="Walk the dog", description="Around the block")

    # Act
    response = call(client, "PATCH", f"/tasks/{created['id']}", json={"completed": True}, expect=200)

    # Assert
    body = json_of(response)
    assert body["completed"] is True
    assert body["title"] == "Walk the dog"
    assert body["description"] == "Around the block"  # the other unsent field is unchanged too
    assert stored_task(db_session, created["id"]) == {**created, "completed": True}


def test_delete_task(client, db_session):
    """DELETE /tasks/{id} removes the task."""
    # Arrange
    created = create_task(client, title="Take out trash")
    kept = create_task(client, title="Water plants")

    # Act
    response = call(client, "DELETE", f"/tasks/{created['id']}", expect=200)

    # Assert
    assert json_of(response) == {"message": f"Task {created['id']} deleted"}
    call(client, "GET", f"/tasks/{created['id']}", expect=404)
    assert json_of(call(client, "GET", "/tasks", expect=200)) == [kept]  # only that one was removed
    assert stored_task(db_session, created["id"]) is None


def test_create_task_invalid_data_returns_422(client, db_session):
    """POST /tasks with an empty title returns 422."""
    response = call(client, "POST", "/tasks", json={"title": ""}, expect=422)
    assert_error_envelope(response, "ValidationError")
    assert stored_titles(db_session) == []


def test_duplicate_task_title_returns_409(client, db_session):
    """Creating two tasks with the same title returns 409."""
    # Arrange
    create_task(client, title="Unique Task")

    # Act
    response = call(client, "POST", "/tasks", json={"title": "Unique Task"}, expect=409)

    # Assert
    body = assert_error_envelope(response, "Conflict")
    assert body["detail"] == "A task with that title already exists"
    assert stored_titles(db_session) == ["Unique Task"]  # the second was not stored


# ============================================================================
# Beyond the brief: the fixture itself
# ============================================================================


def test_tests_run_against_the_in_memory_database(client):
    """The app's own engine points at memory, and the override is in place."""
    import app.database
    from app.main import app as fastapi_app

    assert app.database.DATABASE_URL == "sqlite:///:memory:"
    assert app.database.get_db in fastapi_app.dependency_overrides


# The next two tests create the same title. Whichever runs second would get a
# 409 if rows leaked from one test to the next.
def test_isolation_first(client):
    call(client, "GET", "/tasks", expect=200)
    create_task(client, title="Isolation check")


def test_isolation_second(client):
    assert json_of(call(client, "GET", "/tasks", expect=200)) == []
    create_task(client, title="Isolation check")


# ============================================================================
# Beyond the brief: create and read
# ============================================================================


def test_create_task_with_every_field(client, db_session):
    sent = {"title": "Pay rent", "description": "Before the 1st", "completed": True}
    body = create_task(client, **sent)
    assert body == {"id": body["id"], **sent}
    assert stored_task(db_session, body["id"]) == body


def test_routes_answer_without_a_redirect(client):
    """/tasks is the route itself, not a redirect to /tasks/ (see routers/tasks.py)."""
    response = call(client, "GET", "/tasks", expect=200)
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "body",
    [
        {},                                   # missing title
        {"title": None},                      # null title
        {"title": 123},                       # not a string
        {"title": "x" * 201},                 # over the 200-character limit
        {"title": "ok", "description": "d" * 501},
        {"title": "ok", "completed": "maybe"},
    ],
    ids=["missing", "null", "number", "title-201", "description-501", "completed-not-bool"],
)
def test_create_task_rejects_invalid_bodies(client, db_session, body):
    response = call(client, "POST", "/tasks", json=body, expect=422)
    assert_error_envelope(response, "ValidationError")
    assert stored_titles(db_session) == []


def test_create_task_accepts_values_at_the_limits(client):
    body = create_task(client, title="t" * 200, description="d" * 500)
    assert (len(body["title"]), len(body["description"])) == (200, 500)


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_non_integer_id_returns_422(client, method):
    kwargs = {"json": {}} if method == "PATCH" else {}
    call(client, method, "/tasks/abc", expect=422, **kwargs)


# ============================================================================
# Beyond the brief: update and delete
# ============================================================================


def test_patch_nonexistent_task_returns_404(client):
    response = call(client, "PATCH", "/tasks/9999", json={"completed": True}, expect=404)
    assert_error_envelope(response, "NotFound")


def test_delete_nonexistent_task_returns_404(client):
    response = call(client, "DELETE", "/tasks/9999", expect=404)
    assert_error_envelope(response, "NotFound")


def test_delete_twice_returns_404_the_second_time(client):
    created = create_task(client, title="Once only")
    call(client, "DELETE", f"/tasks/{created['id']}", expect=200)
    call(client, "DELETE", f"/tasks/{created['id']}", expect=404)


def test_patch_several_fields_at_once(client, db_session):
    created = create_task(client, title="Draft", description="old")
    changes = {"title": "Final", "description": "new", "completed": True}
    body = json_of(call(client, "PATCH", f"/tasks/{created['id']}", json=changes, expect=200))
    assert body == {"id": created["id"], **changes}
    assert stored_task(db_session, created["id"]) == body


def test_patch_with_empty_body_changes_nothing(client, db_session):
    created = create_task(client, title="Leave me alone", description="as is", completed=True)
    body = json_of(call(client, "PATCH", f"/tasks/{created['id']}", json={}, expect=200))
    assert body == created
    assert stored_task(db_session, created["id"]) == created


def test_patch_to_its_own_title_is_not_a_conflict(client):
    """A task does not clash with itself when a client re-sends its title."""
    created = create_task(client, title="Same name")
    body = json_of(call(client, "PATCH", f"/tasks/{created['id']}", json={"title": "Same name", "completed": True}, expect=200))
    assert body == {**created, "completed": True}


def test_patch_description_to_null_is_allowed(client, db_session):
    """description is nullable in the table, so an explicit null clears it."""
    created = create_task(client, title="Has notes", description="notes")
    body = json_of(call(client, "PATCH", f"/tasks/{created['id']}", json={"description": None}, expect=200))
    assert body["description"] is None
    assert stored_task(db_session, created["id"])["description"] is None


# ============================================================================
# Regressions: bugs found by probing the starter API
# ============================================================================
#
# Every one of these fails on the starter code. All were 500s there except
# ids 0 and -1, which were 404s: harmless, but they are not ids any row can
# have, so they are now refused with the out-of-range ones.


def test_patch_to_another_tasks_title_returns_409(client, db_session):
    """Starter: UNIQUE constraint IntegrityError, 500."""
    first = create_task(client, title="Alpha")
    second = create_task(client, title="Beta")

    response = call(client, "PATCH", f"/tasks/{second['id']}", json={"title": "Alpha"}, expect=409)

    assert_error_envelope(response, "Conflict")
    assert stored_task(db_session, first["id"]) == first
    assert stored_task(db_session, second["id"]) == second  # rolled back, not half-applied
    # The session was rolled back cleanly, so the API is still usable.
    call(client, "PATCH", f"/tasks/{second['id']}", json={"completed": True}, expect=200)


@pytest.mark.parametrize("field", ["title", "completed"])
def test_patch_null_into_required_field_returns_422(client, db_session, field):
    """Starter: NOT NULL constraint IntegrityError, 500."""
    created = create_task(client, title="Keep my fields")

    response = call(client, "PATCH", f"/tasks/{created['id']}", json={field: None}, expect=422)

    body = assert_error_envelope(response, "ValidationError")
    assert body["errors"][0]["loc"] == ["body", field]
    assert stored_task(db_session, created["id"]) == created


def test_overlong_patch_description_is_rejected_and_the_list_survives(client, db_session):
    """Starter: the 600-character value was SAVED, the PATCH was a 500, and
    from then on GET /tasks was a 500 for every client, because the response
    model could not validate the stored row."""
    created = create_task(client, title="Fragile")

    call(client, "PATCH", f"/tasks/{created['id']}", json={"description": "d" * 600}, expect=422)

    assert stored_task(db_session, created["id"]) == created  # nothing was saved
    assert json_of(call(client, "GET", "/tasks", expect=200)) == [created]
    call(client, "GET", f"/tasks/{created['id']}", expect=200)


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
@pytest.mark.parametrize("task_id", [0, -1, SQLITE_MAX_INTEGER + 1, 10**20])
def test_out_of_range_id_returns_422(client, method, task_id):
    """Starter: ids past the 64-bit limit raised OverflowError in the sqlite3
    driver (500); 0 and -1 were 404."""
    kwargs = {"json": {}} if method == "PATCH" else {}
    response = call(client, method, f"/tasks/{task_id}", expect=422, **kwargs)
    assert_error_envelope(response, "ValidationError")


def test_largest_valid_id_is_a_normal_404(client):
    call(client, "GET", f"/tasks/{SQLITE_MAX_INTEGER}", expect=404)


def test_unique_clash_at_commit_is_409_not_500(client, db_session):
    """Two POSTs with the same new title can both pass create_task()'s
    duplicate check before either commits. The database's UNIQUE constraint
    then rejects the second one at commit time. This reproduces that moment
    directly: a duplicate row reaches commit, bypassing the check."""
    create_task(client, title="Raced")
    db_session.add(Task(title="Raced"))

    with pytest.raises(HTTPException) as caught:
        commit_or_conflict(db_session)

    assert caught.value.status_code == 409
    assert stored_titles(db_session) == ["Raced"]  # rolled back; the session is usable again


# ============================================================================
# Regressions: bugs found by the second round of edge-case probing
# ============================================================================


@pytest.mark.parametrize("moment", ["before UPDATE", "at commit"])
def test_patch_racing_a_delete_returns_404_not_500(client, db_session, deleted_mid_request, moment):
    """Before: the PATCH loaded the task, then another request deleted it, and
    the save raised StaleDataError or "Could not refresh instance" (500). On a
    live server, 4 of 3,000 racing PATCH/DELETE requests failed this way."""
    created = create_task(client, title="Contested")

    with deleted_mid_request(created["id"], moment):
        response = call(client, "PATCH", f"/tasks/{created['id']}", json={"completed": True}, expect=404)

    assert_error_envelope(response, "NotFound")
    assert stored_task(db_session, created["id"]) is None
    create_task(client, title="Still works")  # the session was left usable


def test_only_one_of_two_racing_deletes_reports_success(client, db_session, deleted_mid_request):
    """Before: a DELETE whose row had just been removed by another request
    still answered 200 "Task N deleted"; on a live server, 61 DELETEs
    reported success for 30 tasks."""
    created = create_task(client, title="Deleted once")

    with deleted_mid_request(created["id"], "before DELETE"):
        response = call(client, "DELETE", f"/tasks/{created['id']}", expect=404)

    assert_error_envelope(response, "NotFound")
    assert stored_task(db_session, created["id"]) is None


def test_deleted_ids_are_never_reused(client, db_session):
    """Before: deleting the newest task freed its id for the next task, so a
    stale link or delayed PATCH reached a different task."""
    create_task(client, title="Older")
    newest = create_task(client, title="Newest")
    call(client, "DELETE", f"/tasks/{newest['id']}", expect=200)

    replacement = create_task(client, title="Replacement")

    assert replacement["id"] > newest["id"]
    call(client, "GET", f"/tasks/{newest['id']}", expect=404)


@pytest.mark.parametrize(
    "method, body",
    [
        ("PATCH", {"complete": True}),               # a typo for "completed"
        ("PATCH", {"completed": True, "id": 999}),
        ("POST", {"title": "Chosen id", "id": 999}),
        ("POST", {"title": "Typo", "descripton": "x"}),
    ],
    ids=["patch-typo", "patch-id", "post-id", "post-typo"],
)
def test_unknown_fields_are_422(client, db_session, method, body):
    """Before: unknown fields were dropped, so PATCH {"complete": true}
    answered 200 while changing nothing."""
    existing = create_task(client, title="Existing")
    path = f"/tasks/{existing['id']}" if method == "PATCH" else "/tasks"

    response = call(client, method, path, json=body, expect=422)

    body_out = assert_error_envelope(response, "ValidationError")
    assert body_out["errors"][0]["type"] == "extra_forbidden"
    assert stored_titles(db_session) == ["Existing"]
    assert stored_task(db_session, existing["id"]) == existing


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
@pytest.mark.parametrize("spelling", ["1_0", "+1", "%201", "01", "0010"])
def test_ids_must_be_plain_digits(client, db_session, method, spelling):
    """Before: /tasks/1_0 served task 10, and /tasks/+1 and /tasks/%201 served
    task 1. The rows are inserted directly so every spelling has a real task
    it could wrongly reach, without spending the rate-limit budget."""
    db_session.add_all(Task(title=f"Task {n}") for n in range(1, 11))
    db_session.commit()

    kwargs = {"json": {"completed": True}} if method == "PATCH" else {}
    response = call(client, method, f"/tasks/{spelling}", expect=422, **kwargs)

    assert_error_envelope(response, "ValidationError")
    assert len(stored_titles(db_session)) == 10
    assert not any(stored_task(db_session, n)["completed"] for n in (1, 10))
