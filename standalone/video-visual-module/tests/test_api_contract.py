"""Contract tests (spec §33): public API, renderer output, schema compatibility."""
from __future__ import annotations

import json

from app.pipeline.timeline import TIMELINE_SCHEMA, TIMELINE_VERSION, validate_timeline
from fixtures.synthetic import SAMPLE_NARRATION
from tests.conftest import import_png, wait_job

SPEC_28 = [
    ("post", "/projects"), ("get", "/projects/{project_id}"),
    ("post", "/assets/import"), ("get", "/assets"), ("get", "/assets/{asset_id}"),
    ("post", "/assets/generate"), ("post", "/assets/{asset_id}/variants"),
    ("post", "/scenes"), ("post", "/visual-plans/build"), ("get", "/visual-plans/{visual_plan_id}"),
    ("get", "/shots/{visual_plan_id}"), ("get", "/jobs/{job_id}"), ("get", "/costs/{project_id}"),
]


def test_openapi_contains_every_spec_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    for method, path in SPEC_28:
        assert path in paths and method in paths[path], (method, path)
    assert "/health" in paths


def test_health_docs_and_ui(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["service"] == "video-visual-module" and h["database"] == "ok"
    assert client.get("/docs").status_code == 200
    ui = client.get("/ui/")
    assert ui.status_code == 200 and "Visual Composition Studio" in ui.text
    assert client.get("/ui/app.js").status_code == 200
    assert client.get("/", follow_redirects=False).status_code in (302, 307)


def test_error_envelope_and_no_stack_traces(client):
    r = client.get("/projects/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert set(body) >= {"error_code", "error_message", "request_id"} and body["error_code"] == "NOT_FOUND"
    r = client.post("/projects", json={"name": 123456789012345678901234567890, "ai_image_budget_usd": "SECRET-VALUE"})
    assert r.status_code == 422 and r.json()["error_code"] == "VALIDATION_ERROR"
    assert "SECRET-VALUE" not in r.text and "Traceback" not in r.text
    assert r.headers.get("X-Request-ID")


def test_unhandled_exception_returns_generic_500(client, monkeypatch):
    import app.api.costs as costs_mod

    def boom(*a, **k):
        raise RuntimeError("internal detail /secret/path")
    monkeypatch.setattr(costs_mod, "defaultdict", boom)
    pid = client.post("/projects", json={"name": "boom"}).json()["project_id"]
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c2:
        r = c2.get(f"/costs/{pid}")
    assert r.status_code == 500 and r.json()["error_code"] == "INTERNAL_ERROR"
    assert "secret" not in r.text and "Traceback" not in r.text


def test_full_workflow_and_renderer_contract(client, project):
    import_png(client, project, seed=2001, title="Kessan Strait fleet", meta={"tags": ["fleet", "kessan", "strait", "talon", "missile"]})
    sc = client.post("/scenes", json={"project_id": project, "narration": SAMPLE_NARRATION}).json()
    assert sc["scene_count"] == 2
    assert client.get(f"/scenes/{sc['scenes'][0]['scene_id']}").json()["narration_segments"]
    job = client.post("/visual-plans/build", json={"project_id": project}).json()
    assert job["status"] == "QUEUED" and job["job_type"] == "build_visual_plan"
    done = wait_job(client, job["job_id"])
    assert done["status"] == "SUCCEEDED" and done["progress"] == 1.0 and done["completed_at"]
    plan = client.get(f"/visual-plans/{done['result_id']}").json()
    assert plan["status"] == "BUILT" and plan["job_id"] == job["job_id"]
    tl = client.get(f"/visual-plans/{done['result_id']}/timeline").json()
    assert tl["schema"] == "video-visual-timeline" and tl["version"] == TIMELINE_VERSION
    assert validate_timeline(tl) == []
    s0 = tl["shots"][0]
    # spec §29 example fields are present with the same meaning
    for k in ("shot_id", "start", "end", "asset_id", "motion"):
        assert k in s0
    assert "type" in s0["motion"]
    for s in tl["shots"]:
        if s["asset_id"]:
            ref = tl["assets"][s["asset_id"]]
            assert ref["uri"].startswith("storage://") and ref["url"] == f"/assets/{s['asset_id']}/file"
            assert ref["rights_label"] and "local_path" not in ref
    assert tl["attributions"] and tl["summary"]["shot_count"] == len(tl["shots"])
    shots = client.get(f"/shots/{done['result_id']}").json()
    assert shots["shot_count"] == len(tl["shots"])
    # artifact on disk equals API output
    from app.storage import resolve_stored_path
    on_disk = json.loads(resolve_stored_path(plan["timeline_path"]).read_text(encoding="utf-8"))
    assert on_disk["shots"] == tl["shots"]
    # previews are real PNGs
    p = client.get(f"/shots/{done['result_id']}/{s0['shot_id']}/preview", params={"t": 1})
    assert p.status_code == 200 and p.content[:8] == b"\x89PNG\r\n\x1a\n"
    cs = client.get(f"/visual-plans/{done['result_id']}/contact-sheet")
    assert cs.status_code == 200 and cs.content[:4] == b"\x89PNG"
    costs = client.get(f"/costs/{project}").json()
    for k in ("ai_image_count", "ai_generation_cost", "source_asset_count", "total_visual_asset_count",
              "cost_per_generated_image", "cost_per_finished_minute"):
        assert k in costs
    assert costs["finished_minutes"] > 0
    jobs = client.get("/jobs", params={"project_id": project}).json()
    assert any(j["job_id"] == job["job_id"] for j in jobs)


def test_timeline_schema_published_and_validator_rejects_bad_output(client):
    assert client.get("/schemas/visual-timeline").json() == json.loads(json.dumps(TIMELINE_SCHEMA))
    good_shot = {"shot_id": "s", "scene_id": "c", "order": 0, "start": 0.0, "end": 2.0, "duration": 2.0, "asset_id": "a",
                 "visual_type": "AI_IMAGE", "source_type": "AI_GENERATED", "editorial_label": "FACTUAL_EVIDENCE",
                 "status": "READY", "motion": {"type": "STATIC"}, "crop": {"x": 0, "y": 0, "w": 1, "h": 1},
                 "transition": "CUT", "narration_anchor": {"segment_ids": ["x"], "text": "t", "start": 0, "end": 2}}
    tl = {"schema": "video-visual-timeline", "version": TIMELINE_VERSION, "visual_plan_id": "p", "project_id": "q",
          "duration": 2.0, "shots": [good_shot], "assets": {"a": {"source_type": "AI_GENERATED"}}}
    errs = validate_timeline(tl)
    assert any("AI" in e for e in errs)  # AI presented as evidence is caught
    tl["shots"] = [{**good_shot, "motion": {"type": "WARP_SPEED"}, "editorial_label": "AI_GENERATED"}]
    assert any("motion" in e for e in validate_timeline(tl))


def test_idempotency_key_replays_job(client, project):
    client.post("/scenes", json={"project_id": project, "narration": SAMPLE_NARRATION})
    h = {"Idempotency-Key": f"build-{project}"}
    a = client.post("/visual-plans/build", json={"project_id": project}, headers=h).json()
    b = client.post("/visual-plans/build", json={"project_id": project}, headers=h).json()
    assert a["job_id"] == b["job_id"] and b["idempotent_replay"] is True
    wait_job(client, a["job_id"])


def test_api_key_protection_when_configured(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "api_key", "s3cret-key")
    assert client.get("/jobs").status_code == 401
    assert client.get("/jobs").json()["error_code"] == "UNAUTHORIZED"
    assert client.get("/jobs", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/jobs", headers={"X-API-Key": "s3cret-key"}).status_code == 200
    assert client.get("/health").status_code == 200  # health stays open
