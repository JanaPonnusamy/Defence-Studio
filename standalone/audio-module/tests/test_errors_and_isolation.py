from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.init_db import init_db
from app.main import app

client = TestClient(app)


def test_init_db_idempotent():
    init_db()
    init_db()  # must not raise on repeated calls


def test_not_found_returns_machine_readable_error():
    resp = client.get("/references/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"


def test_job_not_found():
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_tts_unknown_provider_rejected():
    resp = client.post("/tts/synthesize", json={"text": "hi", "provider": "nonexistent-provider"})
    # Submission succeeds (job queued); failure surfaces asynchronously as a FAILED job.
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    import time
    for _ in range(40):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("SUCCEEDED", "FAILED"):
            assert job["status"] == "FAILED"
            assert job["error_message"]
            return
        time.sleep(0.25)
    raise AssertionError("job did not complete in time")


def test_url_ingestion_disabled_by_default():
    from pathlib import Path
    from app.pipeline.ingest import ingest_url
    from app.errors import UnsupportedMediaError
    import pytest
    with pytest.raises(UnsupportedMediaError):
        ingest_url("ref-1", "https://example.com/video", {"authorized": True})


def test_unsupported_extension_rejected(tmp_path):
    from app.pipeline.ingest import ingest_upload
    from app.errors import UnsupportedMediaError
    import pytest
    bogus = tmp_path / "file.exe"
    bogus.write_bytes(b"not audio")
    with pytest.raises(UnsupportedMediaError):
        ingest_upload("ref-x", bogus, "file.exe")
