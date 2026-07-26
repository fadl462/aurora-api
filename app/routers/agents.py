"""
Real endpoints backing the Agents / Agent Console surfaces:

  GET  /v1/agents
  GET  /v1/agents/{id}
  GET  /v1/agents/{id}/runs
  GET  /v1/agents/{id}/approvals
  POST /v1/agents/{id}/approvals/{approval_id}/approve
  POST /v1/agents/{id}/approvals/{approval_id}/deny

Same ownership-scoping pattern as conversations.py: an agent (or
approval) belonging to another user returns 404, not 403.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _not_found(resource: str, resource_id: str) -> HTTPException:
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


def _get_owned_agent(agent_id: str, current_user: models.User, db: Session) -> models.Agent:
    agent = db.get(models.Agent, agent_id)
    if not agent or agent.user_id != current_user.id:
        raise _not_found("agent", agent_id)
    return agent


@router.get("", response_model=list[schemas.AgentOut])
def list_agents(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(models.Agent).filter(models.Agent.user_id == current_user.id).all()


@router.get("/{agent_id}", response_model=schemas.AgentOut)
def get_agent(
    agent_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return _get_owned_agent(agent_id, current_user, db)


@router.get("/{agent_id}/runs", response_model=list[schemas.AgentRunOut])
def list_runs(
    agent_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_owned_agent(agent_id, current_user, db)
    return sorted(agent.runs, key=lambda r: r.created_at, reverse=True)


@router.get("/{agent_id}/approvals", response_model=list[schemas.PendingApprovalOut])
def list_approvals(
    agent_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    agent = _get_owned_agent(agent_id, current_user, db)
    return [a for a in agent.approvals if a.status == "pending"]


def _decide_approval(
    agent_id: str,
    approval_id: str,
    decision: str,
    current_user: models.User,
    db: Session,
) -> models.PendingApproval:
    agent = _get_owned_agent(agent_id, current_user, db)
    approval = next((a for a in agent.approvals if a.id == approval_id), None)
    if not approval:
        raise _not_found("approval", approval_id)
    if approval.status != "pending":
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "approval_already_decided",
                    "message": f"This approval was already {approval.status}.",
                }
            },
        )
    approval.status = decision
    approval.decided_at = models.utcnow()
    db.commit()
    db.refresh(approval)
    return approval


@router.post("/{agent_id}/approvals/{approval_id}/approve", response_model=schemas.PendingApprovalOut)
def approve(
    agent_id: str,
    approval_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return _decide_approval(agent_id, approval_id, "approved", current_user, db)


@router.post("/{agent_id}/approvals/{approval_id}/deny", response_model=schemas.PendingApprovalOut)
def deny(
    agent_id: str,
    approval_id: str,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return _decide_approval(agent_id, approval_id, "denied", current_user, db)
