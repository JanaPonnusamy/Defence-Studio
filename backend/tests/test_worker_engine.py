from __future__ import annotations

import multiprocessing as mp
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import SCHEMA_VERSION, initialize_database
from defence_studio.services.jobs import JobService
from defence_studio.services.worker import (
    ClaimLost,
    InvalidQueueTransition,
    Worker,
    WorkerService,
)

SECRET = "phase-one-test-signing-secret-32-bytes-minimum"
OWNER = "worker-test-user"


def _new_job(path: Path, key: str = "worker-test") -> str:
    initialize_database(path)
    job = JobService(path).create(OWNER, key, "Rafale update", "ta")
    WorkerService(path).queue(job["job_id"], OWNER)
    return job["job_id"]


def _claim_process(database_file: str, worker_id: str, barrier, results):
    store = WorkerService(Path(database_file))
    barrier.wait(timeout=10)
    claim = store.claim_next(worker_id, {JOB_STAGES[0]}, lease_seconds=30)
    results.put(claim.claim_id if claim else None)


def _fail_retry_attempt(database_file: str, started_at: str, results):
    started = datetime.fromisoformat(started_at)
    store = WorkerService(Path(database_file))
    claim = store.claim_next("retry-worker", {JOB_STAGES[0]}, 20, started)
    if claim is None:
        results.put(None)
        return
    store.fail(claim, f"controlled failure {claim.attempt}", started + timedelta(milliseconds=1))
    results.put(claim.attempt)


def _crashing_worker(database_file: str, marker_file: str):
    def stop_inside_stage(context, _cancel):
        Path(marker_file).write_text(context.claim_id, encoding="utf-8")
        while True:
            time.sleep(1)

    worker = Worker(
        Path(database_file), "crash-worker", {JOB_STAGES[1]: stop_inside_stage},
        lease_seconds=0.7, retry_safe_stages={JOB_STAGES[1]},
    )
    worker.run_once()


def _app_client(path: Path):
    app = create_app(path, SECRET)
    return TestClient(app)


def test_queued_job_survives_backend_restart(client, auth_headers, token):
    created = client.post(
        "/api/jobs", headers={**auth_headers, "Idempotency-Key": "queue-restart"},
        json={"topic": "Rafale update", "language": "ta"},
    )
    job_id = created.json()["job_id"]
    queued = client.post(f"/api/jobs/{job_id}/queue", headers=auth_headers)
    assert queued.status_code == 200
    assert queued.json()["status"] == "QUEUED"
    assert client.post(f"/api/jobs/{job_id}/queue", headers=auth_headers).json()["status"] == "QUEUED"

    headers = auth_headers
    with _app_client(client.app.state.database_file) as restarted:
        state = restarted.get(f"/api/jobs/{job_id}", headers=headers)
    assert state.status_code == 200
    assert state.json()["status"] == "QUEUED"
    assert state.json()["queued_at"]


def test_live_claim_is_not_stolen_and_dispatch_is_idempotent(tmp_path):
    path = tmp_path / "queue.sqlite3"
    job_id = _new_job(path)
    store = WorkerService(path)
    first = store.claim_next("worker-a", {JOB_STAGES[0]}, lease_seconds=30)
    assert first and first.job_id == job_id
    assert store.claim_next("worker-b", {JOB_STAGES[0]}, lease_seconds=30) is None
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE job_id = ? AND status = 'ACTIVE'", (job_id,)
        ).fetchone()[0] == 1


def test_two_process_workers_cannot_claim_the_same_stage(tmp_path):
    path = tmp_path / "multiprocess.sqlite3"
    _new_job(path)
    context = mp.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    workers = [
        context.Process(target=_claim_process, args=(str(path), f"worker-{index}", barrier, results))
        for index in range(2)
    ]
    for process in workers:
        process.start()
    for process in workers:
        process.join(timeout=20)
        if process.is_alive():
            process.terminate()
            process.join()
            pytest.fail("claiming worker process did not exit")
        assert process.exitcode == 0
    claims = [results.get(timeout=3), results.get(timeout=3)]
    assert sum(value is not None for value in claims) == 1
    assert len({value for value in claims if value}) == 1


def test_expired_and_superseded_workers_cannot_commit(tmp_path):
    path = tmp_path / "fencing.sqlite3"
    job_id = _new_job(path)
    store = WorkerService(path)
    started = datetime(2026, 10, 10, tzinfo=timezone.utc)
    stale = store.claim_next("worker-old", {JOB_STAGES[0]}, 2, started)
    assert stale
    with pytest.raises(ClaimLost):
        store.complete(stale, now=started + timedelta(seconds=3))
    assert store.recover_expired(started + timedelta(seconds=3)) == 1
    retry_at = started + timedelta(seconds=4)
    current = store.claim_next("worker-new", {JOB_STAGES[0]}, 30, retry_at)
    assert current and current.claim_id != stale.claim_id
    with pytest.raises(ClaimLost):
        store.complete(stale, now=retry_at + timedelta(milliseconds=1))
    store.complete(current, now=retry_at + timedelta(milliseconds=2))
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT status FROM workflow_stages WHERE job_id = ? AND stage_key = ?",
            (job_id, JOB_STAGES[0]),
        ).fetchone()[0] == "COMPLETED"


def test_process_termination_recovers_claim_without_losing_completed_artifacts(tmp_path, token):
    path = tmp_path / "crash.sqlite3"
    job_id = _new_job(path)
    store = WorkerService(path)

    def finish_foundation(context, _cancel):
        return [{"artifact_type": "foundation", "uri": f"jobs/{job_id}/foundation.json",
                 "sha256": "a" * 64, "schema_version": "foundation.v1"}]

    assert Worker(
        path, "foundation-worker", {JOB_STAGES[0]: finish_foundation},
        retry_safe_stages={JOB_STAGES[0]},
    ).run_once()
    marker = tmp_path / "stage-started.txt"
    process = mp.get_context("spawn").Process(
        target=_crashing_worker, args=(str(path), str(marker))
    )
    process.start()
    deadline = time.monotonic() + 15
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), "worker did not enter the controlled stage"
    process.terminate()
    process.join(timeout=10)
    assert not process.is_alive()
    assert process.exitcode != 0

    time.sleep(0.8)
    # Constructing a new app runs the actual lifespan startup recovery path.
    with _app_client(path) as restarted:
        response = restarted.get(
            f"/api/jobs/{job_id}",
            headers={"Authorization": f"Bearer {token(OWNER)}"},
        )
    assert response.status_code == 200
    state = JobService(path).get(job_id, OWNER)
    assert state["status"] == "QUEUED"
    foundation = next(stage for stage in state["stages"] if stage["stage_key"] == JOB_STAGES[0])
    crashed_stage = next(stage for stage in state["stages"] if stage["stage_key"] == JOB_STAGES[1])
    assert foundation["status"] == "COMPLETED"
    assert crashed_stage["status"] == "FAILED"
    assert crashed_stage["attempt_count"] == 1
    assert crashed_stage["error"]
    assert len(state["artifacts"]) == 1
    assert state["artifacts"][0]["uri"].endswith("foundation.json")
    claim = next(item for item in state["claims"] if item["stage_key"] == JOB_STAGES[1])
    assert claim["worker_id"] == "crash-worker"
    assert claim["status"] == "EXPIRED"


def test_retry_count_and_terminal_limit_survive_restart(tmp_path):
    path = tmp_path / "retry.sqlite3"
    job_id = _new_job(path, "retry-limit")
    store = WorkerService(path)
    clock = datetime(2026, 10, 10, tzinfo=timezone.utc)
    for attempt in range(1, 4):
        context = mp.get_context("spawn")
        results = context.Queue()
        process = context.Process(
            target=_fail_retry_attempt, args=(str(path), clock.isoformat(), results)
        )
        process.start()
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join()
            pytest.fail("retry worker process did not exit")
        assert process.exitcode == 0
        assert results.get(timeout=3) == attempt
        initialize_database(path)
        state = JobService(path).get(job_id, OWNER)
        stage = state["stages"][0]
        assert stage["attempt_count"] == attempt
        assert stage["max_attempts"] == 3
        if attempt < 3:
            assert state["status"] == "QUEUED"
            clock += timedelta(seconds=(2 ** (attempt - 1)) + 2)
        else:
            assert state["status"] == "FAILED"
            assert stage["retry_after"] is None
    with pytest.raises(InvalidQueueTransition):
        store.queue(job_id, OWNER)
    assert store.claim_next("after-terminal", {JOB_STAGES[0]}, 20, clock + timedelta(days=1)) is None


def test_f1_schema_migrates_additively_and_preserves_job_and_artifacts(tmp_path):
    path = tmp_path / "from-f1.sqlite3"
    initialize_database(path)
    jobs = JobService(path)
    job = jobs.create(OWNER, "legacy-row", "Rafale update", "ta")
    job_id = job["job_id"]
    jobs.transition(job_id, OWNER, JOB_STAGES[0], "RUNNING")
    jobs.add_artifact(job_id, OWNER, JOB_STAGES[0], "manifest",
                      f"jobs/{job_id}/manifest.json", "c" * 64, "manifest.v1")
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE worker_claims")
        db.execute("ALTER TABLE workflow_jobs DROP COLUMN queued_at")
        db.execute("ALTER TABLE workflow_stages DROP COLUMN retry_after")
        db.execute("ALTER TABLE workflow_stages DROP COLUMN max_attempts")
        db.execute("ALTER TABLE workflow_stages DROP COLUMN attempt_count")
        db.execute("UPDATE schema_info SET version = 1 WHERE singleton = 1")

    initialize_database(path)
    state = jobs.get(job_id, OWNER)
    assert SCHEMA_VERSION == 10
    assert state["topic"] == "Rafale update"
    assert state["stages"][0]["status"] == "RUNNING"
    assert state["stages"][0]["attempt_count"] == 0
    assert state["artifacts"][0]["sha256"] == "c" * 64
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_info").fetchone()[0] == 10


def test_unsupported_database_schema_fails_closed(tmp_path):
    path = tmp_path / "unsupported.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE schema_info (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)")
        db.execute("INSERT INTO schema_info VALUES (1, 77)")
    with pytest.raises(RuntimeError, match="Unsupported Defence Studio schema version: 77"):
        initialize_database(path)
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "workflow_jobs" not in tables
    assert "worker_claims" not in tables


def test_handler_registration_requires_explicit_retry_safe_optin(tmp_path):
    path = tmp_path / "registration.sqlite3"
    initialize_database(path)
    handler = lambda context, _cancel: None

    # No handlers at all fails closed.
    with pytest.raises(ValueError, match="At least one"):
        Worker(path, "worker", {})

    # A handler with no retry-safe declaration fails closed.
    with pytest.raises(ValueError, match="explicitly declared retry-safe"):
        Worker(path, "worker", {JOB_STAGES[0]: handler})

    # Declaring a different stage than the one registered fails closed.
    with pytest.raises(ValueError, match="explicitly declared retry-safe"):
        Worker(path, "worker", {JOB_STAGES[0]: handler}, retry_safe_stages={JOB_STAGES[1]})

    # Declaring a retry-safe stage that has no handler fails closed.
    with pytest.raises(ValueError, match="explicitly declared retry-safe"):
        Worker(
            path, "worker", {JOB_STAGES[0]: handler},
            retry_safe_stages={JOB_STAGES[0], JOB_STAGES[1]},
        )

    # Exact match of handlers and retry-safe declarations is accepted.
    worker = Worker(path, "worker", {JOB_STAGES[0]: handler}, retry_safe_stages={JOB_STAGES[0]})
    assert set(worker.handlers) == {JOB_STAGES[0]}


def test_f1_stage_transitions_and_owner_checks_still_apply(client, auth_headers, token):
    response = client.post(
        "/api/jobs", headers={**auth_headers, "Idempotency-Key": "f1-worker-regression"},
        json={"topic": "Rafale update", "language": "ta"},
    )
    job_id = response.json()["job_id"]
    started = client.post(
        f"/api/jobs/{job_id}/stages/{JOB_STAGES[0]}/transitions",
        headers=auth_headers, json={"status": "RUNNING"},
    )
    assert started.status_code == 200
    other = {"Authorization": f"Bearer {token('someone-else')}"}
    assert client.post(f"/api/jobs/{job_id}/queue", headers=other).status_code == 404
    assert client.post(f"/api/jobs/{job_id}/queue", headers=auth_headers).status_code == 409
