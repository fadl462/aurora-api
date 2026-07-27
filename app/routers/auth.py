"""
POST  /v1/auth/register
POST  /v1/auth/login
GET   /v1/auth/me
PATCH /v1/auth/me
GET   /v1/auth/sessions      (real sign-in activity — device + best-effort location)
"""

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
    token = auth.create_access_token(user.id)

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

    return schemas.Token(access_token=token)


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
