def test_registration_seeds_four_starter_projects(client, auth_headers):
    headers = auth_headers()
    response = client.get("/v1/projects", headers=headers)
    assert response.status_code == 200
    projects = response.json()
    assert len(projects) == 4
    names = {p["name"] for p in projects}
    assert names == {
        "JoblyHub Outreach",
        "JMK Tender Monitor",
        "RoutePilot AI",
        "Freelancer Profile Repositioning",
    }


def test_seeded_projects_start_with_zero_threads(client, auth_headers):
    headers = auth_headers()
    projects = client.get("/v1/projects", headers=headers).json()
    assert all(p["thread_count"] == 0 for p in projects)


def test_projects_require_auth(client):
    response = client.get("/v1/projects")
    assert response.status_code == 401


def test_create_custom_project(client, auth_headers):
    headers = auth_headers()
    response = client.post("/v1/projects", json={"name": "My New Project"}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "My New Project"
    assert body["thread_count"] == 0

    all_projects = client.get("/v1/projects", headers=headers).json()
    assert len(all_projects) == 5  # 4 seeded + 1 custom


def test_create_project_rejects_empty_name(client, auth_headers):
    headers = auth_headers()
    response = client.post("/v1/projects", json={"name": ""}, headers=headers)
    assert response.status_code == 422


def test_users_only_see_their_own_projects(client, auth_headers):
    headers_a = auth_headers("proj-a@example.com", "correcthorse")
    headers_b = auth_headers("proj-b@example.com", "correcthorse")

    client.post("/v1/projects", json={"name": "Alice's private project"}, headers=headers_a)

    projects_b = client.get("/v1/projects", headers=headers_b).json()
    assert "Alice's private project" not in {p["name"] for p in projects_b}


def test_conversation_can_be_linked_to_a_project(client, auth_headers):
    headers = auth_headers()
    project = client.post("/v1/projects", json={"name": "Linked project"}, headers=headers).json()

    conv = client.post(
        "/v1/conversations", json={"project_id": project["id"]}, headers=headers
    ).json()
    assert conv["project_id"] == project["id"]

    updated_project = next(
        p for p in client.get("/v1/projects", headers=headers).json() if p["id"] == project["id"]
    )
    assert updated_project["thread_count"] == 1


def test_conversation_creation_rejects_another_users_project(client, auth_headers):
    headers_a = auth_headers("linker-a@example.com", "correcthorse")
    headers_b = auth_headers("linker-b@example.com", "correcthorse")

    project_a = client.post("/v1/projects", json={"name": "A's project"}, headers=headers_a).json()

    response = client.post(
        "/v1/conversations", json={"project_id": project_a["id"]}, headers=headers_b
    )
    assert response.status_code == 404


def test_conversation_without_project_id_still_works(client, auth_headers):
    headers = auth_headers()
    response = client.post("/v1/conversations", json={}, headers=headers)
    assert response.status_code == 201
    assert response.json()["project_id"] is None
