import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest

from app import billing


def test_free_plan_has_no_paystack_plan_code():
    plan = billing.get_plan("free")
    assert plan.paystack_plan_code is None


def test_free_plan_is_not_purchasable_via_missing_plan_code():
    plan = billing.get_plan("free")
    assert plan.paystack_plan_code is None


def test_paid_plans_pick_up_plan_code_from_environment(monkeypatch):
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test_pro")
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_TEAM", "PLN_test_team")
    assert billing.get_plan("pro").paystack_plan_code == "PLN_test_pro"
    assert billing.get_plan("team").paystack_plan_code == "PLN_test_team"


def test_paid_plans_have_no_plan_code_when_env_unset(monkeypatch):
    monkeypatch.delenv("PAYSTACK_PLAN_CODE_PRO", raising=False)
    monkeypatch.delenv("PAYSTACK_PLAN_CODE_TEAM", raising=False)
    assert billing.get_plan("pro").paystack_plan_code is None
    assert billing.get_plan("team").paystack_plan_code is None


def test_get_plan_returns_none_for_unknown_id():
    assert billing.get_plan("enterprise-super-tier") is None


def test_list_plans_returns_all_three():
    ids = {p.id for p in billing.list_plans()}
    assert ids == {"free", "pro", "team"}


def test_plan_by_paystack_code_finds_matching_plan(monkeypatch):
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_abc123")
    plan = billing.plan_by_paystack_code("PLN_abc123")
    assert plan is not None
    assert plan.id == "pro"


def test_plan_by_paystack_code_returns_none_for_unknown_code():
    assert billing.plan_by_paystack_code("PLN_does_not_exist") is None


def test_is_paystack_configured_reflects_env(monkeypatch):
    monkeypatch.delenv("PAYSTACK_SECRET_KEY", raising=False)
    assert billing.is_paystack_configured() is False
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    assert billing.is_paystack_configured() is True


def test_initialize_transaction_raises_not_configured_without_api_key(monkeypatch):
    monkeypatch.delenv("PAYSTACK_SECRET_KEY", raising=False)
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test")
    plan = billing.get_plan("pro")
    with pytest.raises(billing.BillingNotConfiguredError):
        billing.initialize_transaction(plan=plan, customer_email="a@example.com", callback_url="https://x.test")


def test_initialize_transaction_raises_value_error_for_unpurchasable_plan(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    plan = billing.get_plan("free")
    with pytest.raises(ValueError):
        billing.initialize_transaction(plan=plan, customer_email="a@example.com", callback_url="https://x.test")


def test_initialize_transaction_returns_real_checkout_url(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test")
    plan = billing.get_plan("pro")

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "status": True,
        "data": {"authorization_url": "https://checkout.paystack.com/abc123", "reference": "ref_abc123"},
    }
    with patch("app.billing.httpx.post", return_value=fake_response) as mock_post:
        result = billing.initialize_transaction(
            plan=plan, customer_email="a@example.com", callback_url="https://x.test/settings"
        )

    assert result["authorization_url"] == "https://checkout.paystack.com/abc123"
    assert result["reference"] == "ref_abc123"
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["plan"] == "PLN_test"
    assert kwargs["json"]["email"] == "a@example.com"


def test_initialize_transaction_raises_paystack_api_error_on_rejection(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test")
    plan = billing.get_plan("pro")

    fake_response = MagicMock()
    fake_response.json.return_value = {"status": False, "message": "Invalid plan code"}
    with patch("app.billing.httpx.post", return_value=fake_response):
        with pytest.raises(billing.PaystackApiError):
            billing.initialize_transaction(
                plan=plan, customer_email="a@example.com", callback_url="https://x.test"
            )


def test_verify_transaction_returns_real_data_on_success(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": True, "data": {"status": "success", "amount": 15000}}
    with patch("app.billing.httpx.get", return_value=fake_response):
        data = billing.verify_transaction("ref_abc123")
    assert data["status"] == "success"


def test_verify_transaction_raises_on_paystack_rejection(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": False, "message": "Transaction not found"}
    with patch("app.billing.httpx.get", return_value=fake_response):
        with pytest.raises(billing.PaystackApiError):
            billing.verify_transaction("ref_does_not_exist")


def test_disable_subscription_succeeds_silently_on_success(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": True, "message": "Subscription disabled"}
    with patch("app.billing.httpx.post", return_value=fake_response) as mock_post:
        billing.disable_subscription(subscription_code="SUB_abc", email_token="tok_abc")
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"code": "SUB_abc", "token": "tok_abc"}


def test_disable_subscription_raises_on_paystack_rejection(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": False, "message": "Invalid token"}
    with patch("app.billing.httpx.post", return_value=fake_response):
        with pytest.raises(billing.PaystackApiError):
            billing.disable_subscription(subscription_code="SUB_abc", email_token="bad_token")


def test_verify_webhook_signature_accepts_correctly_signed_body(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    raw_body = b'{"event":"charge.success"}'
    real_signature = hmac.new(b"sk_test_fake", raw_body, hashlib.sha512).hexdigest()
    assert billing.verify_webhook_signature(raw_body, real_signature) is True


def test_verify_webhook_signature_rejects_wrong_signature(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    raw_body = b'{"event":"charge.success"}'
    assert billing.verify_webhook_signature(raw_body, "not-the-real-signature") is False


def test_verify_webhook_signature_rejects_when_not_configured(monkeypatch):
    monkeypatch.delenv("PAYSTACK_SECRET_KEY", raising=False)
    raw_body = b'{"event":"charge.success"}'
    real_signature = hmac.new(b"whatever", raw_body, hashlib.sha512).hexdigest()
    assert billing.verify_webhook_signature(raw_body, real_signature) is False


def test_verify_webhook_signature_rejects_missing_signature(monkeypatch):
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", "sk_test_fake")
    assert billing.verify_webhook_signature(b"{}", "") is False


def test_plan_id_from_transaction_data_reads_plain_plan_field(monkeypatch):
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_PRO", "PLN_test_pro")
    assert billing.plan_id_from_transaction_data({"plan": "PLN_test_pro"}) == "pro"


def test_plan_id_from_transaction_data_reads_nested_plan_object(monkeypatch):
    monkeypatch.setenv("PAYSTACK_PLAN_CODE_TEAM", "PLN_test_team")
    assert billing.plan_id_from_transaction_data({"plan_object": {"plan_code": "PLN_test_team"}}) == "team"


def test_plan_id_from_transaction_data_returns_none_when_absent():
    assert billing.plan_id_from_transaction_data({}) is None


def test_plan_id_from_transaction_data_returns_none_for_unmatched_code():
    assert billing.plan_id_from_transaction_data({"plan": "PLN_unknown_to_us"}) is None
