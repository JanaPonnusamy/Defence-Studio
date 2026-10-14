from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_openapi_schema_generates():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema["paths"]
    for required_path in [
        "/references", "/references/{reference_id}", "/references/{reference_id}/process",
        "/jobs/{job_id}", "/audio/{audio_id}", "/transcripts/{transcript_id}",
        "/alignment/{alignment_id}", "/analysis/{analysis_id}",
        "/profiles/build", "/profiles", "/profiles/{profile_id}",
        "/tts/synthesize", "/generated/{generated_id}",
    ]:
        assert required_path in paths, f"Missing required contract path: {required_path}"


def test_error_responses_are_json_with_error_key():
    resp = client.get("/references/nonexistent")
    assert resp.headers["content-type"].startswith("application/json")
    assert "error" in resp.json()
