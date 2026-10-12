"""Phase 2 / E: timed narration. Supplied timings are used and labelled SUPPLIED; estimated ones ESTIMATED;
malformed / overlapping timing is rejected with clear errors; gaps are closed by holding the visual."""
from __future__ import annotations

import time

import pytest

from app.pipeline.timeline import validate_timeline
from tests.conftest import import_png, wait_job


def _build(client, project, **opts):
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project, "options": opts}).json()["job_id"],
                    timeout=300)
    assert done["status"] == "SUCCEEDED", done
    return client.get(f"/visual-plans/{done['result_id']}/timeline").json()


def _contiguous(tl):
    for a, b in zip(tl["shots"], tl["shots"][1:]):
        assert b["start"] == pytest.approx(a["end"], abs=1e-3)
    assert validate_timeline(tl) == []


def test_supplied_segments_are_used_exactly_and_labelled(client, project):
    import_png(client, project, seed=7001, title="fleet photo", meta={"tags": ["fleet"]})
    segs = [{"text": "The fleet left port at dawn.", "start": 1.0, "end": 3.2, "segment_id": "a1"},
            {"text": "It moved north along the coast.", "start": 3.2, "end": 6.0, "segment_id": "a2"},
            {"text": "By noon it had stopped.", "start": 8.5, "end": 10.0, "segment_id": "a3"}]  # 2.5 s pause
    sc = client.post("/scenes", json={"project_id": project, "segments": segs, "replace_existing": True}).json()
    assert all(s["timing_source"] == "SUPPLIED" for s in sc["scenes"])
    assert all(g["timing_source"] == "SUPPLIED" for s in sc["scenes"] for g in s["narration_segments"])
    tl = _build(client, project)
    assert tl["timing"]["timing_source"] == "SUPPLIED" and tl["timing"]["estimated"] is False
    assert tl["start"] == 1.0 and tl["shots"][0]["start"] == 1.0 and tl["shots"][-1]["end"] == 10.0
    assert all(s["narration_anchor"]["timing_source"] == "SUPPLIED" for s in tl["shots"])
    _contiguous(tl)  # the 2.5 s pause does not leave a hole in the visual timeline
    held = [s for s in tl["shots"] if s["narration_anchor"].get("hold_extended_s")]
    assert held and tl["timing"]["gap_seconds_closed"] == pytest.approx(2.5, abs=1e-3)


def test_estimated_timing_is_labelled(client, project):
    client.post("/scenes", json={"project_id": project, "narration": "Ships moved. Then they stopped.", "replace_existing": True})
    tl = _build(client, project)
    assert tl["timing"]["timing_source"] == "ESTIMATED" and tl["timing"]["estimated"] is True
    assert all(s["narration_anchor"]["timing_source"] == "ESTIMATED" for s in tl["shots"])
    plan = client.get("/visual-plans", params={"project_id": project}).json()[0]
    assert any("ESTIMATED" in w for w in plan["warnings"])


def test_very_short_narration(client, project):
    sc = client.post("/scenes", json={"project_id": project, "narration": "Yes.", "replace_existing": True}).json()
    assert sc["scene_count"] == 1 and sc["scenes"][0]["duration"] > 0
    tl = _build(client, project)
    assert len(tl["shots"]) == 1 and tl["shots"][0]["duration"] > 0
    sc = client.post("/scenes", json={"project_id": project, "replace_existing": True,
                                      "segments": [{"text": "Go.", "start": 0.0, "end": 0.3}]}).json()
    tl = _build(client, project)
    assert tl["duration"] == pytest.approx(0.3) and tl["shots"][0]["duration"] == pytest.approx(0.3)


def test_long_narration_plans_quickly(client, project):
    sentence = "Naval forces moved along the northern coast as analysts tracked the convoy closely."
    paras = ["\n".join([sentence] * 6) for _ in range(60)]  # 360 sentences, ~35 min estimated
    t0 = time.perf_counter()
    sc = client.post("/scenes", json={"project_id": project, "narration": "\n\n".join(paras), "replace_existing": True}).json()
    assert sc["scenes"][-1]["end_time"] > 1800
    tl = _build(client, project)
    elapsed = time.perf_counter() - t0
    assert len(tl["shots"]) > 300 and elapsed < 30, elapsed
    _contiguous(tl)


@pytest.mark.parametrize("segments,code", [
    ([{"text": "a b c"}], "MISSING_NARRATION_ANCHOR"),                                   # missing timestamps
    ([{"text": "a", "start": 0.0}], "MISSING_NARRATION_ANCHOR"),
    ([{"text": "a", "start": 0, "end": 2}, {"text": "b", "start": 1.5, "end": 3}], "INVALID_TIMING"),  # overlap 0.5 s
    ([{"text": "a", "start": 2, "end": 4}, {"text": "b", "start": 0, "end": 1}], "INVALID_TIMING"),    # out of order
    ([{"text": "a", "start": 1, "end": 1}], "INVALID_TIMING"),                           # zero duration
    ([{"text": "a", "start": -1, "end": 1}], "INVALID_TIMING"),                          # negative
    ([{"text": "a", "start": 0, "end": 0.01}], "INVALID_TIMING"),                        # below 50 ms
    ([{"text": "one two three four five six seven eight", "start": 0, "end": 0.2}], "INVALID_TIMING"),  # 40 words/s
    ([{"text": "a", "start": 0, "end": 1, "segment_id": "x"},
      {"text": "b", "start": 1, "end": 2, "segment_id": "x"}], "INVALID_TIMING"),          # duplicate id
    ([{"text": "a", "start": "soon", "end": 2}], "VALIDATION_ERROR"),                     # malformed type (schema)
])
def test_malformed_or_overlapping_timing_is_rejected(client, project, segments, code):
    r = client.post("/scenes", json={"project_id": project, "segments": segments, "replace_existing": True})
    assert r.status_code == 422, r.text
    assert r.json()["error_code"] == code


def test_non_finite_timing_rejected():
    from app.errors import VideoModuleError
    from app.pipeline.scene_parser import scenes_from_timed_segments

    for bad in (float("nan"), float("inf")):
        with pytest.raises(VideoModuleError) as e:
            scenes_from_timed_segments([{"text": "a", "start": 0, "end": bad}])
        assert e.value.code == "INVALID_TIMING"


def test_tiny_overlap_is_normalised_and_recorded(client, project):
    segs = [{"text": "First part.", "start": 0.0, "end": 2.004}, {"text": "Second part.", "start": 2.0, "end": 4.0}]
    sc = client.post("/scenes", json={"project_id": project, "segments": segs, "replace_existing": True}).json()
    s2 = sc["scenes"][0]["narration_segments"][1]
    assert s2["start"] == pytest.approx(2.004) and s2["normalized"]["start_moved_by_s"] == pytest.approx(0.004)
    _contiguous(_build(client, project))
