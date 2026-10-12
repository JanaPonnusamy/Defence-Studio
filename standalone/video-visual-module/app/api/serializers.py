"""Response shaping shared by routers (stable public field names, spec §28)."""
from __future__ import annotations

from app.db.models import Asset, GenerationRecord, Job, Project, Scene, Shot, VisualPlan
from app.pipeline.import_assets import evidence_eligible, provenance_class, rights_label, rights_usable
from app.pipeline.timeline import shot_to_contract


def _iso(dt):
    if not dt:
        return None
    if dt.tzinfo is None:  # SQLite drops tzinfo; values are always stored as UTC
        return dt.isoformat() + "Z"
    return dt.isoformat()


def project_out(p: Project) -> dict:
    return {"project_id": p.project_id, "name": p.name, "description": p.description,
            "ai_image_budget_usd": p.ai_image_budget_usd, "created_at": _iso(p.created_at)}


def asset_out(a: Asset, *, deduplicated: bool | None = None) -> dict:
    out = {
        "asset_id": a.asset_id, "project_id": a.project_id, "asset_type": a.asset_type,
        "source_type": a.source_type, "source_url": a.source_url, "title": a.title, "description": a.description,
        "creator": a.creator, "licence": a.licence, "attribution": a.attribution,
        "rights_status": a.rights_status, "rights_label": rights_label(a.rights_status, a.licence),
        "editorial_category": a.editorial_category, "is_mock": bool(a.is_mock),
        "provider": a.provider, "model": a.model, "generation_cost": a.generation_cost,
        "checksum_sha256": a.checksum_sha256, "local_path": a.local_path, "mime_type": a.mime_type,
        "file_size_bytes": a.file_size_bytes, "width": a.width, "height": a.height, "duration": a.duration,
        "acquired_at": _iso(a.acquired_at), "reuse_count": a.reuse_count,
        "extra_metadata": a.extra_metadata or {},
        "file_url": f"/assets/{a.asset_id}/file" if a.local_path else None,
        # ---- Phase 2 (API 1.1): classification, cost status, provenance
        "generation_kind": a.generation_kind or "NOT_GENERATED",
        "provenance_class": provenance_class(a),
        "is_real_ai": (a.generation_kind == "AI_GENERATED" and not a.is_mock),
        "is_placeholder": provenance_class(a) == "PLACEHOLDER",
        "evidence_eligible": evidence_eligible(a),
        "rights_usable": rights_usable(a),
        "cost_status": a.cost_status,
        "is_fallback": bool(a.is_fallback), "fallback_from": a.fallback_from, "fallback_reason": a.fallback_reason,
        "provider_request_id": a.provider_request_id,
        "source_identity": a.source_identity, "licence_url": a.licence_url, "usage_rights": a.usage_rights,
        "attribution_required": bool(a.attribution_required), "source_retrieved_at": _iso(a.source_retrieved_at),
        "perceptual_hash": a.perceptual_hash, "duplicate_of": a.duplicate_of,
    }
    if deduplicated is not None:
        out["deduplicated"] = deduplicated
    return out


def generation_out(g: GenerationRecord) -> dict:
    return {
        "generation_id": g.generation_id, "project_id": g.project_id, "job_id": g.job_id, "asset_id": g.asset_id,
        "provider": g.provider, "model": g.model, "is_mock": bool(g.is_mock),
        "generation_kind": g.generation_kind, "is_real_ai": g.generation_kind == "AI_GENERATED" and not g.is_mock,
        "status": g.status, "provider_state": g.provider_state, "attempts": g.attempts,
        "error_code": g.error_code, "error_message": g.error_message,
        "request_id": g.request_id, "provider_request_id": g.provider_request_id,
        "prompt": g.prompt, "purpose": g.purpose, "quality": g.quality, "resolution": g.resolution,
        "aspect_ratio": g.aspect_ratio, "scene_id": g.scene_id,
        "cost_status": g.cost_status, "estimated_cost": g.estimated_cost, "pre_call_estimate_usd": g.pre_call_estimate_usd,
        "currency": g.currency, "generation_time_seconds": g.generation_time_seconds,
        "is_fallback": bool(g.is_fallback), "fallback_from": g.fallback_from, "fallback_reason": g.fallback_reason,
        "provider_metadata": g.provider_metadata or {}, "created_at": _iso(g.created_at),
    }


def job_out(j: Job, *, reused: bool | None = None) -> dict:
    out = {
        "job_id": j.job_id, "job_type": j.job_type, "project_id": j.project_id, "status": j.status,
        "progress": j.progress, "stage": j.stage, "created_at": _iso(j.created_at),
        "started_at": _iso(j.started_at), "completed_at": _iso(j.completed_at),
        "error_code": j.error_code, "error_message": j.error_message, "result_id": j.result_id,
        "retry_of": j.retry_of, "cancel_requested": bool(j.cancel_requested),
        "idempotency_key": j.idempotency_key,
    }
    if reused is not None:
        out["idempotent_replay"] = reused
    return out


def scene_out(s: Scene) -> dict:
    return {
        "scene_id": s.scene_id, "project_id": s.project_id, "order_index": s.order_index, "title": s.title,
        "start_time": s.start_time, "end_time": s.end_time, "duration": round(s.end_time - s.start_time, 3),
        "narration_text": s.narration_text, "narration_segments": s.narration_segments,
        "narration_segment_ids": s.narration_segment_ids, "timing_source": s.timing_source,
        "visual_intents": s.visual_intents, "asset_ids": s.asset_ids, "shot_ids": s.shot_ids,
        "transition": s.transition, "importance": s.importance, "created_at": _iso(s.created_at),
    }


def plan_out(p: VisualPlan, *, include_timeline: bool = True) -> dict:
    out = {
        "visual_plan_id": p.visual_plan_id, "project_id": p.project_id, "job_id": p.job_id,
        "scene_ids": p.scene_ids, "status": p.status, "options": p.options, "stats": p.stats,
        "warnings": p.warnings, "timeline_path": p.timeline_path, "error_code": p.error_code,
        "error_message": p.error_message, "created_at": _iso(p.created_at), "completed_at": _iso(p.completed_at),
    }
    if include_timeline:
        out["timeline"] = p.timeline_json
    return out


def shot_out(s: Shot) -> dict:
    return {"visual_plan_id": s.visual_plan_id, **shot_to_contract(s)}
