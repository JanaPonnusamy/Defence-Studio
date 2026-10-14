"""RM-3: approved scene plan -> Visual Module timeline artifact.

Two layers:
* contract tests against a faithful in-process fake of the module's public API
  (httpx.MockTransport) -- deterministic, always run;
* an INTEGRATED test against the real standalone Visual Module over HTTP,
  skipped (with a printed reason) when it is not running at
  DEFENCE_STUDIO_VISUAL_MODULE_URL (default http://127.0.0.1:8765).
"""
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.integrations.visual_module import VisualModuleClient, VisualModuleError
from defence_studio.services.integration import IntegrationStale
from defence_studio.services.visual import VisualService
from test_scene_service_m1 import (  # noqa: F401  (fixture re-export)
    SCENE_CONFIG, OWNER, _generate_plan, _plan_sha, approved_script,
)
from conftest import SECRET, access_token


class FakeVisualModule:
    """Implements the documented subset of the Visual Module API."""

    def __init__(self, fail_build=False, drop_scene=False, timeline_version="1.1"):
        self.fail_build, self.drop_scene, self.timeline_version = fail_build, drop_scene, timeline_version
        self.calls, self.segments, self.polls = [], [], 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        body = json.loads(request.content) if request.content else {}
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "1.1.0"})
        if path == "/projects":
            return httpx.Response(201, json={"project_id": "p1"})
        if path == "/scenes":
            self.segments = body["segments"]
            return httpx.Response(201, json={"project_id": "p1", "scene_count": len(self.segments), "scenes": []})
        if path == "/visual-plans/build":
            return httpx.Response(202, json={"job_id": "j1", "status": "QUEUED", "progress": 0.0})
        if path == "/jobs/j1":
            self.polls += 1
            if self.fail_build:
                return httpx.Response(200, json={"job_id": "j1", "status": "FAILED", "error_code": "RIGHTS_UNKNOWN",
                                                 "error_message": "no cleared asset"})
            return httpx.Response(200, json={"job_id": "j1", "status": "SUCCEEDED", "progress": 1.0,
                                             "stage": "done", "result_id": "vp1"})
        if path == "/visual-plans/vp1/timeline":
            segs = self.segments[:-1] if self.drop_scene else self.segments
            shots = [{"shot_id": f"sh{i}", "scene_id": f"m{i}", "order": i, "start": s["start"], "end": s["end"],
                      "duration": s["end"] - s["start"], "status": "GAP", "gap_code": "SOURCE_UNAVAILABLE",
                      "asset_id": None, "visual_type": "SOURCE_PHOTO",
                      "narration_anchor": {"segment_ids": [s["segment_id"]], "text": s["text"]}}
                     for i, s in enumerate(segs)]
            return httpx.Response(200, json={"schema": "video-visual-timeline", "version": self.timeline_version,
                                             "duration": segs[-1]["end"] if segs else 0, "shots": shots,
                                             "assets": {}, "attributions": []})
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "error_message": path})


def _client(fake: FakeVisualModule) -> VisualModuleClient:
    return VisualModuleClient(base_url="http://vvm.test", poll_interval=0,
                              client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(fake.handler), **kw))


def _approved_plan(s):
    plan = _generate_plan(s)
    s["scene"].approve(s["job"], OWNER, "ta", plan["version"], _plan_sha(s["scene"], s["job"], plan["version"]),
                       "Plan approved", "plan-approve-1")
    return plan


def _service(s, fake):
    return VisualService(s["db"], s["root"], provider=s["provider"], visual_client=_client(fake))


def test_visual_plan_requires_an_approved_scene_plan(approved_script):
    s = approved_script
    _generate_plan(s)  # DRAFT only
    with pytest.raises(IntegrationStale, match="approved scene plan"):
        _service(s, FakeVisualModule()).start_visual_plan(s["job"], OWNER, "ta", "v-1", background=False)


def test_visual_timeline_is_bound_stored_hashed_and_reports_gaps(approved_script):
    s = approved_script
    plan = _approved_plan(s)
    fake = FakeVisualModule()
    svc = _service(s, fake)
    item = svc.start_visual_plan(s["job"], OWNER, "ta", "v-1", background=False)
    assert item["status"] == "SUCCEEDED" and item["sha256"] and item["progress"] == 1.0
    body = svc.read_integration_body(s["job"], OWNER, "ta", "visual_timeline", item["version"])
    assert body["schema_version"] == "defence-studio.visual-timeline.v1"
    assert body["binding"]["scene_plan_sha256"] == _plan_sha(s["scene"], s["job"], plan["version"])
    assert body["timing_origin"] == "scene_plan_estimate"
    assert body["summary"]["gap_count"] == len(plan["scenes"]) and body["summary"]["ready_for_render"] is False
    # our scene ids were sent verbatim as segment ids, in plan order, narration unchanged
    assert [x["segment_id"] for x in fake.segments] == [sc["scene_id"] for sc in plan["scenes"]]
    assert [x["text"] for x in fake.segments] == [sc["narration"] for sc in plan["scenes"]]
    # contiguous estimated timing
    assert all(abs(a["end"] - b["start"]) < 1e-6 for a, b in zip(fake.segments, fake.segments[1:]))
    events = [e["event"] for e in svc.integration_events(s["job"], OWNER, "ta", "visual_timeline", item["version"])]
    assert events[0] == "QUEUED" and "RUNNING" in events and events[-1] == "SUCCEEDED"


def test_idempotent_replay_and_same_binding_do_not_duplicate_work(approved_script):
    s = approved_script
    _approved_plan(s)
    fake = FakeVisualModule()
    svc = _service(s, fake)
    a = svc.start_visual_plan(s["job"], OWNER, "ta", "same-key", background=False)
    builds = sum(1 for c in fake.calls if c[1] == "/visual-plans/build")
    b = svc.start_visual_plan(s["job"], OWNER, "ta", "same-key", background=False)
    c = svc.start_visual_plan(s["job"], OWNER, "ta", "other-key", background=False)  # same binding -> reuse
    assert a["version"] == b["version"] == c["version"] == 1
    assert sum(1 for x in fake.calls if x[1] == "/visual-plans/build") == builds


def test_module_failure_is_recorded_retryable_and_never_empty_success(approved_script):
    s = approved_script
    _approved_plan(s)
    failing = _service(s, FakeVisualModule(fail_build=True))
    item = failing.start_visual_plan(s["job"], OWNER, "ta", "f-1", background=False)
    assert item["status"] == "FAILED" and "RIGHTS_UNKNOWN" in item["detail"] and item["uri"] is None
    with pytest.raises(IntegrationStale):
        failing.export_visual_timeline(s["job"], OWNER, "ta")
    ok = _service(s, FakeVisualModule())
    retry = ok.retry_visual_plan(s["job"], OWNER, "ta", item["version"], "f-retry", background=False)
    assert retry["status"] == "SUCCEEDED" and retry["version"] == 2
    with pytest.raises(IntegrationStale):  # a SUCCEEDED run cannot be "retried"
        ok.retry_visual_plan(s["job"], OWNER, "ta", 2, "f-retry-2", background=False)


def test_incomplete_coverage_and_unsupported_timeline_fail_explicitly(approved_script):
    s = approved_script
    _approved_plan(s)
    dropped = _service(s, FakeVisualModule(drop_scene=True)).start_visual_plan(s["job"], OWNER, "ta", "d-1", background=False)
    assert dropped["status"] == "FAILED" and "scene_coverage" in dropped["detail"]
    old = _service(s, FakeVisualModule(timeline_version="0.9")).start_visual_plan(
        s["job"], OWNER, "ta", "o-1", {"max_ai_images": 0}, background=False)
    assert old["status"] == "FAILED" and "unsupported_timeline" in old["detail"]


def test_ai_generation_is_not_enabled_from_this_stage(approved_script):
    s = approved_script
    _approved_plan(s)
    from defence_studio.services.integration import IntegrationValidationError
    with pytest.raises(IntegrationValidationError):
        _service(s, FakeVisualModule()).start_visual_plan(
            s["job"], OWNER, "ta", "ai-1", {"generate_ai": True}, background=False)


def test_changed_script_makes_timeline_stale_and_unexportable(approved_script):
    s = approved_script
    _approved_plan(s)
    svc = _service(s, FakeVisualModule())
    svc.start_visual_plan(s["job"], OWNER, "ta", "st-1", background=False)
    assert svc.export_visual_timeline(s["job"], OWNER, "ta")["summary"]["shot_count"] > 0
    # Re-approve a new script revision: the old plan binds a superseded script.
    script = s["script"]
    new_rev = script.generate(s["job"], OWNER, {**__import__("test_script_phase3").GOOD_CONFIG}, "gen-2")
    rows = script.list_revisions(s["job"], OWNER, "ta")["revisions"]
    sha = next(r["revision_sha256"] for r in rows if r["version"] == new_rev["version"])
    script.approve(s["job"], OWNER, "ta", new_rev["version"], sha, "Re-approved", "script-approve-2")
    status = svc.visual_status(s["job"], OWNER, "ta")
    assert status["current_scene_plan_sha256"] is None or all(i["stale"] for i in status["items"])
    with pytest.raises(IntegrationStale):
        svc.export_visual_timeline(s["job"], OWNER, "ta")
    with pytest.raises(IntegrationStale):
        svc.start_visual_plan(s["job"], OWNER, "ta", "st-2", background=False)


def test_startup_recovery_marks_running_rows_interrupted(approved_script):
    s = approved_script
    _approved_plan(s)
    svc = _service(s, FakeVisualModule())
    row = svc._create_row(s["job"], OWNER, "ta", "visual_timeline", {"x": 1}, "rec-1")[0]
    svc._transition(s["job"], "ta", "visual_timeline", row["version"], "RUNNING")
    assert svc.recover_interrupted() == 1
    assert svc.get_integration(s["job"], OWNER, "ta", "visual_timeline", row["version"])["status"] == "INTERRUPTED"


def test_unreachable_module_is_a_retryable_structured_error():
    client = VisualModuleClient(base_url="http://127.0.0.1:9", poll_interval=0, request_timeout=1)
    with pytest.raises(VisualModuleError) as err:
        client.health()
    assert err.value.code in ("unreachable", "timeout") and err.value.retryable


def test_http_routes_auth_ownership_and_background_run(approved_script):
    s = approved_script
    _approved_plan(s)
    fake = FakeVisualModule()
    app = create_app(s["db"], SECRET, s["root"], integration_clients={"visual": _client(fake)})
    mine = {"Authorization": f"Bearer {access_token(OWNER)}", "Idempotency-Key": "http-v1"}
    other = {"Authorization": f"Bearer {access_token('intruder')}", "Idempotency-Key": "http-v2"}
    base = f"/api/jobs/{s['job']}/visual/ta"
    with TestClient(app) as client:
        assert client.post(f"{base}/plan", json={}).status_code == 401
        assert client.post(f"{base}/plan", json={}, headers=other).status_code == 404
        started = client.post(f"{base}/plan", json={}, headers=mine)
        assert started.status_code == 202
        import time
        for _ in range(50):
            status = client.get(base, headers=mine).json()
            if status["items"] and status["items"][0]["status"] in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(0.1)
        assert status["items"][0]["status"] == "SUCCEEDED" and status["items"][0]["stale"] is False
        assert client.get(f"{base}/timeline", headers=mine).json()["schema_version"] == "defence-studio.visual-timeline.v1"
        assert client.get(f"{base}/timeline", headers=other).status_code == 404
        assert client.post(f"{base}/plan", json={"bogus": 1}, headers=mine).status_code == 422


# -- INTEGRATED against the real standalone module --------------------------


def _real_module_up() -> bool:
    try:
        return VisualModuleClient(request_timeout=2).health().get("service") == "video-visual-module"
    except Exception:
        return False


@pytest.mark.skipif(not _real_module_up(), reason="real Visual Module not running at DEFENCE_STUDIO_VISUAL_MODULE_URL")
def test_real_visual_module_integration(approved_script):
    s = approved_script
    plan = _approved_plan(s)
    svc = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=VisualModuleClient(poll_interval=0.2))
    item = svc.start_visual_plan(s["job"], OWNER, "ta", "real-1", background=False)
    assert item["status"] == "SUCCEEDED", item["detail"]
    body = svc.read_integration_body(s["job"], OWNER, "ta", "visual_timeline", item["version"])
    tl = body["timeline"]
    assert tl["schema"] == "video-visual-timeline" and tl["version"] == "1.1"
    covered = {sid for sh in tl["shots"] for sid in sh["narration_anchor"]["segment_ids"]}
    assert covered == {sc["scene_id"] for sc in plan["scenes"]}
    assert body["source_module"]["version"]
