"""F3 acquisition executed through the verified F2 durable worker."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from defence_studio.acquisition.handler import (
    ACQUISITION_MANIFEST_ARTIFACT_TYPE,
    build_acquisition_handler,
)
from defence_studio.acquisition.providers.youtube import SubtitleTrack, YoutubeSubtitles
from defence_studio.acquisition.service import default_registry
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.api.app import create_app
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import initialize_database
from defence_studio.services.jobs import JobService
from defence_studio.services.worker import Worker, WorkerService
from defence_studio.storage.artifacts import ArtifactStore

SECRET = "phase-one-test-signing-secret-32-bytes-minimum"
OWNER = "f3-owner"
PUBLIC = "93.184.216.34"
HTML = (
    b"<html lang='ta'><head><title>S-400 update</title>"
    b"<meta property='og:site_name' content='PIB'></head><body>x</body></html>"
)


def _resolver(host, port):
    return [PUBLIC]


def _http_factory(counter):
    def handler(_req):
        counter["n"] += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, content=HTML)

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    return factory


def _youtube_backend(url, languages, max_bytes, cancel):
    return YoutubeSubtitles(
        video_id="vid123", title="Navy briefing", channel="Indian Navy",
        upload_date="20260110", webpage_url=url,
        tracks=(SubtitleTrack(language="en", is_auto=False, ext="vtt", content=b"WEBVTT\n\nhi"),),
    )


def _request_doc(job_id):
    return {
        "schema_version": "defence-studio.acquisition-request.v1",
        "job_id": job_id,
        "targets": [
            {"target_id": "a", "url": "https://pib.example/s400", "method": "http_page"},
            {"target_id": "b", "url": "https://www.youtube.com/watch?v=vid123",
             "method": "youtube_subtitles", "language_preferences": ["en"]},
        ],
    }


def _handlers(store, counter):
    def trivial(_context, _cancel):
        return None

    def produce_request(context, _cancel):
        data = json.dumps(_request_doc(context.job_id)).encode("utf-8")
        uri = f"jobs/{context.job_id}/research/acquisition_request.json"
        stored = store.write_bytes(context.job_id, uri, data, "application/json")
        return [{
            "artifact_type": "acquisition_request", "uri": stored.uri,
            "sha256": stored.sha256, "schema_version": "defence-studio.acquisition-request.v1",
        }]

    acquire = build_acquisition_handler(
        store,
        registry=default_registry(youtube_backend=_youtube_backend),
        guard=UrlGuard(resolver=_resolver),
        client_factory=_http_factory(counter),
    )
    return {
        JOB_STAGES[0]: trivial,
        JOB_STAGES[1]: produce_request,
        JOB_STAGES[2]: acquire,
    }


def _new_queued_job(db, key="f3"):
    initialize_database(db)
    job = JobService(db).create(OWNER, key, "S-400 developments", "ta")
    WorkerService(db).queue(job["job_id"], OWNER)
    return job["job_id"]


def test_full_acquisition_runs_through_durable_worker(tmp_path):
    db = tmp_path / "db.sqlite3"
    store = ArtifactStore(tmp_path / "storage")
    counter = {"n": 0}
    job_id = _new_queued_job(db)

    worker = Worker(db, "w1", _handlers(store, counter), retry_safe_stages=set(_handlers(store, counter)))
    # run the registered stages (foundation, request producer, acquisition)
    while worker.run_once():
        pass

    state = JobService(db).get(job_id, OWNER)
    completed = {s["stage_key"]: s["status"] for s in state["stages"]}
    assert completed[JOB_STAGES[0]] == "COMPLETED"
    assert completed[JOB_STAGES[1]] == "COMPLETED"
    assert completed[JOB_STAGES[2]] == "COMPLETED"
    # Downstream stages have no handler, so the job waits as QUEUED, not complete.
    assert state["status"] == "QUEUED"

    manifests = [a for a in state["artifacts"] if a["artifact_type"] == ACQUISITION_MANIFEST_ARTIFACT_TYPE]
    assert len(manifests) == 1
    manifest_ref = manifests[0]
    # DB-recorded hash matches the actual manifest bytes on disk.
    assert store.sha256_of(job_id, manifest_ref["uri"]) == manifest_ref["sha256"]

    manifest = json.loads(store.read_text(job_id, manifest_ref["uri"]))
    assert (manifest["requested"], manifest["succeeded"], manifest["failed"]) == (2, 2, 0)
    by_target = {s["target_id"]: s for s in manifest["sources"]}
    assert by_target["a"]["source_type"] == "web_page"
    assert by_target["a"]["declared_metadata"]["title"] == "S-400 update"
    assert by_target["b"]["source_type"] == "youtube_video"
    assert by_target["b"]["original_metadata"]["has_video_download"] is False

    # Every per-source artifact file exists and hashes to the manifest value.
    for source in manifest["sources"]:
        for art in source["artifacts"]:
            assert store.exists(job_id, art["uri"])
            assert store.sha256_of(job_id, art["uri"]) == art["sha256"]


def test_missing_request_artifact_fails_the_stage_closed(tmp_path):
    db = tmp_path / "db.sqlite3"
    store = ArtifactStore(tmp_path / "storage")
    job_id = _new_queued_job(db)

    def trivial(_context, _cancel):
        return None

    acquire = build_acquisition_handler(store, registry=default_registry(youtube_backend=_youtube_backend),
                                        guard=UrlGuard(resolver=_resolver))
    # Stage 1 produces NO request artifact, so stage 2 must fail closed.
    handlers = {JOB_STAGES[0]: trivial, JOB_STAGES[1]: trivial, JOB_STAGES[2]: acquire}
    worker = Worker(db, "w1", handlers, retry_safe_stages=set(handlers))
    while worker.run_once():
        pass

    state = JobService(db).get(job_id, OWNER)
    stage2 = next(s for s in state["stages"] if s["stage_key"] == JOB_STAGES[2])
    assert stage2["status"] == "FAILED"
    assert "acquisition_request" in stage2["error"]


def test_retry_through_worker_reuses_artifacts_without_redownload(tmp_path):
    db = tmp_path / "db.sqlite3"
    store = ArtifactStore(tmp_path / "storage")
    counter = {"n": 0}
    job_id = _new_queued_job(db)
    handlers = _handlers(store, counter)

    # Drive foundation + request stages to completion via the worker.
    producer = Worker(db, "w-prod", {JOB_STAGES[0]: handlers[JOB_STAGES[0]],
                                     JOB_STAGES[1]: handlers[JOB_STAGES[1]]},
                      retry_safe_stages={JOB_STAGES[0], JOB_STAGES[1]})
    while producer.run_once():
        pass

    ws = WorkerService(db)
    acquire = handlers[JOB_STAGES[2]]

    # Attempt 1: claim the acquisition stage, run the handler (which writes the
    # per-source files + reuse sidecar), then simulate a crash by letting the
    # lease expire before the result commits.
    t0 = datetime(2026, 10, 10, tzinfo=timezone.utc)
    ctx1 = ws.claim_next("w-a", {JOB_STAGES[2]}, lease_seconds=5, now=t0)
    assert ctx1 is not None
    import threading
    acquire(ctx1, threading.Event())          # side effects happen here
    assert counter["n"] == 1                   # youtube backend + 1 http GET
    recovered = ws.recover_expired(t0 + timedelta(seconds=6))
    assert recovered == 1

    # Attempt 2: re-claim after the retry delay; the handler reuses the stored,
    # hash-verified artifacts instead of re-fetching, then commits.
    t1 = t0 + timedelta(seconds=8)
    ctx2 = ws.claim_next("w-b", {JOB_STAGES[2]}, lease_seconds=30, now=t1)
    assert ctx2 is not None and ctx2.attempt == 2
    outputs = acquire(ctx2, threading.Event())
    ws.complete(ctx2, outputs, now=t1 + timedelta(seconds=1))
    assert counter["n"] == 1                   # no re-download on retry

    state = JobService(db).get(job_id, OWNER)
    stage2 = next(s for s in state["stages"] if s["stage_key"] == JOB_STAGES[2])
    assert stage2["status"] == "COMPLETED"
    manifest_ref = next(a for a in state["artifacts"]
                        if a["artifact_type"] == ACQUISITION_MANIFEST_ARTIFACT_TYPE)
    manifest = json.loads(store.read_text(job_id, manifest_ref["uri"]))
    assert manifest["succeeded"] == 2


def test_another_user_cannot_read_the_acquired_job(tmp_path, token):
    db = tmp_path / "db.sqlite3"
    store = ArtifactStore(tmp_path / "storage")
    counter = {"n": 0}
    job_id = _new_queued_job(db)
    worker = Worker(db, "w1", _handlers(store, counter), retry_safe_stages=set(_handlers(store, counter)))
    while worker.run_once():
        pass

    app = create_app(db, SECRET)
    with TestClient(app) as client:
        owner_view = client.get(f"/api/jobs/{job_id}",
                                headers={"Authorization": f"Bearer {token(OWNER)}"})
        other_view = client.get(f"/api/jobs/{job_id}",
                                headers={"Authorization": f"Bearer {token('intruder')}"})
    assert owner_view.status_code == 200
    assert any(a["artifact_type"] == ACQUISITION_MANIFEST_ARTIFACT_TYPE
               for a in owner_view.json()["artifacts"])
    assert other_view.status_code == 404
