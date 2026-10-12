"""
Optional still previews (NOT video rendering -- spec §0/§35).

`render_shot_frame` produces a real PNG of what a shot's crop + motion shows
at a normalized time t (0 = first frame, 1 = last frame), so an editor can
check framing. `render_contact_sheet` tiles the first frames of a plan's
shots. Phase 2: video clips (imageio-ffmpeg) and PDF pages (pypdfium2) are
decoded when those optional decoders are available; otherwise the tile states
the exact reason ("preview unavailable - ...") instead of faking a picture.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.pipeline.media_probe import PreviewUnavailable, pdf_page, video_frame
from app.storage import resolve_stored_path

TILE_W, TILE_H = 480, 270


def _label(img: Image.Image, text: str, color=(255, 255, 255), bg=(0, 0, 0)) -> None:
    d = ImageDraw.Draw(img)
    f = ImageFont.load_default()
    d.rectangle([0, img.height - 16, img.width, img.height], fill=bg)
    d.text((4, img.height - 14), text[:90], fill=color, font=f)


def _placeholder(text: str, size=(TILE_W, TILE_H), color=(60, 60, 60)) -> Image.Image:
    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    d.text((12, size[1] // 2 - 8), text[:70], fill=(240, 240, 240), font=ImageFont.load_default())
    return img


def _window(crop: dict, motion: dict, t: float) -> tuple[float, float, float, float]:
    t = min(1.0, max(0.0, t))
    x, y, w, h = crop["x"], crop["y"], crop["w"], crop["h"]
    mtype = motion.get("type", "STATIC")
    if mtype == "PARALLAX":
        motion = {**motion.get("fallback", {}), "type": "SLOW_ZOOM_IN"}
        mtype = "SLOW_ZOOM_IN"
    scale = 1.0
    if "from_scale" in motion:
        scale = motion["from_scale"] + (motion["to_scale"] - motion["from_scale"]) * t
    elif "scale" in motion:
        scale = motion["scale"]
    cx, cy = x + w / 2, y + h / 2
    if "from_offset" in motion:
        fo, to = motion["from_offset"], motion["to_offset"]
        cx += (fo["x"] + (to["x"] - fo["x"]) * t) * w
        cy += (fo["y"] + (to["y"] - fo["y"]) * t) * h
    nw, nh = w / scale, h / scale
    nx = min(max(0.0, cx - nw / 2), 1 - nw)
    ny = min(max(0.0, cy - nh / 2), 1 - nh)
    return nx, ny, nw, nh


def load_source_image(asset: dict, *, at_seconds: float = 0.0, page: int = 1) -> Image.Image:
    """The pixels a preview is cut from: the image itself, a decoded video frame, or a rendered PDF page.
    Raises PreviewUnavailable (with the reason) instead of ever inventing a picture."""
    mime = asset.get("mime_type") or ""
    path = resolve_stored_path(asset.get("local_path")) if asset.get("local_path") else None
    if path is None or not path.exists():
        raise PreviewUnavailable("source file missing")
    if mime.startswith("image/"):
        with Image.open(path) as im:
            return im.convert("RGB")
    if mime.startswith("video/"):
        return video_frame(path, at_seconds)
    if mime == "application/pdf":
        return pdf_page(path, page)
    raise PreviewUnavailable(f"no decoder for {mime or 'unknown type'}")


def _source_for_shot(shot: dict, asset: dict, t: float) -> Image.Image:
    motion = shot.get("motion") or {}
    if (asset.get("mime_type") or "").startswith("video/"):
        seg = motion.get("clip_segment") or {}
        a, b = float(seg.get("in_point", 0.0)), float(seg.get("out_point", seg.get("in_point", 0.0)))
        return load_source_image(asset, at_seconds=a + (b - a) * min(1.0, max(0.0, t)))
    page = 1
    for r in (motion.get("focus_region"), motion.get("region")):
        if isinstance(r, dict) and r.get("page"):
            page = int(r["page"])
    return load_source_image(asset, page=page)


def render_shot_frame(shot: dict, asset: dict | None, t: float = 0.0, size=(TILE_W, TILE_H)) -> Image.Image:
    if shot.get("status") == "GAP" or asset is None:
        return _placeholder(f"GAP: {shot.get('gap_code')} ({shot.get('visual_type')})", size, (90, 40, 40))
    mime = asset.get("mime_type") or ""
    try:
        src = _source_for_shot(shot, asset, t)
    except PreviewUnavailable as exc:
        color = (120, 30, 30) if exc.reason == "source file missing" else (60, 60, 60)
        return _placeholder(f"{asset.get('asset_type')} ({mime or 'no file'}): preview unavailable - {exc.reason}",
                            size, color)
    with src:
        nx, ny, nw, nh = _window(shot["crop"], shot.get("motion") or {}, t)
        box = (int(nx * src.width), int(ny * src.height), max(int((nx + nw) * src.width), int(nx * src.width) + 1),
               max(int((ny + nh) * src.height), int(ny * src.height) + 1))
        region = src.crop(box)
        frame = ImageOps.pad(region, size, color=(0, 0, 0))  # letterbox, never stretch
        motion = shot.get("motion") or {}
        if motion.get("type") == "HIGHLIGHT_REGION" and motion.get("region"):
            r = motion["region"]
            # map region (source-normalized) into frame coordinates
            scale = min(size[0] / region.width, size[1] / region.height)
            ox = (size[0] - region.width * scale) / 2
            oy = (size[1] - region.height * scale) / 2
            rx0 = ox + (r["x"] * src.width - box[0]) * scale
            ry0 = oy + (r["y"] * src.height - box[1]) * scale
            rx1 = rx0 + r["w"] * src.width * scale
            ry1 = ry0 + r["h"] * src.height * scale
            ImageDraw.Draw(frame).rectangle([rx0, ry0, rx1, ry1], outline=(255, 220, 0), width=3)
    tag = asset.get("editorial_label", "")
    if asset.get("is_mock"):
        tag += " MOCK"
    if asset.get("is_fallback"):
        tag += " FALLBACK"
    _label(frame, f"{motion.get('type')} t={t:.1f} | {tag}")
    return frame


def render_contact_sheet(shots: list[dict], assets: dict, out_path: Path, cols: int = 4, max_shots: int = 48) -> Path:
    shots = shots[:max_shots]
    tw, th = 320, 180
    rows = max(1, (len(shots) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + 18)), (10, 10, 10))
    d = ImageDraw.Draw(sheet)
    f = ImageFont.load_default()
    for i, s in enumerate(shots):
        tile = render_shot_frame(s, assets.get(s.get("asset_id")) if s.get("asset_id") else None, 0.0, (tw, th))
        x, y = (i % cols) * tw, (i // cols) * (th + 18)
        sheet.paste(tile, (x, y))
        d.text((x + 4, y + th + 3), f"#{s['order']} {s['start']:.1f}-{s['end']:.1f}s {s.get('framing') or ''}"[:52],
               fill=(220, 220, 220), font=f)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, format="PNG")
    return out_path
