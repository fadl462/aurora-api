"""
Real subscription billing endpoints, backed by app/billing.py's Stripe
integration.

  GET  /v1/billing/plans              (public — no auth needed to see pricing)
  POST /v1/billing/checkout           (real Stripe Checkout Session)
  GET  /v1/billing/portal             (real Stripe Billing Portal)
  POST /v1/billing/webhook            (Stripe calls this — not the frontend)

Every endpoint that would touch a real Stripe API call returns a clear
"billing isn't configured yet" error (503, not a fabricated success) if
STRIPE_SECRET_KEY isn't set — same honesty principle as
app/orchestration.py's Anthropic integration.
"""

import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import auth, billing, models, schemas
from ..database import get_db

router = APIRouter(prefix="/v1/billing", tags=["billing"])


def _not_configured_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": {
                "code": "billing_not_configured",
                "message": "Billing isn't configured on this deployment yet. Set STRIPE_SECRET_KEY "
                "(and STRIPE_PRICE_ID_PRO / STRIPE_PRICE_ID_TEAM) in the environment to enable it.",
            }
        },
    )


@router.get("/plans", response_model=list[schemas.PlanOut])
def list_plans():
    return [
        schemas.PlanOut(
            id=p.id,
            name=p.name,
            monthly_price_usd=p.monthly_price_usd,
            token_allowance=p.token_allowance,
            purchasable=p.stripe_price_id is not None,
        )
        for p in billing.list_plans()
    ]


@router.post("/checkout", response_model=schemas.CheckoutSessionOut)
def create_checkout(
    payload: schemas.CheckoutSessionCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    plan = billing.get_plan(payload.plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "plan_not_found", "message": f"No plan '{payload.plan_id}'"}},
        )
    if plan.stripe_price_id is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "plan_not_purchasable",
                    "message": f"'{plan.name}' isn't a purchasable plan (it has no price attached).",
                }
            },
        )

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    try:
        checkout_url = billing.create_checkout_session(
            plan=plan,
            customer_email=current_user.email,
            existing_customer_id=current_user.stripe_customer_id,
            success_url=f"{frontend_url}/settings?checkout=success",
            cancel_url=f"{frontend_url}/settings?checkout=cancelled",
        )
    except billing.BillingNotConfiguredError:
        raise _not_configured_error()
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=502,
            detail={"error": {"code": "stripe_error", "message": str(e) or "Stripe rejected the request."}},
        )

    return schemas.CheckoutSessionOut(checkout_url=checkout_url)


@router.get("/portal", response_model=schemas.BillingPortalOut)
def create_portal_session(
    current_user: models.User = Depends(auth.get_current_user),
):
    if not current_user.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "no_stripe_customer",
                    "message": "No billing account on file yet — subscribe to a paid plan first.",
                }
            },
        )

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    try:
        portal_url = billing.create_billing_portal_session(
            stripe_customer_id=current_user.stripe_customer_id,
            return_url=f"{frontend_url}/settings",
        )
    except billing.BillingNotConfiguredError:
        raise _not_configured_error()
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=502,
            detail={"error": {"code": "stripe_error", "message": str(e) or "Stripe rejected the request."}},
        )

    return schemas.BillingPortalOut(portal_url=portal_url)


@router.post("/webhook", status_code=200)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Stripe calls this directly — never the frontend. Signature
    verification (construct_webhook_event) is what makes this safe to
    trust; without a valid signature this rejects the request outright
    rather than processing an unverified body."""
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        event = billing.construct_webhook_event(payload, signature)
    except billing.BillingNotConfiguredError:
        raise _not_configured_error()
    except stripe.error.SignatureVerificationError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "invalid_signature", "message": "Webhook signature verification failed."}},
        )

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data, db)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_cancelled(data, db)

    return {"received": True}


def _handle_checkout_completed(session: dict, db: Session) -> None:
    plan_id = billing.plan_id_from_checkout_session(session)
    plan = billing.get_plan(plan_id) if plan_id else None
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    email = (session.get("customer_details") or {}).get("email") or session.get("customer_email")

    user = None
    if customer_id:
        user = db.query(models.User).filter(models.User.stripe_customer_id == customer_id).first()
    if user is None and email:
        user = db.query(models.User).filter(models.User.email == email).first()

    if user is None or plan is None:
        return  # nothing we can safely apply this to — leave state untouched, don't guess

    user.stripe_customer_id = customer_id or user.stripe_customer_id
    user.stripe_subscription_id = subscription_id
    user.plan_tier = plan.id
    user.token_balance = plan.token_allowance
    db.commit()


def _handle_subscription_cancelled(subscription: dict, db: Session) -> None:
    """Resets straight to the free plan's baseline allowance rather than
    prorating whatever was left on the paid plan — a simplifying,
    honest choice: no invented partial-period math, just "you're back
    on Free, here's Free's real allowance." A more sophisticated
    proration policy is a real product decision for later, not
    something to fake here."""
    subscription_id = subscription.get("id")
    if not subscription_id:
        return
    user = db.query(models.User).filter(models.User.stripe_subscription_id == subscription_id).first()
    if user is None:
        return
    free_plan = billing.get_plan("free")
    user.plan_tier = "free"
    user.stripe_subscription_id = None
    if free_plan:
        user.token_balance = free_plan.token_allowance
    db.commit()
