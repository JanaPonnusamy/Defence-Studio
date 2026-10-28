from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serializers import job_out, plan_out, shot_out
from app.config import get_settings
from app.db.models import Project, Scene, Shot, VisualPlan
from app.errors import VideoModuleError
from app.generation.registry import canonical_name
from app.generation.service import ensure_requestable
from app.jobs.queue import create_job
from app.pipeline.import_assets import sanitize_text
from app.pipeline.preview import render_contact_sheet, render_shot_frame
from app.pipeline.timeline import TIMELINE_SCHEMA

router = APIRouter(tags=["visual-plans"])


class PlanBuildOptions(BaseModel):
    generate_ai: bool = False
    ai_provider: str | None = None
    max_ai_images: int = Field(default=4, ge=0, le=12)
    ai_hook: bool = True
    ai_quality: str = "standard"
    ai_resolution: str = "1280x720"
    allow_unknown_rights: bool = False
    max_shot_seconds: float | None = Field(default=None, ge=1.0, le=60.0)
    min_shot_seconds: float | None = Field(default=None, ge=0.5, le=30.0)
    # Phase 2
    allow_placeholder_fallback: bool | None = Field(
        default=None, description="Accept clearly labelled PLACEHOLDER images if a real AI provider fails / is not configured.")
    output_width: int | None = Field(default=None, ge=16, le=8192)
    output_height: int | None = Field(default=None, ge=16, le=8192)
    frame_rate: float | None = Field(default=None, gt=0, le=240)
    photo_fallback_for_data_intents: bool = Field(
        default=False, description="Show a strictly relevant rights-cleared photo instead of a GAP when a "
                                   "statistic/timeline/comparison beat has no sourced graphic.")
    fill_frame_photos: bool = Field(
        default=False, description="Crop photos to the output aspect (no black bars) and open every photo "
                                   "shot with gentle motion; protected regions still force the full frame.")


class PlanBuildRequest(BaseModel):
    project_id: str
    scene_ids: list[str] | None = Field(default=None, max_length=2000)
    options: PlanBuildOptions = Field(default_factory=PlanBuildOptions)


@router.post("/visual-plans/build", status_code=202)
def build_plan(body: PlanBuildRequest, db: Session = Depends(get_db),
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if db.get(Project, body.project_id) is None:
        raise VideoModuleError("NOT_FOUND", f"Project {body.project_id} not found.")
    if db.query(Scene).filter(Scene.project_id == body.project_id).count() == 0:
        raise VideoModuleError("MISSING_NARRATION_ANCHOR", "Project has no scenes; POST /scenes first.")
    opts = body.options.model_dump()
    opts["ai_provider"] = canonical_name(opts["ai_provider"] or get_settings().default_image_provider)
    if opts["generate_ai"]:
        ensure_requestable(opts["ai_provider"], opts.get("allow_placeholder_fallback"))
    if (opts["output_width"] is None) != (opts["output_height"] is None):
        raise VideoModuleError("VALIDATION_ERROR", "output_width and output_height must be given together.")
    if opts["min_shot_seconds"] and opts["max_shot_seconds"] and opts["min_shot_seconds"] > opts["max_shot_seconds"]:
        raise VideoModuleError("VALIDATION_ERROR", "min_shot_seconds must be <= max_shot_seconds.")
    payload = {"project_id": body.project_id, "scene_ids": body.scene_ids, "options": opts}
    job, reused = create_job(db, "build_visual_plan", payload, project_id=body.project_id,
                             idempotency_key=sanitize_text(idempotency_key, 128))
    return job_out(job, reused=reused)


@router.get("/visual-plans")
def list_plans(project_id: str, db: Session = Depends(get_db)):
    rows = db.query(VisualPlan).filter(VisualPlan.project_id == project_id).order_by(VisualPlan.created_at.desc()).all()
    return [plan_out(p, include_timeline=False) for p in rows]


def _plan(db: Session, plan_id: str) -> VisualPlan:
    p = db.get(VisualPlan, plan_id)
    if p is None:
        raise VideoModuleError("NOT_FOUND", f"Visual plan {plan_id} not found.")
    return p


@router.get("/visual-plans/{visual_plan_id}")
def get_plan(visual_plan_id: str, include_timeline: bool = True, db: Session = Depends(get_db)):
    return plan_out(_plan(db, visual_plan_id), include_timeline=include_timeline)


@router.get("/visual-plans/{visual_plan_id}/timeline")
def get_timeline(visual_plan_id: str, db: Session = Depends(get_db)):
    p = _plan(db, visual_plan_id)
    if p.status != "BUILT":
        raise VideoModuleError("CONFLICT", f"Plan is {p.status}; no timeline available.")
    return p.timeline_json


@router.get("/visual-plans/{visual_plan_id}/contact-sheet")
def contact_sheet(visual_plan_id: str, db: Session = Depends(get_db)):
    p = _plan(db, visual_plan_id)
    if p.status != "BUILT":
        raise VideoModuleError("CONFLICT", f"Plan is {p.status}; no shots to preview.")
    tl = p.timeline_json
    out = get_settings().plans_dir / f"{visual_plan_id}_contact_sheet.png"
    render_contact_sheet(tl["shots"], {k: {**v, "local_path": (v["uri"] or "").replace("storage://", "") or None}
                                       for k, v in tl["assets"].items()}, out)
    return FileResponse(out, media_type="image/png")


@router.get("/schemas/visual-timeline")
def timeline_schema():
    return TIMELINE_SCHEMA


@router.get("/shots/{visual_plan_id}")
def list_shots(visual_plan_id: str, db: Session = Depends(get_db)):
    _plan(db, visual_plan_id)
    rows = db.query(Shot).filter(Shot.visual_plan_id == visual_plan_id).order_by(Shot.order_index).all()
    return {"visual_plan_id": visual_plan_id, "shot_count": len(rows), "shots": [shot_out(s) for s in rows]}


@router.get("/shots/{visual_plan_id}/{shot_id}/preview")
def shot_preview(visual_plan_id: str, shot_id: str, t: float = 0.0, db: Session = Depends(get_db)):
    p = _plan(db, visual_plan_id)
    shot = next((s for s in (p.timeline_json or {}).get("shots", []) if s["shot_id"] == shot_id), None)
    if shot is None:
        raise VideoModuleError("NOT_FOUND", "Shot not found in this plan.")
    t = min(1.0, max(0.0, t))
    a = p.timeline_json["assets"].get(shot["asset_id"]) if shot["asset_id"] else None
    asset = {**a, "local_path": (a["uri"] or "").replace("storage://", "") or None} if a else None
    img = render_shot_frame(shot, asset, t)
    out = get_settings().plans_dir / "previews" / f"{shot_id}_{int(t * 100):03d}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG")
    return FileResponse(out, media_type="image/png")
