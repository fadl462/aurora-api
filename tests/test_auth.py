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


def test_update_me_sets_a_new_name(client, auth_headers):
    headers = auth_headers()
    response = client.patch("/v1/auth/me", json={"name": "Sady"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Sady"

    # Persisted, not just echoed back — confirm via a fresh GET.
    me = client.get("/v1/auth/me", headers=headers).json()
    assert me["name"] == "Sady"


def test_update_me_strips_surrounding_whitespace(client, auth_headers):
    headers = auth_headers()
    response = client.patch("/v1/auth/me", json={"name": "  Padded  "}, headers=headers)
    assert response.json()["name"] == "Padded"


def test_update_me_with_empty_string_clears_name_back_to_null(client, auth_headers):
    headers = auth_headers()
    client.patch("/v1/auth/me", json={"name": "Sady"}, headers=headers)
    response = client.patch("/v1/auth/me", json={"name": ""}, headers=headers)
    assert response.json()["name"] is None


def test_update_me_with_whitespace_only_also_clears_name(client, auth_headers):
    headers = auth_headers()
    client.patch("/v1/auth/me", json={"name": "Sady"}, headers=headers)
    response = client.patch("/v1/auth/me", json={"name": "   "}, headers=headers)
    assert response.json()["name"] is None


def test_update_me_with_no_name_field_leaves_existing_name_untouched(client, auth_headers):
    headers = auth_headers()
    client.patch("/v1/auth/me", json={"name": "Sady"}, headers=headers)
    response = client.patch("/v1/auth/me", json={}, headers=headers)
    assert response.json()["name"] == "Sady"


def test_update_me_requires_auth(client):
    response = client.patch("/v1/auth/me", json={"name": "Sady"})
    assert response.status_code == 401


def test_update_me_does_not_affect_other_users(client, auth_headers):
    headers_a = auth_headers("name-a@example.com", "correcthorse")
    headers_b = auth_headers("name-b@example.com", "correcthorse")

    client.patch("/v1/auth/me", json={"name": "Alice"}, headers=headers_a)

    me_b = client.get("/v1/auth/me", headers=headers_b).json()
    assert me_b["name"] is None


def test_sign_out_device_revokes_that_sessions_refresh_token(client):
    client.post("/v1/auth/register", json={"email": "signout@example.com", "password": "correcthorse"})
    login = client.post(
        "/v1/auth/login", data={"username": "signout@example.com", "password": "correcthorse"}
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    refresh_token = login["refresh_token"]

    event_id = client.get("/v1/auth/sessions", headers=headers).json()[0]["id"]

    response = client.delete(f"/v1/auth/sessions/{event_id}", headers=headers)
    assert response.status_code == 204

    # The refresh token issued alongside that login must now be dead.
    refresh_response = client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_response.status_code == 401


def test_sign_out_device_keeps_the_login_event_as_history(client, auth_headers):
    headers = auth_headers()
    event_id = client.get("/v1/auth/sessions", headers=headers).json()[0]["id"]

    client.delete(f"/v1/auth/sessions/{event_id}", headers=headers)

    sessions = client.get("/v1/auth/sessions", headers=headers).json()
    assert any(s["id"] == event_id for s in sessions)  # still there — it's a history record, not deleted


def test_sign_out_device_requires_auth(client):
    response = client.delete("/v1/auth/sessions/some-id")
    assert response.status_code == 401


def test_sign_out_device_rejects_another_users_login_event(client, auth_headers):
    headers_a = auth_headers("signout-a@example.com", "correcthorse")
    headers_b = auth_headers("signout-b@example.com", "correcthorse")

    event_a = client.get("/v1/auth/sessions", headers=headers_a).json()[0]["id"]

    response = client.delete(f"/v1/auth/sessions/{event_a}", headers=headers_b)
    assert response.status_code == 404


def test_sign_out_device_on_unknown_id_returns_404(client, auth_headers):
    headers = auth_headers()
    response = client.delete("/v1/auth/sessions/not-a-real-id", headers=headers)
    assert response.status_code == 404


def test_sign_out_device_twice_is_idempotent(client, auth_headers):
    headers = auth_headers()
    event_id = client.get("/v1/auth/sessions", headers=headers).json()[0]["id"]

    first = client.delete(f"/v1/auth/sessions/{event_id}", headers=headers)
    second = client.delete(f"/v1/auth/sessions/{event_id}", headers=headers)
    assert first.status_code == 204
    assert second.status_code == 204  # already-revoked is still a valid end state, not an error


def test_signing_out_one_device_does_not_affect_another_login(client):
    client.post("/v1/auth/register", json={"email": "multi-device@example.com", "password": "correcthorse"})
    login1 = client.post(
        "/v1/auth/login", data={"username": "multi-device@example.com", "password": "correcthorse"}
    ).json()
    login2 = client.post(
        "/v1/auth/login", data={"username": "multi-device@example.com", "password": "correcthorse"}
    ).json()
    headers2 = {"Authorization": f"Bearer {login2['access_token']}"}

    sessions = client.get("/v1/auth/sessions", headers=headers2).json()
    # Most recent first — sessions[0] is login2's own event, sessions[1] is login1's.
    login1_event_id = sessions[1]["id"]

    client.delete(f"/v1/auth/sessions/{login1_event_id}", headers=headers2)

    # login2's own refresh token must still work — signing out a
    # different device's session shouldn't touch this one.
    refresh_response = client.post("/v1/auth/refresh", json={"refresh_token": login2["refresh_token"]})
    assert refresh_response.status_code == 200


def test_login_returns_a_refresh_token_too(client):
    client.post("/v1/auth/register", json={"email": "refresh-basic@example.com", "password": "correcthorse"})
    login = client.post(
        "/v1/auth/login", data={"username": "refresh-basic@example.com", "password": "correcthorse"}
    )
    body = login.json()
    assert "refresh_token" in body
    assert body["refresh_token"]  # non-empty


def test_refresh_returns_a_new_working_access_token(client):
    client.post("/v1/auth/register", json={"email": "refresh-flow@example.com", "password": "correcthorse"})
    login = client.post(
        "/v1/auth/login", data={"username": "refresh-flow@example.com", "password": "correcthorse"}
    )
    refresh_token = login.json()["refresh_token"]

    refreshed = client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    new_access_token = refreshed.json()["access_token"]

    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {new_access_token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "refresh-flow@example.com"


def test_refresh_rotates_the_token_so_it_cannot_be_reused(client):
    """Core security property: a refresh token is single-use. Replaying
    an already-redeemed one must fail, or a stolen token would work
    forever undetected."""
    client.post("/v1/auth/register", json={"email": "rotate@example.com", "password": "correcthorse"})
    login = client.post("/v1/auth/login", data={"username": "rotate@example.com", "password": "correcthorse"})
    old_refresh_token = login.json()["refresh_token"]

    first_use = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh_token})
    assert first_use.status_code == 200
    new_refresh_token = first_use.json()["refresh_token"]
    assert new_refresh_token != old_refresh_token

    replay = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh_token})
    assert replay.status_code == 401

    # The rotated (new) token should still work.
    second_use = client.post("/v1/auth/refresh", json={"refresh_token": new_refresh_token})
    assert second_use.status_code == 200


def test_refresh_rejects_garbage_token(client):
    response = client.post("/v1/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert response.status_code == 401


def test_refresh_rejects_expired_token(client):
    from datetime import datetime, timedelta, timezone
    from app import models
    from app import auth as auth_module

    client.post("/v1/auth/register", json={"email": "expired-refresh@example.com", "password": "correcthorse"})
    login = client.post(
        "/v1/auth/login", data={"username": "expired-refresh@example.com", "password": "correcthorse"}
    )
    refresh_token = login.json()["refresh_token"]

    # Force the stored token's expiry into the past, via the SAME
    # database the test client's request handling actually uses.
    import conftest as conftest_module

    db = conftest_module.TestingSessionLocal()
    record = (
        db.query(models.RefreshToken)
        .filter(models.RefreshToken.token_hash == auth_module._hash_refresh_token(refresh_token))
        .first()
    )
    record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    db.close()

    response = client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 401


def test_logout_revokes_the_refresh_token(client):
    client.post("/v1/auth/register", json={"email": "logout-test@example.com", "password": "correcthorse"})
    login = client.post(
        "/v1/auth/login", data={"username": "logout-test@example.com", "password": "correcthorse"}
    )
    refresh_token = login.json()["refresh_token"]

    logout_response = client.post("/v1/auth/logout", json={"refresh_token": refresh_token})
    assert logout_response.status_code == 204

    refresh_after_logout = client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_after_logout.status_code == 401


def test_logout_with_unknown_token_does_not_error(client):
    """Logging out should always succeed from the caller's perspective
    — the end state (this token doesn't work) is already true for a
    token that never existed, so there's nothing to reject."""
    response = client.post("/v1/auth/logout", json={"refresh_token": "never-existed"})
    assert response.status_code == 204


def test_each_login_issues_its_own_independent_refresh_token(client):
    client.post("/v1/auth/register", json={"email": "multi-refresh@example.com", "password": "correcthorse"})
    login1 = client.post(
        "/v1/auth/login", data={"username": "multi-refresh@example.com", "password": "correcthorse"}
    )
    login2 = client.post(
        "/v1/auth/login", data={"username": "multi-refresh@example.com", "password": "correcthorse"}
    )
    token1 = login1.json()["refresh_token"]
    token2 = login2.json()["refresh_token"]
    assert token1 != token2

    # Redeeming one must not invalidate the other independent session.
    client.post("/v1/auth/refresh", json={"refresh_token": token1})
    still_works = client.post("/v1/auth/refresh", json={"refresh_token": token2})
    assert still_works.status_code == 200


def test_login_records_a_real_login_event(client, auth_headers):
    headers = auth_headers()
    sessions = client.get("/v1/auth/sessions", headers=headers).json()
    assert len(sessions) == 1
    assert sessions[0]["device_label"]  # non-empty — some label was derived
    assert "id" in sessions[0]
    assert "created_at" in sessions[0]


def test_login_event_never_exposes_raw_ip_or_user_agent(client, auth_headers):
    """The person needs 'was this me,' not their own IP echoed back —
    ip_address/user_agent are logged server-side but must never appear
    in the API response (see LoginEventOut in schemas.py)."""
    headers = auth_headers()
    sessions = client.get("/v1/auth/sessions", headers=headers).json()
    assert "ip_address" not in sessions[0]
    assert "user_agent" not in sessions[0]


def test_sessions_requires_auth(client):
    response = client.get("/v1/auth/sessions")
    assert response.status_code == 401


def test_sessions_only_shows_current_users_own_events(client, auth_headers):
    headers_a = auth_headers("sessions-a@example.com", "correcthorse")
    headers_b = auth_headers("sessions-b@example.com", "correcthorse")

    sessions_a = client.get("/v1/auth/sessions", headers=headers_a).json()
    sessions_b = client.get("/v1/auth/sessions", headers=headers_b).json()

    assert len(sessions_a) == 1
    assert len(sessions_b) == 1
    assert sessions_a[0]["id"] != sessions_b[0]["id"]


def test_sessions_ordered_most_recent_first(client):
    client.post("/v1/auth/register", json={"email": "multi-login@example.com", "password": "correcthorse"})
    login1 = client.post(
        "/v1/auth/login", data={"username": "multi-login@example.com", "password": "correcthorse"}
    )
    headers = {"Authorization": f"Bearer {login1.json()['access_token']}"}

    login2 = client.post(
        "/v1/auth/login", data={"username": "multi-login@example.com", "password": "correcthorse"}
    )
    headers2 = {"Authorization": f"Bearer {login2.json()['access_token']}"}

    sessions = client.get("/v1/auth/sessions", headers=headers2).json()
    assert len(sessions) == 2
    # Most recent first: the second login's event should sort before the first's.
    assert sessions[0]["created_at"] >= sessions[1]["created_at"]


def test_device_label_reflects_the_actual_user_agent_sent(client):
    client.post("/v1/auth/register", json={"email": "ua-test@example.com", "password": "correcthorse"})
    login = client.post(
        "/v1/auth/login",
        data={"username": "ua-test@example.com", "password": "correcthorse"},
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        },
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    sessions = client.get("/v1/auth/sessions", headers=headers).json()
    assert sessions[0]["device_label"] == "Chrome on Windows"


def test_login_from_private_test_client_leaves_location_unresolved(client, auth_headers):
    """The test client's connection doesn't have a real public IP, so
    location_label should honestly be null — not a fabricated place."""
    headers = auth_headers()
    sessions = client.get("/v1/auth/sessions", headers=headers).json()
    assert sessions[0]["location_label"] is None


def test_background_location_resolution_updates_the_event(monkeypatch, db_session):
    """Exercises _resolve_and_store_location directly, since the real
    background task uses app.database.SessionLocal (the production
    session factory) rather than the test's overridden in-memory
    session — by design, this is the one path that needs a direct unit
    test rather than a full HTTP round trip."""
    from app.routers import auth as auth_router
    from app import models
    import conftest as conftest_module

    user = models.User(email="bg-loc@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    event = models.LoginEvent(
        user_id=user.id, ip_address="8.8.8.8", device_label="Chrome on Windows", location_label=None
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    monkeypatch.setattr(auth_router, "SessionLocal", conftest_module.TestingSessionLocal)
    monkeypatch.setattr(auth_router.login_activity, "resolve_location", lambda ip: "Mountain View, California, US")

    auth_router._resolve_and_store_location(event.id, "8.8.8.8")

    db_session.refresh(event)
    assert event.location_label == "Mountain View, California, US"


def test_background_location_resolution_leaves_event_untouched_on_lookup_failure(monkeypatch, db_session):
    from app.routers import auth as auth_router
    from app import models
    import conftest as conftest_module

    user = models.User(email="bg-loc-fail@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    event = models.LoginEvent(
        user_id=user.id, ip_address="8.8.8.8", device_label="Chrome on Windows", location_label=None
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    monkeypatch.setattr(auth_router, "SessionLocal", conftest_module.TestingSessionLocal)
    monkeypatch.setattr(auth_router.login_activity, "resolve_location", lambda ip: None)

    auth_router._resolve_and_store_location(event.id, "8.8.8.8")

    db_session.refresh(event)
    assert event.location_label is None
