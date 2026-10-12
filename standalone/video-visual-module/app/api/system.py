from __future__ import annotations

import platform
import sys

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.config import MODULE_NAME, MODULE_VERSION, get_settings
from app.db.models import Asset, Job, Project, VisualPlan
from app.generation.registry import list_providers
from app.jobs.queue import get_job_queue
from app.logging_conf import tail_log
from app.pipeline.media_probe import capabilities as media_capabilities

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "service": MODULE_NAME, "version": MODULE_VERSION,
            "database": "ok" if db_ok else "unavailable"}


@router.get("/diagnostics", dependencies=[Depends(require_api_key)])
def diagnostics(db: Session = Depends(get_db)):
    s = get_settings()
    jobs_by_status = dict(db.query(Job.status, func.count()).group_by(Job.status).all())
    return {
        "service": MODULE_NAME, "version": MODULE_VERSION,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "storage_root": str(s.storage_root),
        "storage_dirs": {d.name: d.exists() for d in s.all_storage_dirs()},
        "database": s.db_url.split("///")[0] + "///<module storage>" if s.db_url.startswith("sqlite") else "configured",
        "job_worker_threads": s.job_worker_threads, "jobs_inflight": get_job_queue().inflight(),
        "jobs_by_status": jobs_by_status,
        "counts": {"projects": db.query(func.count(Project.project_id)).scalar(),
                   "assets": db.query(func.count(Asset.asset_id)).scalar(),
                   "visual_plans": db.query(func.count(VisualPlan.visual_plan_id)).scalar()},
        "providers": list_providers(),
        "previews": media_capabilities(),
        "limits": {"max_upload_bytes": s.max_upload_bytes, "max_image_pixels": s.max_image_pixels,
                   "default_max_ai_images_per_plan": s.default_max_ai_images_per_plan,
                   "max_shot_seconds": s.max_shot_seconds, "min_shot_seconds": s.min_shot_seconds},
        "api_key_required": bool(s.api_key),
    }


@router.get("/capabilities", dependencies=[Depends(require_api_key)])
def capabilities():
    """What this installation can actually do right now (no network calls, nothing generated)."""
    return {"version": MODULE_VERSION, "previews": media_capabilities(), "providers": list_providers()}


@router.get("/diagnostics/logs", dependencies=[Depends(require_api_key)])
def logs(lines: int = 200):
    return {"lines": tail_log(lines)}
