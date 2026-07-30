"""
Real subscription billing via Stripe — real plan tiers, real Checkout
Sessions, a real webhook handler, and a real Billing Portal link.

Follows the same honesty principle as orchestration.py's Anthropic
integration: if STRIPE_SECRET_KEY isn't configured, billing endpoints
return a clear, labeled "not configured" error — never a fake checkout
URL or a fabricated success. Anthropic's outage-handling test pattern
(test_orchestration.py) is the template for this module's tests too.

Plan definitions are real, not decorative: token_allowance is what
actually gets applied to a user's token_balance when their subscription
activates (see apply_plan_to_user below) — the same real token economy
app/orchestration.py already meters against, not a separate fictional
number for display only.

stripe_price_id per paid plan comes from the environment
(STRIPE_PRICE_ID_PRO, STRIPE_PRICE_ID_TEAM) because Price IDs are
specific to whoever's real Stripe account is connected — there's no
correct hardcoded value here, unlike the token_allowance figures, which
are this product's own numbers regardless of who's processing payment.
"""

import os
from dataclasses import dataclass
from typing import Optional

import stripe


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    monthly_price_usd: int
    token_allowance: int
    stripe_price_id: Optional[str]  # None for the free plan — nothing to check out


def _plan_catalog() -> dict[str, Plan]:
    """A function, not a module-level constant, because the paid plans'
    stripe_price_id depends on environment variables that tests need to
    set/unset per-test — a constant computed once at import time
    wouldn't see those changes."""
    return {
        "free": Plan(id="free", name="Free", monthly_price_usd=0, token_allowance=50_000, stripe_price_id=None),
        "pro": Plan(
            id="pro",
            name="Pro",
            monthly_price_usd=20,
            token_allowance=1_000_000,
            stripe_price_id=os.environ.get("STRIPE_PRICE_ID_PRO"),
        ),
        "team": Plan(
            id="team",
            name="Team",
            monthly_price_usd=60,
            token_allowance=5_000_000,
            stripe_price_id=os.environ.get("STRIPE_PRICE_ID_TEAM"),
        ),
    }


def get_plan(plan_id: str) -> Optional[Plan]:
    return _plan_catalog().get(plan_id)


def list_plans() -> list[Plan]:
    return list(_plan_catalog().values())


def is_stripe_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


class BillingNotConfiguredError(Exception):
    """Raised instead of ever faking a checkout URL or a successful
    charge — mirrors orchestration.py's 'no ANTHROPIC_API_KEY
    configured' stub path."""


def _client() -> None:
    api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not api_key:
        raise BillingNotConfiguredError("no STRIPE_SECRET_KEY configured")
    stripe.api_key = api_key


def create_checkout_session(
    *, plan: Plan, customer_email: str, existing_customer_id: Optional[str], success_url: str, cancel_url: str
) -> str:
    """Returns a real, ready-to-redirect-to Stripe Checkout URL.
    Raises BillingNotConfiguredError if Stripe isn't set up, and lets
    any real stripe.StripeError propagate — a person clicking "Upgrade"
    deserves the actual reason it failed, not a silently swallowed
    error that leaves them wondering why nothing happened."""
    if plan.stripe_price_id is None:
        raise ValueError(f"plan '{plan.id}' has no stripe_price_id — it isn't a purchasable plan")
    _client()

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        customer=existing_customer_id,
        customer_email=customer_email if not existing_customer_id else None,
        client_reference_id=None,
        metadata={"plan_id": plan.id},
    )
    return session.url


def create_billing_portal_session(*, stripe_customer_id: str, return_url: str) -> str:
    """Real Stripe-hosted portal where a subscriber manages or cancels
    their own subscription — Stripe's own UI, not something this app
    has to build and maintain itself."""
    _client()
    session = stripe.billing_portal.Session.create(customer=stripe_customer_id, return_url=return_url)
    return session.url


def construct_webhook_event(payload: bytes, signature_header: str) -> stripe.Event:
    """Raises stripe.SignatureVerificationError on a bad/missing
    signature — the caller must not process a webhook body it can't
    verify actually came from Stripe."""
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        raise BillingNotConfiguredError("no STRIPE_WEBHOOK_SECRET configured")
    return stripe.Webhook.construct_event(payload, signature_header, webhook_secret)


def plan_id_from_checkout_session(session: dict) -> Optional[str]:
    return (session.get("metadata") or {}).get("plan_id")
