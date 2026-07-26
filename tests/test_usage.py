from app import models as m
from app.models import STARTING_TOKEN_BALANCE


def test_new_user_starts_with_full_balance(client, auth_headers):
    headers = auth_headers()
    response = client.get("/v1/usage", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == STARTING_TOKEN_BALANCE
    assert body["starting_balance"] == STARTING_TOKEN_BALANCE
    assert body["percent_remaining"] == 100.0


def test_usage_requires_auth(client):
    response = client.get("/v1/usage")
    assert response.status_code == 401


def test_sending_a_message_deducts_real_tokens(client, auth_headers):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()

    before = client.get("/v1/usage", headers=headers).json()["balance"]
    client.post(f"/v1/conversations/{conv['id']}/messages", json={"content": "Hello there"}, headers=headers)
    after = client.get("/v1/usage", headers=headers).json()["balance"]

    assert after < before


def test_percent_remaining_tracks_balance(client, auth_headers):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()
    client.post(f"/v1/conversations/{conv['id']}/messages", json={"content": "hi"}, headers=headers)

    usage = client.get("/v1/usage", headers=headers).json()
    expected_percent = round(usage["balance"] / STARTING_TOKEN_BALANCE * 100, 1)
    assert usage["percent_remaining"] == expected_percent


def test_running_out_of_balance_blocks_further_messages(client, auth_headers, db_session):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()

    # Drain the balance directly rather than sending thousands of
    # messages — we're testing the enforcement boundary, not the
    # deduction math (that's covered by test_sending_a_message_deducts_real_tokens).
    user = db_session.query(m.User).filter(m.User.email == "test@example.com").first()
    user.token_balance = 0
    db_session.commit()

    response = client.post(f"/v1/conversations/{conv['id']}/messages", json={"content": "one more"}, headers=headers)
    assert response.status_code == 402
    body = response.json()
    assert body["detail"]["error"]["code"] == "token_balance_exhausted"


def test_balance_never_goes_negative(client, auth_headers, db_session):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()

    user = db_session.query(m.User).filter(m.User.email == "test@example.com").first()
    user.token_balance = 1  # just barely enough to attempt a send
    db_session.commit()

    client.post(
        f"/v1/conversations/{conv['id']}/messages",
        json={"content": "a longer message than one token"},
        headers=headers,
    )
    usage = client.get("/v1/usage", headers=headers).json()
    assert usage["balance"] >= 0


def test_exhausted_balance_does_not_create_a_message(client, auth_headers, db_session):
    """A blocked send must not silently create a message anyway —
    confirms the balance check happens before any DB write, not after."""
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()

    user = db_session.query(m.User).filter(m.User.email == "test@example.com").first()
    user.token_balance = 0
    db_session.commit()

    client.post(f"/v1/conversations/{conv['id']}/messages", json={"content": "blocked"}, headers=headers)
    messages = client.get(f"/v1/conversations/{conv['id']}/messages", headers=headers).json()
    assert messages == []
