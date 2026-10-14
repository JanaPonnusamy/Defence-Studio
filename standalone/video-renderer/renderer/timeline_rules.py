"""Consumer-side validation of the Video Visual timeline ("video-visual-timeline" v1.0/v1.1).

Re-implements the producer's published rules (VVM docs/API_CONTRACTS.md, `validate_timeline`)
so this renderer never imports the producer's code. Keep in step with the contract document,
not with the producer's source tree.
"""
from __future__ import annotations

TIMELINE_SCHEMA_ID = "video-visual-timeline"
ACCEPTED_VERSIONS = ("1.0", "1.1")
BASE_MOTIONS = ["STATIC", "SLOW_ZOOM_IN", "SLOW_ZOOM_OUT", "PAN_LEFT", "PAN_RIGHT",
                "PAN_UP", "PAN_DOWN", "CROP_FOCUS", "PARALLAX", "HIGHLIGHT_REGION"]
EDITORIAL_LABELS = ["FACTUAL_EVIDENCE", "ILLUSTRATIVE_VISUAL", "AI_GENERATED", "PLACEHOLDER", "UNKNOWN"]
NEVER_EVIDENCE_CLASSES = {"AI_GENERATED", "PLACEHOLDER", "TEST_FIXTURE", "UNKNOWN_ORIGIN"}
CONTIGUITY_TOLERANCE_S = 0.01

TOP_REQUIRED = ["schema", "version", "visual_plan_id", "project_id", "duration", "shots", "assets"]
SHOT_REQUIRED = ["shot_id", "scene_id", "order", "start", "end", "duration", "asset_id", "visual_type",
                 "source_type", "editorial_label", "status", "motion", "crop", "transition", "narration_anchor"]


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_timeline(tl: dict) -> list[str]:
    """Structural + semantic validation. Returns problems (empty list = valid)."""
    if not isinstance(tl, dict):
        return ["timeline is not a JSON object"]
    errs = [f"missing top-level '{k}'" for k in TOP_REQUIRED if k not in tl]
    if errs:
        return errs
    if tl["schema"] != TIMELINE_SCHEMA_ID or tl["version"] not in ACCEPTED_VERSIONS:
        errs.append("schema/version mismatch")
    shots, assets = tl["shots"], tl["assets"]
    if not isinstance(shots, list) or not isinstance(assets, dict):
        return errs + ["'shots' must be an array and 'assets' an object"]
    if not shots:
        errs.append("timeline has no shots")
    prev_end = None
    for i, s in enumerate(shots):
        if not isinstance(s, dict):
            errs.append(f"shot {i} is not an object")
            continue
        missing = [k for k in SHOT_REQUIRED if k not in s]
        if missing:
            errs.append(f"shot {i} missing {missing}")
            continue
        if not all(_num(s[k]) for k in ("start", "end", "duration")):
            errs.append(f"shot {i} has non-numeric timing")
            continue
        if not (s["end"] > s["start"]) or abs((s["end"] - s["start"]) - s["duration"]) > 0.01:
            errs.append(f"shot {i} has inconsistent timing")
        if s["start"] < 0:
            errs.append(f"shot {i} starts before 0")
        if prev_end is not None and s["start"] < prev_end - CONTIGUITY_TOLERANCE_S:
            errs.append(f"shot {i} overlaps previous shot")
        if prev_end is not None and s["start"] > prev_end + CONTIGUITY_TOLERANCE_S:
            errs.append(f"shot {i} leaves an unplanned gap after the previous shot")
        if s.get("order") != i:
            errs.append(f"shot {i} order index {s.get('order')} is not sequential")
        prev_end = s["end"]
        if s["editorial_label"] not in EDITORIAL_LABELS:
            errs.append(f"shot {i} has unknown editorial_label")
        if not isinstance(s["motion"], dict) or s["motion"].get("type") not in BASE_MOTIONS:
            errs.append(f"shot {i} has unknown motion type")
        c = s["crop"]
        if not isinstance(c, dict) or not all(_num(c.get(k)) for k in ("x", "y", "w", "h")) or \
                c["w"] <= 0 or c["h"] <= 0 or \
                c["x"] < -1e-6 or c["y"] < -1e-6 or c["x"] + c["w"] > 1.0001 or c["y"] + c["h"] > 1.0001:
            errs.append(f"shot {i} crop outside 0..1")
        if not isinstance(s["transition"], str):
            errs.append(f"shot {i} transition is not a string")
        overlays = s.get("overlays") or []
        if not isinstance(overlays, list) or not all(isinstance(o, dict) for o in overlays):
            errs.append(f"shot {i} overlays must be an array of objects")
            overlays = []
        if s["status"] == "READY":
            if not s["asset_id"] or s["asset_id"] not in assets:
                errs.append(f"shot {i} READY but asset reference missing")
            else:
                ref = assets[s["asset_id"]] or {}
                pclass = ref.get("provenance_class")
                labels = [str(o.get("text", "")) for o in overlays if o.get("type") == "editorial_label"]
                if ref.get("source_type") == "AI_GENERATED" and s["editorial_label"] != "AI_GENERATED":
                    errs.append(f"shot {i} AI asset not labelled AI_GENERATED")
                if ref.get("source_type") == "AI_GENERATED" and not any("AI-GENERATED" in t for t in labels):
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
