"""Deterministic motion: the visible source window of a shot at progress p in [0, 1].

All rectangles are normalised to the SOURCE image (x, y, w, h in 0..1). The window is always
a uniform scaling of the shot's crop (motion never changes the crop's aspect), it is kept
inside the source, and composition letterboxes it into the output frame, so nothing is ever
stretched. Easing is linear (the only easing the timeline contract defines).
"""
from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_MOTIONS = {"STATIC", "SLOW_ZOOM_IN", "SLOW_ZOOM_OUT", "PAN_LEFT", "PAN_RIGHT", "PAN_UP", "PAN_DOWN",
                     "CROP_FOCUS"}
_PAN = {"PAN_LEFT", "PAN_RIGHT", "PAN_UP", "PAN_DOWN"}
_DEFAULT_ZOOM = 1.08
_DEFAULT_PAN_SCALE = 1.1
_DEFAULT_PAN_D = 0.04
_PAN_VEC = {"PAN_LEFT": (_DEFAULT_PAN_D, 0, -_DEFAULT_PAN_D, 0), "PAN_RIGHT": (-_DEFAULT_PAN_D, 0, _DEFAULT_PAN_D, 0),
            "PAN_UP": (0, _DEFAULT_PAN_D, 0, -_DEFAULT_PAN_D), "PAN_DOWN": (0, -_DEFAULT_PAN_D, 0, _DEFAULT_PAN_D)}


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    def key(self, nd: int = 6) -> tuple:
        return (round(self.x, nd), round(self.y, nd), round(self.w, nd), round(self.h, nd))


def _num(v, default: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _rect(d: dict | None, fallback: Rect) -> Rect:
    if not isinstance(d, dict):
        return fallback
    r = Rect(_num(d.get("x"), fallback.x), _num(d.get("y"), fallback.y),
             _num(d.get("w"), fallback.w), _num(d.get("h"), fallback.h))
    return r if r.w > 0 and r.h > 0 else fallback


def clamp_inside(cx: float, cy: float, w: float, h: float) -> Rect:
    """Window of size w x h centred near (cx, cy), shifted (never resized beyond 1) to stay in 0..1."""
    w, h = min(w, 1.0), min(h, 1.0)
    x = min(max(0.0, cx - w / 2), 1.0 - w)
    y = min(max(0.0, cy - h / 2), 1.0 - h)
    return Rect(x, y, w, h)


def lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


@dataclass(frozen=True)
class MotionPlan:
    """Normalised motion for one shot (resolved once, evaluated per frame)."""
    type: str          # effective motion type actually rendered
    requested: str     # motion type in the timeline
    crop: Rect
    from_scale: float = 1.0
    to_scale: float = 1.0
    anchor: tuple[float, float] = (0.5, 0.5)
    from_offset: tuple[float, float] = (0.0, 0.0)
    to_offset: tuple[float, float] = (0.0, 0.0)
    warning: str | None = None

    @property
    def is_static(self) -> bool:
        return self.type in ("STATIC", "CROP_FOCUS") or (
            self.from_scale == self.to_scale and self.from_offset == self.to_offset)


def plan_motion(motion: dict | None, crop: dict | None) -> MotionPlan:
    motion = motion if isinstance(motion, dict) else {}
    c = _rect(crop, Rect(0.0, 0.0, 1.0, 1.0))
    c = clamp_inside(c.x + c.w / 2, c.y + c.h / 2, c.w, c.h)
    mtype = motion.get("type") or "STATIC"
    centre = (c.x + c.w / 2, c.y + c.h / 2)
    a = motion.get("anchor")
    anchor = (_num(a.get("x"), centre[0]), _num(a.get("y"), centre[1])) if isinstance(a, dict) else centre
    warning = None
    easing = motion.get("easing")
    if easing not in (None, "linear"):
        warning = f"easing '{easing}' not supported in R1; linear used"
    if mtype in ("SLOW_ZOOM_IN", "SLOW_ZOOM_OUT"):
        lo, hi = (1.0, _DEFAULT_ZOOM) if mtype == "SLOW_ZOOM_IN" else (_DEFAULT_ZOOM, 1.0)
        fs, ts = _num(motion.get("from_scale"), lo), _num(motion.get("to_scale"), hi)
        if fs < 1.0 or ts < 1.0:  # a scale < 1 would show outside the crop
            fs, ts = max(fs, 1.0), max(ts, 1.0)
            warning = "zoom scale < 1 clamped to 1"
        return MotionPlan(mtype, mtype, c, fs, ts, anchor, warning=warning)
    if mtype in _PAN:
        scale = max(1.0, _num(motion.get("scale"), _DEFAULT_PAN_SCALE))
        dv = _PAN_VEC[mtype]
        fo, to = motion.get("from_offset"), motion.get("to_offset")
        fo = (_num(fo.get("x"), dv[0]), _num(fo.get("y"), dv[1])) if isinstance(fo, dict) else (dv[0], dv[1])
        to = (_num(to.get("x"), dv[2]), _num(to.get("y"), dv[3])) if isinstance(to, dict) else (dv[2], dv[3])
        return MotionPlan(mtype, mtype, c, scale, scale, anchor, fo, to, warning=warning)
    if mtype == "CROP_FOCUS":
        fr = _rect(motion.get("focus_region"), c)
        fr = clamp_inside(fr.x + fr.w / 2, fr.y + fr.h / 2, fr.w, fr.h)
        return MotionPlan("CROP_FOCUS", mtype, fr, anchor=(fr.x + fr.w / 2, fr.y + fr.h / 2), warning=warning)
    if mtype != "STATIC":
        warning = f"motion '{mtype}' not supported in R1; rendered as STATIC"
    return MotionPlan("STATIC", mtype, c, anchor=centre, warning=warning)


def window_at(m: MotionPlan, p: float) -> Rect:
    """Visible source window at progress p (clamped to 0..1)."""
    p = 0.0 if p < 0 else 1.0 if p > 1 else p
    if m.type in ("STATIC", "CROP_FOCUS"):
        return m.crop
    s = lerp(m.from_scale, m.to_scale, p)
    ox, oy = lerp(m.from_offset[0], m.to_offset[0], p), lerp(m.from_offset[1], m.to_offset[1], p)
    w, h = m.crop.w / s, m.crop.h / s
    cx, cy = m.anchor[0] + ox * m.crop.w, m.anchor[1] + oy * m.crop.h
    return clamp_inside(cx, cy, w, h)


def letterbox(src_w: float, src_h: float, out_w: int, out_h: int) -> tuple[int, int, int, int]:
    """Largest rectangle of the source aspect that fits the output: (x, y, w, h) in output pixels."""
    scale = min(out_w / src_w, out_h / src_h)
    w = max(1, min(out_w, round(src_w * scale)))
    h = max(1, min(out_h, round(src_h * scale)))
    return (out_w - w) // 2, (out_h - h) // 2, w, h
