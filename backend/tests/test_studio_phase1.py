"""Studio Phase 1: unified stage read model over persisted pipeline state."""
import json

import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.database import connect
from defence_studio.services.script import ScriptService
from defence_studio.services.studio import STAGES, build_studio_state, studio_state_name
from defence_studio.services.youtube import YouTubeService
from test_scene_service_m1 import OWNER, _generate_plan, _plan_sha, approved_script  # noqa: F401
from conftest import SECRET, access_token


def _state(s, language="ta"):
    """A fresh service pair each call: nothing cached (refresh / restart / new device)."""
    overview = YouTubeService(s["db"], s["root"]).pipeline_overview(s["job"], OWNER, language)
    return build_studio_state(overview, ScriptService(s["db"], s["root"]).revision_staleness(s["job"], OWNER, language))


def _overview(**statuses):
    stages = [{"stage": source, "status": "NONE"} for _, _, source, _, _ in STAGES]
    for stage in stages:
        stage.update(statuses.get(stage["stage"], {}))
    return {"job_id": "j", "language": "ta", "stages": stages, "workflow": None, "needs_attention": []}


# -- pure mapping ---------------------------------------------------------------

def test_status_vocabulary_mapping_keeps_unknown_values_honest():
    assert studio_state_name("NONE") == "NOT_STARTED"
    assert studio_state_name("SUCCEEDED") == "GENERATED"
    assert studio_state_name("CHANGES_REQUESTED") == "REJECTED"
    assert studio_state_name("INTERRUPTED") == "FAILED"
    assert studio_state_name("APPROVED", stale=True) == "STALE"
    assert studio_state_name("NONE", stale=True) == "NOT_STARTED"
    assert studio_state_name("SOMETHING_NEW") == "SOMETHING_NEW"


def test_generation_is_never_approval_on_gated_stages():
    out = build_studio_state(_overview(
        evidence_package={"status": "APPROVED"},
        script={"status": "APPROVED", "version": 1, "approved_version": 1},
        scene_plan={"status": "APPROVED", "version": 1},
        audio_set={"status": "SUCCEEDED", "version": 1, "stale": False},
        visual_timeline={"status": "SUCCEEDED", "version": 1, "stale": False},
        render={"status": "SUCCEEDED", "version": 1, "stale": False},
        qc_report={"status": "SUCCEEDED", "version": 1},
        final_approval={"status": "AWAITING_REVIEW", "version": 1},
    ), None)
    stages = {s["key"]: s for s in out["stages"]}
    assert stages["audio"]["state"] == "GENERATED" and stages["audio"]["review_gate"] is False and stages["audio"]["done"]
    assert stages["final_review"]["state"] == "AWAITING_REVIEW" and not stages["final_review"]["done"]
    assert out["current_stage"] == "final_review"
    assert out["publishing"] == {"state": "NONE", "separate_authorised_action": True}


def test_stale_artifact_blocks_progress_and_explains_why():
    out = build_studio_state(_overview(
        evidence_package={"status": "APPROVED"},
        script={"status": "APPROVED", "version": 1, "approved_version": 1},
        scene_plan={"status": "APPROVED", "version": 2},
        audio_set={"status": "SUCCEEDED", "version": 1, "stale": True},
    ), None)
    audio = next(s for s in out["stages"] if s["key"] == "audio")
    assert audio["state"] == "STALE" and audio["raw_status"] == "SUCCEEDED" and not audio["done"]
    assert audio["stale_reason"] and out["current_stage"] == "audio"


def test_script_newer_draft_keeps_approved_version_but_stale_approval_does_not():
    overview = _overview(evidence_package={"status": "APPROVED"},
                         script={"status": "DRAFT", "version": 3, "approved_version": 2})
    current = build_studio_state(overview, {"revisions": [{"language": "ta", "version": 2, "state": "CURRENT"}]})
    script = next(s for s in current["stages"] if s["key"] == "script")
    assert script["state"] == "DRAFT" and script["approved_version"] == 2 and script["done"]
    assert current["current_stage"] == "scene_plan"
    stale = build_studio_state(overview, {"revisions": [
        {"language": "ta", "version": 2, "state": "STALE", "reason": "Upstream evidence changed"}]})
    script = next(s for s in stale["stages"] if s["key"] == "script")
    assert script["state"] == "STALE" and not script["done"] and script["stale_reason"] == "Upstream evidence changed"
    assert stale["current_stage"] == "script"


# -- persisted state ---------------------------------------------------------------

def test_studio_resumes_from_persisted_state_across_fresh_services(approved_script):
    s = approved_script
    first = _state(s)
    assert [x["key"] for x in first["stages"]][:3] == ["research", "script", "scene_plan"]
    assert first["current_stage"] == "scene_plan"
    plan = _generate_plan(s)
    assert _state(s)["current_stage"] == "scene_plan"  # a generated plan is not an approved one
    s["scene"].approve(s["job"], OWNER, "ta", plan["version"], _plan_sha(s["scene"], s["job"], plan["version"]),
                       "ok", "studio-ap")
    after = _state(s)
    assert after["current_stage"] == "audio"
    assert next(x for x in after["stages"] if x["key"] == "scene_plan")["state"] == "APPROVED"
    again = _state(s)
    assert {k: v for k, v in again.items() if k != "generated_at"} == \
        {k: v for k, v in after.items() if k != "generated_at"}


def test_audio_bound_to_an_old_plan_is_reported_stale(approved_script):
    s = approved_script
    plan = _generate_plan(s)
    s["scene"].approve(s["job"], OWNER, "ta", plan["version"], _plan_sha(s["scene"], s["job"], plan["version"]),
                       "ok", "studio-ap2")
    with connect(s["db"]) as c:
        c.execute("INSERT INTO integration_artifacts(job_id,language,kind,version,status,input_binding,created_by,"
                  "created_at,updated_at,idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (s["job"], "ta", "audio_set", 1, "SUCCEEDED", json.dumps({"scene_plan_sha256": "0" * 64}),
                   OWNER, "2026-10-28T00:00:00Z", "2026-10-28T00:00:00Z", "studio-audio-1"))
    overview = YouTubeService(s["db"], s["root"]).pipeline_overview(s["job"], OWNER, "ta")
    assert next(x for x in overview["stages"] if x["stage"] == "audio_set")["stale"] is True
    audio = next(x for x in _state(s)["stages"] if x["key"] == "audio")
    assert audio["state"] == "STALE" and not audio["done"]


def test_http_studio_route_auth_scope_and_validation(approved_script):
    s = approved_script
    app = create_app(s["db"], SECRET, s["root"])
    mine = {"Authorization": f"Bearer {access_token(OWNER)}"}
    other = {"Authorization": f"Bearer {access_token('intruder')}"}
    url = f"/api/jobs/{s['job']}/studio/ta"
    with TestClient(app) as client:
        assert client.get(url).status_code == 401
        assert client.get(url, headers=other).status_code == 404
        assert client.get(f"/api/jobs/{s['job']}/studio/../x", headers=mine).status_code in (404, 422)
        assert client.get(f"/api/jobs/{s['job']}/studio/TAMIL", headers=mine).status_code == 422
        body = client.get(url, headers=mine)
        assert body.status_code == 200
        data = body.json()
        assert data["schema"] == "defence-studio.studio-state.v1" and data["current_stage"] == "scene_plan"
        assert [x["key"] for x in data["stages"]] == [k for k, *_ in STAGES]
