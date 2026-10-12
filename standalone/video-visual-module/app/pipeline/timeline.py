"""
Visual Timeline / renderer contract (spec §29).

Renderer-neutral JSON. No FFmpeg or engine specifics: shots carry normalized
crop rectangles, declarative motion (type + scales/offsets/regions), overlays
and the asset reference (module-relative URI + API URL). The renderer decides
how to realise frames.

`TIMELINE_SCHEMA` is published at GET /schemas/visual-timeline and
`validate_timeline` is used to verify every plan before its job is marked
SUCCEEDED.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import Asset, Shot
from app.pipeline.import_assets import evidence_eligible, provenance_class, rights_label
from app.pipeline.motion import BASE_MOTIONS

TIMELINE_SCHEMA_ID = "video-visual-timeline"
# 1.1 (Phase 2): PLACEHOLDER editorial label + PLACEHOLDER_IMAGE visual type, per-asset provenance
# (generation_kind, provenance_class, evidence_eligible, provider/model/request ids, fallback), `output`
# (dimensions / frame rate when defined), `timing`, optional per-shot frame numbers, contiguity validation.
# All v1.0 fields are unchanged. Consumers that hard-code version == "1.0" must accept "1.1".
TIMELINE_VERSION = "1.1"
EDITORIAL_LABELS = ["FACTUAL_EVIDENCE", "ILLUSTRATIVE_VISUAL", "AI_GENERATED", "PLACEHOLDER", "UNKNOWN"]
CONTIGUITY_TOLERANCE_S = 0.01
NEVER_EVIDENCE_CLASSES = {"AI_GENERATED", "PLACEHOLDER", "TEST_FIXTURE", "UNKNOWN_ORIGIN"}

_RECT = {"type": "object", "required": ["x", "y", "w", "h"],
         "properties": {k: {"type": "number", "minimum": 0, "maximum": 1} for k in ("x", "y", "w", "h")}}
TIMELINE_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"{TIMELINE_SCHEMA_ID}/{TIMELINE_VERSION}",
    "title": "Video Visual Module - renderer-neutral visual timeline",
    "type": "object",
    "required": ["schema", "version", "visual_plan_id", "project_id", "duration", "shots", "assets"],
    "properties": {
        "schema": {"const": TIMELINE_SCHEMA_ID},
        "version": {"const": TIMELINE_VERSION},
        "visual_plan_id": {"type": "string"},
        "project_id": {"type": "string"},
        "generated_at": {"type": "string"},
        "duration": {"type": "number", "minimum": 0},
        "shots": {"type": "array", "items": {
            "type": "object",
            "required": ["shot_id", "scene_id", "order", "start", "end", "duration", "asset_id", "visual_type",
                         "source_type", "editorial_label", "status", "motion", "crop", "transition",
                         "narration_anchor"],
            "properties": {
                "shot_id": {"type": "string"}, "scene_id": {"type": "string"}, "order": {"type": "integer"},
                "start": {"type": "number"}, "end": {"type": "number"}, "duration": {"type": "number"},
                "asset_id": {"type": ["string", "null"]},
                "status": {"enum": ["READY", "GAP"]},
                "gap_code": {"type": ["string", "null"]},
                "editorial_label": {"enum": EDITORIAL_LABELS},
                "motion": {"type": "object", "required": ["type"], "properties": {"type": {"enum": BASE_MOTIONS}}},
                "crop": _RECT,
                "transition": {"type": "string"},
                "overlays": {"type": "array"},
                "narration_anchor": {"type": "object", "required": ["segment_ids", "text", "start", "end"]},
                "frame_start": {"type": ["integer", "null"]},
                "frame_end": {"type": ["integer", "null"]},
            },
        }},
        "assets": {"type": "object", "additionalProperties": {
            "type": "object",
            "required": ["asset_id", "asset_type", "source_type", "editorial_label", "generation_kind",
                         "provenance_class", "evidence_eligible", "uri", "mime_type"],
        }},
        "attributions": {"type": "array"},
        "output": {"type": "object", "properties": {
            "width": {"type": ["integer", "null"]}, "height": {"type": ["integer", "null"]},
            "frame_rate": {"type": ["number", "null"]}, "defined": {"type": "boolean"}}},
        "timing": {"type": "object"},
        "summary": {"type": "object"},
    },
}


def asset_ref(a: Asset) -> dict:
    return {
        "asset_id": a.asset_id,
        "asset_type": a.asset_type,
        "source_type": a.source_type,
        "editorial_label": a.editorial_category,
        "is_mock": bool(a.is_mock),
        "uri": f"storage://{a.local_path}" if a.local_path else None,
        "url": f"/assets/{a.asset_id}/file" if a.local_path else None,
        "mime_type": a.mime_type,
        "width": a.width, "height": a.height, "duration": a.duration,
        "title": a.title,
        "attribution": a.attribution,
        "licence": a.licence,
        "rights_status": a.rights_status,
        "rights_label": rights_label(a.rights_status, a.licence),
        "source_url": a.source_url,
        "checksum_sha256": a.checksum_sha256,
        # ---- v1.1 provenance
        "generation_kind": a.generation_kind or "NOT_GENERATED",
        "provenance_class": provenance_class(a),
        "evidence_eligible": evidence_eligible(a),
        "is_fallback": bool(a.is_fallback),
        "fallback_from": a.fallback_from,
        "provider": a.provider, "model": a.model, "provider_request_id": a.provider_request_id,
        "creator": a.creator,
        "source_identity": a.source_identity, "licence_url": a.licence_url,
        "attribution_required": bool(a.attribution_required),
        "acquired_at": a.acquired_at.isoformat() if a.acquired_at else None,
    }


def shot_to_contract(s: Shot) -> dict:
    return {
        "shot_id": s.shot_id,
        "scene_id": s.scene_id,
        "order": s.order_index,
        "start": s.start_time,
        "end": s.end_time,
        "duration": s.duration,
        "asset_id": s.asset_id,
        "visual_type": s.visual_type,
        "source_type": s.source_type,
        "editorial_label": s.editorial_label,
        "intent": s.intent,
        "status": s.status,
        "gap_code": s.gap_code,
        "narration_anchor": s.narration_anchor,
        "change_reason": s.change_reason,
        "framing": s.framing,
        "reuse_index": s.reuse_index,
        "motion": s.motion,
        "crop": s.crop,
        "transition": s.transition,
        "overlays": s.overlays or [],
        "attribution": s.attribution,
        "editorial_reason": s.editorial_reason,
    }


def build_renderer_timeline(plan_id: str, project_id: str, shots: list[Shot], assets: dict[str, Asset],
                            stats: dict | None = None, options: dict | None = None) -> dict:
    opts = options or {}
    ordered = sorted(shots, key=lambda s: (s.start_time, s.order_index))
    used = {s.asset_id for s in ordered if s.asset_id}
    attributions = []
    for aid in sorted(used):
        a = assets[aid]
        attributions.append({"asset_id": aid, "title": a.title, "attribution": a.attribution, "creator": a.creator,
                             "licence": a.licence, "rights_label": rights_label(a.rights_status, a.licence),
                             "source_url": a.source_url, "editorial_label": a.editorial_category,
                             "attribution_required": bool(a.attribution_required),
                             "provenance_class": provenance_class(a)})
    fps = opts.get("frame_rate")
    width, height = opts.get("output_width"), opts.get("output_height")
    shot_dicts = [shot_to_contract(s) for s in ordered]
    for sd in shot_dicts:
        sd["frame_start"] = int(round(sd["start"] * fps)) if fps else None
        sd["frame_end"] = int(round(sd["end"] * fps)) if fps else None
    st = stats or {}
    sources = st.get("timing_sources") or sorted({(sd["narration_anchor"] or {}).get("timing_source", "ESTIMATED")
                                                  for sd in shot_dicts})
    return {
        "schema": TIMELINE_SCHEMA_ID,
        "version": TIMELINE_VERSION,
        "visual_plan_id": plan_id,
        "project_id": project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": ordered[0].start_time if ordered else 0.0,
        "duration": round(ordered[-1].end_time - ordered[0].start_time, 3) if ordered else 0.0,
        "output": {"width": width, "height": height, "frame_rate": fps,
                   "defined": bool(width and height and fps),
                   "note": None if (width and height and fps) else
                   "not defined by the plan; the renderer chooses output size / frame rate (crops are normalised)"},
        "timing": {"sources": sources,
                   "timing_source": sources[0] if len(sources) == 1 else "MIXED",
                   "estimated": "ESTIMATED" in sources,
                   "gaps_closed": st.get("timing_gaps_closed", 0),
                   "gap_seconds_closed": st.get("timing_gap_seconds_closed", 0.0),
                   "units": "seconds on the narration timeline"},
        "shots": shot_dicts,
        "assets": {aid: asset_ref(assets[aid]) for aid in sorted(used)},
        "attributions": attributions,
        "summary": st,
    }


def validate_timeline(tl: dict) -> list[str]:
    """Structural + semantic validation of the renderer contract. Returns a list of problems (empty = valid)."""
    errs: list[str] = []
    for key in TIMELINE_SCHEMA["required"]:
        if key not in tl:
            errs.append(f"missing top-level '{key}'")
    if errs:
        return errs
    if tl["schema"] != TIMELINE_SCHEMA_ID or tl["version"] not in (TIMELINE_VERSION, "1.0"):
        errs.append("schema/version mismatch")
    shots = tl["shots"]
    if not shots:
        errs.append("timeline has no shots")
    req = TIMELINE_SCHEMA["properties"]["shots"]["items"]["required"]
    prev_end = None
    for i, s in enumerate(shots):
        missing = [k for k in req if k not in s]
        if missing:
            errs.append(f"shot {i} missing {missing}")
            continue
        if not (s["end"] > s["start"]) or abs((s["end"] - s["start"]) - s["duration"]) > 0.01:
            errs.append(f"shot {i} has inconsistent timing")
        if prev_end is not None and s["start"] < prev_end - CONTIGUITY_TOLERANCE_S:
            errs.append(f"shot {i} overlaps previous shot")
        if prev_end is not None and s["start"] > prev_end + CONTIGUITY_TOLERANCE_S:
            errs.append(f"shot {i} leaves an unplanned gap after the previous shot")
        if s.get("order") != i:
            errs.append(f"shot {i} order index {s.get('order')} is not sequential")
        prev_end = s["end"]
        if s["editorial_label"] not in EDITORIAL_LABELS:
            errs.append(f"shot {i} has unknown editorial_label")
        if s["motion"].get("type") not in BASE_MOTIONS:
            errs.append(f"shot {i} has unknown motion type")
        c = s["crop"]
        if not all(isinstance(c.get(k), (int, float)) for k in ("x", "y", "w", "h")) or \
                c["x"] < -1e-6 or c["y"] < -1e-6 or c["x"] + c["w"] > 1.0001 or c["y"] + c["h"] > 1.0001:
            errs.append(f"shot {i} crop outside 0..1")
        if s["status"] == "READY":
            if not s["asset_id"] or s["asset_id"] not in tl["assets"]:
                errs.append(f"shot {i} READY but asset reference missing")
            else:
                ref = tl["assets"][s["asset_id"]]
                pclass = ref.get("provenance_class")
                labels = [o.get("text", "") for o in s.get("overlays") or [] if o.get("type") == "editorial_label"]
                if ref["source_type"] == "AI_GENERATED" and s["editorial_label"] != "AI_GENERATED":
                    errs.append(f"shot {i} AI asset not labelled AI_GENERATED")
                if ref["source_type"] == "AI_GENERATED" and not any("AI-GENERATED" in t for t in labels):
                    errs.append(f"shot {i} AI asset lacks the required on-screen AI label")
                if pclass == "PLACEHOLDER" or ref.get("asset_type") == "PLACEHOLDER_IMAGE":
                    if s["editorial_label"] != "PLACEHOLDER":
                        errs.append(f"shot {i} placeholder asset not labelled PLACEHOLDER")
                    if not any("PLACEHOLDER" in t for t in labels):
                        errs.append(f"shot {i} placeholder asset lacks the required on-screen PLACEHOLDER label")
                if s["editorial_label"] == "FACTUAL_EVIDENCE" and pclass in NEVER_EVIDENCE_CLASSES:
                    errs.append(f"shot {i} presents a non-original ({pclass}) asset as evidence")
                if ref.get("rights_status") == "RESTRICTED":
                    errs.append(f"shot {i} uses a RESTRICTED asset")
        elif s["status"] == "GAP":
            if s["asset_id"] is not None or not s.get("gap_code"):
                errs.append(f"shot {i} GAP must have null asset and a gap_code")
        else:
            errs.append(f"shot {i} has unknown status")
        if s["source_type"] == "AI_GENERATED" and s["editorial_label"] == "FACTUAL_EVIDENCE":
            errs.append(f"shot {i} presents AI imagery as evidence")
    return errs
