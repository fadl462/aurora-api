"""
Implements the Chat section of docs/06-api-specification.md:

  GET  /v1/conversations
  POST /v1/conversations
  GET  /v1/conversations/{id}
  POST /v1/conversations/{id}/messages
  GET  /v1/conversations/{id}/messages

All endpoints require authentication and are scoped to the requesting
user — a conversation belonging to another user returns 404, not 403,
so ownership isn't leaked via status code.
"""

import uuid

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db
from ..orchestration import generate_reply

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

TITLE_MAX_LENGTH = 60


def _not_found(conversation_id: str, request_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "conversation_not_found",
                "message": f"No conversation with id {conversation_id}",
                "request_id": request_id,
            }
        },
    )


def _not_found_generic(resource: str, resource_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": f"{resource}_not_found",
                "message": f"No {resource} with id {resource_id}",
                "request_id": str(uuid.uuid4()),
            }
        },
    )


def _get_owned_conversation(
    conversation_id: str, current_user: models.User, db: Session
) -> models.Conversation:
    conversation = db.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise _not_found(conversation_id, str(uuid.uuid4()))
    return conversation


def _derive_title(content: str) -> str:
    stripped = content.strip()
    if len(stripped) <= TITLE_MAX_LENGTH:
        return stripped
    return stripped[:TITLE_MAX_LENGTH].rstrip() + "…"


@router.get("", response_model=list[schemas.ConversationOut])
def list_conversations(
    project_id: Optional[str] = Query(
        None,
        description=(
            "Real context wall: omit entirely to see only unscoped/personal "
            "conversations (project_id IS NULL). Pass a project id to see "
            "only that project's conversations. This is deliberate — a "
            "project's chats never bleed into another project's view, or "
            "into the personal/unscoped view, by design."
        ),
    ),
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    if project_id is not None:
        project = db.get(models.Project, project_id)
        if not project or project.user_id != current_user.id:
            raise _not_found_generic("project", project_id)

    return (
        db.query(models.Conversation)
        .filter(models.Conversation.user_id == current_user.id)
        .filter(models.Conversation.project_id == project_id)
        .order_by(models.Conversation.updated_at.desc())
        .all()
    )


@router.post("", response_model=schemas.ConversationOut, status_code=201)
def create_conversation(
    payload: schemas.ConversationCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    if payload.project_id is not None:
        project = db.get(models.Project, payload.project_id)
        if not project or project.user_id != current_user.id:
            raise _not_found_generic("project", payload.project_id)

    conversation = models.Conversation(
        title=payload.title, user_id=current_user.id, project_id=payload.project_id
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=schemas.ConversationOut)
def get_conversation(
    conversation_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return _get_owned_conversation(conversation_id, current_user, db)


@router.get("/{conversation_id}/messages", response_model=list[schemas.MessageOut])
def list_messages(
    conversation_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_owned_conversation(conversation_id, current_user, db)
    return conversation.messages


@router.post("/{conversation_id}/messages", response_model=schemas.MessageOut, status_code=201)
def send_message(
    conversation_id: str,
    payload: schemas.MessageCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    conversation = _get_owned_conversation(conversation_id, current_user, db)

    # Enforce the balance BEFORE spending anything — the whole point of
    # a visible usage meter is that running out is never a surprise, so
    # the backend has to actually stop spending at zero, not just report
    # a number the frontend happens to display.
    if current_user.token_balance <= 0:
        raise HTTPException(
            status_code=402,
            detail={
                "error": {
                    "code": "token_balance_exhausted",
                    "message": "You're out of tokens for this account. No message was sent.",
                    "request_id": str(uuid.uuid4()),
                }
            },
        )

    user_message = models.Message(
        conversation_id=conversation_id,
        role="user",
        content=payload.content,
    )
    db.add(user_message)

    # Auto-title from the first message, same pattern as most real chat
    # products — a conversation with no title yet isn't very useful in
    # a "recent threads" list.
    if conversation.title is None:
        conversation.title = _derive_title(payload.content)

    conversation.updated_at = models.utcnow()

    reply = generate_reply(payload.content, mode=payload.mode, model_choice=payload.model)
    assistant_message = models.Message(
        conversation_id=conversation_id,
        role="assistant",
        content=reply["content"],
        model_used=reply["model_used"],
        citations=[c.model_dump() for c in reply["citations"]] if reply["citations"] else None,
        confidence=str(reply["confidence"]) if reply["confidence"] is not None else None,
    )
    db.add(assistant_message)

    # Deduct real usage. Balance never goes negative — if a single reply
    # would have cost more than the remaining balance, the account is
    # simply left at zero rather than in debt.
    current_user.token_balance = max(0, current_user.token_balance - reply.get("tokens_used", 0))

    db.commit()
    db.refresh(assistant_message)

    # Confidence is stored as a string (simple schema for now) but the
    # API contract exposes it as a float per docs/06-api-specification.md.
    response = schemas.MessageOut.model_validate(assistant_message)
    if assistant_message.confidence is not None:
        response.confidence = float(assistant_message.confidence)
    return response
