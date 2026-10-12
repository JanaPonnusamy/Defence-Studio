"""Phase 2 / F: the exported timeline carries everything a FUTURE renderer needs (this module never renders).
Validated on the representative synthetic 10-minute project."""
from __future__ import annotations

import pytest

from app.pipeline.motion import BASE_MOTIONS
from app.pipeline.timeline import TIMELINE_VERSION, validate_timeline
from fixtures.episode_loader import load_episode
from tests.conftest import wait_job

TRANSITIONS = {"CUT", "DISSOLVE", "FADE", "FADE_IN", "FADE_OUT", "WIPE"}


@pytest.fixture(scope="module")
def ten_minute_timeline(client):
    pid = client.post("/projects", json={"name": "contract-10min"}).json()["project_id"]
    ep = load_episode(client, pid)
    client.post("/scenes", json={"project_id": pid, "narration": ep["narration"], "replace_existing": True})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": pid, "options": {
        "generate_ai": True, "max_ai_images": 4, "output_width": 1920, "output_height": 1080, "frame_rate": 30}}
    ).json()["job_id"], timeout=300)
    assert done["status"] == "SUCCEEDED", done
    return client.get(f"/visual-plans/{done['result_id']}/timeline").json()


def test_contract_top_level(ten_minute_timeline):
    tl = ten_minute_timeline
    assert tl["schema"] == "video-visual-timeline" and tl["version"] == TIMELINE_VERSION == "1.1"
    assert validate_timeline(tl) == []
    assert tl["output"] == {"width": 1920, "height": 1080, "frame_rate": 30, "defined": True, "note": None}
    assert 540 <= tl["duration"] <= 720 and tl["timing"]["timing_source"] == "ESTIMATED"


def test_shots_are_ordered_contiguous_and_complete(ten_minute_timeline):
    tl = ten_minute_timeline
    shots = tl["shots"]
    assert len(shots) >= 100
    total = 0.0
    for i, s in enumerate(shots):
        assert s["order"] == i and s["duration"] > 0
        assert s["end"] - s["start"] == pytest.approx(s["duration"], abs=0.002)
        if i:
            assert s["start"] == pytest.approx(shots[i - 1]["end"], abs=1e-3)
        assert s["transition"] in TRANSITIONS and s["motion"]["type"] in BASE_MOTIONS
        assert {"x", "y", "w", "h"} <= set(s["crop"]) and isinstance(s["overlays"], list)
        assert s["frame_start"] == round(s["start"] * 30) and s["frame_end"] == round(s["end"] * 30)
        assert s["narration_anchor"]["segment_ids"] and s["status"] in ("READY", "GAP")
        total += s["duration"]
    assert total == pytest.approx(tl["duration"], abs=0.01)


def test_asset_refs_resolve_with_provenance(ten_minute_timeline):
    tl = ten_minute_timeline
    used = {s["asset_id"] for s in tl["shots"] if s["asset_id"]}
    assert used == set(tl["assets"]) and {a["asset_id"] for a in tl["attributions"]} == used
    for aid, ref in tl["assets"].items():
        for k in ("uri", "url", "mime_type", "width", "height", "generation_kind", "provenance_class",
                  "evidence_eligible", "rights_status", "rights_label", "checksum_sha256", "licence", "attribution",
                  "acquired_at", "provider", "is_fallback"):
            assert k in ref, (aid, k)
        assert ref["uri"].startswith("storage://") and ref["url"] == f"/assets/{aid}/file"
        assert ref["rights_status"] != "RESTRICTED"
    kinds = {r["provenance_class"] for r in tl["assets"].values()}
    assert "PLACEHOLDER" in kinds and "AI_GENERATED" not in kinds  # the local generator makes placeholders only


def test_generated_material_is_labelled_on_screen(ten_minute_timeline):
    tl = ten_minute_timeline
    for s in tl["shots"]:
        if not s["asset_id"]:
            continue
        ref = tl["assets"][s["asset_id"]]
        texts = " ".join(o["text"] for o in s["overlays"] if o["type"] == "editorial_label")
        if ref["provenance_class"] == "PLACEHOLDER":
            assert s["editorial_label"] == "PLACEHOLDER" and "NOT AI" in texts
        if ref["provenance_class"] in ("PLACEHOLDER", "AI_GENERATED", "TEST_FIXTURE"):
            assert s["editorial_label"] != "FACTUAL_EVIDENCE"


def test_output_unspecified_is_explicit(client, project):
    client.post("/scenes", json={"project_id": project, "narration": "Ships moved north.", "replace_existing": True})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project}).json()["job_id"])
    tl = client.get(f"/visual-plans/{done['result_id']}/timeline").json()
    assert tl["output"]["defined"] is False and tl["output"]["frame_rate"] is None and tl["output"]["note"]
    assert tl["shots"][0]["frame_start"] is None
    r = client.post("/visual-plans/build", json={"project_id": project, "options": {"output_width": 1920}})
    assert r.status_code == 422


def test_validator_rejects_gaps_and_disorder(ten_minute_timeline):
    import copy

    bad = copy.deepcopy(ten_minute_timeline)
    bad["shots"][5]["start"] += 0.5
    bad["shots"][5]["duration"] = round(bad["shots"][5]["end"] - bad["shots"][5]["start"], 3)
    assert any("gap" in e for e in validate_timeline(bad))
    bad = copy.deepcopy(ten_minute_timeline)
    bad["shots"][3]["order"] = 99
    assert any("order" in e for e in validate_timeline(bad))
