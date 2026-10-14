"""Job store: states, idempotency, immutability, cancel, retry, recovery."""
import sqlite3

import pytest

from align_engine.jobs import JobStore
from align_engine.errors import JobConflictError, JobStateError
from conftest import build_request, make_config


def test_create_and_process_completes_with_immutable_artifact(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    req = build_request(config)
    row = store.create_and_process(req)
    assert row["status"] == "COMPLETED"
    assert row["artifact_sha256"] and row["artifact_uri"]
    artifact = store.get_artifact(req.alignment_job_id)
    assert artifact["alignment_job_id"] == req.alignment_job_id
    # Immutable: the store refuses to overwrite the completed artifact.
    from align_engine.errors import AlignError
    with pytest.raises(AlignError, match="immutable"):
        store.store.write_output(req.alignment_job_id, artifact)


def test_idempotent_create_replays_without_recompute(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    req = build_request(config)
    first = store.create_and_process(req)
    second = store.create_and_process(req)   # same request
    assert first["artifact_sha256"] == second["artifact_sha256"]
    assert second["attempt_count"] == first["attempt_count"]  # not reprocessed


def test_conflicting_payload_same_id_rejected(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    store.create_and_process(build_request(config, job_id="dup"))
    other = build_request(config, job_id="dup", language="ta",
                          sections=[{"section_id": "x", "segments": [
                              {"segment_id": "y", "original_text": "வணக்கம்"}]}])
    with pytest.raises(JobConflictError):
        store.create(other)


def test_cancel_prevents_processing(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    req = build_request(config)
    store.create(req)                 # QUEUED, not processed
    cancelled = store.cancel(req.alignment_job_id)
    assert cancelled["status"] == "CANCELLED"
    after = store.process(req.alignment_job_id)
    assert after["status"] == "CANCELLED"       # never runs
    assert not store.store.exists(req.alignment_job_id)  # no artifact written


def test_failed_job_can_retry_and_reuses_verified_inputs(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    req = build_request(config)
    audio_abs = config.input_root / req.audio_path
    audio_abs.unlink()                # make the first attempt fail (audio missing)
    failed = store.create_and_process(req)
    assert failed["status"] == "FAILED" and failed["error_code"] == "audio_missing"
    # Restore the identical audio bytes; retry reuses the persisted request.
    from conftest import make_wav
    make_wav(audio_abs, seconds=1.0)
    retried = store.retry(req.alignment_job_id)
    assert retried["status"] == "COMPLETED"
    assert retried["attempt_count"] == 2


def test_completed_job_is_not_overwritten_by_reprocess(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    req = build_request(config)
    done = store.create_and_process(req)
    reprocessed = store.process(req.alignment_job_id)
    assert reprocessed["status"] == "COMPLETED"
    assert reprocessed["artifact_sha256"] == done["artifact_sha256"]
    # retry on a completed job is a no-op returning the same artifact
    assert store.retry(req.alignment_job_id)["artifact_sha256"] == done["artifact_sha256"]


def test_recover_interrupted_marks_processing_failed_retryable(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    req = build_request(config)
    store.create(req)
    with sqlite3.connect(config.database_path) as conn:
        conn.execute("UPDATE alignment_jobs SET status='PROCESSING' WHERE alignment_job_id=?",
                     (req.alignment_job_id,))
    assert store.recover_interrupted() == 1
    recovered = store.get(req.alignment_job_id)
    assert recovered["status"] == "FAILED" and recovered["error_code"] == "interrupted"
    # recovered job is retryable
    assert store.retry(req.alignment_job_id)["status"] == "COMPLETED"


def test_get_artifact_requires_completion(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    req = build_request(config)
    store.create(req)
    with pytest.raises(JobStateError):
        store.get_artifact(req.alignment_job_id)


def test_synthetic_output_is_flagged_distinct_from_real(tmp_path):
    config = make_config(tmp_path)
    store = JobStore(config)
    req = build_request(config)
    store.create_and_process(req)
    artifact = store.get_artifact(req.alignment_job_id)
    assert artifact["is_synthetic"] is True
    assert artifact["engine_name"] == "deterministic-test-aligner"
