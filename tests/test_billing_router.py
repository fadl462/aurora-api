import hashlib
import hmac
from unittest.mock import MagicMock, patch

from app import models


def test_list_plans_is_public_no_auth_required(client):
    response = client.get("/v1/billing/plans")
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert ids == {"free", "pro", "team"}


def test_list_plans_free_plan_is_not_purchasable(client):
    plans = client.get("/v1/billing/plans").json()
    free = next(p for p in plans if p["id"] == "free")
    assert free["purchasable"] is False


def test_list_plans_shows_currency(client):
    plans = client.get("/v1/billing/plans").json()
    assert all(p["currency"] == "GHS" for p in plans)


def test_checkout_requires_auth(client):
    response = client.post("/v1/billing/checkout", json={"plan_id": "pro"})
    assert response.status_code == 401


def test_checkout_returns_503_when_paystack_not_configured(client, auth_headers, monkeypatch):
    monkeypatch.delenv("PAYSTACK_SECRET_KEY", raising=False)
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test")
    headers = auth_headers()
    response = client.post("/v1/billing/checkout", json={"plan_id": "pro"}, headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "billing_not_configured"


def test_checkout_returns_400_for_unpurchasable_plan(client, auth_headers, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    headers = auth_headers()
    response = client.post("/v1/billing/checkout", json={"plan_id": "free"}, headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "plan_not_purchasable"


def test_checkout_returns_404_for_unknown_plan(client, auth_headers, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    headers = auth_headers()
    response = client.post("/v1/billing/checkout", json={"plan_id": "not-a-real-plan"}, headers=headers)
    assert response.status_code == 404


def test_checkout_returns_real_checkout_url_on_success(client, auth_headers, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test")
    headers = auth_headers()

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "status": True,
        "data": {"authorization_url": "https://checkout.paystack.com/xyz", "reference": "ref_xyz"},
    }
    with patch("app.billing.httpx.post", return_value=fake_response):
        response = client.post("/v1/billing/checkout", json={"plan_id": "pro"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["checkout_url"] == "https://checkout.paystack.com/xyz"


def test_verify_requires_auth(client):
    response = client.get("/v1/billing/verify?reference=abc")
    assert response.status_code == 401


def test_verify_applies_plan_on_successful_transaction(client, auth_headers, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test_pro")
    headers = auth_headers()

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "status": True,
        "data": {
            "status": "success",
            "plan": "PLN_test_pro",
            "customer": {"customer_code": "CUS_abc123", "email": "verify-test@example.com"},
        },
    }
    with patch("app.billing.httpx.get", return_value=fake_response):
        response = client.get("/v1/billing/verify?reference=ref_abc", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["plan_tier"] == "pro"

    me = client.get("/v1/auth/me", headers=headers).json()
    assert me["plan_tier"] == "pro"


def test_verify_does_not_apply_plan_when_transaction_not_successful(client, auth_headers, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    headers = auth_headers()

    fake_response = MagicMock()
    fake_response.json.return_value = {"status": True, "data": {"status": "failed"}}
    with patch("app.billing.httpx.get", return_value=fake_response):
        response = client.get("/v1/billing/verify?reference=ref_abc", headers=headers)

    assert response.json()["verified"] is False
    me = client.get("/v1/auth/me", headers=headers).json()
    assert me["plan_tier"] == "free"


def test_verify_does_not_apply_plan_when_plan_code_unrecognized(client, auth_headers, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    headers = auth_headers()

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "status": True,
        "data": {"status": "success", "plan": "PLN_totally_unknown", "customer": {}},
    }
    with patch("app.billing.httpx.get", return_value=fake_response):
        response = client.get("/v1/billing/verify?reference=ref_abc", headers=headers)

    assert response.json()["verified"] is False


def test_cancel_requires_auth(client):
    response = client.post("/v1/billing/cancel")
    assert response.status_code == 401


def test_cancel_returns_400_with_no_active_subscription(client, auth_headers, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    headers = auth_headers()
    response = client.post("/v1/billing/cancel", headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "no_active_subscription"


def test_cancel_downgrades_to_free_on_success(client, auth_headers, monkeypatch, db_session):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test_pro")
    headers = auth_headers()

    # Simulate an already-active Pro subscriber.
    me = client.get("/v1/auth/me", headers=headers).json()
    user = db_session.get(models.User, me["id"])
    user.plan_tier = "pro"
    user.token_balance = 1_000_000
    user.paystack_subscription_code = "SUB_abc"
    user.paystack_email_token = "tok_abc"
    db_session.commit()

    fake_response = MagicMock()
    fake_response.json.return_value = {"status": True, "message": "Subscription disabled"}
    with patch("app.billing.httpx.post", return_value=fake_response):
        response = client.post("/v1/billing/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["plan_tier"] == "free"

    me_after = client.get("/v1/auth/me", headers=headers).json()
    assert me_after["plan_tier"] == "free"


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha512).hexdigest()


def test_webhook_rejects_invalid_signature(client, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    body = b'{"event":"charge.success","data":{}}'
    response = client.post(
        "/v1/billing/webhook", content=body, headers={"x-paystack-signature": "bogus"}
    )
    assert response.status_code == 400


def test_webhook_returns_503_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("PAYSTACK_SECRET_KEY", raising=False)
    body = b'{"event":"charge.success","data":{}}'
    response = client.post("/v1/billing/webhook", content=body, headers={"x-paystack-signature": "whatever"})
    assert response.status_code == 503


def test_webhook_charge_success_applies_plan_to_matching_user(client, auth_headers, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_TEAM", "PLN_test_team")
    headers = auth_headers("webhook-charge@example.com", "correcthorse")

    import json

    body_dict = {
        "event": "charge.success",
        "data": {"plan": "PLN_test_team", "customer": {"customer_code": "CUS_xyz", "email": "webhook-charge@example.com"}},
    }
    body = json.dumps(body_dict).encode()
    signature = _sign(body, "sk_test_fake")

    response = client.post("/v1/billing/webhook", content=body, headers={"x-paystack-signature": signature})
    assert response.status_code == 200

    me = client.get("/v1/auth/me", headers=headers).json()
    assert me["plan_tier"] == "team"


def test_webhook_subscription_create_stores_subscription_details(client, auth_headers, monkeypatch, db_session):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    headers = auth_headers("webhook-sub@example.com", "correcthorse")
    me = client.get("/v1/auth/me", headers=headers).json()

    import json

    body_dict = {
        "event": "subscription.create",
        "data": {
            "subscription_code": "SUB_new123",
            "email_token": "tok_new123",
            "customer": {"customer_code": "CUS_new123", "email": "webhook-sub@example.com"},
        },
    }
    body = json.dumps(body_dict).encode()
    signature = _sign(body, "sk_test_fake")
    client.post("/v1/billing/webhook", content=body, headers={"x-paystack-signature": signature})

    user = db_session.get(models.User, me["id"])
    assert user.paystack_subscription_code == "SUB_new123"
    assert user.paystack_email_token == "tok_new123"


def test_webhook_subscription_disable_downgrades_user(client, auth_headers, monkeypatch, db_session):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    headers = auth_headers("webhook-disable@example.com", "correcthorse")
    me = client.get("/v1/auth/me", headers=headers).json()

    user = db_session.get(models.User, me["id"])
    user.plan_tier = "pro"
    user.token_balance = 1_000_000
    user.paystack_subscription_code = "SUB_to_disable"
    db_session.commit()

    import json

    body_dict = {"event": "subscription.disable", "data": {"subscription_code": "SUB_to_disable"}}
    body = json.dumps(body_dict).encode()
    signature = _sign(body, "sk_test_fake")
    client.post("/v1/billing/webhook", content=body, headers={"x-paystack-signature": signature})

    me_after = client.get("/v1/auth/me", headers=headers).json()
    assert me_after["plan_tier"] == "free"


def test_webhook_ignores_unmatched_customer_without_crashing(client, monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test")

    import json

    body_dict = {
        "event": "charge.success",
        "data": {"plan": "PLN_test", "customer": {"customer_code": "CUS_ghost", "email": "nobody@example.com"}},
    }
    body = json.dumps(body_dict).encode()
    signature = _sign(body, "sk_test_fake")
    response = client.post("/v1/billing/webhook", content=body, headers={"x-paystack-signature": signature})
    assert response.status_code == 200  # accepted, just nothing to apply it to
