"""Planning tests (spec §33): scene parsing, visual intent, asset selection, shot timing, reuse, motion."""
from __future__ import annotations

import pytest

from app.errors import VideoModuleError
from app.pipeline.intent import classify_segment, derive_visual_intents
from app.pipeline.motion import compute_crop, ladder_for, plan_motion
from app.pipeline.scene_parser import parse_narration, scenes_from_timed_segments, split_sentences
from fixtures.synthetic import SAMPLE_NARRATION
from tests.conftest import import_png, wait_job


# ------------------------------------------------------------------ scene parser
def test_sentence_split_and_estimated_timing():
    assert split_sentences("One two. Three four! Five six? Seven") == ["One two.", "Three four!", "Five six?", "Seven"]
    scenes = parse_narration("First sentence here. Second one follows.\n\nNew paragraph now.", wpm=150)
    assert len(scenes) == 2
    segs = scenes[0].segments + scenes[1].segments
    assert [s.segment_id for s in segs] == ["seg-0001", "seg-0002", "seg-0003"]
    for a, b in zip(segs, segs[1:]):
        assert b.start == pytest.approx(a.end)
    assert scenes[0].timing_source == "ESTIMATED" and scenes[0].importance == "HIGH"
    # 3 words at 150 wpm = 1.2s (+0.3 pause) -> floor of 1.2 applies to shorter sentences
    assert segs[0].end - segs[0].start == pytest.approx(1.5)


def test_long_paragraph_is_chunked_into_scenes():
    text = " ".join(f"Sentence number {i} is here." for i in range(20))
    scenes = parse_narration(text)
    assert len(scenes) == 3 and all(len(s.segments) <= 8 for s in scenes)


def test_timed_segments_contract_and_validation():
    segs = [{"text": "A first sentence.", "start": 0.0, "end": 2.5, "scene": "Intro"},
            {"text": "Second.", "start": 2.5, "end": 4.0, "scene": "Intro"},
            {"text": "Third in a new scene.", "start": 4.2, "end": 7.0, "scene": "Body"}]
    scenes = scenes_from_timed_segments(segs)
    assert [s.title for s in scenes] == ["Intro", "Body"]
    assert scenes[1].start_time == 4.2 and scenes[1].timing_source == "SUPPLIED"  # "PROVIDED" in v1.0
    for bad, code in (([{"text": "x", "start": 2, "end": 1}], "INVALID_TIMING"),
                      ([{"text": "", "start": 0, "end": 1}], "MISSING_NARRATION_ANCHOR"),
                      ([{"text": "a", "start": 0, "end": 2}, {"text": "b", "start": 1, "end": 3}], "INVALID_TIMING"),
                      ([{"start": 0, "end": 1}], "MISSING_NARRATION_ANCHOR"), ([], "MISSING_NARRATION_ANCHOR")):
        with pytest.raises(VideoModuleError) as e:
            scenes_from_timed_segments(bad)
        assert e.value.code == code
    with pytest.raises(VideoModuleError):
        parse_narration("   ")


# ------------------------------------------------------------------ intent
@pytest.mark.parametrize("text,intent", [
    ("The fleet is concentrated near the Black Sea.", "GEOGRAPHIC_CONTEXT"),
    ("Officials say 40 warships have been deployed.", "STATISTIC"),
    ("According to a leaked document, the order was signed.", "EVIDENCE"),
    ("The missile system uses a mobile radar.", "HARDWARE_DETAIL"),
    ("Compared to last year, the force is larger.", "COMPARISON"),
    ("In conclusion, tensions remain high.", "CONCLUSION"),
    ("The attack escalated overnight.", "ESCALATION"),
    ("Something happened.", "EVENT_EXPLANATION"),
])
def test_intent_classification(text, intent):
    c = classify_segment(text)
    assert c["primary_intent"] == intent


def test_spec_example_black_sea_prefers_map():
    c = classify_segment("The fleet is concentrated near the Black Sea.")
    assert c["preferred_visuals"][0] == "MAP" and c["ai_allowed"] is False
    assert "Black Sea" in c["entities"]
    scene_level = derive_visual_intents("The fleet is concentrated near the Black Sea.")
    assert scene_level[0]["preferred_visual"] == "MAP" and scene_level[0]["fallback_visual"] == "SATELLITE"


def test_years_are_not_statistics_and_no_number_no_statistic():
    assert classify_segment("The plan began in 2024.")["numbers"] == []
    assert classify_segment("The number of units is unclear.")["primary_intent"] != "STATISTIC"


# ------------------------------------------------------------------ helpers
def _build(client, project, **options):
    j = client.post("/visual-plans/build", json={"project_id": project, "options": options}).json()
    done = wait_job(client, j["job_id"])
    assert done["status"] == "SUCCEEDED", done
    return client.get(f"/shots/{done['result_id']}").json()["shots"], client.get(f"/visual-plans/{done['result_id']}").json()


def _scenes(client, project, narration=SAMPLE_NARRATION):
    r = client.post("/scenes", json={"project_id": project, "narration": narration, "replace_existing": True})
    assert r.status_code == 201, r.text
    return r.json()["scenes"]


# ------------------------------------------------------------------ selection + editorial safety
def test_selection_priority_and_evidence_never_ai(client, project):
    import_png(client, project, seed=1001, title="Kessan Strait fleet photo", meta={"tags": ["fleet", "kessan", "strait"]})
    # an existing AI image in the project must NOT be used for evidence/statistics/geography
    import_png(client, project, seed=1002, asset_type="AI_IMAGE", source_type="AI_GENERATED", rights_status="PERMITTED",
               title="AI document scene", meta={"tags": ["document", "deployment", "order", "warships"]})
    _scenes(client, project)
    shots, plan = _build(client, project)
    by_intent = {s["intent"]: s for s in shots}
    ev = by_intent["EVIDENCE"]
    assert ev["status"] == "GAP" and ev["asset_id"] is None and ev["gap_code"] == "SOURCE_UNAVAILABLE"
    assert "AI substitution refused" in ev["editorial_reason"]
    st = by_intent["STATISTIC"]
    assert st["status"] == "GAP"  # no stat graphic with the number 40 -> never invented
    geo = by_intent["GEOGRAPHIC_CONTEXT"]
    assert geo["source_type"] == "EXTERNAL_SOURCE"  # priority 1 source, no map available -> photo fallback
    for s in shots:
        if s["intent"] in ("EVIDENCE", "STATISTIC", "GEOGRAPHIC_CONTEXT", "LOCATION", "TIMELINE", "COMPARISON"):
            assert s["source_type"] != "AI_GENERATED"
    assert any("GAP" in w for w in plan["warnings"])


def test_restricted_and_unknown_rights(client, project):
    import_png(client, project, seed=1101, rights_status="RESTRICTED", licence=None, title="restricted fleet",
               meta={"tags": ["fleet", "kessan", "strait"]})
    import_png(client, project, seed=1102, rights_status="UNKNOWN", licence=None, title="unknown fleet",
               meta={"tags": ["fleet", "kessan", "strait"]})
    _scenes(client, project, "The fleet is concentrated near the Kessan Strait this week.")
    shots, _ = _build(client, project)
    assert shots[0]["status"] == "GAP" and shots[0]["gap_code"] == "RIGHTS_UNKNOWN"
    shots, plan = _build(client, project, allow_unknown_rights=True)
    assert shots[0]["status"] == "READY"
    asset = client.get(f"/assets/{shots[0]['asset_id']}").json()
    assert asset["rights_status"] == "UNKNOWN"  # never the RESTRICTED one
    assert any(o["type"] == "rights_warning" for o in shots[0]["overlays"])
    assert any("UNKNOWN rights" in w for w in plan["warnings"])


def test_stat_graphic_only_when_number_matches(client, project):
    spec = {"graphic_type": "STAT_CARD", "title": "Warships deployed", "data": [
        {"label": "warships", "value": 40, "unit": "ships", "source": "MoD", "as_of": "2026-01-01", "confidence": "OFFICIAL"}]}
    assert client.post("/assets/stats", json={"project_id": project, "spec": spec, "tags": ["warships"]}).status_code == 201
    _scenes(client, project, "Officials say 40 warships have been deployed. Officials say 55 warships may follow next month.")
    shots, _ = _build(client, project)
    stat_shots = [s for s in shots if s["intent"] == "STATISTIC"]
    assert stat_shots[0]["visual_type"] == "STAT_CARD" and stat_shots[0]["status"] == "READY"
    assert stat_shots[-1]["status"] == "GAP"  # 55 != 40: the card is never reused for a different figure


def test_photo_fallback_for_data_intents_is_opt_in_and_strictly_relevant(client, project):
    import_png(client, project, seed=1111, title="warships photo", meta={"tags": ["warships", "deployed"]})
    import_png(client, project, seed=1112, title="mountain", meta={"tags": ["mountain", "snow"]})
    _scenes(client, project, "Officials say 40 warships have been deployed.")
    shots, _ = _build(client, project)
    assert shots[0]["intent"] == "STATISTIC" and shots[0]["status"] == "GAP"   # default: no fake stat visual
    shots, _ = _build(client, project, photo_fallback_for_data_intents=True)
    assert shots[0]["status"] == "READY" and shots[0]["visual_type"] == "SOURCE_PHOTO"
    assert client.get(f"/assets/{shots[0]['asset_id']}").json()["title"] == "warships photo"
    _scenes(client, project, "Officials say 40 tanks have been counted.")              # no relevant photo
    shots, _ = _build(client, project, photo_fallback_for_data_intents=True)
    assert shots[0]["status"] == "GAP"                                               # unrelated photo never used


# ------------------------------------------------------------------ timing, narration-driven changes, reuse, motion
def test_shot_timing_is_contiguous_and_anchored(client, project):
    import_png(client, project, seed=1201, title="fleet photo", meta={"tags": ["fleet", "missile", "talon", "system"]})
    scenes = _scenes(client, project)
    shots, plan = _build(client, project)
    assert shots[0]["start"] == pytest.approx(scenes[0]["start_time"])
    assert shots[-1]["end"] == pytest.approx(scenes[-1]["end_time"])
    for a, b in zip(shots, shots[1:]):
        assert b["start"] == pytest.approx(a["end"], abs=1e-3)
    seg_ids = {g["segment_id"] for s in scenes for g in s["narration_segments"]}
    for s in shots:
        assert s["duration"] > 0 and s["narration_anchor"]["segment_ids"]
        assert set(s["narration_anchor"]["segment_ids"]) <= seg_ids
        assert s["narration_anchor"]["start"] <= s["end"] and s["narration_anchor"]["end"] >= s["start"]
    assert plan["stats"]["visual_change_count"] >= 4


def test_changes_are_narration_driven_not_fixed_interval(client, project):
    # many short sentences on the same subject -> few cuts; a sentence introducing a statistic -> a cut there
    import_png(client, project, seed=1301, title="fleet", meta={"tags": ["fleet"]})
    narration = "The fleet sailed. It moved slowly. It waited. Officials say 12 ships joined it later that day."
    scenes = _scenes(client, project, narration)
    shots, _ = _build(client, project)
    segs = scenes[0]["narration_segments"]
    stat_seg = segs[3]
    cut_times = [s["start"] for s in shots if not s["change_reason"].startswith("reframe")]
    assert any(abs(t - stat_seg["start"]) < 1e-3 for t in cut_times)
    assert not any(abs(t - segs[1]["start"]) < 1e-3 for t in cut_times)  # short connective sentence: no cut


def test_one_asset_many_meaningful_shots(client, project):
    import_png(client, project, seed=1401, w=1280, h=720, title="harbour photo", meta={"tags": ["harbour", "ships", "port"],
               "regions": [{"x": 0.6, "y": 0.5, "w": 0.2, "h": 0.2, "label": "pier"}]})
    long_text = " ".join(["The ships in the harbour are preparing for a long journey across the open water."] * 6)
    _scenes(client, project, long_text)
    shots, plan = _build(client, project, max_shot_seconds=3.0, min_shot_seconds=1.5)
    same = [s for s in shots if s["asset_id"]]
    assert len({s["asset_id"] for s in same}) == 1 and len(same) >= 5
    framings = [(s["crop"]["composition"], s["motion"]["type"], s["crop"]["x"], s["crop"]["w"]) for s in same]
    for a, b in zip(framings, framings[1:]):
        assert a != b, "consecutive reuse must be meaningfully reframed"
    assert len(set(framings)) >= 4
    assert [s["reuse_index"] for s in same] == list(range(len(same)))
    assert plan["stats"]["fallback_duration_splits"] > 0


def test_motion_is_deterministic_and_renderer_neutral(client, project):
    import_png(client, project, seed=1501, title="fleet", meta={"tags": ["fleet", "kessan", "strait", "missile"]})
    _scenes(client, project)
    a, _ = _build(client, project)
    b, _ = _build(client, project)
    strip = lambda shots: [(s["start"], s["end"], s["asset_id"], s["motion"], s["crop"], s["framing"]) for s in shots]  # noqa: E731
    assert strip(a) == strip(b)
    for s in a:
        m = s["motion"]
        assert set(m) <= {"type", "easing", "duration", "from_scale", "to_scale", "anchor", "scale", "from_offset", "to_offset",
                          "focus_region", "region", "style", "label", "layers", "depth", "direction", "fallback",
                          "downgraded_from", "reason", "clip_segment"}
        assert not any("ffmpeg" in str(v).lower() for v in m.values())


def test_crop_preserves_aspect_and_protected_regions():
    meta = {"protected_regions": [{"x": 0.02, "y": 0.02, "w": 0.1, "h": 0.1, "label": "logo"}], "focus_point": {"x": 0.8, "y": 0.8}}
    for comp in ("wide", "medium", "close", "subject", "region"):
        c = compute_crop(comp, meta)
        assert c["w"] == c["h"] and c["aspect_preserved"] is True
        pr = meta["protected_regions"][0]
        assert c["x"] <= pr["x"] and c["y"] <= pr["y"]
        assert c["x"] + c["w"] >= pr["x"] + pr["w"] and c["y"] + c["h"] >= pr["y"] + pr["h"]


def test_motion_downgraded_when_it_would_cut_protected_region():
    meta = {"protected_regions": [{"x": 0.0, "y": 0.0, "w": 0.06, "h": 0.06, "label": "hull number"}]}
    crop = compute_crop("wide", meta)
    m = plan_motion("SOURCE_PHOTO", "SLOW_ZOOM_IN", crop, 4.0, meta)
    assert m["type"] == "STATIC" and m["downgraded_from"] == "SLOW_ZOOM_IN"
    m = plan_motion("SOURCE_PHOTO", "SLOW_ZOOM_IN", crop, 4.0, {})
    assert m["type"] == "SLOW_ZOOM_IN" and m["from_scale"] == 1.0 and m["to_scale"] == 1.08


def test_documents_and_stat_cards_use_non_distorting_motion():
    for vt in ("DOCUMENT", "STAT_CARD", "CHART"):
        for _comp, motion in ladder_for(vt):
            assert motion in ("STATIC", "CROP_FOCUS", "HIGHLIGHT_REGION", "SLOW_ZOOM_IN", "PAN_DOWN")
    m = plan_motion("STAT_CARD", "SLOW_ZOOM_IN", compute_crop("wide", {}), 3.0)
    assert m["to_scale"] <= 1.04


def test_build_without_scenes_fails_explicitly(client, project):
    r = client.post("/visual-plans/build", json={"project_id": project})
    assert r.status_code == 422 and r.json()["error_code"] == "MISSING_NARRATION_ANCHOR"


def test_tamil_words_keep_vowel_signs_and_match_assigned_keywords():
    from app.pipeline.intent import content_tokens
    narration = content_tokens("இந்த ஒப்புதலின் 82 சதவீதம் இந்திய தொழில்துறையிலிருந்து")
    assert len(narration) >= 4 and all(len(t) >= 3 for t in narration)
    assert narration & content_tokens("சதவீதம் தொழில்துறையிலிருந்து")
    assert content_tokens("café u-boat S-400") == content_tokens("cafe u-boat S-400")


def test_fill_crop_matches_output_aspect_and_respects_protected_regions():
    from app.pipeline.motion import FILL_PHOTO_LADDER, compute_crop, fill_crop, plan_motion
    full = compute_crop("wide", {})
    # 4:3 photo into a 16:9 frame: full width, reduced height, centred
    c = fill_crop(full, 1600, 1200, 1920, 1080, [])
    assert c["fill"] and c["w"] == 1.0 and abs((c["w"] * 1600) / (c["h"] * 1200) - 16 / 9) < 0.01
    assert abs(c["y"] - (1 - c["h"]) / 2) < 1e-3
    # a protected caption at the very top would be cut -> keep the full frame
    assert fill_crop(full, 1600, 1200, 1920, 1080, [{"x": 0.1, "y": 0.0, "w": 0.3, "h": 0.05}]) == full
    # already 16:9 -> unchanged; and every fill-ladder step moves
    assert fill_crop(full, 1920, 1080, 1920, 1080, []) == full
    assert all(m != "STATIC" for _, m in FILL_PHOTO_LADDER)
    assert plan_motion("SOURCE_PHOTO", "SLOW_ZOOM_IN", c, 4.0)["type"] == "SLOW_ZOOM_IN"
