"""
Maps / Statistics / Data graphics as first-class assets (spec §18, §19, §34).

Statistics: rendered ONLY from structured data points
{label, value, unit, source, as_of, confidence}. Missing/invalid fields raise
INVALID_STAT_DATA -- a number is never invented, rounded away or inferred.
Bar charts always start at zero; mixed units cannot be compared on one chart.

Maps: schematic maps plotted ONLY from supplied coordinates (markers, route,
region polygon, labels, ordered movements). No basemap, coastline or border
is drawn -- the module never invents boundaries or military locations. A
coordinate `data_source` is mandatory (attribution/licensing, spec §18).
"""
from __future__ import annotations

import math
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.basemap import BasemapUnavailable, merc_y, render_basemap
from app.config import get_settings
from app.db.models import Asset, Project
from app.errors import VideoModuleError
from app.pipeline.import_assets import sanitize_text, sha256_of_file, write_source_metadata
from app.pipeline.intent import number_key
from app.storage import to_rel

W, H = 1280, 720
GRAPHIC_TYPES = {"STAT_CARD", "NUMBER_CALLOUT", "BAR_CHART", "LINE_CHART", "COMPARISON", "TIMELINE"}
CONFIDENCE = {"VERIFIED", "OFFICIAL", "REPORTED", "ESTIMATE", "DISPUTED", "UNVERIFIED"}
REQUIRED_STAT_FIELDS = ("value", "unit", "source", "as_of")


def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _fmt(v: float | int) -> str:
    if isinstance(v, int) or float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,}"


# ------------------------------------------------------------------ statistics
def validate_stat_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise VideoModuleError("INVALID_STAT_DATA", "Statistic spec must be an object.")
    gtype = spec.get("graphic_type", "STAT_CARD")
    if gtype not in GRAPHIC_TYPES:
        raise VideoModuleError("INVALID_STAT_DATA", f"graphic_type must be one of {sorted(GRAPHIC_TYPES)}")
    data = spec.get("data")
    if not isinstance(data, list) or not data:
        raise VideoModuleError("INVALID_STAT_DATA", "data must be a non-empty list of data points.")
    if len(data) > 24:
        raise VideoModuleError("INVALID_STAT_DATA", "At most 24 data points per graphic.")
    points = []
    for i, p in enumerate(data):
        if not isinstance(p, dict):
            raise VideoModuleError("INVALID_STAT_DATA", f"data[{i}] must be an object.")
        if gtype == "TIMELINE":
            missing = [k for k in ("label", "source", "as_of") if p.get(k) in (None, "")]
        else:  # value + unit keys must be present (unit may be "" for counts); source/as_of non-empty
            missing = [k for k in ("value", "unit") if k not in p] + \
                      [k for k in ("source", "as_of") if p.get(k) in (None, "")]
        if missing:
            raise VideoModuleError("INVALID_STAT_DATA", f"data[{i}] missing required fields {missing} (value/unit/source/as_of).")
        value = p.get("value")
        if value is not None or gtype != "TIMELINE":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise VideoModuleError("INVALID_STAT_DATA", f"data[{i}].value must be a finite number (got {type(value).__name__}).")
        conf = str(p.get("confidence") or "UNVERIFIED").upper()
        if conf not in CONFIDENCE:
            raise VideoModuleError("INVALID_STAT_DATA", f"data[{i}].confidence must be one of {sorted(CONFIDENCE)}")
        if gtype == "TIMELINE" and not p.get("event"):
            raise VideoModuleError("INVALID_STAT_DATA", f"data[{i}] TIMELINE points need an 'event' description.")
        points.append({
            "label": sanitize_text(p.get("label"), 80) or "",
            "value": value,
            "unit": sanitize_text(p.get("unit"), 24) or "",
            "source": sanitize_text(p.get("source"), 200),
            "as_of": sanitize_text(p.get("as_of"), 40),
            "confidence": conf,
            "event": sanitize_text(p.get("event"), 160),
        })
    if gtype in ("STAT_CARD", "NUMBER_CALLOUT") and len(points) != 1:
        raise VideoModuleError("INVALID_STAT_DATA", f"{gtype} takes exactly one data point.")
    if gtype in ("COMPARISON", "LINE_CHART", "TIMELINE") and len(points) < 2:
        raise VideoModuleError("INVALID_STAT_DATA", f"{gtype} needs at least two data points.")
    if gtype in ("BAR_CHART", "LINE_CHART", "COMPARISON"):
        units = {p["unit"] for p in points}
        if len(units) > 1:
            raise VideoModuleError("INVALID_STAT_DATA", f"Cannot plot mixed units {sorted(units)} on one chart.")
        if any(not p["label"] for p in points):
            raise VideoModuleError("INVALID_STAT_DATA", "Chart data points need labels.")
    title = sanitize_text(spec.get("title"), 120)
    if not title:
        raise VideoModuleError("INVALID_STAT_DATA", "A title describing the statistic is required.")
    return {"graphic_type": gtype, "title": title, "data": points}


def _footer(draw: ImageDraw.ImageDraw, points: list[dict]) -> None:
    f = _font(18)
    seen, lines = set(), []
    for p in points:
        key = (p["source"], p["as_of"], p["confidence"])
        if key not in seen:
            seen.add(key)
            lines.append(f"Source: {p['source']} | as of {p['as_of']} | status: {p['confidence']}")
    y = H - 28 * min(4, len(lines)) - 14
    for line in lines[:4]:
        draw.text((40, y), line[:140], fill=(190, 196, 206), font=f)
        y += 28


def render_stat_graphic(spec: dict, out_path: Path) -> None:
    gtype, title, pts = spec["graphic_type"], spec["title"], spec["data"]
    img = Image.new("RGB", (W, H), (16, 20, 28))
    d = ImageDraw.Draw(img)
    d.text((40, 30), title, fill=(255, 255, 255), font=_font(40))
    if gtype in ("STAT_CARD", "NUMBER_CALLOUT"):
        p = pts[0]
        d.text((60, 220), f"{_fmt(p['value'])} {p['unit']}".strip(), fill=(255, 210, 90), font=_font(120 if gtype == "NUMBER_CALLOUT" else 96))
        if p["label"]:
            d.text((64, 380), p["label"], fill=(220, 225, 235), font=_font(34))
    elif gtype in ("BAR_CHART", "COMPARISON"):
        vmax = max(max(abs(p["value"]) for p in pts), 1e-9)
        x0, y0, x1, y1 = 80, 120, W - 60, H - 170
        bw = (x1 - x0) / len(pts)
        d.line([(x0, y1), (x1, y1)], fill=(140, 140, 150), width=2)  # zero baseline (bars start at zero)
        for i, p in enumerate(pts):
            h = (abs(p["value"]) / vmax) * (y1 - y0 - 40)
            bx = x0 + i * bw + bw * 0.15
            d.rectangle([bx, y1 - h, bx + bw * 0.7, y1], fill=(70, 140, 220) if p["value"] >= 0 else (220, 90, 70))
            d.text((bx, y1 - h - 30), f"{_fmt(p['value'])} {p['unit']}".strip(), fill=(255, 255, 255), font=_font(20))
            d.text((bx, y1 + 8), p["label"][:22], fill=(210, 210, 220), font=_font(18))
    elif gtype == "LINE_CHART":
        vals = [p["value"] for p in pts]
        vmin, vmax = min(0, min(vals)), max(vals)
        span = (vmax - vmin) or 1
        x0, y0, x1, y1 = 80, 120, W - 80, H - 170
        d.line([(x0, y1), (x1, y1)], fill=(140, 140, 150), width=2)
        xy = [(x0 + i * (x1 - x0) / (len(pts) - 1), y1 - (p["value"] - vmin) / span * (y1 - y0 - 40)) for i, p in enumerate(pts)]
        d.line(xy, fill=(90, 200, 140), width=4)
        for (x, y), p in zip(xy, pts):
            d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(90, 200, 140))
            d.text((x - 20, y - 34), _fmt(p["value"]), fill=(255, 255, 255), font=_font(18))
            d.text((x - 20, y1 + 8), p["label"][:14], fill=(210, 210, 220), font=_font(16))
    elif gtype == "TIMELINE":
        x0, x1, y = 80, W - 80, 330
        d.line([(x0, y), (x1, y)], fill=(200, 200, 210), width=3)
        for i, p in enumerate(pts):
            x = x0 + i * (x1 - x0) / (len(pts) - 1)
            d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(255, 210, 90))
            d.text((x - 40, y - 60 if i % 2 == 0 else y + 22), p["label"][:16], fill=(255, 255, 255), font=_font(20))
            d.text((x - 40, y - 34 if i % 2 == 0 else y + 48), (p["event"] or "")[:24], fill=(200, 205, 215), font=_font(16))
    _footer(d, pts)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


def create_stat_asset(db: Session, project_id: str, spec: dict, tags: list[str] | None = None) -> Asset:
    if db.get(Project, project_id) is None:
        raise VideoModuleError("NOT_FOUND", f"Project {project_id} not found.")
    norm = validate_stat_spec(spec)
    out = get_settings().graphics_dir / project_id / f"{norm['graphic_type'].lower()}_{uuid.uuid4().hex[:12]}.png"
    render_stat_graphic(norm, out)
    verified = all(p["confidence"] in ("VERIFIED", "OFFICIAL") for p in norm["data"])
    sources = sorted({f"{p['source']} (as of {p['as_of']})" for p in norm["data"]})
    words = {w.lower() for w in norm["title"].split() if len(w) > 3}
    for p in norm["data"]:
        words |= {w.lower() for w in (p["label"] or "").split() if len(w) > 3}
    asset = Asset(
        project_id=project_id,
        asset_type="STAT_CARD" if norm["graphic_type"] in ("STAT_CARD", "NUMBER_CALLOUT") else "CHART",
        source_type="SYSTEM_GENERATED", local_path=to_rel(out), mime_type="image/png",
        file_size_bytes=out.stat().st_size, title=norm["title"],
        description=f"{norm['graphic_type']} rendered from {len(norm['data'])} structured data point(s).",
        creator="video-visual-module graphics", licence="Generated graphic; data rights per cited source",
        attribution="Data: " + "; ".join(sources), rights_status="PERMITTED",
        checksum_sha256=sha256_of_file(out), width=W, height=H,
        editorial_category="FACTUAL_EVIDENCE" if verified else "UNKNOWN",
        extra_metadata={
            "graphic_type": norm["graphic_type"], "stat": norm,
            "numbers": [number_key(p["value"], p["unit"]) for p in norm["data"] if p["value"] is not None],
            "tags": sorted(words | {t.lower() for t in (tags or [])}),
            "protected_regions": [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "label": "full graphic"}],
        },
    )
    db.add(asset)
    db.commit()
    write_source_metadata(asset)
    return asset


# ------------------------------------------------------------------ maps
def _ll(p, what: str) -> tuple[float, float]:
    try:
        lat, lon = float(p["lat"]), float(p["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoModuleError("MAP_RENDER_FAILED", f"{what} needs numeric lat/lon.") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise VideoModuleError("MAP_RENDER_FAILED", f"{what} lat/lon out of range.")
    return lat, lon


def validate_map_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise VideoModuleError("MAP_RENDER_FAILED", "Map spec must be an object.")
    title = sanitize_text(spec.get("title"), 120)
    source = sanitize_text(spec.get("data_source"), 300)
    if not title:
        raise VideoModuleError("MAP_RENDER_FAILED", "Map title is required.")
    if not source:
        raise VideoModuleError("MAP_RENDER_FAILED", "data_source (origin/licence of the coordinates) is required for maps.")
    markers = [{"label": sanitize_text(m.get("label"), 60) or f"M{i + 1}", "lat": _ll(m, f"markers[{i}]")[0],
                "lon": _ll(m, f"markers[{i}]")[1]} for i, m in enumerate(spec.get("markers") or [])]
    route = [dict(zip(("lat", "lon"), _ll(p, f"route[{i}]"))) for i, p in enumerate(spec.get("route") or [])]
    if route and len(route) < 2:
        raise VideoModuleError("MAP_RENDER_FAILED", "route needs at least two points.")
    region = None
    if spec.get("region"):
        poly = spec["region"].get("polygon") or []
        if len(poly) < 3:
            raise VideoModuleError("MAP_RENDER_FAILED", "region.polygon needs at least three points.")
        region = {"label": sanitize_text(spec["region"].get("label"), 60),
                  "polygon": [dict(zip(("lat", "lon"), _ll(p, f"region.polygon[{i}]"))) for i, p in enumerate(poly)]}
    labels = [{"text": sanitize_text(lb.get("text"), 60) or "", "lat": _ll(lb, f"labels[{i}]")[0],
               "lon": _ll(lb, f"labels[{i}]")[1]} for i, lb in enumerate(spec.get("labels") or [])]
    movements = []
    for i, mv in enumerate(spec.get("movements") or []):
        a, b = _ll(mv.get("from") or {}, f"movements[{i}].from"), _ll(mv.get("to") or {}, f"movements[{i}].to")
        movements.append({"label": sanitize_text(mv.get("label"), 60), "order": int(mv.get("order", i + 1)),
                          "from": {"lat": a[0], "lon": a[1]}, "to": {"lat": b[0], "lon": b[1]}})
    if not (markers or route or region or movements):
        raise VideoModuleError("MAP_RENDER_FAILED", "Map needs at least one marker, route, region or movement.")
    pts = [(m["lat"], m["lon"]) for m in markers] + [(p["lat"], p["lon"]) for p in route] + \
          ([(p["lat"], p["lon"]) for p in region["polygon"]] if region else []) + \
          [(lb["lat"], lb["lon"]) for lb in labels] + \
          [(m["from"]["lat"], m["from"]["lon"]) for m in movements] + [(m["to"]["lat"], m["to"]["lon"]) for m in movements]
    if spec.get("bbox"):
        bb = spec["bbox"]
        try:
            bbox = {k: float(bb[k]) for k in ("min_lat", "min_lon", "max_lat", "max_lon")}
        except (KeyError, TypeError, ValueError) as exc:
            raise VideoModuleError("MAP_RENDER_FAILED", "bbox needs min_lat,min_lon,max_lat,max_lon.") from exc
        if bbox["min_lat"] >= bbox["max_lat"] or bbox["min_lon"] >= bbox["max_lon"]:
            raise VideoModuleError("MAP_RENDER_FAILED", "bbox min must be < max.")
        for lat, lon in pts:
            if not (bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lon"] <= lon <= bbox["max_lon"]):
                raise VideoModuleError("MAP_RENDER_FAILED", "A plotted point lies outside the supplied bbox.")
    else:
        lats, lons = [p[0] for p in pts], [p[1] for p in pts]
        pad_lat = max(0.5, (max(lats) - min(lats)) * 0.2)
        pad_lon = max(0.5, (max(lons) - min(lons)) * 0.2)
        bbox = {"min_lat": max(-90, min(lats) - pad_lat), "max_lat": min(90, max(lats) + pad_lat),
                "min_lon": max(-180, min(lons) - pad_lon), "max_lon": min(180, max(lons) + pad_lon)}
    return {"basemap": bool(spec.get("basemap", False)),
            "title": title, "data_source": source, "bbox": bbox, "markers": markers, "route": route,
            "region": region, "labels": labels, "movements": sorted(movements, key=lambda m: m["order"]),
            "zoom": spec.get("zoom", 1)}


def render_map(spec: dict, out_path: Path) -> list[dict]:
    """Renders the schematic map; returns normalized highlight regions (for HIGHLIGHT_REGION motion)."""
    bb = spec["bbox"]
    mx0, my0, mx1, my1 = 40, 90, W - 40, H - 60

    # Phase 2: optional attributed raster basemap (off by default; schematic is the fallback)
    bg, info = None, {"used": False, "requested": bool(spec.get("basemap")), "reason": "not requested"}
    if spec.get("basemap"):
        try:
            bg, info = render_basemap(bb, (mx1 - mx0, my1 - my0))
            info["requested"] = True
        except BasemapUnavailable as exc:
            info = {"used": False, "requested": True, "reason": exc.reason, "fallback": "schematic"}
    spec["basemap_info"] = info

    if bg is not None:  # Web Mercator, matching the tiles (x is linear in lon; y uses the Mercator transform)
        ytop, ybot = merc_y(bb["max_lat"], 0), merc_y(bb["min_lat"], 0)

        def xy(lat, lon):
            x = mx0 + (lon - bb["min_lon"]) / (bb["max_lon"] - bb["min_lon"]) * (mx1 - mx0)
            y = my0 + (merc_y(lat, 0) - ytop) / (ybot - ytop) * (my1 - my0)
            return x, y
    else:  # schematic: equirectangular
        def xy(lat, lon):
            x = mx0 + (lon - bb["min_lon"]) / (bb["max_lon"] - bb["min_lon"]) * (mx1 - mx0)
            y = my1 - (lat - bb["min_lat"]) / (bb["max_lat"] - bb["min_lat"]) * (my1 - my0)
            return x, y

    img = Image.new("RGB", (W, H), (22, 34, 48))
    if bg is not None:
        img.paste(bg, (mx0, my0))
    d = ImageDraw.Draw(img, "RGBA")
    f_small, f_mid = _font(16), _font(22)
    d.rectangle([mx0, my0, mx1, my1], outline=(90, 110, 130), width=2)
    # graticule (coordinate grid only -- no invented coastlines/borders)
    step = max(0.5, round((bb["max_lon"] - bb["min_lon"]) / 6, 1))
    lon = math.ceil(bb["min_lon"] / step) * step
    while lon < bb["max_lon"]:
        x, _ = xy(bb["min_lat"], lon)
        d.line([(x, my0), (x, my1)], fill=(60, 80, 100), width=1)
        d.text((x + 3, my1 + 4), f"{lon:.1f}°", fill=(140, 160, 180), font=f_small)
        lon += step
    step_lat = max(0.5, round((bb["max_lat"] - bb["min_lat"]) / 4, 1))
    lat = math.ceil(bb["min_lat"] / step_lat) * step_lat
    while lat < bb["max_lat"]:
        _, y = xy(lat, bb["min_lon"])
        d.line([(mx0, y), (mx1, y)], fill=(60, 80, 100), width=1)
        d.text((mx0 + 4, y - 18), f"{lat:.1f}°", fill=(140, 160, 180), font=f_small)
        lat += step_lat
    regions: list[dict] = []

    def norm_box(points_xy, label, pad=24):
        xs, ys = [p[0] for p in points_xy], [p[1] for p in points_xy]
        x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
        x1, y1 = min(W, max(xs) + pad), min(H, max(ys) + pad)
        regions.append({"x": round(x0 / W, 4), "y": round(y0 / H, 4), "w": round(max(1, x1 - x0) / W, 4),
                        "h": round(max(1, y1 - y0) / H, 4), "label": label})

    if spec["region"]:
        poly = [xy(p["lat"], p["lon"]) for p in spec["region"]["polygon"]]
        d.polygon(poly, fill=(220, 80, 60, 70), outline=(240, 110, 90, 255))
        if spec["region"]["label"]:
            cx, cy = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
            d.text((cx - 30, cy - 10), spec["region"]["label"], fill=(255, 220, 210), font=f_mid)
        norm_box(poly, spec["region"]["label"] or "region")
    if spec["route"]:
        line = [xy(p["lat"], p["lon"]) for p in spec["route"]]
        d.line(line, fill=(250, 200, 80, 255), width=4)
        norm_box(line, "route")
    for mv in spec["movements"]:
        a, b = xy(mv["from"]["lat"], mv["from"]["lon"]), xy(mv["to"]["lat"], mv["to"]["lon"])
        d.line([a, b], fill=(120, 220, 255, 255), width=3)
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        for s in (-0.5, 0.5):
            d.line([b, (b[0] - 16 * math.cos(ang + s), b[1] - 16 * math.sin(ang + s))], fill=(120, 220, 255, 255), width=3)
        d.text(((a[0] + b[0]) / 2 + 6, (a[1] + b[1]) / 2 - 20), f"{mv['order']}. {mv['label'] or ''}", fill=(200, 240, 255), font=f_small)
    for m in spec["markers"]:
        x, y = xy(m["lat"], m["lon"])
        d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=(255, 255, 255, 255), outline=(20, 20, 20, 255), width=2)
        d.text((x + 12, y - 10), m["label"], fill=(255, 255, 255), font=f_mid)
        norm_box([(x, y)], m["label"], pad=70)
    for lb in spec["labels"]:
        x, y = xy(lb["lat"], lb["lon"])
        d.text((x, y), lb["text"], fill=(200, 210, 220), font=f_small)
    d.text((40, 24), spec["title"], fill=(255, 255, 255), font=_font(34))
    if bg is not None:
        footer = f"Basemap: {info['attribution']} | Positions plotted from: {spec['data_source']}"
    else:
        footer = f"Schematic - positions plotted from: {spec['data_source']}. No basemap or boundaries drawn."
    d.rectangle([0, H - 34, W, H], fill=(10, 14, 20))
    d.text((40, H - 30), footer[:170], fill=(200, 210, 220), font=f_small)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return regions


def create_map_asset(db: Session, project_id: str, spec: dict, tags: list[str] | None = None) -> Asset:
    if db.get(Project, project_id) is None:
        raise VideoModuleError("NOT_FOUND", f"Project {project_id} not found.")
    norm = validate_map_spec(spec)
    out = get_settings().maps_dir / project_id / f"map_{uuid.uuid4().hex[:12]}.png"
    try:
        regions = render_map(norm, out)
    except VideoModuleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise VideoModuleError("MAP_RENDER_FAILED", f"Map rendering failed: {type(exc).__name__}") from exc
    words = {w.lower() for w in norm["title"].split() if len(w) > 3}
    for m in norm["markers"]:
        words |= {w.lower() for w in m["label"].split() if len(w) > 2}
    if norm["region"] and norm["region"]["label"]:
        words |= {w.lower() for w in norm["region"]["label"].split() if len(w) > 2}
    for lb in norm["labels"]:
        words |= {w.lower() for w in lb["text"].split() if len(w) > 2}
    bm = norm.get("basemap_info") or {"used": False}
    if bm.get("used"):
        description = "Map plotted from supplied coordinates over an attributed raster basemap (Web Mercator)."
        licence = "Generated map; basemap tiles per configured source licence; coordinate data per cited source"
        attribution = f"Map data: {norm['data_source']}; Basemap: {bm['attribution']}"
    else:
        description = "Schematic map plotted from supplied coordinates (no basemap)."
        licence = "Generated schematic; coordinate data per cited source"
        attribution = f"Map data: {norm['data_source']}"
    asset = Asset(
        project_id=project_id, asset_type="MAP", source_type="SYSTEM_GENERATED", local_path=to_rel(out),
        mime_type="image/png", file_size_bytes=out.stat().st_size, title=norm["title"],
        description=description, creator="video-visual-module maps", licence=licence,
        attribution=attribution, rights_status="PERMITTED", attribution_required=bool(bm.get("used")),
        checksum_sha256=sha256_of_file(out), width=W, height=H, editorial_category="ILLUSTRATIVE_VISUAL",
        extra_metadata={"map": norm, "regions": regions, "basemap": bm,
                        "tags": sorted(words | {t.lower() for t in (tags or [])})},
    )
    db.add(asset)
    db.commit()
    write_source_metadata(asset)
    return asset
