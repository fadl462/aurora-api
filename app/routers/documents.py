"""
GET  /v1/documents
POST /v1/documents
GET  /v1/documents/{id}
PUT  /v1/documents/{id}

Same ownership-scoping pattern as everything else in this API.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(prefix="/v1/documents", tags=["documents"])


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


@router.put("/{document_id}", response_model=schemas.DocumentOut)
def update_document(
    document_id: str,
    payload: schemas.DocumentUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    document = _get_owned_document(document_id, current_user, db)
    if payload.title is not None:
        document.title = payload.title
    if payload.content is not None:
        document.content = payload.content
    document.updated_at = models.utcnow()
    db.commit()
    db.refresh(document)
    return document
