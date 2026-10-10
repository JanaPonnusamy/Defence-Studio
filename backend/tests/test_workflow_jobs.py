from concurrent.futures import ThreadPoolExecutor

import pytest

from defence_studio.api.app import create_app
from defence_studio.contracts.jobs import JOB_STAGES
def create(client, headers, key="request-1", topic="S-400 developments"):
    return client.post(
        "/api/jobs", headers={**headers, "Idempotency-Key": key},
        json={"topic": topic, "language": "ta"},
    )


def test_restart_persists_job_transition_and_versioned_artifact(client, auth_headers, tmp_path):
    response = create(client, auth_headers)
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert [stage["stage_key"] for stage in response.json()["stages"]] == list(JOB_STAGES)

    running = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "RUNNING"},
    )
    assert running.status_code == 200
    assert running.json()["status"] == "RUNNING"
    failed = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "FAILED", "error": "test failure record"},
    )
    assert failed.status_code == 200
    digest = "a" * 64
    artifact = client.post(
        f"/api/jobs/{job_id}/artifacts", headers=auth_headers,
        json={"stage_key": "application_foundation", "artifact_type": "manifest",
              "uri": f"jobs/{job_id}/manifest.json", "sha256": digest,
              "schema_version": "job-manifest.v1"},
    )
    assert artifact.status_code == 201
    assert artifact.json()["version"] == 1
    artifact_v2 = client.post(
        f"/api/jobs/{job_id}/artifacts", headers=auth_headers,
        json={"stage_key": "application_foundation", "artifact_type": "manifest",
              "uri": f"jobs/{job_id}/manifest-v2.json", "sha256": "b" * 64,
              "schema_version": "job-manifest.v1"},
    )
    assert artifact_v2.status_code == 201
    assert artifact_v2.json()["version"] == 2

    restarted = create_app(tmp_path / "workflow.sqlite3", "phase-one-test-signing-secret-32-bytes-minimum")
    from fastapi.testclient import TestClient
    with TestClient(restarted) as reopened:
        state = reopened.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert state.status_code == 200
    body = state.json()
    stage = next(value for value in body["stages"] if value["stage_key"] == "application_foundation")
    assert stage["status"] == "FAILED"
    assert body["status"] == "FAILED"
    assert stage["error"] == "test failure record"
    assert stage["updated_at"]
    assert body["artifacts"][0]["sha256"] == digest
    assert body["artifacts"][0]["schema_version"] == "job-manifest.v1"
    assert [item["version"] for item in body["artifacts"]] == [1, 2]


def test_concurrent_duplicate_requests_return_one_stable_job(client, auth_headers):
    def submit(_):
        return create(client, auth_headers, key="concurrent-key").json()["job_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        identifiers = list(pool.map(submit, range(24)))
    assert len(set(identifiers)) == 1


def test_idempotency_key_rejects_different_payload(client, auth_headers):
    first = create(client, auth_headers, key="same-key", topic="Rafale update")
    conflict = create(client, auth_headers, key="same-key", topic="S-400 update")
    assert first.status_code == 201
    assert conflict.status_code == 409


def test_invalid_transition_is_rejected_and_failed_transition_requires_error(client, auth_headers):
    job_id = create(client, auth_headers).json()["job_id"]
    completed = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "COMPLETED"},
    )
    assert completed.status_code == 409
    failed_without_detail = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "FAILED"},
    )
    assert failed_without_detail.status_code == 422
    running = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "RUNNING"},
    )
    assert running.status_code == 200
    complete = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "COMPLETED"},
    )
    assert complete.status_code == 200
    rerun = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "RUNNING"},
    )
    assert rerun.status_code == 409


def test_pipeline_stages_cannot_start_out_of_order(client, auth_headers):
    job_id = create(client, auth_headers).json()["job_id"]
    blocked = client.post(
        f"/api/jobs/{job_id}/stages/news_feed_and_topic_discovery/transitions",
        headers=auth_headers, json={"status": "RUNNING"},
    )
    assert blocked.status_code == 409
    skipped_out_of_order = client.post(
        f"/api/jobs/{job_id}/stages/news_feed_and_topic_discovery/transitions",
        headers=auth_headers, json={"status": "SKIPPED"},
    )
    assert skipped_out_of_order.status_code == 409
    start = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "RUNNING"},
    )
    finish = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=auth_headers, json={"status": "COMPLETED"},
    )
    allowed = client.post(
        f"/api/jobs/{job_id}/stages/news_feed_and_topic_discovery/transitions",
        headers=auth_headers, json={"status": "RUNNING"},
    )
    assert start.status_code == finish.status_code == allowed.status_code == 200


def test_user_cannot_read_or_mutate_another_users_job(client, auth_headers, token):
    job_id = create(client, auth_headers).json()["job_id"]
    other = {"Authorization": f"Bearer {token('user-2')}"}
    read = client.get(f"/api/jobs/{job_id}", headers=other)
    mutate = client.post(
        f"/api/jobs/{job_id}/stages/application_foundation/transitions",
        headers=other, json={"status": "RUNNING"},
    )
    assert read.status_code == 404
    assert mutate.status_code == 404


def test_unauthenticated_and_invalid_tokens_are_rejected(client):
    missing = client.post("/api/jobs", headers={"Idempotency-Key": "x"},
                          json={"topic": "test", "language": "ta"})
    invalid = client.get("/api/jobs/not-a-job", headers={"Authorization": "Bearer invalid"})
    assert missing.status_code == 401
    assert invalid.status_code == 401
