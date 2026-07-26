"""
GET  /v1/projects
POST /v1/projects

Same ownership-scoping pattern as everything else in this API.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(prefix="/v1/projects", tags=["projects"])


def _to_out(project: models.Project) -> schemas.ProjectOut:
    return schemas.ProjectOut(
        id=project.id,
        name=project.name,
        color=project.color,
        created_at=project.created_at,
        thread_count=len(project.conversations),
    )


@router.get("", response_model=list[schemas.ProjectOut])
def list_projects(
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    projects = db.query(models.Project).filter(models.Project.user_id == current_user.id).all()
    return [_to_out(p) for p in projects]


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(
    payload: schemas.ProjectCreate,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db),
):
    project = models.Project(user_id=current_user.id, name=payload.name, color=payload.color)
    db.add(project)
    db.commit()
    db.refresh(project)
    return _to_out(project)
