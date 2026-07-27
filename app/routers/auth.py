"""
POST /v1/auth/register
POST /v1/auth/login
GET  /v1/auth/me
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db
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


@router.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    email = form_data.username.lower().strip()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise auth.credentials_error()
    token = auth.create_access_token(user.id)
    return schemas.Token(access_token=token)


@router.get("/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user
