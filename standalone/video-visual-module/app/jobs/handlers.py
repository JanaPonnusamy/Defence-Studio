"""Job handlers. Each returns a result id only after verifying real output."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import Job, Project, Scene, VisualPlan
from app.errors import VideoModuleError
from app.generation.base import ImageGenerationRequest
from app.generation.service import generate_image
from app.jobs.queue import JobCancelled, JobContext, register_handler
from app.pipeline.shot_planner import PlanOptions, plan_project
from app.pipeline.timeline import build_renderer_timeline, validate_timeline
from app.storage import to_rel, write_json_atomic


def _load_scenes(db, project_id: str, scene_ids: list[str] | None) -> list[Scene]:
    q = db.query(Scene).filter(Scene.project_id == project_id)
    scenes = q.all() if not scene_ids else [s for s in q.filter(Scene.scene_id.in_(scene_ids)).all()]
    if scene_ids:
        found = {s.scene_id for s in scenes}
        missing = [s for s in scene_ids if s not in found]
        if missing:
            raise VideoModuleError("NOT_FOUND", f"Scene(s) not found in project: {missing[:5]}")
    if not scenes:
        raise VideoModuleError("MISSING_NARRATION_ANCHOR", "Project has no scenes to plan; create scenes from narration first.")
    scenes.sort(key=lambda s: (s.start_time, s.order_index))
    for a, b in zip(scenes, scenes[1:]):
        if b.start_time < a.end_time - 1e-3:
            raise VideoModuleError("VALIDATION_ERROR",
                                   f"Scenes overlap in time ('{a.title}' ends {a.end_time}s after '{b.title}' starts {b.start_time}s).")
    return scenes


def handle_build_visual_plan(job_id: str, ctx: JobContext) -> str:
    settings = get_settings()
    db = SessionLocal()
    plan = None
    try:
        job = db.get(Job, job_id)
        payload = job.payload or {}
        project_id = payload["project_id"]
        if db.get(Project, project_id) is None:
            raise VideoModuleError("NOT_FOUND", f"Project {project_id} not found.")
        opts = PlanOptions(**(payload.get("options") or {}))
        scenes = _load_scenes(db, project_id, payload.get("scene_ids"))
        plan = VisualPlan(project_id=project_id, job_id=job_id, scene_ids=[s.scene_id for s in scenes],
                          status="BUILDING", options=payload.get("options") or {})
        db.add(plan)
        db.commit()
        ctx.update(progress=0.05, stage="planning")

        def gen(prompt: str, purpose: str, scene_id: str | None):
            asset, _ = generate_image(
                db, project_id=project_id, provider_name=opts.ai_provider, job_id=job_id, cancel_check=ctx.check_cancel,
                request=ImageGenerationRequest(prompt=prompt, purpose=purpose, scene_id=scene_id,
                                               quality=opts.ai_quality, resolution=opts.ai_resolution),
                allow_fallback=opts.allow_placeholder_fallback,
            )
            return asset

        out = plan_project(db, project_id, scenes, opts, generate=gen,
                           progress=lambda p, s: ctx.update(progress=0.05 + p * 0.8, stage=s))
        for s in out.shots:
            s.visual_plan_id = plan.visual_plan_id
            db.add(s)
        ctx.update(progress=0.88, stage="building renderer timeline")
        timeline = build_renderer_timeline(plan.visual_plan_id, project_id, out.shots, out.assets, out.stats,
                                           options=payload.get("options") or {})
        problems = validate_timeline(timeline)
        if problems:
            raise VideoModuleError("OUTPUT_VERIFICATION_FAILED", f"Timeline failed validation: {problems[:5]}")

        # persist + verify the artifact on disk before declaring success
        path = settings.plans_dir / f"{plan.visual_plan_id}.json"
        write_json_atomic(path, timeline)
        with open(path, encoding="utf-8") as fh:
            reread = json.load(fh)
        if len(reread.get("shots", [])) != len(out.shots) or validate_timeline(reread):
            raise VideoModuleError("OUTPUT_VERIFICATION_FAILED", "Timeline file on disk does not match the plan.")
        write_json_atomic(settings.attribution_dir / f"{plan.visual_plan_id}.json",
                          {"visual_plan_id": plan.visual_plan_id, "attributions": timeline["attributions"]})
        for sc in scenes:
            write_json_atomic(settings.scenes_dir / f"{sc.scene_id}.json", {
                "scene_id": sc.scene_id, "title": sc.title, "start_time": sc.start_time, "end_time": sc.end_time,
                "narration_segments": sc.narration_segments, "visual_intents": sc.visual_intents,
                "asset_ids": sc.asset_ids, "shot_ids": sc.shot_ids, "transition": sc.transition,
                "importance": sc.importance, "visual_plan_id": plan.visual_plan_id,
            })
        ctx.check_cancel()
        plan.timeline_json = timeline
        plan.timeline_path = to_rel(path)
        plan.stats = out.stats
        plan.warnings = out.warnings
        plan.status = "BUILT"
        plan.completed_at = datetime.now(timezone.utc)
        db.commit()
        return plan.visual_plan_id
    except BaseException as exc:
        db.rollback()
        if plan is not None:
            p = db.get(VisualPlan, plan.visual_plan_id)
            if p is not None:
                cancelled = isinstance(exc, JobCancelled)
                p.status = "CANCELLED" if cancelled else "FAILED"
                p.error_code = "JOB_CANCELLED" if cancelled else getattr(exc, "code", "INTERNAL_ERROR")
                p.error_message = "Cancelled." if cancelled else getattr(exc, "message", type(exc).__name__)
                p.completed_at = datetime.now(timezone.utc)
                db.commit()
        raise
    finally:
        db.close()


def handle_generate_image(job_id: str, ctx: JobContext) -> str:
    db = SessionLocal()
    try:
        payload = db.get(Job, job_id).payload
        ctx.update(progress=0.1, stage="generating")
        asset, _ = generate_image(
            db, project_id=payload["project_id"], provider_name=payload["provider"], job_id=job_id,
            cancel_check=ctx.check_cancel,
            request=ImageGenerationRequest(
                prompt=payload["prompt"], aspect_ratio=payload.get("aspect_ratio", "16:9"),
                resolution=payload.get("resolution", "1280x720"), quality=payload.get("quality", "standard"),
                purpose=payload.get("purpose"), scene_id=payload.get("scene_id"),
                reference_assets=payload.get("reference_assets") or [],
            ),
            allow_fallback=payload.get("allow_placeholder_fallback"),
        )
        ctx.update(progress=0.95, stage="verified output")
        return asset.asset_id
    finally:
        db.close()


register_handler("build_visual_plan", handle_build_visual_plan)
register_handler("generate_image", handle_generate_image)
