"""End-to-end API test: upload -> process (ingest/normalize/align/analyze)
-> build profile -> build performance plan -> synthesize (mock TTS) ->
validate. Exercises the real pipeline described in the spec, using the
synthetic test fixture audio, with job polling against the in-process job
queue (no external services)."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.db.init_db import init_db


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/jobs/{job_id}")
        job = resp.json()
        if job["status"] in ("SUCCEEDED", "FAILED"):
            return job
        time.sleep(0.3)
    raise TimeoutError(f"Job {job_id} did not finish in time")


def test_full_pipeline_end_to_end(fixture_wav_path, fixture_transcript_text):
    init_db()
    from app.main import app
    client = TestClient(app)

    assert client.get("/health").json()["status"] == "ok"

    with open(fixture_wav_path, "rb") as f:
        resp = client.post(
            "/references",
            data={
                "title": "E2E Test Reference",
                "language": "en",
                "rights_metadata": '{"authorized": true, "notes": "synthetic test fixture"}',
                "transcript_text": fixture_transcript_text,
                "transcript_format": "plain",
            },
            files={"audio_file": ("test_fixture.wav", f, "audio/wav")},
        )
    assert resp.status_code == 200, resp.text
    ref = resp.json()
    reference_id = ref["reference_id"]
    assert ref["status"] == "INGESTED"
    assert ref["audio_id"]
    assert ref["transcript_id"]

    resp = client.post(f"/references/{reference_id}/process")
    assert resp.status_code == 200, resp.text
    job = _wait_for_job(client, resp.json()["job_id"])
    assert job["status"] == "SUCCEEDED", job

    resp = client.get(f"/references/{reference_id}")
    ref = resp.json()
    assert ref["status"] == "ANALYZED"
    assert ref["alignment_id"]
    assert ref["analysis_id"]

    alignment = client.get(f"/alignment/{ref['alignment_id']}").json()
    assert alignment["quality_label"] in ("approximate_proportional_vad", "segment_timed_word_proportional")
    assert len(alignment["segments"]) > 0

    analysis = client.get(f"/analysis/{ref['analysis_id']}").json()
    assert analysis["total_duration"] > 0
    assert "word_occurrences" in analysis

    audio_meta = client.get(f"/audio/{ref['audio_id']}").json()
    assert audio_meta["is_normalized"] is True
    assert audio_meta["waveform_url"]
    waveform = client.get(audio_meta["waveform_url"]).json()
    assert waveform["buckets"] > 0

    resp = client.post("/profiles/build", json={"profile_name": "E2E Profile", "analysis_ids": [ref["analysis_id"]]})
    profile_job = _wait_for_job(client, resp.json()["job_id"])
    assert profile_job["status"] == "SUCCEEDED", profile_job
    profile_id = profile_job["result_id"]

    profile = client.get(f"/profiles/{profile_id}").json()
    assert profile["tempo"] in ("slow", "moderate", "fast")

    resp = client.post("/profiles/plan", json={
        "profile_id": profile_id, "script": "This is a major escalation. Stand ready.", "language": "en",
    })
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    plan_id = plan["plan_id"]
    assert len(plan["instructions"]) == 2

    resp = client.post("/tts/synthesize", json={
        "text": "This is a major escalation. Stand ready.",
        "provider": "mock-tts", "performance_plan_id": plan_id,
    })
    assert resp.status_code == 200, resp.text
    tts_job = _wait_for_job(client, resp.json()["job_id"])
    assert tts_job["status"] == "SUCCEEDED", tts_job
    generated_id = tts_job["result_id"]

    generated = client.get(f"/generated/{generated_id}").json()
    assert generated["provider"] == "mock-tts"
    assert generated["provider_metadata"]["is_mock"] is True
    assert generated["validation_status"] in ("PASSED", "WARNING")
    assert generated["duration"] > 0

    file_resp = client.get(f"/generated/{generated_id}/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"] == "audio/wav"

    resp = client.delete(f"/references/{reference_id}")
    assert resp.status_code == 200
    assert client.get(f"/references/{reference_id}").status_code == 404
