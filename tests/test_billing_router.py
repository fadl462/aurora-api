from unittest.mock import MagicMock, patch


def test_list_plans_is_public_no_auth_needed(client):
    response = client.get("/v1/billing/plans")
    assert response.status_code == 200
    plans = response.json()
    ids = {p["id"] for p in plans}
    assert ids == {"free", "pro", "team"}


def test_free_plan_marked_not_purchasable(client):
    plans = client.get("/v1/billing/plans").json()
    free = next(p for p in plans if p["id"] == "free")
    assert free["purchasable"] is False


def test_checkout_requires_auth(client):
    response = client.post("/v1/billing/checkout", json={"plan_id": "pro"})
    assert response.status_code == 401


def test_checkout_returns_503_when_stripe_not_configured(client, auth_headers, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_test_pro")
    headers = auth_headers()

    response = client.post("/v1/billing/checkout", json={"plan_id": "pro"}, headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "billing_not_configured"


def test_checkout_rejects_unknown_plan(client, auth_headers, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    headers = auth_headers()
    response = client.post("/v1/billing/checkout", json={"plan_id": "nonexistent"}, headers=headers)
    assert response.status_code == 404


def test_checkout_rejects_free_plan_as_not_purchasable(client, auth_headers, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    headers = auth_headers()
    response = client.post("/v1/billing/checkout", json={"plan_id": "free"}, headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "plan_not_purchasable"


def test_checkout_returns_real_url_when_stripe_configured(client, auth_headers, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_test_pro")
    headers = auth_headers()

    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/pay/cs_test_fake"

    with patch("app.billing.stripe.checkout.Session.create", return_value=fake_session):
        response = client.post("/v1/billing/checkout", json={"plan_id": "pro"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_test_fake"


def test_portal_requires_auth(client):
    response = client.get("/v1/billing/portal")
    assert response.status_code == 401


def test_portal_requires_existing_stripe_customer(client, auth_headers):
    headers = auth_headers()
    response = client.get("/v1/billing/portal", headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "no_stripe_customer"


def test_webhook_rejects_missing_signature(client):
    response = client.post("/v1/billing/webhook", content=b'{"type": "checkout.session.completed"}')
    assert response.status_code in (400, 503)  # 503 if webhook secret also unset in this test env


def test_webhook_checkout_completed_upgrades_user_plan(client, auth_headers, db_session, monkeypatch):
    """Simulates a real Stripe webhook event by calling the handler
    function directly with a realistic payload shape — the signature
    verification itself is tested separately in test_billing.py, this
    test is about what happens to the user's plan/balance once a
    legitimately-verified event is processed."""
    from app import models
    from app.routers import billing as billing_router

    headers = auth_headers("checkout-webhook@example.com", "correcthorse")
    user = db_session.query(models.User).filter(models.User.email == "checkout-webhook@example.com").first()
    assert user.plan_tier == "free"

    fake_session_data = {
        "customer": "cus_new123",
        "subscription": "sub_new123",
        "customer_details": {"email": "checkout-webhook@example.com"},
        "metadata": {"plan_id": "pro"},
    }

    billing_router._handle_checkout_completed(fake_session_data, db_session)

    db_session.refresh(user)
    assert user.plan_tier == "pro"
    assert user.stripe_customer_id == "cus_new123"
    assert user.stripe_subscription_id == "sub_new123"
    assert user.token_balance == 1_000_000


def test_webhook_subscription_cancelled_reverts_to_free(client, auth_headers, db_session):
    from app import models
    from app.routers import billing as billing_router

    headers = auth_headers("cancel-webhook@example.com", "correcthorse")
    user = db_session.query(models.User).filter(models.User.email == "cancel-webhook@example.com").first()
    user.plan_tier = "pro"
    user.stripe_subscription_id = "sub_to_cancel"
    user.token_balance = 900_000
    db_session.commit()

    billing_router._handle_subscription_cancelled({"id": "sub_to_cancel"}, db_session)

    db_session.refresh(user)
    assert user.plan_tier == "free"
    assert user.stripe_subscription_id is None
    assert user.token_balance == 50_000


def test_webhook_checkout_completed_ignores_unknown_customer(db_session):
    """No matching user by customer id or email — must not raise, and
    must not silently create/corrupt anything. A no-op is the only
    honest behavior here."""
    from app.routers import billing as billing_router

    fake_session_data = {
        "customer": "cus_totally_unknown",
        "subscription": "sub_x",
        "customer_details": {"email": "nobody-real@example.com"},
        "metadata": {"plan_id": "pro"},
    }
    billing_router._handle_checkout_completed(fake_session_data, db_session)  # should not raise
