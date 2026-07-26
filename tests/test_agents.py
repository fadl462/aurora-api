def test_registration_seeds_five_starter_agents(client, auth_headers):
    headers = auth_headers()
    response = client.get("/v1/agents", headers=headers)
    assert response.status_code == 200
    agents = response.json()
    assert len(agents) == 5
    names = {a["name"] for a in agents}
    assert names == {"Researcher", "Developer", "Outreach Manager", "Data Analyst", "Proposal Writer"}


def test_seeded_researcher_has_expected_tools_and_status(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    researcher = next(a for a in agents if a["name"] == "Researcher")
    assert researcher["status"] == "active"
    tool_names = {t["name"] for t in researcher["tools"]}
    assert "web_search" in tool_names
    assert all(t["tier"] in {"read", "low", "medium", "high"} for t in researcher["tools"])


def test_agents_require_auth(client):
    response = client.get("/v1/agents")
    assert response.status_code == 401


def test_user_cannot_access_another_users_agent(client, auth_headers):
    headers_a = auth_headers("agent-owner@example.com", "correcthorse")
    headers_b = auth_headers("agent-intruder@example.com", "correcthorse")

    agent_a = client.get("/v1/agents", headers=headers_a).json()[0]
    response = client.get(f"/v1/agents/{agent_a['id']}", headers=headers_b)
    assert response.status_code == 404


def test_researcher_has_seeded_run_history(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    researcher = next(a for a in agents if a["name"] == "Researcher")

    runs = client.get(f"/v1/agents/{researcher['id']}/runs", headers=headers).json()
    assert len(runs) == 3
    statuses = {r["status"] for r in runs}
    assert statuses == {"done", "running"}


def test_other_agents_have_no_run_history(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    developer = next(a for a in agents if a["name"] == "Developer")

    runs = client.get(f"/v1/agents/{developer['id']}/runs", headers=headers).json()
    assert runs == []


def test_researcher_has_one_pending_approval(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    researcher = next(a for a in agents if a["name"] == "Researcher")

    approvals = client.get(f"/v1/agents/{researcher['id']}/approvals", headers=headers).json()
    assert len(approvals) == 1
    assert approvals[0]["status"] == "pending"
    assert approvals[0]["tier"] == "medium"


def test_other_agents_have_no_pending_approvals(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    analyst = next(a for a in agents if a["name"] == "Data Analyst")

    approvals = client.get(f"/v1/agents/{analyst['id']}/approvals", headers=headers).json()
    assert approvals == []


def test_approve_marks_approval_decided_and_removes_it_from_pending_list(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    researcher = next(a for a in agents if a["name"] == "Researcher")
    approval = client.get(f"/v1/agents/{researcher['id']}/approvals", headers=headers).json()[0]

    response = client.post(
        f"/v1/agents/{researcher['id']}/approvals/{approval['id']}/approve",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["decided_at"] is not None

    remaining = client.get(f"/v1/agents/{researcher['id']}/approvals", headers=headers).json()
    assert remaining == []


def test_deny_marks_approval_denied(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    researcher = next(a for a in agents if a["name"] == "Researcher")
    approval = client.get(f"/v1/agents/{researcher['id']}/approvals", headers=headers).json()[0]

    response = client.post(
        f"/v1/agents/{researcher['id']}/approvals/{approval['id']}/deny",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "denied"


def test_cannot_decide_an_already_decided_approval(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    researcher = next(a for a in agents if a["name"] == "Researcher")
    approval = client.get(f"/v1/agents/{researcher['id']}/approvals", headers=headers).json()[0]

    client.post(f"/v1/agents/{researcher['id']}/approvals/{approval['id']}/approve", headers=headers)
    second_attempt = client.post(
        f"/v1/agents/{researcher['id']}/approvals/{approval['id']}/deny",
        headers=headers,
    )
    assert second_attempt.status_code == 409


def test_two_users_each_get_their_own_independent_approval(client, auth_headers):
    """Approving user A's Researcher approval must not affect user B's."""
    headers_a = auth_headers("approver-a@example.com", "correcthorse")
    headers_b = auth_headers("approver-b@example.com", "correcthorse")

    researcher_a = next(a for a in client.get("/v1/agents", headers=headers_a).json() if a["name"] == "Researcher")
    researcher_b = next(a for a in client.get("/v1/agents", headers=headers_b).json() if a["name"] == "Researcher")

    approval_a = client.get(f"/v1/agents/{researcher_a['id']}/approvals", headers=headers_a).json()[0]
    client.post(f"/v1/agents/{researcher_a['id']}/approvals/{approval_a['id']}/approve", headers=headers_a)

    # User B's approval should still be pending.
    approvals_b = client.get(f"/v1/agents/{researcher_b['id']}/approvals", headers=headers_b).json()
    assert len(approvals_b) == 1
    assert approvals_b[0]["status"] == "pending"
