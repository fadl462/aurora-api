def test_documents_require_auth(client):
    response = client.get("/v1/documents")
    assert response.status_code == 401


def test_no_documents_for_new_user(client, auth_headers):
    headers = auth_headers()
    response = client.get("/v1/documents", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_create_document_with_defaults(client, auth_headers):
    headers = auth_headers()
    response = client.post("/v1/documents", json={}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Untitled document"
    assert body["content"] == ""


def test_create_document_with_content(client, auth_headers):
    headers = auth_headers()
    response = client.post(
        "/v1/documents", json={"title": "Client brief", "content": "Draft text here."}, headers=headers
    )
    body = response.json()
    assert body["title"] == "Client brief"
    assert body["content"] == "Draft text here."


def test_get_document(client, auth_headers):
    headers = auth_headers()
    created = client.post("/v1/documents", json={"title": "Fetchable"}, headers=headers).json()
    response = client.get(f"/v1/documents/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_nonexistent_document_returns_404(client, auth_headers):
    headers = auth_headers()
    response = client.get("/v1/documents/does-not-exist", headers=headers)
    assert response.status_code == 404


def test_update_document_content(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "v1"}, headers=headers).json()

    response = client.put(f"/v1/documents/{doc['id']}", json={"content": "v2"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "v2"
    assert body["title"] == "Draft"  # untouched — partial update


def test_update_document_title_only(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Old title", "content": "keep me"}, headers=headers).json()

    response = client.put(f"/v1/documents/{doc['id']}", json={"title": "New title"}, headers=headers)
    body = response.json()
    assert body["title"] == "New title"
    assert body["content"] == "keep me"


def test_update_bumps_updated_at(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={}, headers=headers).json()
    original_updated_at = doc["updated_at"]

    updated = client.put(f"/v1/documents/{doc['id']}", json={"content": "changed"}, headers=headers).json()
    assert updated["updated_at"] != original_updated_at


def test_updating_another_users_document_returns_404(client, auth_headers):
    headers_a = auth_headers("doc-owner@example.com", "correcthorse")
    headers_b = auth_headers("doc-intruder@example.com", "correcthorse")

    doc = client.post("/v1/documents", json={"title": "Private"}, headers=headers_a).json()
    response = client.put(f"/v1/documents/{doc['id']}", json={"content": "hijacked"}, headers=headers_b)
    assert response.status_code == 404


def test_list_documents_ordered_most_recently_updated_first(client, auth_headers):
    headers = auth_headers()
    first = client.post("/v1/documents", json={"title": "First"}, headers=headers).json()
    client.post("/v1/documents", json={"title": "Second"}, headers=headers).json()

    client.put(f"/v1/documents/{first['id']}", json={"content": "bump me to the top"}, headers=headers)

    listing = client.get("/v1/documents", headers=headers).json()
    assert listing[0]["id"] == first["id"]


def test_users_only_see_their_own_documents(client, auth_headers):
    headers_a = auth_headers("doc-list-a@example.com", "correcthorse")
    headers_b = auth_headers("doc-list-b@example.com", "correcthorse")

    client.post("/v1/documents", json={"title": "Alice's doc"}, headers=headers_a)

    docs_b = client.get("/v1/documents", headers=headers_b).json()
    assert "Alice's doc" not in {d["title"] for d in docs_b}
