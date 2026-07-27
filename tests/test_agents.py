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


def test_inbox_returns_pending_approvals_across_all_agents(client, auth_headers):
    """The real point of the inbox: one call surfaces every pending
    approval this user has, not just one agent's."""
    headers = auth_headers()
    inbox = client.get("/v1/agents/approvals", headers=headers).json()
    assert len(inbox) == 1
    assert inbox[0]["agent_name"] == "Researcher"
    assert inbox[0]["status"] == "pending"
    assert "agent_avatar_letter" in inbox[0]
    assert "agent_avatar_color_class" in inbox[0]


def test_inbox_excludes_decided_approvals(client, auth_headers):
    headers = auth_headers()
    agents = client.get("/v1/agents", headers=headers).json()
    researcher = next(a for a in agents if a["name"] == "Researcher")
    approval = client.get(f"/v1/agents/{researcher['id']}/approvals", headers=headers).json()[0]

    client.post(f"/v1/agents/{researcher['id']}/approvals/{approval['id']}/approve", headers=headers)

    inbox = client.get("/v1/agents/approvals", headers=headers).json()
    assert inbox == []


def test_inbox_only_shows_the_current_users_approvals(client, auth_headers):
    headers_a = auth_headers("inbox-a@example.com", "correcthorse")
    headers_b = auth_headers("inbox-b@example.com", "correcthorse")

    inbox_a = client.get("/v1/agents/approvals", headers=headers_a).json()
    inbox_b = client.get("/v1/agents/approvals", headers=headers_b).json()

    assert len(inbox_a) == 1
    assert len(inbox_b) == 1
    assert inbox_a[0]["id"] != inbox_b[0]["id"]


def test_inbox_requires_auth(client):
    response = client.get("/v1/agents/approvals")
    assert response.status_code == 401


def test_inbox_route_is_not_shadowed_by_agent_id_route(client, auth_headers):
    """Regression guard: GET /v1/agents/approvals must resolve to the
    inbox endpoint, not be swallowed by GET /{agent_id} treating
    "approvals" as an agent id and 404ing."""
    headers = auth_headers()
    response = client.get("/v1/agents/approvals", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_create_agent_appears_in_list(client, auth_headers):
    headers = auth_headers()
    response = client.post(
        "/v1/agents",
        json={
            "name": "Contract Reviewer",
            "description": "Flags risky clauses in vendor contracts.",
            "system_prompt": "You review contracts for unusual liability terms.",
            "tools": [{"name": "pdf_extract", "tier": "read"}],
        },
        headers=headers,
    )
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Contract Reviewer"
    assert created["status"] == "idle"  # a new agent hasn't earned "active" yet
    assert created["avatar_letter"] == "C"

    agents = client.get("/v1/agents", headers=headers).json()
    names = {a["name"] for a in agents}
    assert "Contract Reviewer" in names
    assert len(agents) == 6  # 5 seeded + 1 created


def test_create_agent_defaults_to_no_tools(client, auth_headers):
    headers = auth_headers()
    response = client.post(
        "/v1/agents",
        json={"name": "Minimal Agent", "description": "Bare minimum.", "system_prompt": "Do the thing."},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["tools"] == []


def test_create_agent_rejects_empty_name(client, auth_headers):
    headers = auth_headers()
    response = client.post(
        "/v1/agents",
        json={"name": "", "description": "x", "system_prompt": "x"},
        headers=headers,
    )
    assert response.status_code == 422


def test_create_agent_rejects_whitespace_only_name(client, auth_headers):
    headers = auth_headers()
    response = client.post(
        "/v1/agents",
        json={"name": "   ", "description": "x", "system_prompt": "x"},
        headers=headers,
    )
    assert response.status_code == 422


def test_create_agent_strips_surrounding_whitespace_from_name(client, auth_headers):
    headers = auth_headers()
    response = client.post(
        "/v1/agents",
        json={"name": "  Padded Name  ", "description": "x", "system_prompt": "x"},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Padded Name"


def test_create_agent_requires_auth(client):
    response = client.post(
        "/v1/agents", json={"name": "x", "description": "x", "system_prompt": "x"}
    )
    assert response.status_code == 401


def test_created_agents_are_owned_and_isolated_per_user(client, auth_headers):
    headers_a = auth_headers("create-agent-a@example.com", "correcthorse")
    headers_b = auth_headers("create-agent-b@example.com", "correcthorse")

    client.post(
        "/v1/agents",
        json={"name": "Alice Only Agent", "description": "x", "system_prompt": "x"},
        headers=headers_a,
    )

    agents_b = client.get("/v1/agents", headers=headers_b).json()
    assert "Alice Only Agent" not in {a["name"] for a in agents_b}


def test_created_agent_avatar_colors_rotate(client, auth_headers):
    """Not a strict requirement on the exact palette, just that
    successive created agents don't all collapse to one identical
    color, which would make them hard to tell apart at a glance."""
    headers = auth_headers()
    colors = set()
    for i in range(3):
        response = client.post(
            "/v1/agents",
            json={"name": f"Agent {i}", "description": "x", "system_prompt": "x"},
            headers=headers,
        )
        colors.add(response.json()["avatar_color_class"])
    assert len(colors) > 1
