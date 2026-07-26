"""
GET /v1/usage

Backs the persistent usage meter in the frontend TopBar — the whole
point is that this number is real and current, not a periodic report
that arrives after the fact. It reads directly off the same
User.token_balance column that send_message() deducts from in
conversations.py, so there's exactly one source of truth.
"""

from fastapi import APIRouter, Depends

from .. import auth, models
from ..models import STARTING_TOKEN_BALANCE
from ..schemas import UsageOut

router = APIRouter(prefix="/v1/usage", tags=["usage"])


@router.get("", response_model=UsageOut)
def get_usage(current_user: models.User = Depends(auth.get_current_user)):
    balance = current_user.token_balance
    percent = (balance / STARTING_TOKEN_BALANCE * 100) if STARTING_TOKEN_BALANCE > 0 else 0.0
    return UsageOut(
        balance=balance,
        starting_balance=STARTING_TOKEN_BALANCE,
        percent_remaining=round(max(0.0, min(100.0, percent)), 1),
    )
