"""Timeline -> frame plan: which shot (and progress) every output frame shows, and transitions.

Video covers [0, narration duration]. Frames before the first shot hold the first shot (progress 0);
frames after the last shot hold the last (progress 1). Shot boundaries use round(start * fps), the
same convention as the timeline's own frame_start/frame_end.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from renderer.errors import RendererError
from renderer.motion import MotionPlan, plan_motion

CROSSFADE = {"FADE", "DISSOLVE"}
CUT_LIKE = {"CUT"}


@dataclass
class Warn:
    """Aggregated warning: one entry per (code, key) with a count and the first few shot orders."""
    code: str
    message: str
    count: int = 0
    shots: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "count": self.count, "shots": self.shots[:10]}


class WarnBag:
    def __init__(self):
        self._w: dict[tuple, Warn] = {}

    def add(self, code: str, message: str, shot: int | None = None, key: str = "") -> None:
        w = self._w.setdefault((code, key), Warn(code, message))
        w.count += 1
        if shot is not None and len(w.shots) < 10:
            w.shots.append(shot)

    def extend(self, items: list[dict]) -> None:
        for it in items:
            w = self._w.setdefault((it["code"], it["message"]), Warn(it["code"], it["message"]))
            w.count += it.get("count", 1)

    def as_list(self) -> list[dict]:
        return [w.as_dict() for w in self._w.values()]


@dataclass
class ShotPlan:
    index: int
    shot: dict
    status: str
    asset_id: str | None
    start: float
    end: float
    frame_start: int          # first output frame showing this shot (inclusive)
    frame_end: int            # exclusive
    motion: MotionPlan
    transition: str           # effective: CUT | FADE | DISSOLVE
    transition_frames: int    # crossfade length in frames (0 for CUT)

    @property
    def frames(self) -> int:
        return self.frame_end - self.frame_start


@dataclass
class FramePlan:
    fps: float
    total_frames: int
    audio_duration: float
    timeline_start: float
    timeline_end: float
    shots: list[ShotPlan]
    warnings: list[dict]


def check_duration(timeline_end: float, audio_duration: float, warn_s: float, reject_s: float) -> dict | None:
    """Return a warning dict when |timeline end - audio| > warn_s; raise when > reject_s."""
    diff = timeline_end - audio_duration
    if abs(diff) > reject_s:
        raise RendererError(
            "DURATION_MISMATCH",
            f"timeline ends at {timeline_end:.3f}s but narration is {audio_duration:.3f}s "
            f"(difference {abs(diff):.3f}s > {reject_s:g}s). Wrong narration for this timeline?")
    if abs(diff) > warn_s:
        word = "longer" if diff > 0 else "shorter"
        return {"code": "DURATION_MISMATCH_WARNING", "count": 1,
                "message": f"timeline is {abs(diff):.3f}s {word} than the narration "
                           f"({timeline_end:.3f}s vs {audio_duration:.3f}s); "
                           f"{'trailing shots are cut' if diff > 0 else 'the last shot is held'}"}
    return None


def clamp_transition_frames(requested_s: float, fps: float, prev_frames: int, cur_frames: int) -> tuple[int, bool]:
    """Crossfade length in frames, clamped to half of the shorter shot. Returns (frames, was_clamped)."""
    want = max(0, int(round(requested_s * fps)))
    cap = min(prev_frames, cur_frames) // 2
    n = min(want, cap)
    return n, n < want


def build_frame_plan(timeline: dict, audio_duration: float, fps: float, transition_seconds: float,
                     warn_s: float = 0.5, reject_s: float = 5.0) -> FramePlan:
    shots = timeline["shots"]
    bag = WarnBag()
    tl_start, tl_end = float(shots[0]["start"]), float(shots[-1]["end"])
    dw = check_duration(tl_end, audio_duration, warn_s, reject_s)
    if dw:
        bag.extend([dw])
    total = max(1, int(round(audio_duration * fps)))

    bounds = [0]
    for s in shots[1:]:
        bounds.append(min(total, max(bounds[-1], int(round(float(s["start"]) * fps)))))
    bounds.append(total)

    plans: list[ShotPlan] = []
    for i, s in enumerate(shots):
        fs, fe = bounds[i], bounds[i + 1]
        if fe <= fs:
            if float(s["start"]) < audio_duration:
                bag.add("SHOT_TOO_SHORT", "shot shorter than one output frame (or beyond the narration); not rendered",
                        i, key="short")
            continue
        mp = plan_motion(s.get("motion"), s.get("crop"))
        if mp.warning:
            bag.add("MOTION_FALLBACK", mp.warning, i, key=mp.warning)
        plans.append(ShotPlan(i, s, s["status"], s.get("asset_id"), float(s["start"]), float(s["end"]), fs, fe,
                              mp, "CUT", 0))
    if not plans:
        raise RendererError("INVALID_PACKAGE", "no shot overlaps the narration duration")
    plans[0].frame_start = 0  # hold the first shot before its own start

    for k in range(1, len(plans)):
        cur, prev = plans[k], plans[k - 1]
        t = cur.shot.get("transition")
        if t in CROSSFADE:
            n, clamped = clamp_transition_frames(transition_seconds, fps, prev.frames, cur.frames)
            if n >= 1:
                cur.transition, cur.transition_frames = t, n
                if clamped:
                    bag.add("TRANSITION_CLAMPED", "transition shortened to half the shorter shot", cur.index,
                            key="clamp")
            else:
                bag.add("TRANSITION_CLAMPED", "transition dropped: shot too short for a crossfade (CUT used)",
                        cur.index, key="drop")
        elif t not in CUT_LIKE:
            bag.add("TRANSITION_UNSUPPORTED", f"transition '{t}' not supported in R1; CUT used", cur.index, key=str(t))
    return FramePlan(fps, total, audio_duration, tl_start, tl_end, plans, bag.as_list())


def shot_progress(sp: ShotPlan, frame: int, fps: float) -> float:
    """Motion progress 0..1 for an output frame, from real time (held before start / after end)."""
    dur = sp.end - sp.start
    if dur <= 0:
        return 0.0
    p = (frame / fps - sp.start) / dur
    return 0.0 if p < 0 else 1.0 if p > 1 else p


def locate(plan: FramePlan, frame: int, hint: int = 0) -> int:
    """Index into plan.shots for an output frame (monotone `hint` makes sequential access O(1))."""
    i = min(hint, len(plan.shots) - 1)
    while i < len(plan.shots) - 1 and frame >= plan.shots[i].frame_end:
        i += 1
    while i > 0 and frame < plan.shots[i].frame_start:
        i -= 1
    return i


def expected_frame_count(duration: float, fps: float) -> int:
    return int(math.floor(duration * fps + 0.5))
