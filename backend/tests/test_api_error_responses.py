"""Start research error contract: server faults answer in JSON and leave no orphan job.

Regression for 2026-10-29: a too-deep DEFENCE_STUDIO_STORAGE_ROOT made the
discovery-request write raise StoragePathError after POST /api/jobs had already
created the job. Starlette answered with a plain-text 500, the UI showed
"unreadable response", and every retry left another DRAFT job.
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import SECRET
from defence_studio.api.app import create_app
from defence_studio.services.discovery import DiscoveryApiService
from defence_studio.storage import artifacts
from defence_studio.storage.artifacts import ArtifactStore, StoragePathTooLong

TAMIL_TOPIC = "இலங்கை மீது இராணுவத் தாக்குதல் நடக்கக்கூடுமா?"


@pytest.fixture
def lenient_client(tmp_path):
    """Like the conftest client, but returns 500 responses instead of re-raising."""
    app = create_app(tmp_path / "errors.sqlite3", SECRET, tmp_path / "store")
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _create_job(client, headers, topic=TAMIL_TOPIC, language="en"):
    return client.post("/api/jobs", json={"topic": topic, "language": language},
                       headers={**headers, "Idempotency-Key": str(uuid.uuid4())})


def test_start_research_with_tamil_topic_and_source_url_succeeds(client, auth_headers):
    created = _create_job(client, auth_headers)
    assert created.status_code == 201
    job = created.json()
    assert job["topic"] == TAMIL_TOPIC and job["status"] == "DRAFT"
    request = client.post(f"/api/jobs/{job['job_id']}/discovery/requests", headers=auth_headers, json={
        "topic": TAMIL_TOPIC, "language": "en", "seed_urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]})
    assert request.status_code == 201, request.text
    queued = client.post(f"/api/jobs/{job['job_id']}/queue", headers=auth_headers)
    assert queued.status_code == 200, queued.text
    assert client.get(f"/api/jobs/{job['job_id']}", headers=auth_headers).json()["topic"] == TAMIL_TOPIC


def test_too_deep_storage_root_is_refused_before_any_job_exists(lenient_client, auth_headers, monkeypatch):
    def too_long(self, job_id, relative_uri):
        raise StoragePathTooLong("Artifact path is 249 characters, over the Windows limit of 240")
    monkeypatch.setattr(ArtifactStore, "ensure_path_fits", too_long)

    response = _create_job(lenient_client, auth_headers)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["error_code"] == "STORAGE_PATH_TOO_LONG"
    assert "DEFENCE_STUDIO_STORAGE_ROOT" in body["detail"] and body["error_id"] in body["detail"]
    assert lenient_client.get("/api/jobs", headers=auth_headers).json()["jobs"] == []


@pytest.mark.skipif(os.name != "nt", reason="the path-length limit only applies on Windows")
def test_real_windows_path_limit_triggers_the_preflight(lenient_client, auth_headers, monkeypatch):
    monkeypatch.setattr(artifacts, "MAX_WINDOWS_PATH", 40)
    response = _create_job(lenient_client, auth_headers)
    assert response.status_code == 500 and response.json()["error_code"] == "STORAGE_PATH_TOO_LONG"
    assert lenient_client.get("/api/jobs", headers=auth_headers).json()["jobs"] == []


def test_preflight_checks_the_same_path_the_request_write_uses(tmp_path):
    service = DiscoveryApiService(tmp_path / "x.sqlite3", tmp_path / "store")
    job_id = str(uuid.uuid4())
    uri = service._request_uri(job_id, "a" * 64)
    assert uri == f"jobs/{job_id}/discovery/{'a' * 64}.json"
    service.preflight_storage()  # a short root fits


def test_unexpected_error_returns_json_without_internal_details(lenient_client, auth_headers, monkeypatch, caplog):
    job_id = _create_job(lenient_client, auth_headers).json()["job_id"]

    def explode(self, *args, **kwargs):
        raise RuntimeError(r"secret-token at C:\internal\path")
    monkeypatch.setattr(DiscoveryApiService, "create_request", explode)

    response = lenient_client.post(f"/api/jobs/{job_id}/discovery/requests", headers=auth_headers,
                                   json={"topic": "t", "language": "en"})

    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR" and body["error_id"] in body["detail"]
    assert "secret-token" not in response.text and "path" not in response.text and "RuntimeError" not in response.text
    logged = [r for r in caplog.records if body["error_id"] in r.getMessage()]
    assert logged and logged[0].exc_info is not None  # the operator can find the traceback by reference


@pytest.mark.parametrize("payload", [{"topic": "", "language": "en"}, {"language": "en"}])
def test_invalid_job_fields_get_readable_validation_errors(client, auth_headers, payload):
    response = client.post("/api/jobs", json=payload, headers={**auth_headers, "Idempotency-Key": "k"})
    assert response.status_code in (400, 422)
    assert response.json()["detail"]


@pytest.mark.parametrize("url", ["ftp://example.com/file", "not a url", "javascript:alert(1)"])
def test_unusable_source_urls_are_rejected_in_json_and_job_stays_editable(client, auth_headers, url):
    job_id = _create_job(client, auth_headers).json()["job_id"]
    response = client.post(f"/api/jobs/{job_id}/discovery/requests", headers=auth_headers,
                           json={"topic": "t", "language": "en", "seed_urls": [url]})
    assert response.status_code in (400, 422), response.text
    assert response.json()["detail"]
    job = client.get(f"/api/jobs/{job_id}", headers=auth_headers).json()
    assert job["status"] == "DRAFT"
    assert client.get(f"/api/jobs/{job_id}/discovery/requests", headers=auth_headers).json()["request"] is None
