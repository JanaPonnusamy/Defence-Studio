"""Standalone API: readiness, capabilities, validation, lifecycle, path safety.

The app must start and serve even though the real model is unavailable.
"""
import json

import pytest
from fastapi.testclient import TestClient

from align_engine.api.app import create_app
from conftest import build_request, make_config


@pytest.fixture
def client(tmp_path):
    config = make_config(tmp_path)
    app = create_app(config)
    with TestClient(app) as c:
        c.config = config
        yield c


def _payload(config, **kw):
    return json.loads(build_request(config, **kw).model_dump_json())


def test_health_reports_real_alignment_unavailable(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["real_alignment_available"] is False  # honest, app still serves


def test_capabilities_distinguish_declared_and_tested(client):
    body = client.get("/capabilities").json()
    engines = {e["engine_name"]: e for e in body["engines"]}
    real = engines["torchaudio-forced-aligner"]
    assert real["available"] is False
    assert "ta" in real["declared_languages"] and real["tested_languages"] == []
    synthetic = engines["deterministic-test-aligner"]
    assert synthetic["is_synthetic"] is True


def test_validate_endpoint_flags_synthetic_opt_in(client):
    payload = _payload(client.config, allow_synthetic=False)
    body = client.post("/alignments/validate", json=payload).json()
    assert body["valid"] is True and body["synthetic_opt_in_required"] is True


def test_validate_rejects_unsafe_path(client):
    payload = _payload(client.config)
    payload["audio_path"] = "../escape.wav"
    resp = client.post("/alignments/validate", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "unsafe_path"


def test_create_get_export_lifecycle(client):
    payload = _payload(client.config)
    created = client.post("/alignments", json=payload)
    assert created.status_code == 201 and created.json()["status"] == "COMPLETED"
    job_id = payload["alignment_job_id"]
    got = client.get(f"/alignments/{job_id}")
    assert got.status_code == 200 and got.json()["status"] == "COMPLETED"
    artifact = client.get(f"/alignments/{job_id}/artifact").json()
    assert artifact["is_synthetic"] is True
    export = client.get(f"/alignments/{job_id}/export")
    assert "attachment" in export.headers["content-disposition"]
    assert export.json()["artifact_sha256"] == artifact["artifact_sha256"]


def test_create_is_idempotent_over_http(client):
    payload = _payload(client.config)
    first = client.post("/alignments", json=payload).json()
    second = client.post("/alignments", json=payload).json()
    assert first["artifact_sha256"] == second["artifact_sha256"]


def test_malformed_request_returns_422(client):
    resp = client.post("/alignments", json={"schema_version": "defence-align.request.v1"})
    assert resp.status_code == 422


def test_cancel_and_unknown_job(client):
    payload = _payload(client.config)
    # Cancel after completion is a conflict (terminal state).
    client.post("/alignments", json=payload)
    resp = client.post(f"/alignments/{payload['alignment_job_id']}/cancel")
    assert resp.status_code == 409 and resp.json()["code"] == "job_state_invalid"
    missing = client.get("/alignments/does-not-exist")
    assert missing.status_code == 404 and missing.json()["code"] == "job_not_found"


def test_unsafe_path_create_fails_closed_not_faked(client):
    payload = _payload(client.config)
    payload["audio_path"] = "../escape.wav"
    created = client.post("/alignments", json=payload)
    # The job is created but fails closed with the unsafe_path reason — never a
    # fabricated success.
    assert created.status_code == 201
    assert created.json()["status"] == "FAILED"
    assert created.json()["error_code"] == "unsafe_path"
