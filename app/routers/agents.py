"""
Real endpoints backing the Agents / Agent Console surfaces:

  GET  /v1/agents
  POST /v1/agents                        (create a real user-defined agent)
  GET  /v1/agents/approvals              (cross-agent inbox — the TopBar bell)
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


# Same visual palette used for the seeded starter agents (seed_data.py)
# — a user-created agent should look native to the product, not like a
# visually distinct second-class citizen next to the built-in five.
AVATAR_PALETTE = [
    "bg-aurora-1/10 text-aurora-1",
    "bg-aurora-2/10 text-aurora-2",
    "bg-aurora-3/10 text-aurora-3",
    "bg-aurora-4/10 text-aurora-4",
    "bg-warning/10 text-warning",
]


@router.get("", response_model=list[schemas.AgentOut])
def list_agents(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(models.Agent).filter(models.Agent.user_id == current_user.id).all()


@router.post("", response_model=schemas.AgentOut, status_code=201)
def create_agent(
    payload: schemas.AgentCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    existing_count = db.query(models.Agent).filter(models.Agent.user_id == current_user.id).count()
    agent = models.Agent(
        user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        tools=[t.model_dump() for t in payload.tools],
        # A brand-new agent hasn't done anything yet — "idle" is the
        # honest starting status, not "active" (that's earned by
        # actually running, same as the non-Researcher starter agents).
        status="idle",
        avatar_letter=payload.name[0].upper(),
        avatar_color_class=AVATAR_PALETTE[existing_count % len(AVATAR_PALETTE)],
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/approvals", response_model=list[schemas.PendingApprovalWithAgentOut])
def list_all_pending_approvals(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    """The real Inbox: every pending approval across every agent this
    user owns, in one place, oldest first — this is what the TopBar's
    notification bell actually shows. Must be registered before
    GET /{agent_id} below, or FastAPI would try to match "approvals"
    as an agent_id and 404.
    """
    rows = (
        db.query(models.PendingApproval)
        .join(models.Agent, models.PendingApproval.agent_id == models.Agent.id)
        .filter(models.Agent.user_id == current_user.id)
        .filter(models.PendingApproval.status == "pending")
        .order_by(models.PendingApproval.created_at.asc())
        .all()
    )
    return [
        schemas.PendingApprovalWithAgentOut(
            id=row.id,
            agent_id=row.agent_id,
            tier=row.tier,
            action=row.action,
            status=row.status,
            created_at=row.created_at,
            decided_at=row.decided_at,
            agent_name=row.agent.name,
            agent_avatar_letter=row.agent.avatar_letter,
            agent_avatar_color_class=row.agent.avatar_color_class,
        )
        for row in rows
    ]


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
