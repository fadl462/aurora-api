"""
Tests the real endpoints implementing docs/06-api-specification.md.
Every endpoint under test requires auth, so each test uses the shared
`auth_headers` fixture from conftest.py.
"""


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_conversations_require_auth(client):
    response = client.post("/v1/conversations", json={"title": "No auth"})
    assert response.status_code == 401


def test_create_conversation(client, auth_headers):
    headers = auth_headers()
    response = client.post("/v1/conversations", json={"title": "My first chat"}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "My first chat"
    assert "id" in body
    assert "created_at" in body


def test_create_conversation_without_title(client, auth_headers):
    headers = auth_headers()
    response = client.post("/v1/conversations", json={}, headers=headers)
    assert response.status_code == 201
    assert response.json()["title"] is None


def test_get_conversation(client, auth_headers):
    headers = auth_headers()
    created = client.post("/v1/conversations", json={"title": "Fetchable"}, headers=headers).json()
    response = client.get(f"/v1/conversations/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_conversation_not_found(client, auth_headers):
    headers = auth_headers()
    response = client.get("/v1/conversations/does-not-exist", headers=headers)
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"]["code"] == "conversation_not_found"
    assert "request_id" in body["detail"]["error"]


def test_user_cannot_access_another_users_conversation(client, auth_headers):
    headers_a = auth_headers("alice@example.com", "correcthorse")
    headers_b = auth_headers("bob@example.com", "correcthorse")

    conv = client.post("/v1/conversations", json={"title": "Alice's private chat"}, headers=headers_a).json()

    # Bob tries to read Alice's conversation — should 404, not 403,
    # so existence isn't leaked to a user who doesn't own it.
    response = client.get(f"/v1/conversations/{conv['id']}", headers=headers_b)
    assert response.status_code == 404


def test_send_message_creates_both_user_and_assistant_messages(client, auth_headers):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()
    response = client.post(
        f"/v1/conversations/{conv['id']}/messages",
        json={"content": "Hello there"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "assistant"
    assert body["conversation_id"] == conv["id"]
    assert body["model_used"] == "aurora-stub"

    messages = client.get(f"/v1/conversations/{conv['id']}/messages", headers=headers).json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello there"
    assert messages[1]["role"] == "assistant"


def test_message_without_api_key_configured_returns_labeled_stub(client, auth_headers, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()
    response = client.post(
        f"/v1/conversations/{conv['id']}/messages",
        json={"content": "Please compare pricing with sources"},
        headers=headers,
    )
    body = response.json()
    assert body["model_used"] == "aurora-stub"
    assert "placeholder response" in body["content"]
    # Honesty constraint: no real search tool exists yet, so this must
    # never fabricate citations or a confidence score, with or without
    # a configured API key.
    assert body["citations"] is None
    assert body["confidence"] is None


def test_plain_message_has_no_citations_or_confidence(client, auth_headers, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()
    response = client.post(
        f"/v1/conversations/{conv['id']}/messages",
        json={"content": "Hey, how are you?"},
        headers=headers,
    )
    body = response.json()
    assert body["citations"] is None
    assert body["confidence"] is None


def test_send_message_to_nonexistent_conversation_returns_404(client, auth_headers):
    headers = auth_headers()
    response = client.post(
        "/v1/conversations/does-not-exist/messages",
        json={"content": "Hello"},
        headers=headers,
    )
    assert response.status_code == 404


def test_send_message_rejects_empty_content(client, auth_headers):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()
    response = client.post(
        f"/v1/conversations/{conv['id']}/messages",
        json={"content": ""},
        headers=headers,
    )
    assert response.status_code == 422


def test_message_ordering_is_chronological(client, auth_headers):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()
    client.post(f"/v1/conversations/{conv['id']}/messages", json={"content": "first"}, headers=headers)
    client.post(f"/v1/conversations/{conv['id']}/messages", json={"content": "second"}, headers=headers)

    messages = client.get(f"/v1/conversations/{conv['id']}/messages", headers=headers).json()
    assert len(messages) == 4
    assert messages[0]["content"] == "first"
    assert messages[2]["content"] == "second"


def test_list_conversations_empty_for_new_user(client, auth_headers):
    headers = auth_headers()
    response = client.get("/v1/conversations", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_list_conversations_only_returns_own_conversations(client, auth_headers):
    headers_a = auth_headers("lister-a@example.com", "correcthorse")
    headers_b = auth_headers("lister-b@example.com", "correcthorse")

    client.post("/v1/conversations", json={"title": "Alice 1"}, headers=headers_a)
    client.post("/v1/conversations", json={"title": "Alice 2"}, headers=headers_a)
    client.post("/v1/conversations", json={"title": "Bob 1"}, headers=headers_b)

    alice_list = client.get("/v1/conversations", headers=headers_a).json()
    assert len(alice_list) == 2
    assert {c["title"] for c in alice_list} == {"Alice 1", "Alice 2"}


def test_list_conversations_ordered_most_recently_updated_first(client, auth_headers):
    headers = auth_headers()
    first = client.post("/v1/conversations", json={"title": "First created"}, headers=headers).json()
    client.post("/v1/conversations", json={"title": "Second created"}, headers=headers).json()

    # Sending a message to the first conversation should bump it to the top.
    client.post(f"/v1/conversations/{first['id']}/messages", json={"content": "hello"}, headers=headers)

    listing = client.get("/v1/conversations", headers=headers).json()
    assert listing[0]["id"] == first["id"]


def test_first_message_auto_titles_untitled_conversation(client, auth_headers):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()
    assert conv["title"] is None

    client.post(
        f"/v1/conversations/{conv['id']}/messages",
        json={"content": "What's the best way to structure a monorepo?"},
        headers=headers,
    )

    updated = client.get(f"/v1/conversations/{conv['id']}", headers=headers).json()
    assert updated["title"] == "What's the best way to structure a monorepo?"


def test_long_first_message_title_is_truncated(client, auth_headers):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={}, headers=headers).json()
    long_message = "a" * 100

    client.post(
        f"/v1/conversations/{conv['id']}/messages",
        json={"content": long_message},
        headers=headers,
    )

    updated = client.get(f"/v1/conversations/{conv['id']}", headers=headers).json()
    assert len(updated["title"]) == 61  # 60 chars + ellipsis
    assert updated["title"].endswith("…")


def test_explicit_title_is_not_overwritten_by_auto_title(client, auth_headers):
    headers = auth_headers()
    conv = client.post("/v1/conversations", json={"title": "My chosen title"}, headers=headers).json()

    client.post(
        f"/v1/conversations/{conv['id']}/messages",
        json={"content": "This should not become the title"},
        headers=headers,
    )

    updated = client.get(f"/v1/conversations/{conv['id']}", headers=headers).json()
    assert updated["title"] == "My chosen title"
