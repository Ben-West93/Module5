# project/starter/tests/test_tasks.py
# Module 5 Project — Test suite

import logging



def test_health_check(client):
    """Health check returns 200."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------- Auth ----------

def test_register(client, register):
    payload = {"username": "alice", "email": "alice@example.com", "password": "password123"}
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == payload["username"]
    assert data["email"] == payload["email"]
    assert isinstance(data["id"], int)
    assert "password" not in data and "hashed_password" not in data


def test_register_duplicate_username_rejected(client, register):
    register(client)
    response = client.post(
        "/auth/register",
        json={"username": "alice", "email": "other@example.com", "password": "password123"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Username already registered"
    assert response.json()["error"] == "duplicate"


def test_register_invalid_input_uses_custom_error_format(client):
    response = client.post(
        "/auth/register",
        json={"username": "al", "email": "not-an-email", "password": "short"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Validation error"
    fields = {err["field"] for err in body["errors"]}
    assert {"username", "email", "password"} <= fields


def test_login(client, register):
    register(client)
    response = client.post("/auth/token", data={"username": "alice", "password": "password123"})
    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert isinstance(data["access_token"], str) and len(data["access_token"]) > 20


def test_login_wrong_password(client, register):
    register(client)
    response = client.post("/auth/token", data={"username": "alice", "password": "wrongpass1"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"


def test_me_returns_current_user(client, alice_headers):
    response = client.get("/auth/me", headers=alice_headers)
    assert response.status_code == 200
    assert response.json()["username"] == "alice"
    assert response.json()["email"] == "alice@example.com"


def test_protected_endpoint_requires_token(client):
    assert client.get("/tasks/").status_code == 401
    bad = client.get("/tasks/", headers={"Authorization": "Bearer not.a.real.token"})
    assert bad.status_code == 401


# ---------- Tasks ----------

def test_create_task_authenticated(client, alice_headers):
    payload = {"title": "Write tests", "description": "Cover all endpoints"}
    response = client.post("/tasks/", json=payload, headers=alice_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == payload["title"]
    assert data["description"] == payload["description"]
    assert data["completed"] is False

    me = client.get("/auth/me", headers=alice_headers).json()
    assert data["owner_id"] == me["id"]

    # Verify it was actually persisted, not just echoed back
    fetched = client.get(f"/tasks/{data['id']}", headers=alice_headers)
    assert fetched.status_code == 200
    assert fetched.json() == data


def test_list_tasks_scoped_to_user(client, alice_headers, bob_headers):
    alice_task = client.post("/tasks/", json={"title": "Alice task"}, headers=alice_headers)
    bob_task = client.post("/tasks/", json={"title": "Bob task"}, headers=bob_headers)
    assert alice_task.status_code == 201 and bob_task.status_code == 201

    alice_list = client.get("/tasks/", headers=alice_headers)
    assert alice_list.status_code == 200
    assert [t["title"] for t in alice_list.json()] == ["Alice task"]

    bob_list = client.get("/tasks/", headers=bob_headers)
    assert bob_list.status_code == 200
    assert [t["title"] for t in bob_list.json()] == ["Bob task"]

    # Bob cannot read, edit, or delete Alice's task
    alice_id = alice_task.json()["id"]
    assert client.get(f"/tasks/{alice_id}", headers=bob_headers).status_code == 404
    assert client.patch(f"/tasks/{alice_id}", json={"completed": True}, headers=bob_headers).status_code == 404
    assert client.delete(f"/tasks/{alice_id}", headers=bob_headers).status_code == 404


def test_patch_task(client, alice_headers):
    created = client.post("/tasks/", json={"title": "Old title"}, headers=alice_headers)
    assert created.status_code == 201
    task_id = created.json()["id"]

    response = client.patch(f"/tasks/{task_id}", json={"completed": True}, headers=alice_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["completed"] is True
    assert data["title"] == "Old title"  # untouched fields keep their values

    empty = client.patch(f"/tasks/{task_id}", json={}, headers=alice_headers)
    assert empty.status_code == 400


def test_delete_task(client, alice_headers):
    created = client.post("/tasks/", json={"title": "Temporary"}, headers=alice_headers)
    assert created.status_code == 201
    task_id = created.json()["id"]

    response = client.delete(f"/tasks/{task_id}", headers=alice_headers)
    assert response.status_code == 200
    assert client.get(f"/tasks/{task_id}", headers=alice_headers).status_code == 404
    assert client.delete(f"/tasks/{task_id}", headers=alice_headers).status_code == 404


def test_get_task_suggest(client, alice_headers):
    created = client.post("/tasks/", json={"title": "Plan sprint"}, headers=alice_headers)
    assert created.status_code == 201
    task_id = created.json()["id"]

    response = client.get(f"/tasks/{task_id}/suggest", headers=alice_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["title"] == "Plan sprint"
    assert data["source"] == "mock"
    assert isinstance(data["suggestion"], str) and data["suggestion"]

    assert client.get("/tasks/9999/suggest", headers=alice_headers).status_code == 404


def test_not_found_uses_custom_error_format(client, alice_headers):
    response = client.get("/tasks/9999", headers=alice_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Task 9999 not found", "error": "not_found"}


def test_background_task_logs_task_creation(client, alice_headers, caplog):
    with caplog.at_level(logging.INFO, logger="task_manager"):
        response = client.post("/tasks/", json={"title": "Logged task"}, headers=alice_headers)
    assert response.status_code == 201
    task_id = response.json()["id"]
    assert f"User alice created task {task_id}" in caplog.text
