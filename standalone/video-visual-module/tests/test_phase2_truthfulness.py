"""Phase 2 / A: placeholder truthfulness. A placeholder (or test fixture) can never be classified,
reported or selected as a real AI image, and no generated image can ever be evidence."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.pipeline.timeline import validate_timeline
from tests.conftest import import_png, wait_job

ROOT = Path(__file__).resolve().parent.parent


def _gen(client, project, provider="placeholder-procedural", **kw):
    done = wait_job(client, client.post("/assets/generate", json={"project_id": project, "prompt": "Coastal radar at dusk",
                                                                  "provider": provider, "resolution": "320x180", **kw}
                                        ).json()["job_id"])
    assert done["status"] == "SUCCEEDED", done
    return client.get(f"/assets/{done['result_id']}").json()


def test_providers_are_honestly_classified(client):
    body = client.get("/providers").json()
    assert body["default"] == "placeholder-procedural"
    p = {x["name"]: x for x in body["providers"]}
    assert p["placeholder-procedural"]["generation_kind"] == "PLACEHOLDER" and p["placeholder-procedural"]["is_real_ai"] is False
    assert "local-procedural" in p["placeholder-procedural"]["aliases"]
    assert "PLACEHOLDER" in p["placeholder-procedural"]["label"] and "not AI" in p["placeholder-procedural"]["label"]
    assert p["mock-local"]["generation_kind"] == "TEST_FIXTURE" and p["mock-local"]["is_real_ai"] is False
    assert p["openai-images"]["generation_kind"] == "AI_GENERATED" and p["openai-images"]["is_real_ai"] is True
    assert p["openai-images"]["state"] == "NOT_CONFIGURED"  # no key in the test environment


def test_placeholder_cannot_be_imported_or_relabelled(client, project):
    from fixtures.synthetic import png_bytes

    r = client.post("/assets/import", data={"project_id": project, "asset_type": "PLACEHOLDER_IMAGE",
                                            "source_type": "SYSTEM_GENERATED", "rights_status": "PERMITTED"},
                    files={"file": ("p.png", png_bytes(64, 64, 4), "image/png")})
    assert r.status_code == 422 and r.json()["error_code"] == "EDITORIAL_POLICY"
    r = import_png(client, project, seed=4401, title="x")
    assert r.status_code == 201
    r2 = client.post("/assets/import", data={"project_id": project, "asset_type": "IMAGE", "source_type": "USER_PROVIDED",
                                             "rights_status": "PERMITTED", "editorial_category": "PLACEHOLDER"},
                     files={"file": ("q.png", png_bytes(64, 64, 4402), "image/png")})
    assert r2.status_code == 422


def test_placeholder_and_fixture_never_count_as_ai_or_evidence(client, project):
    ph = _gen(client, project)
    mock = _gen(client, project, provider="mock-local")
    assert ph["is_real_ai"] is False and ph["evidence_eligible"] is False and ph["is_placeholder"] is True
    assert mock["generation_kind"] == "TEST_FIXTURE" and mock["is_real_ai"] is False and mock["evidence_eligible"] is False
    assert mock["provenance_class"] == "TEST_FIXTURE"
    gens = client.get("/generations", params={"project_id": project}).json()
    kinds = sorted(g["generation_kind"] for g in gens)
    assert kinds == ["PLACEHOLDER", "TEST_FIXTURE"] and all(g["is_real_ai"] is False for g in gens)
    assert all(g["provider_state"] == "SUCCEEDED" and g["estimated_cost"] is None for g in gens)
    c = client.get(f"/costs/{project}").json()
    assert c["ai_image_count"] == 0 and c["placeholder_image_count"] == 1 and c["mock_generation_count"] == 1
    assert c["assets_by_provenance_class"] == {"PLACEHOLDER": 1, "TEST_FIXTURE": 1}
    assert c["ai_generation_cost"] == 0 and c["cost_per_generated_image"] is None and c["totals_complete"] is True
    # filters
    only = client.get("/assets", params={"project_id": project, "generation_kind": "AI_GENERATED"}).json()
    assert only == []


def test_placeholder_never_selected_for_factual_intents(client, project):
    _gen(client, project)
    client.post("/scenes", json={"project_id": project, "replace_existing": True, "narration":
                                 "According to a leaked document, the order was signed. The fleet is concentrated near the coast."})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project}).json()["job_id"])
    assert done["status"] == "SUCCEEDED", done
    for s in client.get(f"/shots/{done['result_id']}").json()["shots"]:
        assert s["visual_type"] != "PLACEHOLDER_IMAGE" and s["status"] == "GAP"


def test_placeholder_shot_labelled_and_validator_guards(client, project):
    client.post("/scenes", json={"project_id": project, "replace_existing": True,
                                 "narration": "Strikes escalated overnight. Attacks continued for hours along the line."})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project, "options": {
        "generate_ai": True, "max_ai_images": 1}}).json()["job_id"])
    assert done["status"] == "SUCCEEDED", done
    tl = client.get(f"/visual-plans/{done['result_id']}/timeline").json()
    ph_shots = [s for s in tl["shots"] if s["visual_type"] == "PLACEHOLDER_IMAGE"]
    assert ph_shots, "expected the placeholder to fill the illustrative beat"
    for s in ph_shots:
        assert s["editorial_label"] == "PLACEHOLDER" and s["source_type"] == "SYSTEM_GENERATED"
        assert any(o["type"] == "editorial_label" and "NOT AI" in o["text"] for o in s["overlays"])
        ref = tl["assets"][s["asset_id"]]
        assert ref["generation_kind"] == "PLACEHOLDER" and ref["provenance_class"] == "PLACEHOLDER"
        assert ref["evidence_eligible"] is False
    plan = client.get(f"/visual-plans/{done['result_id']}?include_timeline=false").json()
    assert plan["stats"]["real_ai_images_generated_in_plan"] == 0 and plan["stats"]["placeholder_images_generated_in_plan"] == 1
    assert plan["stats"]["ai_asset_count"] == 0 and any("PLACEHOLDER" in w for w in plan["warnings"])
    assert validate_timeline(tl) == []
    # tampering: relabelling a placeholder as AI or as evidence, or dropping its overlay, is caught
    import copy

    for label in ("AI_GENERATED", "FACTUAL_EVIDENCE"):
        bad = copy.deepcopy(tl)
        bad["shots"][ph_shots[0]["order"]]["editorial_label"] = label
        assert any("placeholder" in e or "evidence" in e for e in validate_timeline(bad))
    bad = copy.deepcopy(tl)
    bad["shots"][ph_shots[0]["order"]]["overlays"] = []
    assert any("PLACEHOLDER label" in e for e in validate_timeline(bad))


def test_unknown_cost_is_reported_not_summed_as_zero(client, project, db):
    from app.db.models import CostRecord

    db.add(CostRecord(project_id=project, provider="openai-images", model="gpt-image-2", generation_kind="AI_GENERATED",
                      cost_status="KNOWN", estimated_cost=0.05, cost_basis="test", quality="standard", resolution="1536x1024"))
    db.add(CostRecord(project_id=project, provider="openai-images", model="gpt-image-2", generation_kind="AI_GENERATED",
                      cost_status="UNKNOWN", estimated_cost=None, pre_call_estimate_usd=0.041, cost_basis="no usage"))
    db.commit()
    c = client.get(f"/costs/{project}").json()
    assert c["known_cost_count"] == 1 and c["unknown_cost_count"] == 1 and c["totals_complete"] is False
    assert c["ai_generation_cost"] == pytest.approx(0.05) and c["ai_generation_cost_is_lower_bound"] is True
    assert c["budget_remaining_is_upper_bound"] is True and c["cost_per_finished_minute_is_lower_bound"] is True
    unk = [r for r in c["records"] if r["cost_status"] == "UNKNOWN"][0]
    assert unk["estimated_cost"] is None and unk["actual_cost"] is None
    bp = c["by_provider"][0]
    assert bp["known_cost"] == pytest.approx(0.05) and bp["unknown_cost_count"] == 1
    # the budget guard counts the unknown one at its pre-call estimate (never as free)
    from app.generation.service import budget_commitment

    assert budget_commitment(db, project) == (pytest.approx(0.091), 1)


def test_migration_0002_reclassifies_v1_rows(tmp_path):
    """Build a v1.0 (0001) database containing a 'local-procedural' image with the old invented cost, then upgrade."""
    store = tmp_path / "mig2"
    env = {**os.environ, "VIDEO_MODULE_STORAGE_ROOT": str(store)}
    code = ("from alembic import command; from app.db.init_db import alembic_config; "
            "import sys; command.upgrade(alembic_config(), sys.argv[1])")
    r = subprocess.run([sys.executable, "-c", code, "0001_initial"], cwd=str(ROOT), env=env, capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    db = store / "video_visual_module.db"
    aid, mid, pid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    tl = {"schema": "video-visual-timeline", "version": "1.0", "assets": {aid: {"asset_type": "AI_IMAGE",
          "source_type": "AI_GENERATED", "editorial_label": "AI_GENERATED"}},
          "shots": [{"asset_id": aid, "visual_type": "AI_IMAGE", "source_type": "AI_GENERATED",
                     "editorial_label": "AI_GENERATED", "overlays": [{"type": "editorial_label", "text": "AI-GENERATED ILLUSTRATION"}]}]}
    with sqlite3.connect(db) as con:
        cols = "asset_id, project_id, asset_type, source_type, rights_status, acquired_at, is_mock, editorial_category, extra_metadata, reuse_count, provider, generation_cost"
        con.execute(f"INSERT INTO assets ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, pid, "AI_IMAGE", "AI_GENERATED", "PERMITTED", "2026-01-01", 0, "AI_GENERATED", "{}", 0, "local-procedural", 0.01))
        con.execute(f"INSERT INTO assets ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, pid, "AI_IMAGE", "AI_GENERATED", "PERMITTED", "2026-01-01", 1, "AI_GENERATED", "{}", 0, "mock-local", 0.0))
        con.execute("INSERT INTO cost_records (cost_record_id, project_id, provider, is_mock, estimated_cost, currency, created_at) "
                    "VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), pid, "local-procedural", 0, 0.01, "USD", "2026-01-01"))
        con.execute("INSERT INTO shots (shot_id, visual_plan_id, scene_id, order_index, start_time, end_time, duration, asset_id, "
                    "visual_type, source_type, editorial_label, status, narration_anchor, reuse_index, motion, crop, transition, overlays) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), "p1", "s1", 0, 0, 1, 1, aid, "AI_IMAGE", "AI_GENERATED", "AI_GENERATED", "READY", "{}", 0, "{}", "{}", "CUT", "[]"))
        import json

        con.execute("INSERT INTO visual_plans (visual_plan_id, project_id, scene_ids, status, options, stats, warnings, timeline_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", ("p1", pid, "[]", "BUILT", "{}", "{}", "[]", json.dumps(tl), "2026-01-01"))
        con.execute("INSERT INTO scenes (scene_id, project_id, order_index, start_time, end_time, narration_segments, "
                    "narration_segment_ids, timing_source, visual_intents, asset_ids, shot_ids, transition, importance, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("s1", pid, 0, 0, 1, "[]", "[]", "PROVIDED", "[]", "[]", "[]", "CUT", "HIGH", "2026-01-01"))
    r = subprocess.run([sys.executable, "-c", code, "head"], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    with sqlite3.connect(db) as con:
        a = con.execute("SELECT asset_type, source_type, editorial_category, generation_kind, generation_cost, cost_status, provider "
                        "FROM assets WHERE asset_id=?", (aid,)).fetchone()
        assert a == ("PLACEHOLDER_IMAGE", "SYSTEM_GENERATED", "PLACEHOLDER", "PLACEHOLDER", None, "NOT_APPLICABLE", "placeholder-procedural")
        assert con.execute("SELECT generation_kind FROM assets WHERE asset_id=?", (mid,)).fetchone()[0] == "TEST_FIXTURE"
        cr = con.execute("SELECT estimated_cost, cost_status, generation_kind FROM cost_records").fetchone()
        assert cr == (None, "NOT_APPLICABLE", "PLACEHOLDER")
        assert con.execute("SELECT editorial_label FROM shots").fetchone()[0] == "PLACEHOLDER"
        assert con.execute("SELECT timing_source FROM scenes").fetchone()[0] == "SUPPLIED"
        new_tl = json.loads(con.execute("SELECT timeline_json FROM visual_plans").fetchone()[0])
        assert new_tl["assets"][aid]["editorial_label"] == "PLACEHOLDER" and new_tl["reclassified_by_migration"] == "0002_phase2"
        assert "NOT AI" in new_tl["shots"][0]["overlays"][0]["text"]
