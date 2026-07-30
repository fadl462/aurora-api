"""
Real subscription billing endpoints, backed by app/billing.py's
Paystack integration (not Stripe — see that module's docstring for why).

  GET  /v1/billing/plans              (public — no auth needed to see pricing)
  POST /v1/billing/checkout           (real Paystack transaction, returns a checkout URL)
  GET  /v1/billing/verify             (synchronous confirm-and-apply on return from checkout)
  POST /v1/billing/cancel             (real Paystack subscription cancellation)
  POST /v1/billing/webhook            (Paystack calls this — not the frontend)

Every endpoint that would touch a real Paystack API call returns a
clear "billing isn't configured yet" error (503, not a fabricated
success) if PAYSTACK_SECRET_KEY isn't set — same honesty principle as
app/orchestration.py's Anthropic integration.
"""

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
                "message": "Billing isn't configured on this deployment yet. Set PAYSTACK_SECRET_KEY "
                "(and PAYSTACK_PLAN_CODE_PRO / PAYSTACK_PLAN_CODE_TEAM) in the environment to enable it.",
            }
        },
    )


def _paystack_error(e: billing.PaystackApiError) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={"error": {"code": "paystack_error", "message": str(e) or "Paystack rejected the request."}},
    )


@router.get("/plans", response_model=list[schemas.PlanOut])
def list_plans():
    return [
        schemas.PlanOut(
            id=p.id,
            name=p.name,
            monthly_price=p.monthly_price,
            currency=p.currency,
            token_allowance=p.token_allowance,
            purchasable=p.paystack_plan_code is not None,
        )
        for p in billing.list_plans()
    ]


@router.post("/checkout", response_model=schemas.CheckoutSessionOut)
def create_checkout(
    payload: schemas.CheckoutSessionCreate,
    current_user: models.User = Depends(auth.get_current_user),
):
    plan = billing.get_plan(payload.plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "plan_not_found", "message": f"No plan '{payload.plan_id}'"}},
        )
    if plan.paystack_plan_code is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "plan_not_purchasable",
                    "message": f"'{plan.name}' isn't a purchasable plan (it has no Paystack plan code attached).",
                }
            },
        )

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
    try:
        result = billing.initialize_transaction(
            plan=plan,
            customer_email=current_user.email,
            callback_url=f"{frontend_url}/settings",
        )
    except billing.BillingNotConfiguredError:
        raise _not_configured_error()
    except billing.PaystackApiError as e:
        raise _paystack_error(e)

    return schemas.CheckoutSessionOut(checkout_url=result["authorization_url"])


@router.get("/verify", response_model=schemas.CheckoutVerifyOut)
def verify_checkout(
    reference: str = Query(..., description="The 'reference' or 'trxref' query param Paystack appends on redirect"),
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """Called by the Settings page right after Paystack redirects back
    from checkout — confirms the transaction directly with Paystack and
    applies the plan immediately, rather than depending solely on the
    webhook (which is reliable but can lag by a few seconds, and won't
    fire at all against a backend that isn't yet publicly reachable —
    e.g. local development)."""
    try:
        data = billing.verify_transaction(reference)
    except billing.BillingNotConfiguredError:
        raise _not_configured_error()
    except billing.PaystackApiError as e:
        raise _paystack_error(e)

    if data.get("status") != "success":
        return schemas.CheckoutVerifyOut(plan_tier=current_user.plan_tier, verified=False)

    plan_id = billing.plan_id_from_transaction_data(data)
    plan = billing.get_plan(plan_id) if plan_id else None
    if plan is None:
        return schemas.CheckoutVerifyOut(plan_tier=current_user.plan_tier, verified=False)

    customer_code = (data.get("customer") or {}).get("customer_code")
    if customer_code:
        current_user.paystack_customer_code = customer_code
    current_user.plan_tier = plan.id
    current_user.token_balance = plan.token_allowance
    db.commit()

    return schemas.CheckoutVerifyOut(plan_tier=current_user.plan_tier, verified=True)


@router.post("/cancel", response_model=schemas.UserOut)
def cancel_subscription(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.paystack_subscription_code or not current_user.paystack_email_token:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "no_active_subscription",
                    "message": "No active paid subscription on file to cancel.",
                }
            },
        )

    try:
        billing.disable_subscription(
            subscription_code=current_user.paystack_subscription_code,
            email_token=current_user.paystack_email_token,
        )
    except billing.BillingNotConfiguredError:
        raise _not_configured_error()
    except billing.PaystackApiError as e:
        raise _paystack_error(e)

    _downgrade_to_free(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


def _downgrade_to_free(user: models.User) -> None:
    """Resets straight to the Free plan's real allowance rather than
    prorating whatever was left on the paid plan — an honest,
    no-invented-math choice, not a bug."""
    free_plan = billing.get_plan("free")
    user.plan_tier = "free"
    user.paystack_subscription_code = None
    user.paystack_email_token = None
    if free_plan:
        user.token_balance = free_plan.token_allowance


@router.post("/webhook", status_code=200)
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    """Paystack calls this directly — never the frontend. Signature
    verification is what makes this safe to trust; without a valid
    x-paystack-signature this rejects the request outright rather than
    processing an unverified body."""
    payload = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    if not billing.is_paystack_configured():
        raise _not_configured_error()
    if not billing.verify_webhook_signature(payload, signature):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "invalid_signature", "message": "Webhook signature verification failed."}},
        )

    event = json.loads(payload)
    event_type = event.get("event")
    data = event.get("data") or {}

    if event_type == "charge.success":
        _handle_charge_success(data, db)
    elif event_type == "subscription.create":
        _handle_subscription_created(data, db)
    elif event_type in ("subscription.disable", "subscription.not_renew"):
        _handle_subscription_disabled(data, db)

    return {"received": True}


def _find_user(data: dict, db: Session) -> "models.User | None":
    customer_code = (data.get("customer") or {}).get("customer_code")
    email = (data.get("customer") or {}).get("email")

    user = None
    if customer_code:
        user = db.query(models.User).filter(models.User.paystack_customer_code == customer_code).first()
    if user is None and email:
        user = db.query(models.User).filter(models.User.email == email).first()
    return user


def _handle_charge_success(data: dict, db: Session) -> None:
    plan_id = billing.plan_id_from_transaction_data(data)
    plan = billing.get_plan(plan_id) if plan_id else None
    user = _find_user(data, db)

    if user is None or plan is None:
        return  # nothing we can safely apply this to — leave state untouched, don't guess

    customer_code = (data.get("customer") or {}).get("customer_code")
    if customer_code:
        user.paystack_customer_code = customer_code
    user.plan_tier = plan.id
    user.token_balance = plan.token_allowance
    db.commit()


def _handle_subscription_created(data: dict, db: Session) -> None:
    """Fired separately from charge.success — this is where Paystack
    actually hands back the subscription_code and email_token needed
    later to cancel. A charge can succeed a few seconds before this
    arrives, which is fine: the plan upgrade already took effect via
    charge.success, and this just fills in what's needed for
    cancellation."""
    user = _find_user(data, db)
    if user is None:
        return

    subscription_code = data.get("subscription_code")
    email_token = data.get("email_token")
    customer_code = (data.get("customer") or {}).get("customer_code")

    if customer_code:
        user.paystack_customer_code = customer_code
    if subscription_code:
        user.paystack_subscription_code = subscription_code
    if email_token:
        user.paystack_email_token = email_token
    db.commit()


def _handle_subscription_disabled(data: dict, db: Session) -> None:
    subscription_code = data.get("subscription_code")
    if not subscription_code:
        return
    user = (
        db.query(models.User).filter(models.User.paystack_subscription_code == subscription_code).first()
    )
    if user is None:
        return
    _downgrade_to_free(user)
    db.commit()
