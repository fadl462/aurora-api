from unittest.mock import MagicMock, patch

import pytest
import stripe

from app import billing


def test_free_plan_is_not_purchasable():
    free = billing.get_plan("free")
    assert free is not None
    assert free.stripe_price_id is None
    assert free.monthly_price_usd == 0


def test_list_plans_returns_all_three_tiers():
    plans = billing.list_plans()
    assert {p.id for p in plans} == {"free", "pro", "team"}


def test_pro_plan_token_allowance_exceeds_free(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_test_pro")
    pro = billing.get_plan("pro")
    free = billing.get_plan("free")
    assert pro.token_allowance > free.token_allowance


def test_unknown_plan_returns_none():
    assert billing.get_plan("nonexistent") is None


def test_pro_plan_stripe_price_id_comes_from_environment(monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_ID_PRO", raising=False)
    assert billing.get_plan("pro").stripe_price_id is None

    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_abc123")
    assert billing.get_plan("pro").stripe_price_id == "price_abc123"


def test_is_stripe_configured_false_without_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert billing.is_stripe_configured() is False


def test_is_stripe_configured_true_with_key(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    assert billing.is_stripe_configured() is True


def test_create_checkout_session_raises_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_test_pro")
    plan = billing.get_plan("pro")

    with pytest.raises(billing.BillingNotConfiguredError):
        billing.create_checkout_session(
            plan=plan,
            customer_email="test@example.com",
            existing_customer_id=None,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )


def test_create_checkout_session_rejects_free_plan():
    free = billing.get_plan("free")
    with pytest.raises(ValueError):
        billing.create_checkout_session(
            plan=free,
            customer_email="test@example.com",
            existing_customer_id=None,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )


def test_create_checkout_session_returns_real_url_when_configured(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_test_pro")
    plan = billing.get_plan("pro")

    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/pay/cs_test_fake"

    with patch("app.billing.stripe.checkout.Session.create", return_value=fake_session) as mock_create:
        url = billing.create_checkout_session(
            plan=plan,
            customer_email="test@example.com",
            existing_customer_id=None,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

    assert url == "https://checkout.stripe.com/pay/cs_test_fake"
    _, kwargs = mock_create.call_args
    assert kwargs["line_items"] == [{"price": "price_test_pro", "quantity": 1}]
    assert kwargs["mode"] == "subscription"


def test_create_checkout_session_reuses_existing_customer(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_test_pro")
    plan = billing.get_plan("pro")

    fake_session = MagicMock()
    fake_session.url = "https://checkout.stripe.com/pay/cs_test_fake"

    with patch("app.billing.stripe.checkout.Session.create", return_value=fake_session) as mock_create:
        billing.create_checkout_session(
            plan=plan,
            customer_email="test@example.com",
            existing_customer_id="cus_existing123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

    _, kwargs = mock_create.call_args
    assert kwargs["customer"] == "cus_existing123"
    # Should not also pass customer_email when we already have a real customer id.
    assert kwargs["customer_email"] is None


def test_create_billing_portal_session_raises_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(billing.BillingNotConfiguredError):
        billing.create_billing_portal_session(stripe_customer_id="cus_123", return_url="https://example.com")


def test_construct_webhook_event_raises_not_configured_without_secret(monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    with pytest.raises(billing.BillingNotConfiguredError):
        billing.construct_webhook_event(b"{}", "fake-signature")


def test_construct_webhook_event_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_fake")
    with pytest.raises(stripe.error.SignatureVerificationError):
        billing.construct_webhook_event(b'{"type": "checkout.session.completed"}', "invalid-signature")


def test_plan_id_from_checkout_session_extracts_metadata():
    session = {"metadata": {"plan_id": "pro"}}
    assert billing.plan_id_from_checkout_session(session) == "pro"


def test_plan_id_from_checkout_session_handles_missing_metadata():
    assert billing.plan_id_from_checkout_session({}) is None
