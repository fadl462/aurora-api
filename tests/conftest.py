"""
Shared test fixtures. Both test_auth.py and test_conversations.py use
these via pytest's fixture injection rather than each creating their
own engine — that duplication was a real bug: two separate in-memory
engines fighting over the same `app.dependency_overrides` dict, so
whichever test file was collected last silently won, leaving the
other's tables missing.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, get_db

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
_client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    return _client


@pytest.fixture
def db_session():
    """Direct access to the SAME in-memory test database the API uses,
    for tests that need to manipulate state the API has no endpoint
    for (e.g. forcing a user's balance to zero to test an edge case)."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def auth_headers(client):
    def _make(email: str = "test@example.com", password: str = "correcthorse") -> dict:
        client.post("/v1/auth/register", json={"email": email, "password": password})
        login = client.post("/v1/auth/login", data={"username": email, "password": password})
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _make
