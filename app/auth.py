"""
Password hashing and JWT session tokens.

This is real, working auth for local development — not a stub. The one
thing that MUST change before any real deployment: SECRET_KEY below is
a dev-only default and must come from an environment variable in
production (see the warning at import time).
"""

import hashlib
import os
import secrets
import warnings
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import models
from .database import get_db

SECRET_KEY = os.environ.get("AURORA_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = "dev-only-insecure-secret-do-not-use-in-production"
    warnings.warn(
        "AURORA_SECRET_KEY is not set — using an insecure default. "
        "Set a real secret via the environment before deploying this anywhere.",
        stacklevel=2,
    )

ALGORITHM = "HS256"
# Short-lived on purpose — a leaked access token now has a small blast
# radius. The "stay logged in for weeks" feel people actually want
# comes from the refresh token below, not from a long-lived access
# token; a long-lived access token would be worse security for no real
# user-facing benefit, since refresh tokens can do the same job while
# also being revocable server-side.
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _hash_refresh_token(raw_token: str) -> str:
    # sha256, not bcrypt: this is a 384-bit cryptographically random
    # opaque token, not a human-chosen password — there's no brute-force
    # guessing risk to slow down, so a fast, deterministic hash (needed
    # anyway for exact-match lookup by hash) is the right tool here.
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_refresh_token(db: Session, user_id: str, login_event_id: str | None = None) -> str:
    """Creates a new refresh token row and returns the RAW token — the
    only place the raw value ever exists is this return value and
    whatever the client stores. The DB only ever holds its hash, same
    principle as password storage."""
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    record = models.RefreshToken(
        user_id=user_id,
        token_hash=_hash_refresh_token(raw_token),
        login_event_id=login_event_id,
        expires_at=expires_at,
    )
    db.add(record)
    db.commit()
    return raw_token


def refresh_token_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "invalid_refresh_token",
                "message": "Session expired or already used elsewhere. Please log in again.",
            }
        },
    )


def redeem_refresh_token(db: Session, raw_token: str) -> tuple[models.User, str]:
    """Validates a refresh token and rotates it: the presented token is
    revoked and a brand-new one is issued in the same call, so a single
    refresh token can only ever be redeemed once. If a stolen, already-
    used token is ever replayed, that alone is a legitimate signal
    something is wrong — rotation is what makes that detectable at all,
    which a reusable long-lived token could never provide.

    Returns (user, new_raw_refresh_token). Raises refresh_token_error()
    for anything invalid: unknown hash, already revoked, or expired.
    """
    token_hash = _hash_refresh_token(raw_token)
    record = db.query(models.RefreshToken).filter(models.RefreshToken.token_hash == token_hash).first()

    if record is None or record.revoked_at is not None:
        raise refresh_token_error()

    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise refresh_token_error()

    user = db.get(models.User, record.user_id)
    if user is None:
        raise refresh_token_error()

    record.revoked_at = datetime.now(timezone.utc)
    db.commit()

    new_raw_token = issue_refresh_token(db, user.id, record.login_event_id)
    return user, new_raw_token


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    """Powers real logout — revokes server-side so a stolen refresh
    token doesn't remain valid just because the client deleted its own
    local copy. Silently no-ops on an unknown/garbage token rather than
    erroring: the end state the caller wants (this token, if it ever
    existed, no longer works) is already true either way."""
    token_hash = _hash_refresh_token(raw_token)
    record = db.query(models.RefreshToken).filter(models.RefreshToken.token_hash == token_hash).first()
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        db.commit()


def credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "invalid_credentials", "message": "Could not validate credentials."}},
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_error()
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "token_expired", "message": "Session expired, please log in again."}},
        )
    except jwt.InvalidTokenError:
        raise credentials_error()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    user_id = decode_access_token(token)
    user = db.get(models.User, user_id)
    if user is None:
        raise credentials_error()
    return user
