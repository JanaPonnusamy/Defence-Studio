from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serializers import project_out
from app.config import get_settings
from app.db.models import Asset, Job, Project, Scene, VisualPlan
from app.errors import VideoModuleError
from app.pipeline.import_assets import sanitize_text

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    ai_image_budget_usd: float | None = Field(default=None, ge=0, le=10_000)


@router.post("", status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    name = sanitize_text(body.name, 255)
    if not name:
        raise VideoModuleError("VALIDATION_ERROR", "Project name is required.")
    budget = body.ai_image_budget_usd if body.ai_image_budget_usd is not None else get_settings().default_ai_image_budget_usd
    project = Project(name=name, description=sanitize_text(body.description, 4000), ai_image_budget_usd=budget)
    db.add(project)
    db.commit()
    return project_out(project)


@router.get("")
def list_projects(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(Project).order_by(Project.created_at.desc()).limit(max(1, min(500, limit))).all()
    return [project_out(p) for p in rows]


@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise VideoModuleError("NOT_FOUND", f"Project {project_id} not found.")

    def count(model, col):
        return db.query(func.count()).select_from(model).filter(col == project_id).scalar()

    return {**project_out(project), "counts": {
        "assets": count(Asset, Asset.project_id), "scenes": count(Scene, Scene.project_id),
        "visual_plans": count(VisualPlan, VisualPlan.project_id), "jobs": count(Job, Job.project_id),
    }}
