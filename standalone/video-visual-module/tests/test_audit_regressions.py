"""Independent audit (2026-10-12) regression tests. Mocked HTTP only; no network, no key, no paid calls."""
from __future__ import annotations

import pytest

from app.config import Settings, get_settings
from app.errors import VideoModuleError
from app.generation.service import generate_image
from tests.conftest import import_png, wait_job
from tests.test_openai_provider import _req, err, ok_response, openai_mock  # noqa: F401  (fixture re-export)


# ---- defect 1: a rejected replace_existing request must not destroy existing scenes
@pytest.mark.parametrize("bad", [
    [{"text": "a b", "start": 2.0, "end": 1.0}],                                          # end <= start
    [{"text": "a b", "start": 0.0, "end": 2.0}, {"text": "c d", "start": 1.0, "end": 3.0}],  # overlap
    [{"text": "a b", "start": 0.0}],                                                       # missing anchor
])
def test_rejected_replace_existing_keeps_existing_scenes(client, project, bad):
    r = client.post("/scenes", json={"project_id": project, "narration": "The frigate left the harbour. It sailed north."})
    assert r.status_code == 201
    before = sorted(s["scene_id"] for s in client.get("/scenes", params={"project_id": project}).json())
    r = client.post("/scenes", json={"project_id": project, "replace_existing": True, "segments": bad})
    assert r.status_code == 422 and r.json()["error_code"] in ("INVALID_TIMING", "MISSING_NARRATION_ANCHOR")
    after = sorted(s["scene_id"] for s in client.get("/scenes", params={"project_id": project}).json())
    assert after == before  # nothing deleted by a rejected request


def test_valid_replace_existing_still_replaces(client, project):
    client.post("/scenes", json={"project_id": project, "narration": "One. Two. Three."})
    r = client.post("/scenes", json={"project_id": project, "replace_existing": True,
                                     "segments": [{"text": "The frigate left.", "start": 0.0, "end": 2.0}]})
    assert r.status_code == 201
    scenes = client.get("/scenes", params={"project_id": project}).json()
    assert [s["scene_id"] for s in scenes] == [s["scene_id"] for s in r.json()["scenes"]]
    assert scenes[0]["timing_source"] == "SUPPLIED"


# ---- defect 2: a key in the module .env must not reach the normal suite / spawned test servers
def test_empty_env_vars_override_dotenv_key(tmp_path, monkeypatch):
    envf = tmp_path / ".env"
    envf.write_text("OPENAI_API_KEY=sk-FAKE-dotenv-key-0000000000\n", encoding="utf-8")
    for k in ("OPENAI_API_KEY", "VIDEO_MODULE_OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    leaked = Settings(_env_file=str(envf)).openai_api_key
    assert leaked is not None and leaked.get_secret_value().startswith("sk-FAKE")  # the mechanism is real
    for value in ("", " "):  # "" (python scripts / conftest) and " " (PS 5.1 self-test cannot hold "")
        monkeypatch.setenv("OPENAI_API_KEY", value)
        monkeypatch.setenv("VIDEO_MODULE_OPENAI_API_KEY", value)
        k = Settings(_env_file=str(envf)).openai_api_key
        assert k is None or not k.get_secret_value().strip()


def test_normal_suite_holds_no_provider_key():
    assert get_settings().openai_api_key is None


# ---- coverage gap: CONFIGURED provider that FAILS, with and without explicit fallback
@pytest.mark.parametrize("response,code,state", [
    (err(401, "invalid_api_key", "bad key"), "PROVIDER_AUTH_FAILED", "FAILED"),
    (err(503, None, "overloaded"), "PROVIDER_UNAVAILABLE", "UNAVAILABLE"),
])
def test_configured_failure_with_fallback_is_labelled_and_failure_kept(db, project, openai_mock, response, code, state):
    from app.db.models import CostRecord, GenerationRecord

    _, script = openai_mock
    script(response)
    asset, gen = generate_image(db, project_id=project, provider_name="openai-images", request=_req(resolution="320x180"),
                                allow_fallback=True)
    assert asset.generation_kind == "PLACEHOLDER" and asset.is_fallback and asset.fallback_from == "openai-images"
    assert asset.title.startswith("[FALLBACK PLACEHOLDER]") and code in asset.fallback_reason
    assert asset.cost_status == "NOT_APPLICABLE" and asset.generation_cost is None and asset.provider == "placeholder-procedural"
    gens = db.query(GenerationRecord).filter(GenerationRecord.project_id == project).all()
    failed = [g for g in gens if g.provider == "openai-images"]
    assert len(failed) == 1 and failed[0].status == "FAILED" and failed[0].provider_state == state and failed[0].error_code == code
    assert failed[0].asset_id is None  # the failed real-AI attempt never points at the placeholder
    recs = db.query(CostRecord).filter(CostRecord.project_id == project).all()
    assert all(r.generation_kind == "PLACEHOLDER" and r.cost_status == "NOT_APPLICABLE" and r.estimated_cost is None for r in recs)


def test_configured_failure_without_fallback_raises_and_creates_nothing(db, project, openai_mock):
    from app.db.models import Asset

    _, script = openai_mock
    script(err(503, None, "overloaded"))
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name="openai-images", request=_req(), allow_fallback=False)
    assert e.value.code == "PROVIDER_UNAVAILABLE"
    assert db.query(Asset).filter(Asset.project_id == project).count() == 0


def test_unknown_cost_success_via_api_is_null_and_flagged(client, project, openai_mock):
    _, script = openai_mock
    script(ok_response(usage=None))
    done = wait_job(client, client.post("/assets/generate", json={"project_id": project, "prompt": "Harbour at dawn",
                                                                  "provider": "openai-images"}).json()["job_id"])
    assert done["status"] == "SUCCEEDED", done
    a = client.get(f"/assets/{done['result_id']}").json()
    assert a["generation_kind"] == "AI_GENERATED" and a["cost_status"] == "UNKNOWN" and a["generation_cost"] is None
    c = client.get(f"/costs/{project}").json()
    assert c["unknown_cost_count"] == 1 and c["totals_complete"] is False and c["ai_generation_cost_is_lower_bound"] is True
    assert all(r["estimated_cost"] is None and r["actual_cost"] is None for r in c["records"])


# ---- coverage gap: renderer handoff with a defined frame rate + supplied timing end to end
def test_timeline_frames_and_supplied_timing(client, project):
    import_png(client, project, seed=7701, title="harbour patrol frigate", meta={"tags": ["harbour", "patrol", "frigate"]})
    segs = [{"segment_id": "s1", "text": "The frigate left the harbour at dawn.", "start": 0.0, "end": 3.2},
            {"segment_id": "s2", "text": "A patrol followed the frigate along the coast.", "start": 3.5, "end": 7.0}]
    assert client.post("/scenes", json={"project_id": project, "segments": segs}).status_code == 201
    done = wait_job(client, client.post("/visual-plans/build", json={
        "project_id": project, "options": {"output_width": 1920, "output_height": 1080, "frame_rate": 25}}).json()["job_id"])
    assert done["status"] == "SUCCEEDED", done
    tl = client.get(f"/visual-plans/{done['result_id']}/timeline").json()
    assert tl["version"] == "1.1" and tl["output"]["defined"] is True and tl["output"]["frame_rate"] == 25
    assert tl["timing"]["timing_source"] == "SUPPLIED" and tl["timing"]["estimated"] is False
    shots = tl["shots"]
    assert shots[0]["start"] == pytest.approx(0.0) and shots[-1]["end"] == pytest.approx(7.0)  # supplied span kept exactly
    assert all(s["narration_anchor"]["timing_source"] == "SUPPLIED" for s in shots)
    assert all(isinstance(s["frame_start"], int) and isinstance(s["frame_end"], int) for s in shots)
    assert shots[0]["frame_start"] == 0 and all(a["frame_end"] == b["frame_start"] for a, b in zip(shots, shots[1:]))
    assert all(abs(a["end"] - b["start"]) <= 0.01 for a, b in zip(shots, shots[1:]))


# ---- defect 3: v1.0 shot anchors / stored timelines kept timing_source "PROVIDED" after 0002 (fixed by 0003)
def test_migration_relabels_v1_shot_anchor_and_timeline_timing(tmp_path):
    import json
    import os
    import sqlite3
    import subprocess
    import sys
    import uuid
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    store = tmp_path / "mig3"
    env = {**os.environ, "VIDEO_MODULE_STORAGE_ROOT": str(store)}
    code = "from alembic import command; from app.db.init_db import alembic_config; import sys; command.upgrade(alembic_config(), sys.argv[1])"
    run = lambda rev: subprocess.run([sys.executable, "-c", code, rev], cwd=str(root), env=env, capture_output=True, text=True, timeout=120)  # noqa: E731
    r = run("0001_initial")
    assert r.returncode == 0, r.stderr[-2000:]
    shot, aid = str(uuid.uuid4()), str(uuid.uuid4())
    anchor = {"segment_ids": ["seg-0001"], "text": "a b", "start": 0.0, "end": 1.0, "timing_source": "PROVIDED"}
    est_anchor = {**anchor, "timing_source": "ESTIMATED"}
    tl = {"schema": "video-visual-timeline", "version": "1.0", "assets": {},
          "shots": [{"shot_id": shot, "asset_id": aid, "narration_anchor": anchor}]}
    with sqlite3.connect(store / "video_visual_module.db") as con:
        q = ("INSERT INTO shots (shot_id, visual_plan_id, scene_id, order_index, start_time, end_time, duration, asset_id, visual_type, "
             "source_type, editorial_label, status, narration_anchor, reuse_index, motion, crop, transition, overlays) "
             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
        con.execute(q, (shot, "p1", "s1", 0, 0, 1, 1, aid, "SOURCE_PHOTO", "EXTERNAL_SOURCE", "FACTUAL_EVIDENCE", "READY",
                        json.dumps(anchor), 0, "{}", "{}", "CUT", "[]"))
        con.execute(q, (str(uuid.uuid4()), "p2", "s2", 0, 0, 1, 1, aid, "SOURCE_PHOTO", "EXTERNAL_SOURCE", "FACTUAL_EVIDENCE", "READY",
                        json.dumps(est_anchor), 0, "{}", "{}", "CUT", "[]"))
        con.execute("INSERT INTO visual_plans (visual_plan_id, project_id, scene_ids, status, options, stats, warnings, timeline_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", ("p1", "proj", "[]", "BUILT", "{}", "{}", "[]", json.dumps(tl), "2026-01-01"))
    r = run("head")
    assert r.returncode == 0, r.stderr[-2000:]
    r = run("head")  # idempotent re-run
    assert r.returncode == 0, r.stderr[-2000:]
    with sqlite3.connect(store / "video_visual_module.db") as con:
        anchors = {p: json.loads(a)["timing_source"] for p, a in con.execute("SELECT visual_plan_id, narration_anchor FROM shots")}
        assert anchors == {"p1": "SUPPLIED", "p2": "ESTIMATED"}
        new_tl = json.loads(con.execute("SELECT timeline_json FROM visual_plans").fetchone()[0])
        assert new_tl["shots"][0]["narration_anchor"]["timing_source"] == "SUPPLIED"
        assert new_tl["timing_relabelled_by_migration"] == "0003_audit_timing"
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0003_audit_timing"
