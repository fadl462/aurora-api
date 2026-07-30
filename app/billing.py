"""
Real subscription billing via Paystack — not Stripe.

Stripe does not operate as a direct payment processor for
Ghana-registered businesses. Ghana is only reachable through Paystack,
which Stripe acquired in 2020 but which runs as a fully separate
platform — its own dashboard, its own API, its own feature set. Since
this product needs to actually work for a Ghana-based account holder,
billing runs on Paystack directly.

Follows the same honesty principle as orchestration.py's Anthropic
integration (and the Stripe module this replaced): if
PAYSTACK_SECRET_KEY isn't configured, billing functions raise
BillingNotConfiguredError rather than ever faking a checkout URL or a
successful charge.

Plan definitions are real, not decorative: token_allowance is what
actually gets applied to a user's token_balance when a subscription
activates (see routers/billing.py's webhook/verify handlers) — the
same real token economy app/orchestration.py already meters against.

paystack_plan_code per paid plan comes from the environment
(PAYSTACK_PLAN_CODE_PRO, PAYSTACK_PLAN_CODE_TEAM) because Plan codes,
and the real amount/currency/billing interval they represent, live in
whoever's actual Paystack account is connected — this module doesn't
invent or convert prices itself. monthly_price/currency here are
purely this app's own *display* copy on the pricing cards
(PAYSTACK_DISPLAY_PRICE_PRO/TEAM, defaulting to placeholder GHS
figures) — keeping that in sync with the real amount configured on the
Paystack Plan is a manual step, same as it would be with any payment
provider's dashboard-managed pricing.
"""

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional

import httpx

PAYSTACK_API_BASE = "https://api.paystack.co"
REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    monthly_price: int
    currency: str
    token_allowance: int
    paystack_plan_code: Optional[str]  # None for the free plan — nothing to check out


def _plan_catalog() -> dict[str, Plan]:
    """A function, not a module-level constant, because the paid
    plans' paystack_plan_code (and display price) depend on
    environment variables that tests need to set/unset per-test — a
    constant computed once at import time wouldn't see those changes."""
    return {
        "free": Plan(id="free", name="Free", monthly_price=0, currency="GHS", token_allowance=50_000, paystack_plan_code=None),
        "pro": Plan(
            id="pro",
            name="Pro",
            monthly_price=int(os.environ.get("PAYSTACK_DISPLAY_PRICE_PRO", "150")),
            currency="GHS",
            token_allowance=1_000_000,
            paystack_plan_code=os.environ.get("PAYSTACK_PLAN_CODE_PRO"),
        ),
        "team": Plan(
            id="team",
            name="Team",
            monthly_price=int(os.environ.get("PAYSTACK_DISPLAY_PRICE_TEAM", "450")),
            currency="GHS",
            token_allowance=5_000_000,
            paystack_plan_code=os.environ.get("PAYSTACK_PLAN_CODE_TEAM"),
        ),
    }


def get_plan(plan_id: str) -> Optional[Plan]:
    return _plan_catalog().get(plan_id)


def list_plans() -> list[Plan]:
    return list(_plan_catalog().values())


def plan_by_paystack_code(plan_code: str) -> Optional[Plan]:
    for plan in list_plans():
        if plan.paystack_plan_code == plan_code:
            return plan
    return None


def is_paystack_configured() -> bool:
    return bool(os.environ.get("PAYSTACK_SECRET_KEY"))


class BillingNotConfiguredError(Exception):
    """Raised instead of ever faking a checkout URL or a successful
    charge — mirrors orchestration.py's 'no ANTHROPIC_API_KEY
    configured' stub path."""


class PaystackApiError(Exception):
    """A real, reachable Paystack API that rejected the request (bad
    plan code, invalid subscription, etc.) — distinct from
    BillingNotConfiguredError, which means we never even tried."""


def _require_api_key() -> str:
    api_key = os.environ.get("PAYSTACK_SECRET_KEY")
    if not api_key:
        raise BillingNotConfiguredError("no PAYSTACK_SECRET_KEY configured")
    return api_key


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_require_api_key()}"}


def initialize_transaction(*, plan: Plan, customer_email: str, callback_url: str) -> dict:
    """Returns {"authorization_url": ..., "reference": ...}. The
    authorization_url is a real, ready-to-redirect-to Paystack checkout
    page. Raises BillingNotConfiguredError if Paystack isn't set up,
    ValueError if the plan itself has no real plan code attached, and
    PaystackApiError for anything Paystack's API itself rejects — a
    person clicking "Upgrade" deserves the actual reason it failed."""
    if plan.paystack_plan_code is None:
        raise ValueError(f"plan '{plan.id}' has no paystack_plan_code — it isn't a purchasable plan")

    response = httpx.post(
        f"{PAYSTACK_API_BASE}/transaction/initialize",
        headers=_auth_headers(),
        json={"email": customer_email, "plan": plan.paystack_plan_code, "callback_url": callback_url},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    body = response.json()
    if not body.get("status"):
        raise PaystackApiError(body.get("message") or "Paystack rejected the checkout request.")

    data = body["data"]
    return {"authorization_url": data["authorization_url"], "reference": data["reference"]}


def verify_transaction(reference: str) -> dict:
    """Confirms a transaction's real status directly with Paystack,
    rather than trusting a client-supplied 'it worked' — used both by
    the webhook handler's charge.success path and by the settings
    page's synchronous return-from-checkout flow, so an upgrade doesn't
    depend solely on a webhook arriving promptly."""
    response = httpx.get(
        f"{PAYSTACK_API_BASE}/transaction/verify/{reference}",
        headers=_auth_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    body = response.json()
    if not body.get("status"):
        raise PaystackApiError(body.get("message") or "Couldn't verify that transaction.")
    return body["data"]


def disable_subscription(*, subscription_code: str, email_token: str) -> None:
    """Paystack's real cancel-subscription call. Requires the
    email_token Paystack hands back in the subscription.create webhook
    payload — there's no way to cancel with just the subscription code
    alone, by Paystack's own design."""
    response = httpx.post(
        f"{PAYSTACK_API_BASE}/subscription/disable",
        headers=_auth_headers(),
        json={"code": subscription_code, "token": email_token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    body = response.json()
    if not body.get("status"):
        raise PaystackApiError(body.get("message") or "Couldn't cancel that subscription.")


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """Paystack signs webhook bodies with HMAC-SHA512 using your own
    secret key (no separate webhook signing secret, unlike Stripe) —
    this must pass before a webhook body is trusted at all."""
    api_key = os.environ.get("PAYSTACK_SECRET_KEY")
    if not api_key or not signature_header:
        return False
    computed = hmac.new(api_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(computed, signature_header)


def plan_id_from_transaction_data(data: dict) -> Optional[str]:
    """Real subscription-linked transactions (initialized with a
    `plan` code) carry that plan code back in the verify/webhook
    payload — either as a plain string under 'plan', or nested under
    'plan_object'. Maps it back to this app's own plan id, returning
    None (never a guess) if it can't be matched to a real plan."""
    plan_code = data.get("plan") or (data.get("plan_object") or {}).get("plan_code")
    if not plan_code:
        return None
    plan = plan_by_paystack_code(plan_code)
    return plan.id if plan else None
