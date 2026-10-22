"""API lifespan acceptance: real queue/worker/storage with empty fixture discovery."""
import json
import time

from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.research_worker import build_research_worker
from defence_studio.storage.artifacts import ArtifactStore
from conftest import SECRET, access_token


def test_enabled_api_worker_processes_job_and_stops_on_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_RESEARCH_WORKER", "1")
    db, root = tmp_path / "workflow.sqlite3", tmp_path / "storage"
    workers = []

    def factory(database_file, storage_path):
        def discover(context, cancel):
            saved = ArtifactStore(storage_path).write_bytes(
                context.job_id, f"jobs/{context.job_id}/empty.json",
                json.dumps({"candidates": [], "returned": 0}).encode(), "application/json")
            return [{"artifact_type": "discovery_result", "uri": saved.uri,
                     "sha256": saved.sha256, "schema_version": "defence-studio.discovery-result.v2"}]
        worker = build_research_worker(database_file, storage_path, discovery_handler=discover)
        workers.append(worker)
        return worker

    monkeypatch.setattr("defence_studio.research_worker.build_research_worker", factory)
    app = create_app(db, SECRET, root)
    headers = {"Authorization": "Bearer " + access_token("worker-owner"), "Idempotency-Key": "one-job"}
    with TestClient(app) as client:
        response = client.post("/api/jobs", headers=headers, json={"topic": "India defence", "language": "en"})
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        assert client.post(f"/api/jobs/{job_id}/discovery/requests", headers=headers,
                           json={"topic": "India defence", "language": "en"}).status_code == 201
        assert client.post(f"/api/jobs/{job_id}/queue", headers=headers).status_code == 200
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
            if job["status"] == "AWAITING_REVIEW":
                break
            time.sleep(0.05)
        assert job["status"] == "AWAITING_REVIEW", job["status"]
        assert job["stages"][5]["status"] == "PENDING"
    assert workers[0]._shutdown.is_set()
    assert not app.state.research_worker_thread.is_alive()
    # A fresh lifespan starts a fresh consumer and preserves the review boundary.
    with TestClient(app) as client:
        assert len(workers) == 2
        assert client.get(f"/api/jobs/{job_id}", headers=headers).json()["status"] == "AWAITING_REVIEW"
        assert len(client.get("/api/jobs", headers=headers).json()["jobs"]) == 1
    assert workers[1]._shutdown.is_set()
    assert not app.state.research_worker_thread.is_alive()


def test_api_worker_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFENCE_STUDIO_RESEARCH_WORKER", raising=False)
    def unexpected(*args, **kwargs):
        raise AssertionError("An API-only process must not start the research consumer")
    monkeypatch.setattr("defence_studio.research_worker.build_research_worker", unexpected)
    with TestClient(create_app(tmp_path / "workflow.sqlite3", SECRET, tmp_path / "storage")) as client:
        headers = {"Authorization": "Bearer " + access_token("worker-owner"), "Idempotency-Key": "one-job"}
        job = client.post("/api/jobs", headers=headers, json={"topic": "India defence"}).json()
        assert client.post(f"/api/jobs/{job['job_id']}/queue", headers=headers).json()["status"] == "QUEUED"
