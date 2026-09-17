# project/starter/tests/conftest.py
# Module 5 Project — Test fixtures

import os
import sys

# Put the project root on the import path so `import app` works when running `pytest`
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    """Test client backed by a fresh in-memory database for every test."""
    Base.metadata.create_all(bind=test_engine)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


# ---------- Helpers shared by tests ----------

def register_user(client, username="alice", email="alice@example.com", password="password123"):
    """Register a user and verify the request succeeded before returning."""
    response = client.post(
        "/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_token(client, username="alice", password="password123"):
    """Log in and return the access token, failing loudly if login fails."""
    response = client.post("/auth/token", data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(client, username="alice", email="alice@example.com", password="password123"):
    """Register + log in, returning ready-to-use Authorization headers."""
    register_user(client, username, email, password)
    token = get_token(client, username, password)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def register():
    """Fixture that returns the register_user helper."""
    return register_user


@pytest.fixture
def alice_headers(client):
    return auth_headers(client, "alice", "alice@example.com")


@pytest.fixture
def bob_headers(client):
    return auth_headers(client, "bob", "bob@example.com")
