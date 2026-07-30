"""
GET    /v1/documents
POST   /v1/documents
GET    /v1/documents/{id}
PUT    /v1/documents/{id}
GET    /v1/documents/{id}/versions              (real version history)
POST   /v1/documents/{id}/versions/{vid}/restore (restore a prior version)

Same ownership-scoping pattern as everything else in this API.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(prefix="/v1/documents", tags=["documents"])

# Throttles how often an edit creates a new version snapshot. Autosave
# fires every ~800ms of typing pause (see CanvasContent.tsx), so
# snapshotting on every single save would flood the history with
# near-identical mid-sentence states. One real checkpoint every few
# minutes of active editing is what makes "history" mean something —
# a restore endpoint, not a per-keystroke undo log.
VERSION_SNAPSHOT_MIN_INTERVAL = timedelta(minutes=3)


def _not_found(document_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": "document_not_found", "message": f"No document with id {document_id}"}},
    )


def _not_found_project(project_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": "project_not_found", "message": f"No project with id {project_id}"}},
    )


def _get_owned_document(document_id: str, current_user: models.User, db: Session) -> models.Document:
    document = db.get(models.Document, document_id)
    if not document or document.user_id != current_user.id:
        raise _not_found(document_id)
    return document


def _validate_project_ownership(project_id: Optional[str], current_user: models.User, db: Session) -> None:
    if project_id is None:
        return
    project = db.get(models.Project, project_id)
    if not project or project.user_id != current_user.id:
        raise _not_found_project(project_id)


@router.get("", response_model=list[schemas.DocumentOut])
def list_documents(
    project_id: Optional[str] = Query(
        None,
        description=(
            "Real context wall, same semantics as conversations: omit this "
            "entirely to see only unscoped/personal documents (project_id "
            "IS NULL). Pass a project id to see only that project's "
            "documents. Documents never bleed between projects, and never "
            "mix with the personal/unscoped view, by design."
        ),
    ),
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    _validate_project_ownership(project_id, current_user, db)
    query = db.query(models.Document).filter(models.Document.user_id == current_user.id)
    query = query.filter(models.Document.project_id == project_id)
    return query.order_by(models.Document.updated_at.desc()).all()


@router.post("", response_model=schemas.DocumentOut, status_code=201)
def create_document(
    payload: schemas.DocumentCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    _validate_project_ownership(payload.project_id, current_user, db)
    document = models.Document(
        user_id=current_user.id,
        project_id=payload.project_id,
        title=payload.title,
        content=payload.content,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.get("/{document_id}", response_model=schemas.DocumentOut)
def get_document(
    document_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return _get_owned_document(document_id, current_user, db)


def _should_snapshot(document: models.Document, db: Session) -> bool:
    latest = (
        db.query(models.DocumentVersion)
        .filter(models.DocumentVersion.document_id == document.id)
        .order_by(models.DocumentVersion.created_at.desc())
        .first()
    )
    if latest is None:
        return True  # first edit ever — always worth a checkpoint of the original
    latest_created_at = latest.created_at
    if latest_created_at.tzinfo is None:
        latest_created_at = latest_created_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - latest_created_at >= VERSION_SNAPSHOT_MIN_INTERVAL


@router.put("/{document_id}", response_model=schemas.DocumentOut)
def update_document(
    document_id: str,
    payload: schemas.DocumentUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    document = _get_owned_document(document_id, current_user, db)
    content_changing = payload.content is not None and payload.content != document.content
    title_changing = payload.title is not None and payload.title != document.title

    if (content_changing or title_changing) and _should_snapshot(document, db):
        db.add(models.DocumentVersion(document_id=document.id, title=document.title, content=document.content))

    if payload.title is not None:
        document.title = payload.title
    if payload.content is not None:
        document.content = payload.content
    document.updated_at = models.utcnow()
    db.commit()
    db.refresh(document)
    return document


@router.get("/{document_id}/versions", response_model=list[schemas.DocumentVersionOut])
def list_document_versions(
    document_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    document = _get_owned_document(document_id, current_user, db)
    return (
        db.query(models.DocumentVersion)
        .filter(models.DocumentVersion.document_id == document.id)
        .order_by(models.DocumentVersion.created_at.desc())
        .all()
    )


@router.post("/{document_id}/versions/{version_id}/restore", response_model=schemas.DocumentOut)
def restore_document_version(
    document_id: str,
    version_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    document = _get_owned_document(document_id, current_user, db)
    version = db.get(models.DocumentVersion, version_id)
    if version is None or version.document_id != document.id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "version_not_found", "message": f"No version with id {version_id}"}},
        )

    # Restoring is itself undoable: always snapshot what was live right
    # before the restore, regardless of the normal throttle — this is a
    # deliberate, one-off user action, not a routine autosave tick, and
    # skipping the checkpoint here would be the one case where losing
    # the pre-restore state could actually hurt someone.
    db.add(models.DocumentVersion(document_id=document.id, title=document.title, content=document.content))

    document.title = version.title
    document.content = version.content
    document.updated_at = models.utcnow()
    db.commit()
    db.refresh(document)
    return document
