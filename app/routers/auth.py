"""
POST   /v1/auth/register
POST   /v1/auth/login
POST   /v1/auth/refresh        (silent re-auth — this is what keeps people logged in)
POST   /v1/auth/logout         (real server-side revocation, not just client-side)
GET    /v1/auth/me
PATCH  /v1/auth/me
GET    /v1/auth/sessions       (real sign-in activity — device + best-effort location)
DELETE /v1/auth/sessions/{id}  (sign out a specific device, remotely)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import auth, login_activity, models, schemas
from ..database import SessionLocal, get_db
from ..seed_data import seed_starter_agents, seed_starter_projects

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", response_model=schemas.UserOut, status_code=201)
def register(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    name = payload.name.strip() if payload.name and payload.name.strip() else None
    user = models.User(
        email=payload.email.lower().strip(),
        name=name,
        hashed_password=auth.hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "email_already_registered",
                    "message": "An account with this email already exists.",
                }
            },
        )
    db.refresh(user)
    seed_starter_agents(db, user.id)
    seed_starter_projects(db, user.id)
    return user


def _resolve_and_store_location(event_id: str, ip_address: str) -> None:
    """Runs as a BackgroundTask, after the login response has already
    been sent — a third-party geolocation call has no business adding
    latency or a new failure mode to the login path itself."""
    location = login_activity.resolve_location(ip_address)
    if not location:
        return
    db = SessionLocal()
    try:
        event = db.get(models.LoginEvent, event_id)
        if event:
            event.location_label = location
            db.commit()
    finally:
        db.close()


@router.post("/login", response_model=schemas.Token)
def login(
    request: Request,
    background_tasks: BackgroundTasks,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    email = form_data.username.lower().strip()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise auth.credentials_error()

    ip_address = login_activity.get_client_ip(
        request.headers, request.client.host if request.client else None
    )
    user_agent = request.headers.get("user-agent")
    event = models.LoginEvent(
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
        device_label=login_activity.parse_device(user_agent),
        location_label=None,  # filled in by the background task below, if resolvable
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    background_tasks.add_task(_resolve_and_store_location, event.id, ip_address)

    token = auth.create_access_token(user.id)
    refresh_token = auth.issue_refresh_token(db, user.id, login_event_id=event.id)
    return schemas.Token(access_token=token, refresh_token=refresh_token)


@router.post("/refresh", response_model=schemas.Token)
def refresh(payload: schemas.RefreshRequest, db: Session = Depends(get_db)):
    """This is the real mechanism behind 'staying logged in' — the
    frontend calls this silently whenever an access token has expired,
    rather than sending the person back to the login screen. The
    refresh token itself rotates on every use (see
    auth.redeem_refresh_token) so it can only ever be redeemed once."""
    user, new_refresh_token = auth.redeem_refresh_token(db, payload.refresh_token)
    new_access_token = auth.create_access_token(user.id)
    return schemas.Token(access_token=new_access_token, refresh_token=new_refresh_token)


@router.post("/logout", status_code=204)
def logout(payload: schemas.LogoutRequest, db: Session = Depends(get_db)):
    """Real server-side logout — revokes the refresh token so it can't
    be silently used again, rather than relying entirely on the client
    to discard its own copy."""
    auth.revoke_refresh_token(db, payload.refresh_token)
    return None


@router.get("/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


@router.patch("/me", response_model=schemas.UserOut)
def update_me(
    payload: schemas.UserUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """The Settings page's name field lands here. Registration already
    lets someone set a name; this is the only way to change it
    afterward — without this endpoint, the name field would be
    write-once, which isn't a real editable profile."""
    if payload.name is not None:
        stripped = payload.name.strip()
        current_user.name = stripped or None
        db.commit()
        db.refresh(current_user)
    return current_user


@router.get("/sessions", response_model=list[schemas.LoginEventOut])
def list_login_events(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.LoginEvent)
        .filter(models.LoginEvent.user_id == current_user.id)
        .order_by(models.LoginEvent.created_at.desc())
        .limit(20)
        .all()
    )


@router.delete("/sessions/{event_id}", status_code=204)
def sign_out_device(
    event_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """The 'sign out this device' feature RefreshToken's docstring
    already planned for — revokes whichever refresh token was issued
    alongside this login event, so that device can no longer silently
    refresh its way to a new access token. The LoginEvent row itself is
    kept, not deleted — it's a historical audit record of "this device
    signed in at this time," and revoking its session doesn't erase
    that it happened.

    Idempotent by design: revoking an already-revoked (or never-issued)
    token still returns 204, since the end state the caller wants —
    "this session, if it exists, no longer works" — is already true
    either way. The only real error case is trying to act on a login
    event that isn't yours.
    """
    event = db.get(models.LoginEvent, event_id)
    if event is None or event.user_id != current_user.id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "login_event_not_found", "message": f"No sign-in event with id {event_id}"}},
        )

    tokens = (
        db.query(models.RefreshToken)
        .filter(models.RefreshToken.login_event_id == event_id)
        .filter(models.RefreshToken.revoked_at.is_(None))
        .all()
    )
    for token in tokens:
        token.revoked_at = datetime.now(timezone.utc)
    if tokens:
        db.commit()
    return None
