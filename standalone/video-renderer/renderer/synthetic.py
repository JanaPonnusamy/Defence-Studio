"""Locally generated test packages (no downloads): gradient/shape images, a tone WAV, VVM-style timelines.

Used by the tests, the self-test and the real-render benchmarks. Images are PNG/JPEG drawn with Pillow,
the narration is a tone-burst WAV; the timeline follows the shape documented in docs/CONTRACTS.md.
"""
from __future__ import annotations

import json
import math
import random
import struct
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from renderer.package import sha256_file, write_manifest

MOTIONS = ["STATIC", "SLOW_ZOOM_IN", "SLOW_ZOOM_OUT", "PAN_LEFT", "PAN_RIGHT", "PAN_UP", "PAN_DOWN", "CROP_FOCUS"]
TRANSITIONS = ["CUT", "DISSOLVE", "FADE", "FADE_IN", "CUT", "WIPE"]


def make_wav(path: Path, seconds: float, rate: int = 24000, freq: float = 330.0) -> None:
    """Mono 16-bit PCM tone with a 2 Hz amplitude pulse. Exactly round(seconds*rate) samples."""
    n = int(round(seconds * rate))
    chunk_len = rate  # one second, tiled (freq chosen so it loops cleanly)
    freq = round(freq)
    one = [int(9000 * math.sin(2 * math.pi * freq * i / rate) * (0.55 + 0.45 * math.sin(2 * math.pi * 2 * i / rate)))
           for i in range(chunk_len)]
    one_b = struct.pack(f"<{chunk_len}h", *one)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        full, rest = divmod(n, chunk_len)
        for _ in range(full):
            w.writeframes(one_b)
        if rest:
            w.writeframes(one_b[: rest * 2])


def make_image(path: Path, w: int, h: int, seed: int, label: str, fmt: str = "PNG") -> None:
    rnd = random.Random(seed)
    c1 = tuple(rnd.randint(30, 200) for _ in range(3))
    c2 = tuple(rnd.randint(30, 200) for _ in range(3))
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):  # vertical gradient
        t = y / max(1, h - 1)
        px.line([(0, y), (w, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    for _ in range(14):  # coloured shapes so motion is visible
        x0, y0 = rnd.randint(0, w - 40), rnd.randint(0, h - 40)
        x1, y1 = min(w, x0 + rnd.randint(40, w // 3)), min(h, y0 + rnd.randint(40, h // 3))
        col = tuple(rnd.randint(0, 255) for _ in range(3))
        (px.ellipse if rnd.random() < 0.5 else px.rectangle)((x0, y0, x1, y1), fill=col, outline=(255, 255, 255), width=3)
    for gx in range(0, w, max(1, w // 8)):  # alignment grid
        px.line([(gx, 0), (gx, h)], fill=(255, 255, 255), width=1)
    for gy in range(0, h, max(1, h // 8)):
        px.line([(0, gy), (w, gy)], fill=(255, 255, 255), width=1)
    try:
        font = ImageFont.load_default(max(24, h // 6))
    except Exception:  # noqa: BLE001
        font = None
    px.text((w // 10, h // 3), label, fill=(255, 255, 255), font=font, stroke_width=3, stroke_fill=(0, 0, 0))
    img.save(path, format=fmt, **({"quality": 90} if fmt == "JPEG" else {}))


def _asset_ref(aid: str, rel: str, mime: str, sha: str, w: int, h: int, label: str) -> dict:
    return {
        "asset_id": aid, "asset_type": "IMAGE", "source_type": "EXTERNAL_SOURCE", "editorial_label": label,
        "is_mock": False, "uri": f"storage://assets/synthetic/{Path(rel).name}", "url": f"/assets/{aid}/file",
        "mime_type": mime, "width": w, "height": h, "duration": None, "title": f"Synthetic {aid}",
        "attribution": "synthetic test fixture", "licence": "CC0", "rights_status": "LICENSED",
        "rights_label": "Synthetic fixture (generated locally)", "source_url": None, "checksum_sha256": sha,
        "generation_kind": "NOT_GENERATED", "provenance_class": "SOURCE_ORIGINAL", "evidence_eligible": True,
        "is_fallback": False, "fallback_from": None, "provider": None, "model": None, "provider_request_id": None,
        "creator": None, "source_identity": None, "licence_url": None, "attribution_required": False,
        "acquired_at": None,
    }


def _motion(kind: str, dur: float, rnd: random.Random) -> dict:
    m: dict = {"type": kind, "easing": "linear", "duration": round(dur, 3)}
    if kind == "SLOW_ZOOM_IN":
        m.update(from_scale=1.0, to_scale=1.08, anchor={"x": 0.5, "y": 0.5})
    elif kind == "SLOW_ZOOM_OUT":
        m.update(from_scale=1.08, to_scale=1.0, anchor={"x": 0.5, "y": 0.5})
    elif kind.startswith("PAN_"):
        d = 0.04
        v = {"PAN_LEFT": (d, 0, -d, 0), "PAN_RIGHT": (-d, 0, d, 0), "PAN_UP": (0, d, 0, -d), "PAN_DOWN": (0, -d, 0, d)}[kind]
        m.update(scale=1.1, from_offset={"x": v[0], "y": v[1]}, to_offset={"x": v[2], "y": v[3]},
                 anchor={"x": 0.5, "y": 0.5})
    elif kind == "CROP_FOCUS":
        m.update(focus_region={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5})
    return m


def build_timeline(*, durations: list[float], start: float, asset_ids: list[str], asset_meta: dict[str, dict],
                   gap_indices: set[int], seed: int, fps: float | None, width: int | None, height: int | None,
                   overlay_every: int = 2, motions: list[str] | None = None,
                   transitions: list[str] | None = None) -> dict:
    rnd = random.Random(seed)
    motions = motions or MOTIONS
    transitions = transitions or TRANSITIONS
    shots, t, used = [], start, set()
    for i, dur in enumerate(durations):
        end = round(t + dur, 3)
        gap = i in gap_indices
        aid = None if gap else asset_ids[i % len(asset_ids)]
        if aid:
            used.add(aid)
        crop = {"composition": "wide", "x": 0, "y": 0, "w": 1, "h": 1, "aspect_preserved": True}
        if not gap and i % 5 == 3:
            crop = {"composition": "medium", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8, "aspect_preserved": True}
        overlays = []
        if not gap and overlay_every and i % overlay_every == 0:
            overlays.append({"type": "source_caption", "text": f"Source: synthetic fixture {aid} (shot {i})"})
            if i % (overlay_every * 2) == 0:
                overlays.append({"type": "rights_warning", "text": "Licence: CC0 - synthetic test image"})
        label = "FACTUAL_EVIDENCE" if i % 3 == 0 else "ILLUSTRATIVE_VISUAL"
        if not gap and label == "ILLUSTRATIVE_VISUAL":
            overlays.append({"type": "editorial_label", "text": "ILLUSTRATIVE"})
        shots.append({
            "shot_id": f"shot-{i:04d}", "scene_id": f"scene-{i // 5:03d}", "order": i,
            "start": round(t, 3), "end": end, "duration": round(end - t, 3),
            "asset_id": aid, "visual_type": "SOURCE_PHOTO", "source_type": "EXTERNAL_SOURCE",
            "editorial_label": label if not gap else "UNKNOWN",
            "intent": "CONTEXT", "status": "GAP" if gap else "READY",
            "gap_code": "NO_APPROVED_ASSET" if gap else None,
            "narration_anchor": {"segment_ids": [f"seg-{i:04d}"], "text": f"Narration for synthetic shot {i}.",
                                 "start": round(t, 3), "end": end, "timing_source": "ESTIMATED"},
            "motion": _motion("STATIC" if gap else motions[i % len(motions)], dur, rnd),
            "crop": crop, "transition": transitions[i % len(transitions)] if i else "FADE_IN",
            "overlays": overlays, "attribution": None, "editorial_reason": "synthetic",
            "frame_start": int(round(t * fps)) if fps else None, "frame_end": int(round(end * fps)) if fps else None,
        })
        t = end
    return {
        "schema": "video-visual-timeline", "version": "1.1", "visual_plan_id": f"synthetic-plan-{seed}",
        "project_id": "synthetic-project", "generated_at": "2026-10-13T00:00:00+00:00", "start": start,
        "duration": round(t - start, 3),
        "output": {"width": width, "height": height, "frame_rate": fps, "defined": bool(width and height and fps),
                   "note": None},
        "timing": {"sources": ["ESTIMATED"], "timing_source": "ESTIMATED", "estimated": True, "gaps_closed": 0,
                   "gap_seconds_closed": 0.0, "units": "seconds on the narration timeline"},
        "shots": shots, "assets": {a: asset_meta[a] for a in sorted(used)}, "attributions": [], "summary": {},
    }


def build_package(root: Path, *, durations: list[float], wav_seconds: float | None = None, start: float = 0.0,
                  n_assets: int = 6, gap_indices: set[int] | None = None, seed: int = 1, profile: dict | None = None,
                  timeline_output: dict | None = None, image_size: tuple[int, int] = (1600, 900),
                  overlay_every: int = 2, motions: list[str] | None = None,
                  transitions: list[str] | None = None) -> Path:
    """Write a complete, valid render package into `root` and return `root`."""
    root = Path(root)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    rels, meta = {}, {}
    sizes = [image_size, (image_size[1], image_size[0]), (image_size[0], image_size[0] * 2 // 3), image_size]
    for n in range(n_assets):
        aid = f"asset-{n:03d}"
        w, h = sizes[n % len(sizes)]
        fmt, ext, mime = ("JPEG", "jpg", "image/jpeg") if n % 3 == 2 else ("PNG", "png", "image/png")
        rel = f"assets/{aid}.{ext}"
        make_image(root / rel, w, h, seed * 100 + n, f"{aid}\n{w}x{h}", fmt)
        rels[aid] = (rel, mime)
        meta[aid] = _asset_ref(aid, rel, mime, sha256_file(root / rel), w, h,
                               "FACTUAL_EVIDENCE" if n % 2 == 0 else "ILLUSTRATIVE_VISUAL")
    to = timeline_output or {}
    tl = build_timeline(durations=durations, start=start, asset_ids=sorted(rels), asset_meta=meta,
                        gap_indices=gap_indices or set(), seed=seed, fps=to.get("frame_rate"),
                        width=to.get("width"), height=to.get("height"), overlay_every=overlay_every,
                        motions=motions, transitions=transitions)
    (root / "timeline.json").write_text(json.dumps(tl, indent=2), encoding="utf-8")
    end = tl["shots"][-1]["end"]
    make_wav(root / "narration.wav", wav_seconds if wav_seconds is not None else end)
    write_manifest(root, timeline_rel="timeline.json", narration_rel="narration.wav",
                   assets=rels, output_profile=profile,
                   source={"generator": "renderer.synthetic", "seed": seed})
    return root


def small_package(root: Path, profile: dict | None = None, **kw) -> Path:
    """20 s, 4 shots, 2 crossfade transitions (DISSOLVE, FADE), overlays, one GAP (shot 2)."""
    return build_package(root, durations=[5.0, 5.0, 5.0, 5.0], gap_indices={2}, seed=7,
                         motions=["SLOW_ZOOM_IN", "PAN_RIGHT", "STATIC", "SLOW_ZOOM_OUT"],
                         transitions=["FADE_IN", "DISSOLVE", "FADE", "CUT"],
                         profile=profile or {"width": 1280, "height": 720, "fps": 25}, **kw)


def long_package(root: Path, minutes: float = 10.0, shots: int = 140, profile: dict | None = None,
                 seed: int = 11) -> Path:
    """VVM-style long episode: `shots` shots of varying length summing to `minutes`, ~3% GAPs, mixed motion."""
    rnd = random.Random(seed)
    total = minutes * 60.0
    raw = [rnd.uniform(0.6, 1.4) for _ in range(shots)]
    scale = total / sum(raw)
    durs = [round(r * scale, 3) for r in raw]
    durs[-1] = round(total - sum(durs[:-1]), 3)
    gaps = {i for i in range(7, shots, 37)}
    return build_package(root, durations=durs, n_assets=12, gap_indices=gaps, seed=seed, image_size=(1920, 1080),
                         profile=profile or {"width": 1920, "height": 1080, "fps": 25}, overlay_every=3)
