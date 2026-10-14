from __future__ import annotations

import random

import pytest

from renderer.errors import RendererError
from renderer.layout import (build_frame_plan, check_duration, clamp_transition_frames, expected_frame_count, locate,
                             shot_progress)
from renderer.motion import SUPPORTED_MOTIONS, Rect, letterbox, plan_motion, window_at
from renderer.synthetic import _motion
from renderer.timeline_rules import BASE_MOTIONS, validate_timeline

FULL = {"x": 0, "y": 0, "w": 1, "h": 1}


def inside(r: Rect) -> bool:
    return r.x >= -1e-9 and r.y >= -1e-9 and r.x + r.w <= 1 + 1e-9 and r.y + r.h <= 1 + 1e-9


@pytest.mark.parametrize("kind", sorted(SUPPORTED_MOTIONS))
def test_windows_deterministic_inside_source_and_aspect_preserving(kind):
    crop = {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.6}
    mp = plan_motion(_motion(kind, 5, random.Random(0)), crop)
    for p in (0, 0.13, 0.5, 0.99, 1):
        a, b = window_at(mp, p), window_at(mp, p)
        assert a == b and inside(a)
        if kind != "CROP_FOCUS":  # zoom/pan keep the crop's aspect ratio exactly
            assert a.w / a.h == pytest.approx(0.8 / 0.6, rel=1e-9)


def test_zoom_in_shrinks_window_zoom_out_grows_it():
    zin = plan_motion({"type": "SLOW_ZOOM_IN", "from_scale": 1.0, "to_scale": 1.2}, FULL)
    zout = plan_motion({"type": "SLOW_ZOOM_OUT", "from_scale": 1.2, "to_scale": 1.0}, FULL)
    assert window_at(zin, 1).w < window_at(zin, 0).w == pytest.approx(1.0)
    assert window_at(zout, 1).w > window_at(zout, 0).w


def test_pan_moves_in_the_named_direction():
    for kind, axis, sign in [("PAN_RIGHT", "x", 1), ("PAN_LEFT", "x", -1), ("PAN_DOWN", "y", 1), ("PAN_UP", "y", -1)]:
        mp = plan_motion(_motion(kind, 5, random.Random(0)), FULL)
        d = getattr(window_at(mp, 1), axis) - getattr(window_at(mp, 0), axis)
        assert d * sign > 0, kind


def test_static_and_crop_focus_do_not_move():
    for t in ("STATIC", "CROP_FOCUS"):
        mp = plan_motion({"type": t, "focus_region": {"x": 0.2, "y": 0.2, "w": 0.5, "h": 0.5}}, FULL)
        assert window_at(mp, 0) == window_at(mp, 1) and mp.is_static


@pytest.mark.parametrize("kind", ["PARALLAX", "HIGHLIGHT_REGION", "WHATEVER"])
def test_unsupported_motion_falls_back_to_static_with_warning(kind):
    mp = plan_motion({"type": kind}, {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5})
    assert mp.type == "STATIC" and mp.requested == kind and "not supported in R1" in mp.warning
    assert window_at(mp, 0.7).key() == Rect(0.1, 0.1, 0.5, 0.5).key()


def test_all_contract_motions_handled_without_exception():
    for kind in BASE_MOTIONS:
        assert inside(window_at(plan_motion({"type": kind}, FULL), 0.5))


def test_out_of_range_inputs_are_sanitised():
    mp = plan_motion({"type": "SLOW_ZOOM_IN", "from_scale": 0.2, "to_scale": 0.5},
                     {"x": 0.5, "y": 0.5, "w": 0.9, "h": 0.9})
    assert inside(window_at(mp, 0)) and inside(window_at(mp, 1)) and mp.warning
    assert inside(window_at(plan_motion({"type": "PAN_LEFT", "scale": 5, "anchor": {"x": 9, "y": -4}}, FULL), 0.5))


@pytest.mark.parametrize("sw,sh,ow,oh", [(1600, 900, 1920, 1080), (900, 1600, 1920, 1080), (1000, 1000, 1920, 1080),
                                          (3000, 500, 1280, 720), (320, 180, 1920, 1080), (1920, 1080, 1280, 720)])
def test_letterbox_never_stretches_and_fits(sw, sh, ow, oh):
    x, y, w, h = letterbox(sw, sh, ow, oh)
    assert 0 <= x and 0 <= y and x + w <= ow and y + h <= oh and (w == ow or h == oh)
    assert abs(w / h - sw / sh) <= 2.0 / min(w, h)  # aspect preserved up to pixel rounding
    assert abs((ow - w) // 2 - x) <= 1 and abs((oh - h) // 2 - y) <= 1


# ---- layout ---------------------------------------------------------------------------------------------------
def tl(durs, start=0.0, transitions=None):
    shots, t = [], start
    for i, d in enumerate(durs):
        shots.append({"order": i, "start": t, "end": round(t + d, 3), "duration": d, "status": "GAP",
                      "asset_id": None, "motion": {"type": "STATIC"}, "crop": FULL,
                      "transition": (transitions or {}).get(i, "CUT")})
        t = round(t + d, 3)
    return {"shots": shots}


def test_transition_clamped_to_half_the_shorter_shot():
    assert clamp_transition_frames(0.5, 25, 100, 100) == (12, False)
    assert clamp_transition_frames(0.5, 25, 100, 10) == (5, True)
    assert clamp_transition_frames(0.5, 25, 3, 100) == (1, True)
    assert clamp_transition_frames(0.5, 25, 1, 100) == (0, True)
    assert clamp_transition_frames(0, 25, 100, 100) == (0, False)
    assert clamp_transition_frames(2.0, 25, 40, 40) == (20, True)


def test_plan_applies_crossfades_and_flags_unknown_transitions():
    plan = build_frame_plan(tl([4, 4, 0.2, 4, 4], transitions={1: "DISSOLVE", 2: "FADE", 3: "WIPE", 4: "BOGUS"}),
                            16.2, 25, 0.5)
    by = {s.index: s for s in plan.shots}
    assert (by[1].transition, by[1].transition_frames) == ("DISSOLVE", 12)
    assert by[2].transition == "FADE" and by[2].transition_frames == 2  # 0.2 s = 5 frames -> half = 2
    assert (by[3].transition, by[3].transition_frames) == ("CUT", 0)
    assert (by[4].transition, by[4].transition_frames) == ("CUT", 0)
    assert {"TRANSITION_CLAMPED", "TRANSITION_UNSUPPORTED"} <= {w["code"] for w in plan.warnings}
    msgs = " ".join(w["message"] for w in plan.warnings if w["code"] == "TRANSITION_UNSUPPORTED")
    assert "WIPE" in msgs and "BOGUS" in msgs


def test_every_frame_maps_to_exactly_one_shot_and_total_matches_audio():
    plan = build_frame_plan(tl([3.3, 1.7, 5.0]), 10.0, 25, 0.5)
    assert plan.total_frames == 250 == expected_frame_count(10.0, 25)
    assert plan.shots[0].frame_start == 0 and plan.shots[-1].frame_end == 250
    for a, b in zip(plan.shots, plan.shots[1:]):
        assert a.frame_end == b.frame_start
    hint, seen = 0, []
    for f in range(250):
        hint = locate(plan, f, hint)
        assert plan.shots[hint].frame_start <= f < plan.shots[hint].frame_end
        seen.append(hint)
    assert seen == sorted(seen) and set(seen) == {0, 1, 2}


def test_hold_first_shot_before_start_and_last_shot_after_end():
    plan = build_frame_plan(tl([2, 2], start=1.5), 5.0, 10, 0.5)  # timeline 1.5..5.5 vs audio 5.0
    s0, s1 = plan.shots
    assert s0.frame_start == 0 and shot_progress(s0, 0, 10) == 0.0 and shot_progress(s0, 14, 10) == 0.0
    assert shot_progress(s1, 49, 10) < 1.0
    plan = build_frame_plan(tl([2, 2]), 4.3, 10, 0.5)  # audio longer than timeline: hold the last shot
    assert plan.shots[-1].frame_end == 43 and shot_progress(plan.shots[-1], 42, 10) == 1.0


def test_trailing_shots_beyond_narration_are_dropped_with_warning():
    plan = build_frame_plan(tl([2, 2, 2]), 3.9, 10, 0.5)  # third shot (4.0 s) starts after the audio ends
    assert len(plan.shots) == 2 and plan.shots[-1].frame_end == 39
    assert any(w["code"] == "DURATION_MISMATCH_WARNING" for w in plan.warnings)


@pytest.mark.parametrize("tl_end,audio,expect", [(20, 20, None), (20.4, 20, None), (20.6, 20, "warn"),
                                                 (19.4, 20, "warn"), (25.0, 20, "warn"), (25.1, 20, "reject"),
                                                 (14.9, 20, "reject")])
def test_duration_mismatch_rules(tl_end, audio, expect):
    if expect == "reject":
        with pytest.raises(RendererError) as e:
            check_duration(tl_end, audio, 0.5, 5.0)
        assert e.value.code == "DURATION_MISMATCH"
    else:
        assert (check_duration(tl_end, audio, 0.5, 5.0) is not None) == (expect == "warn")


def test_plan_rejects_big_mismatch_before_rendering():
    with pytest.raises(RendererError) as e:
        build_frame_plan(tl([5, 5]), 30.0, 25, 0.5)
    assert e.value.code == "DURATION_MISMATCH"


# ---- consumer-side timeline rules (re-implementation) -------------------------------------------------------------
def _valid_tl():
    t = tl([2, 2])
    t.update(schema="video-visual-timeline", version="1.1", visual_plan_id="p", project_id="x", duration=4, assets={})
    for s in t["shots"]:
        s.update(shot_id="s", scene_id="c", visual_type="MAP", source_type="X", editorial_label="UNKNOWN",
                 narration_anchor={}, gap_code="G")
    return t


def test_validate_timeline_flags_the_documented_problems():
    ok = _valid_tl()
    assert validate_timeline(ok) == []
    assert "schema/version mismatch" in validate_timeline({**ok, "version": "9"})
    assert validate_timeline({"schema": "x"})[0].startswith("missing top-level")
    ok["shots"][1]["order"] = 5
    assert any("not sequential" in e for e in validate_timeline(ok))


def test_validate_timeline_ai_and_placeholder_labelling():
    t = _valid_tl()
    t["shots"][0].update(status="READY", asset_id="a", gap_code=None, editorial_label="FACTUAL_EVIDENCE", overlays=[])
    t["assets"]["a"] = {"asset_id": "a", "source_type": "AI_GENERATED", "provenance_class": "AI_GENERATED"}
    errs = " | ".join(validate_timeline(t))
    assert "not labelled AI_GENERATED" in errs and "lacks the required on-screen AI label" in errs
    assert "non-original" in errs
