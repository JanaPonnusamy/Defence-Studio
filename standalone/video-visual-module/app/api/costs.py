"""Cost accounting report (spec §24), Phase 2 honest-cost version.

Rules:
  * only KNOWN costs (computed from documented pricing x provider usage) are summed
  * UNKNOWN costs are never summed as zero: they are counted and reported, and every
    total that could be affected says so (`totals_complete: false`, `*_is_lower_bound`)
  * placeholder and test-fixture generations are NOT_APPLICABLE and never count as AI images
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serializers import _iso
from app.db.models import Asset, CostRecord, GenerationRecord, Project, Scene, VisualPlan
from app.errors import VideoModuleError
from app.generation.openai_pricing import PRICING_RETRIEVED_ON
from app.pipeline.import_assets import provenance_class

router = APIRouter(tags=["costs"])
COST_REPORT_VERSION = "2.0"


@router.get("/costs/{project_id}")
def project_costs(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        raise VideoModuleError("NOT_FOUND", f"Project {project_id} not found.")
    records = db.query(CostRecord).filter(CostRecord.project_id == project_id).order_by(CostRecord.created_at).all()
    assets = db.query(Asset).filter(Asset.project_id == project_id).all()
    gens = db.query(GenerationRecord).filter(GenerationRecord.project_id == project_id).all()
    failed = [g for g in gens if g.status == "FAILED"]

    real_ai = [r for r in records if r.generation_kind == "AI_GENERATED" and not r.is_mock]
    placeholder = [r for r in records if r.generation_kind == "PLACEHOLDER"]
    fixture = [r for r in records if r.generation_kind == "TEST_FIXTURE" or r.is_mock]
    known = [r for r in real_ai if r.cost_status == "KNOWN" and r.estimated_cost is not None]
    unknown = [r for r in real_ai if r.cost_status != "KNOWN" or r.estimated_cost is None]
    known_cost = round(sum(r.estimated_cost for r in known), 6)
    complete = not unknown

    classes = defaultdict(int)
    for a in assets:
        classes[provenance_class(a)] += 1
    ai_real_assets = [a for a in assets if provenance_class(a) == "AI_GENERATED"]
    source_assets = [a for a in assets if provenance_class(a) in ("ORIGINAL_SOURCE", "SYSTEM_GRAPHIC")]

    plan = (db.query(VisualPlan).filter(VisualPlan.project_id == project_id, VisualPlan.status == "BUILT")
            .order_by(VisualPlan.created_at.desc()).first())
    if plan is not None:
        seconds, basis = float((plan.stats or {}).get("total_duration_s") or 0.0), f"latest built plan {plan.visual_plan_id}"
    else:
        scenes = db.query(Scene).filter(Scene.project_id == project_id).all()
        seconds = sum(s.end_time - s.start_time for s in scenes)
        basis = "sum of scene durations (no built plan yet)"
    minutes = seconds / 60.0

    by_provider: dict = defaultdict(lambda: {"count": 0, "known_cost": 0.0, "unknown_cost_count": 0,
                                             "not_applicable_count": 0, "is_mock": False, "generation_kind": None,
                                             "models": set()})
    for r in records:
        bp = by_provider[r.provider]
        bp["count"] += 1
        bp["is_mock"] = bool(r.is_mock)
        bp["generation_kind"] = r.generation_kind
        bp["models"].add(r.model)
        if r.cost_status == "KNOWN" and r.estimated_cost is not None:
            bp["known_cost"] = round(bp["known_cost"] + r.estimated_cost, 6)
        elif r.cost_status == "NOT_APPLICABLE":
            bp["not_applicable_count"] += 1
        else:
            bp["unknown_cost_count"] += 1

    per_image = round(known_cost / len(known), 6) if known else None
    return {
        "project_id": project_id,
        "cost_report_version": COST_REPORT_VERSION,
        "currency": "USD",
        "cost_basis": ("KNOWN costs only: documented provider pricing x usage returned by the provider "
                       f"(OpenAI pricing table retrieved {PRICING_RETRIEVED_ON}; see docs/PROVIDER_SETUP.md). "
                       "UNKNOWN costs are counted, never summed as zero. Placeholder and test-fixture images have no "
                       "cost (NOT_APPLICABLE) and are not AI images."),
        "totals_complete": complete,
        "unknown_cost_count": len(unknown),
        "known_cost_count": len(known),
        "not_applicable_count": len(placeholder) + len(fixture),
        # spec §24 fields (honest semantics)
        "ai_image_count": len(ai_real_assets),          # REAL AI images only
        "ai_generation_cost": known_cost,               # sum of KNOWN costs
        "ai_generation_cost_is_lower_bound": not complete,
        "ai_generation_cost_known": known_cost,
        "placeholder_image_count": len(placeholder),
        "fallback_placeholder_count": len([r for r in placeholder if r.is_fallback]),
        "mock_generation_count": len(fixture),
        "mock_ai_image_count": len([a for a in assets if provenance_class(a) == "TEST_FIXTURE"]),
        "failed_generation_count": len(failed),
        # requests that may have been processed/billed but returned no result (e.g. timeout); their cost is
        # UNKNOWN and included in unknown_cost_count
        "uncertain_outcome_count": len([g for g in failed if g.provider_state == "UNCERTAIN"]),
        "failed_by_provider_state": dict(_count(g.provider_state or "FAILED" for g in failed)),
        "source_asset_count": len(source_assets),
        "total_visual_asset_count": len(assets),
        "assets_by_provenance_class": dict(classes),
        "cost_per_generated_image": per_image,
        "cost_per_generated_image_basis": ("KNOWN-cost real AI images only" if known else
                                           "no real AI image with a known cost"),
        "finished_minutes": round(minutes, 3),
        "finished_minutes_basis": basis,
        "cost_per_finished_minute": round(known_cost / minutes, 6) if minutes > 0 and real_ai else None,
        "cost_per_finished_minute_is_lower_bound": not complete,
        "budget_usd": project.ai_image_budget_usd,
        "budget_remaining_usd": round(project.ai_image_budget_usd - known_cost, 6),
        "budget_remaining_is_upper_bound": not complete,
        "by_provider": [{"provider": k, **{**v, "models": sorted(m for m in v["models"] if m)}}
                        for k, v in by_provider.items()],
        "records": [
            {"cost_record_id": r.cost_record_id, "provider": r.provider, "is_mock": bool(r.is_mock), "model": r.model,
             "generation_kind": r.generation_kind, "is_real_ai": r.generation_kind == "AI_GENERATED" and not r.is_mock,
             "is_fallback": bool(r.is_fallback),
             "asset_id": r.asset_id, "quality": r.quality, "resolution": r.resolution,
             "cost_status": r.cost_status, "estimated_cost": r.estimated_cost, "actual_cost": r.estimated_cost,
             "pre_call_estimate_usd": r.pre_call_estimate_usd, "cost_basis": r.cost_basis, "usage": r.usage,
             "currency": r.currency, "request_id": r.request_id, "provider_request_id": r.provider_request_id,
             "created_at": _iso(r.created_at)}
            for r in records
        ],
    }


def _count(items):
    out: dict = defaultdict(int)
    for i in items:
        out[i] += 1
    return out
