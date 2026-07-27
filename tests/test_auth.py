def test_register_creates_user(client):
    response = client.post("/v1/auth/register", json={"email": "fadl@example.com", "password": "correcthorse"})
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "fadl@example.com"
    assert "id" in body
    assert "hashed_password" not in body  # never leak the hash


def test_register_accepts_optional_name(client):
    response = client.post(
        "/v1/auth/register",
        json={"email": "named@example.com", "password": "correcthorse", "name": "Sady"},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Sady"


def test_register_without_name_returns_null_name(client):
    response = client.post(
        "/v1/auth/register", json={"email": "noname@example.com", "password": "correcthorse"}
    )
    assert response.status_code == 201
    assert response.json()["name"] is None


def test_register_rejects_duplicate_email(client):
    client.post("/v1/auth/register", json={"email": "dupe@example.com", "password": "correcthorse"})
    response = client.post("/v1/auth/register", json={"email": "dupe@example.com", "password": "different"})
    assert response.status_code == 409


def test_register_rejects_short_password(client):
    response = client.post("/v1/auth/register", json={"email": "short@example.com", "password": "abc"})
    assert response.status_code == 422


def test_login_returns_token(client):
    client.post("/v1/auth/register", json={"email": "login@example.com", "password": "correcthorse"})
    response = client.post(
        "/v1/auth/login",
        data={"username": "login@example.com", "password": "correcthorse"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


def test_login_rejects_wrong_password(client):
    client.post("/v1/auth/register", json={"email": "wrongpw@example.com", "password": "correcthorse"})
    response = client.post(
        "/v1/auth/login",
        data={"username": "wrongpw@example.com", "password": "incorrect"},
    )
    assert response.status_code == 401


def test_login_rejects_unknown_email(client):
    response = client.post(
        "/v1/auth/login",
        data={"username": "nobody@example.com", "password": "whatever"},
    )
    assert response.status_code == 401


def test_me_requires_token(client):
    response = client.get("/v1/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user_with_valid_token(client):
    client.post("/v1/auth/register", json={"email": "me@example.com", "password": "correcthorse"})
    login = client.post("/v1/auth/login", data={"username": "me@example.com", "password": "correcthorse"})
    token = login.json()["access_token"]

    response = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


def test_me_rejects_garbage_token(client):
    response = client.get("/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
