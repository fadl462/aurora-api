"""
POST /v1/generated-documents            — generate a real .pptx/.docx/.xlsx from a prompt
GET  /v1/generated-documents             — list the current user's generated documents
GET  /v1/generated-documents/{id}/download — download the actual file bytes

Deliberately a separate prefix from /v1/documents (the Canvas text
documents) — these are a different kind of object entirely (real binary
office files, not editable plain text), and conflating the two would
make both APIs harder to reason about.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db
from ..document_generation import MIME_TYPES, generate_document

router = APIRouter(prefix="/v1/generated-documents", tags=["document-generation"])


def _not_found(document_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "generated_document_not_found",
                "message": f"No generated document with id {document_id}",
                "request_id": str(uuid.uuid4()),
            }
        },
    )


def _get_owned(document_id: str, current_user: models.User, db: Session) -> models.GeneratedDocument:
    document = db.get(models.GeneratedDocument, document_id)
    if not document or document.user_id != current_user.id:
        raise _not_found(document_id)
    return document


def _to_out(document: models.GeneratedDocument) -> schemas.GeneratedDocumentOut:
    return schemas.GeneratedDocumentOut(
        id=document.id,
        title=document.title,
        format=document.format,
        prompt=document.prompt,
        is_placeholder=bool(document.is_placeholder),
        size_bytes=len(document.file_data),
        created_at=document.created_at,
    )


@router.get("", response_model=list[schemas.GeneratedDocumentOut])
def list_generated_documents(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    documents = (
        db.query(models.GeneratedDocument)
        .filter(models.GeneratedDocument.user_id == current_user.id)
        .order_by(models.GeneratedDocument.created_at.desc())
        .all()
    )
    return [_to_out(d) for d in documents]


@router.post("", response_model=schemas.GeneratedDocumentOut, status_code=201)
def create_generated_document(
    payload: schemas.GeneratedDocumentCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    # Real generation happens here — see document_generation.py for why
    # this never raises for real-world failures (missing key, bad model
    # output, upstream errors) and instead returns a clearly-labeled
    # placeholder file rather than a 500.
    result = generate_document(payload.prompt, payload.format)

    document = models.GeneratedDocument(
        user_id=current_user.id,
        title=result["title"],
        format=payload.format,
        prompt=payload.prompt,
        file_data=result["file_bytes"],
        is_placeholder=1 if result["is_placeholder"] else 0,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _to_out(document)


@router.get("/{document_id}/download")
def download_generated_document(
    document_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    document = _get_owned(document_id, current_user, db)
    filename = f"{document.title}.{document.format}".replace("/", "-")
    return Response(
        content=document.file_data,
        media_type=MIME_TYPES[document.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
