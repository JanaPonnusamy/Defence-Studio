"""
Motion Planner (spec §11, §16, §17).

Deterministic and renderer-neutral. Each visual type has a "framing ladder":
the ordered set of (composition, motion) pairs used when the same asset
appears in successive shots, so reuse always means a meaningfully different
frame (wide -> slow zoom -> close crop -> pan -> highlighted region ...).

All crop rectangles are normalized to the SOURCE image (x, y, w, h in 0..1)
and always have w == h, i.e. the source aspect ratio is preserved -- motion
never stretches or distorts factual content. Protected regions (text, logos,
faces, markings, evidence details) supplied in asset metadata are always kept
fully inside the crop and inside the final frame of any zoom/pan; if a motion
would cut one, it is downgraded (zoom reduced or STATIC) and the reason is
recorded.
"""
from __future__ import annotations

BASE_MOTIONS = [
    "STATIC", "SLOW_ZOOM_IN", "SLOW_ZOOM_OUT", "PAN_LEFT", "PAN_RIGHT",
    "PAN_UP", "PAN_DOWN", "CROP_FOCUS", "PARALLAX", "HIGHLIGHT_REGION",
]
COMPOSITIONS = ["wide", "medium", "close", "subject", "region"]

_PHOTO = [("wide", "STATIC"), ("wide", "SLOW_ZOOM_IN"), ("close", "CROP_FOCUS"), ("medium", "PAN_RIGHT"),
          ("region", "HIGHLIGHT_REGION"), ("medium", "SLOW_ZOOM_OUT"), ("subject", "PAN_LEFT"), ("medium", "PAN_UP")]
FRAMING_LADDERS: dict[str, list[tuple[str, str]]] = {
    "IMAGE": _PHOTO,
    "SOURCE_PHOTO": _PHOTO,
    "AI_IMAGE": [("wide", "SLOW_ZOOM_IN"), ("medium", "PARALLAX"), ("close", "CROP_FOCUS"), ("medium", "PAN_RIGHT"),
                 ("wide", "SLOW_ZOOM_OUT"), ("subject", "PAN_LEFT")],
    "SATELLITE": [("wide", "STATIC"), ("wide", "SLOW_ZOOM_IN"), ("region", "HIGHLIGHT_REGION"), ("close", "CROP_FOCUS")],
    "MAP": [("wide", "STATIC"), ("wide", "SLOW_ZOOM_IN"), ("region", "HIGHLIGHT_REGION"), ("medium", "PAN_RIGHT")],
    "DOCUMENT": [("wide", "STATIC"), ("region", "CROP_FOCUS"), ("region", "HIGHLIGHT_REGION"), ("medium", "PAN_DOWN")],
    "STAT_CARD": [("wide", "STATIC"), ("wide", "SLOW_ZOOM_IN")],
    "CHART": [("wide", "STATIC"), ("wide", "SLOW_ZOOM_IN")],
    "DIAGRAM": [("wide", "STATIC"), ("medium", "CROP_FOCUS"), ("wide", "SLOW_ZOOM_IN")],
    "TITLE_CARD": [("wide", "STATIC"), ("wide", "SLOW_ZOOM_IN")],
    "VIDEO_CLIP": [("wide", "STATIC")],
    "TRANSITION": [("wide", "STATIC")],
}
# gentler zoom on data graphics / documents so nothing is pushed off-frame
_ZOOM_AMOUNT = {"STAT_CARD": 1.04, "CHART": 1.04, "TITLE_CARD": 1.04, "DOCUMENT": 1.04}
_SIZES = {"wide": 1.0, "medium": 0.8, "close": 0.55, "subject": 0.6, "region": None}


def ladder_for(visual_type: str) -> list[tuple[str, str]]:
    return FRAMING_LADDERS.get(visual_type, [("wide", "STATIC")])


def _clamp_rect(cx: float, cy: float, size: float) -> dict:
    size = min(1.0, max(0.05, size))
    x = min(max(0.0, cx - size / 2), 1.0 - size)
    y = min(max(0.0, cy - size / 2), 1.0 - size)
    return {"x": round(x, 4), "y": round(y, 4), "w": round(size, 4), "h": round(size, 4)}


def _contains(outer: dict, inner: dict, eps: float = 1e-4) -> bool:
    return (inner["x"] >= outer["x"] - eps and inner["y"] >= outer["y"] - eps
            and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + eps
            and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + eps)


def _expand_to_include(rect: dict, regions: list[dict]) -> dict:
    if not regions:
        return rect
    x0 = min([rect["x"]] + [r["x"] for r in regions])
    y0 = min([rect["y"]] + [r["y"] for r in regions])
    x1 = max([rect["x"] + rect["w"]] + [r["x"] + r["w"] for r in regions])
    y1 = max([rect["y"] + rect["h"]] + [r["y"] + r["h"] for r in regions])
    size = max(x1 - x0, y1 - y0)
    return _clamp_rect((x0 + x1) / 2, (y0 + y1) / 2, size)


def compute_crop(composition: str, asset_meta: dict, region_index: int = 0) -> dict:
    meta = asset_meta or {}
    focus = meta.get("focus_point") or {"x": 0.5, "y": 0.5}
    regions = meta.get("regions") or []
    protected = meta.get("protected_regions") or []
    region_used = None
    if composition == "region" and regions:
        region_used = regions[region_index % len(regions)]
        size = max(region_used["w"], region_used["h"]) * 1.25
        rect = _clamp_rect(region_used["x"] + region_used["w"] / 2, region_used["y"] + region_used["h"] / 2, max(0.35, size))
    elif composition == "region":
        composition = "medium"
        rect = _clamp_rect(focus["x"], focus["y"], 0.8)
    elif composition == "wide":
        rect = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    else:
        center = focus if composition in ("subject", "close") else {"x": 0.5, "y": 0.5}
        rect = _clamp_rect(center["x"], center["y"], _SIZES[composition])
    expanded = _expand_to_include(rect, protected)
    crop = {
        "composition": composition,
        **expanded,
        "aspect_preserved": True,
        "protect_regions": [r.get("label") or "protected" for r in protected],
    }
    if expanded != rect:
        crop["note"] = "crop widened to keep protected regions in frame"
    if region_used:
        crop["region"] = region_used
    return crop


def _windows(crop: dict, motion: dict) -> list[dict]:
    """Visible windows (normalized to source) at the start and end of the motion."""
    anchor = motion.get("anchor") or {"x": crop["x"] + crop["w"] / 2, "y": crop["y"] + crop["h"] / 2}
    out = []
    for scale_key, off_key in (("from_scale", "from_offset"), ("to_scale", "to_offset")):
        scale = motion.get(scale_key, motion.get("scale", 1.0))
        off = motion.get(off_key) or {"x": 0.0, "y": 0.0}
        w = crop["w"] / scale
        cx, cy = anchor["x"] + off["x"] * crop["w"], anchor["y"] + off["y"] * crop["h"]
        out.append({"x": cx - w / 2, "y": cy - w / 2, "w": w, "h": w})
    return out


def plan_motion(visual_type: str, motion_type: str, crop: dict, duration: float, asset_meta: dict | None = None) -> dict:
    meta = asset_meta or {}
    protected = meta.get("protected_regions") or []
    center = {"x": round(crop["x"] + crop["w"] / 2, 4), "y": round(crop["y"] + crop["h"] / 2, 4)}
    zoom = _ZOOM_AMOUNT.get(visual_type, 1.08)
    m: dict = {"type": motion_type, "easing": "linear", "duration": round(duration, 3)}
    if motion_type == "SLOW_ZOOM_IN":
        m.update(from_scale=1.0, to_scale=zoom, anchor=center)
    elif motion_type == "SLOW_ZOOM_OUT":
        m.update(from_scale=zoom, to_scale=1.0, anchor=center)
    elif motion_type in ("PAN_LEFT", "PAN_RIGHT", "PAN_UP", "PAN_DOWN"):
        d = 0.04
        vec = {"PAN_LEFT": (d, 0, -d, 0), "PAN_RIGHT": (-d, 0, d, 0), "PAN_UP": (0, d, 0, -d), "PAN_DOWN": (0, -d, 0, d)}[motion_type]
        m.update(scale=1.1, from_offset={"x": vec[0], "y": vec[1]}, to_offset={"x": vec[2], "y": vec[3]}, anchor=center)
    elif motion_type == "CROP_FOCUS":
        m.update(focus_region={k: crop[k] for k in ("x", "y", "w", "h")})
    elif motion_type == "HIGHLIGHT_REGION":
        region = crop.get("region") or {k: crop[k] for k in ("x", "y", "w", "h")}
        m.update(region={k: region[k] for k in ("x", "y", "w", "h")}, style="outline", label=region.get("label"))
    elif motion_type == "PARALLAX":
        m.update(layers=2, depth=0.03, direction="right", fallback={"type": "SLOW_ZOOM_IN", "from_scale": 1.0, "to_scale": 1.05})

    # §17 protection: never zoom/pan a protected region out of frame
    if protected and motion_type in ("SLOW_ZOOM_IN", "SLOW_ZOOM_OUT", "PAN_LEFT", "PAN_RIGHT", "PAN_UP", "PAN_DOWN", "PARALLAX"):
        probe = m if motion_type != "PARALLAX" else {"from_scale": 1.0, "to_scale": 1.05, "anchor": center}
        if not all(_contains(win, r) for win in _windows(crop, probe) for r in protected):
            return {"type": "STATIC", "easing": "linear", "duration": round(duration, 3),
                    "downgraded_from": motion_type, "reason": "motion would cut a protected region"}
    return m


def plan_crop(visual_type: str) -> dict:
    """Back-compat helper: default composition for a visual type."""
    return compute_crop(ladder_for(visual_type)[0][0], {})
