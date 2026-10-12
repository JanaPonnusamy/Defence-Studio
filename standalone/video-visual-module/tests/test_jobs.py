"""Job persistence, cancellation, retry and restart recovery (spec §25)."""
from __future__ import annotations

import threading
import time

from app.db.models import GenerationRecord, Job, VisualPlan
from app.generation.base import AIImageProvider
from app.generation.local_procedural import LocalProceduralProvider
from app.generation.registry import register_provider, unregister_provider
from app.jobs.queue import get_job_queue, recover_jobs
from fixtures.synthetic import SAMPLE_NARRATION
from tests.conftest import wait_job


class SlowProvider(AIImageProvider):
    name = "test-slow"
    model = "slow-v1"
    is_mock = True

    def __init__(self):
        self.release = threading.Event()

    def generate(self, request, out_dir):
        self.release.wait(10)
        r = LocalProceduralProvider().generate(request, out_dir)
        r.provider, r.is_mock = self.name, True
        return r


def test_job_lifecycle_is_persisted(client, project, db):
    client.post("/scenes", json={"project_id": project, "narration": SAMPLE_NARRATION})
    j = client.post("/visual-plans/build", json={"project_id": project}).json()
    done = wait_job(client, j["job_id"])
    row = db.get(Job, j["job_id"])
    assert row.status == "SUCCEEDED" and row.started_at and row.completed_at and row.result_id == done["result_id"]
    assert row.payload["project_id"] == project
    from app.config import get_settings
    assert (get_settings().jobs_dir / f"{j['job_id']}.json").is_file()


def test_restart_recovery_marks_running_failed_and_requeues_queued(client, project, db):
    client.post("/scenes", json={"project_id": project, "narration": SAMPLE_NARRATION})
    # simulate a process that died mid-job: RUNNING job + BUILDING plan + PENDING generation
    running = Job(job_type="build_visual_plan", status="RUNNING", project_id=project, payload={"project_id": project},
                  progress=0.4, stage="planning")
    db.add(running)
    db.commit()
    db.add(VisualPlan(project_id=project, job_id=running.job_id, status="BUILDING"))
    db.add(GenerationRecord(project_id=project, job_id=running.job_id, prompt="p", provider="local-procedural", status="PENDING"))
    queued = Job(job_type="build_visual_plan", status="QUEUED", project_id=project,
                 payload={"project_id": project, "scene_ids": None, "options": {}})
    db.add(queued)
    db.commit()

    counts = recover_jobs()
    assert counts["interrupted"] >= 1 and counts["requeued"] >= 1
    r = client.get(f"/jobs/{running.job_id}").json()
    assert r["status"] == "FAILED" and r["error_code"] == "JOB_INTERRUPTED" and r["result_id"] is None
    db.expire_all()
    assert db.query(VisualPlan).filter(VisualPlan.job_id == running.job_id).one().status == "FAILED"
    assert db.query(GenerationRecord).filter(GenerationRecord.job_id == running.job_id).one().status == "FAILED"
    q = wait_job(client, queued.job_id)
    assert q["status"] == "SUCCEEDED"
    # the interrupted job can be retried
    retry = client.post(f"/jobs/{running.job_id}/retry").json()
    assert retry["retry_of"] == running.job_id
    assert wait_job(client, retry["job_id"])["status"] == "SUCCEEDED"


def test_recovery_on_real_app_startup(client, project, db):
    """A RUNNING row present when the app starts must come out FAILED, never SUCCEEDED."""
    stale = Job(job_type="generate_image", status="RUNNING", project_id=project,
                payload={"project_id": project, "provider": "mock-local", "prompt": "x"})
    db.add(stale)
    db.commit()
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c2:  # runs lifespan startup -> recover_jobs()
        j = c2.get(f"/jobs/{stale.job_id}").json()
    assert j["status"] == "FAILED" and j["error_code"] == "JOB_INTERRUPTED"


def test_cancel_queued_and_running_jobs(client, project):
    slow = SlowProvider()
    register_provider(slow)
    try:
        workers = get_job_queue()._executor._max_workers
        jobs = [client.post("/assets/generate", json={"project_id": project, "prompt": f"slow {i}", "provider": "test-slow"}).json()
                for i in range(workers + 1)]
        time.sleep(0.3)
        statuses = {j["job_id"]: client.get(f"/jobs/{j['job_id']}").json()["status"] for j in jobs}
        queued = [k for k, v in statuses.items() if v == "QUEUED"]
        running = [k for k, v in statuses.items() if v == "RUNNING"]
        assert queued and running
        c = client.post(f"/jobs/{queued[0]}/cancel").json()
        assert c["status"] == "CANCELLED"
        c = client.post(f"/jobs/{running[0]}/cancel").json()
        assert c["status"] == "RUNNING" and c["cancel_requested"] is True
        slow.release.set()
        assert wait_job(client, queued[0])["status"] == "CANCELLED"
        # running job had already passed its last cancel checkpoint before release -> it either finishes
        # (output verified) or is CANCELLED; it must never be FAILED/SUCCEEDED without being consistent
        final = wait_job(client, running[0])
        assert final["status"] in ("CANCELLED", "SUCCEEDED")
        if final["status"] == "SUCCEEDED":
            assert client.get(f"/assets/{final['result_id']}").status_code == 200
        assert client.post(f"/jobs/{queued[0]}/cancel").status_code == 409
    finally:
        slow.release.set()
        unregister_provider("test-slow")


def test_failed_job_carries_explicit_error_and_can_retry(client, monkeypatch):
    # Phase 2: the placeholder provider has no cost, so the budget failure is exercised with the billable
    # openai-images adapter and a FAKE key; the budget guard stops it BEFORE any request (the test network
    # guard would fail the test if one were attempted).
    from pydantic import SecretStr

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-test-FAKE-not-a-real-key-000000"))
    pid = client.post("/projects", json={"name": "fail", "ai_image_budget_usd": 0}).json()["project_id"]
    done = wait_job(client, client.post("/assets/generate", json={"project_id": pid, "prompt": "x",
                                                                  "provider": "openai-images"}).json()["job_id"])
    assert done["status"] == "FAILED" and done["error_code"] == "BUDGET_EXCEEDED" and done["error_message"]
    assert client.post(f"/jobs/{done['job_id']}/retry").status_code == 202
    assert client.post("/jobs/nope/retry").status_code == 404


def test_jobs_listing_filters(client):
    assert client.get("/jobs", params={"status": "BOGUS"}).status_code == 422
    for j in client.get("/jobs", params={"status": "SUCCEEDED"}).json():
        assert j["status"] == "SUCCEEDED"
