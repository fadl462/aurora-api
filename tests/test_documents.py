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


def test_document_can_be_created_inside_a_project(client, auth_headers):
    headers = auth_headers()
    project = client.post("/v1/projects", json={"name": "GAYO"}, headers=headers).json()

    doc = client.post(
        "/v1/documents", json={"title": "Project doc", "project_id": project["id"]}, headers=headers
    ).json()
    assert doc["project_id"] == project["id"]


def test_document_without_project_id_still_works(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Personal doc"}, headers=headers).json()
    assert doc["project_id"] is None


def test_document_creation_rejects_another_users_project(client, auth_headers):
    headers_a = auth_headers("doc-proj-a@example.com", "correcthorse")
    headers_b = auth_headers("doc-proj-b@example.com", "correcthorse")
    project_a = client.post("/v1/projects", json={"name": "Alice's project"}, headers=headers_a).json()

    response = client.post(
        "/v1/documents", json={"title": "Sneaky", "project_id": project_a["id"]}, headers=headers_b
    )
    assert response.status_code == 404


def test_listing_documents_with_no_project_id_excludes_project_documents(client, auth_headers):
    """The real context wall: the default/unscoped view must not leak a
    project's documents into it, and vice versa."""
    headers = auth_headers()
    project = client.post("/v1/projects", json={"name": "GAYO"}, headers=headers).json()

    client.post("/v1/documents", json={"title": "Personal doc"}, headers=headers)
    client.post("/v1/documents", json={"title": "Project doc", "project_id": project["id"]}, headers=headers)

    unscoped = client.get("/v1/documents", headers=headers).json()
    titles = {d["title"] for d in unscoped}
    assert "Personal doc" in titles
    assert "Project doc" not in titles


def test_listing_documents_scoped_to_a_project_excludes_personal_documents(client, auth_headers):
    headers = auth_headers()
    project = client.post("/v1/projects", json={"name": "GAYO"}, headers=headers).json()

    client.post("/v1/documents", json={"title": "Personal doc"}, headers=headers)
    client.post("/v1/documents", json={"title": "Project doc", "project_id": project["id"]}, headers=headers)

    scoped = client.get(f"/v1/documents?project_id={project['id']}", headers=headers).json()
    titles = {d["title"] for d in scoped}
    assert "Project doc" in titles
    assert "Personal doc" not in titles


def test_listing_documents_for_another_users_project_returns_404(client, auth_headers):
    headers_a = auth_headers("doc-scope-a@example.com", "correcthorse")
    headers_b = auth_headers("doc-scope-b@example.com", "correcthorse")
    project_a = client.post("/v1/projects", json={"name": "Alice's project"}, headers=headers_a).json()

    response = client.get(f"/v1/documents?project_id={project_a['id']}", headers=headers_b)
    assert response.status_code == 404


def test_first_edit_snapshots_the_original_content(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "Original text"}, headers=headers).json()

    client.put(f"/v1/documents/{doc['id']}", json={"content": "Edited text"}, headers=headers)

    versions = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()
    assert len(versions) == 1
    assert versions[0]["content"] == "Original text"


def test_identical_content_does_not_create_a_version(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "Same text"}, headers=headers).json()

    client.put(f"/v1/documents/{doc['id']}", json={"content": "Same text"}, headers=headers)

    versions = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()
    assert versions == []


def test_rapid_successive_edits_are_throttled_to_one_snapshot(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "v1"}, headers=headers).json()

    client.put(f"/v1/documents/{doc['id']}", json={"content": "v2"}, headers=headers)  # snapshots "v1"
    client.put(f"/v1/documents/{doc['id']}", json={"content": "v3"}, headers=headers)  # too soon — no new snapshot

    versions = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()
    assert len(versions) == 1
    assert versions[0]["content"] == "v1"


def test_a_new_snapshot_is_taken_once_the_throttle_window_has_elapsed(client, auth_headers, db_session):
    from datetime import datetime, timedelta, timezone
    from app import models

    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "v1"}, headers=headers).json()

    client.put(f"/v1/documents/{doc['id']}", json={"content": "v2"}, headers=headers)  # snapshots "v1"

    # Backdate the one existing version past the throttle window, as if
    # real time had actually passed, rather than making the test slow.
    version = db_session.query(models.DocumentVersion).filter_by(document_id=doc["id"]).first()
    version.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_session.commit()

    client.put(f"/v1/documents/{doc['id']}", json={"content": "v3"}, headers=headers)  # snapshots "v2"

    versions = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()
    assert len(versions) == 2
    contents = {v["content"] for v in versions}
    assert contents == {"v1", "v2"}


def test_versions_ordered_most_recent_first(client, auth_headers, db_session):
    from datetime import datetime, timedelta, timezone
    from app import models

    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "v1"}, headers=headers).json()
    client.put(f"/v1/documents/{doc['id']}", json={"content": "v2"}, headers=headers)

    version = db_session.query(models.DocumentVersion).filter_by(document_id=doc["id"]).first()
    version.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db_session.commit()
    client.put(f"/v1/documents/{doc['id']}", json={"content": "v3"}, headers=headers)

    versions = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()
    assert versions[0]["content"] == "v2"  # most recently snapshotted
    assert versions[1]["content"] == "v1"


def test_versions_requires_auth(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft"}, headers=headers).json()
    response = client.get(f"/v1/documents/{doc['id']}/versions")
    assert response.status_code == 401


def test_versions_of_another_users_document_returns_404(client, auth_headers):
    headers_a = auth_headers("ver-a@example.com", "correcthorse")
    headers_b = auth_headers("ver-b@example.com", "correcthorse")
    doc = client.post("/v1/documents", json={"title": "Alice's doc"}, headers=headers_a).json()

    response = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers_b)
    assert response.status_code == 404


def test_restore_applies_the_old_content(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "Original"}, headers=headers).json()
    client.put(f"/v1/documents/{doc['id']}", json={"content": "Changed"}, headers=headers)

    version = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()[0]
    restored = client.post(f"/v1/documents/{doc['id']}/versions/{version['id']}/restore", headers=headers).json()

    assert restored["content"] == "Original"


def test_restore_snapshots_the_pre_restore_state_first(client, auth_headers):
    """Restoring is itself undoable — what was live right before the
    restore must not be lost."""
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "Original"}, headers=headers).json()
    client.put(f"/v1/documents/{doc['id']}", json={"content": "Changed"}, headers=headers)

    version = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()[0]
    client.post(f"/v1/documents/{doc['id']}/versions/{version['id']}/restore", headers=headers)

    versions_after = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()
    contents = {v["content"] for v in versions_after}
    assert "Changed" in contents  # the pre-restore state, preserved
    assert "Original" in contents


def test_restore_unknown_version_returns_404(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft"}, headers=headers).json()
    response = client.post(f"/v1/documents/{doc['id']}/versions/not-a-real-id/restore", headers=headers)
    assert response.status_code == 404


def test_restore_rejects_a_version_belonging_to_a_different_document(client, auth_headers):
    headers = auth_headers()
    doc_a = client.post("/v1/documents", json={"title": "A", "content": "A original"}, headers=headers).json()
    doc_b = client.post("/v1/documents", json={"title": "B", "content": "B original"}, headers=headers).json()
    client.put(f"/v1/documents/{doc_a['id']}", json={"content": "A changed"}, headers=headers)

    version_of_a = client.get(f"/v1/documents/{doc_a['id']}/versions", headers=headers).json()[0]

    response = client.post(
        f"/v1/documents/{doc_b['id']}/versions/{version_of_a['id']}/restore", headers=headers
    )
    assert response.status_code == 404


def test_restore_requires_auth(client, auth_headers):
    headers = auth_headers()
    doc = client.post("/v1/documents", json={"title": "Draft", "content": "x"}, headers=headers).json()
    client.put(f"/v1/documents/{doc['id']}", json={"content": "y"}, headers=headers)
    version = client.get(f"/v1/documents/{doc['id']}/versions", headers=headers).json()[0]

    response = client.post(f"/v1/documents/{doc['id']}/versions/{version['id']}/restore")
    assert response.status_code == 401
