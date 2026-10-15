from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.contracts.acquisition import ACQUISITION_RESULT_SCHEMA
from defence_studio.contracts.analysis import ACQUISITION_RESULT_ARTIFACT
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import connect, initialize_database
from defence_studio.services.analysis import AnalysisService
from defence_studio.services.jobs import JobService
from defence_studio.services.review import (
    RevisionConflict,
    ReviewNotFound,
    ReviewService,
    ReviewValidationError,
    StaleRevision,
)
from defence_studio.storage.artifacts import ArtifactStore

OWNER = "review-test-user"
ACQUISITION_STAGE = JOB_STAGES[2]
ANALYSIS_STAGE = JOB_STAGES[3]


def _setup(tmp_path: Path):
    database_file = tmp_path / "review.sqlite3"
    storage_root = tmp_path / "storage"
    initialize_database(database_file)
    return database_file, storage_root, JobService(database_file), ArtifactStore(storage_root)


def _source(store, job_id, source_id, url, text, *, status="ACQUIRED"):
    data = text.encode("utf-8")
    uri = f"jobs/{job_id}/sources/{source_id}.bin"
    stored = store.write_bytes(job_id, uri, data, "text/plain")
    return {
        "source_id": source_id, "target_id": source_id, "status": status,
        "method": "http_page", "source_type": "article",
        "requested_url": url, "canonical_url": url, "final_url": url,
        "acquisition_timestamp": "2026-10-09T00:00:00.000000+00:00",
        "declared_metadata": {
            "title": None, "title_status": "NOT_COLLECTED", "publisher": None,
            "publisher_status": "NOT_COLLECTED", "published_at": None,
            "published_at_status": "NOT_COLLECTED", "language": None, "language_status": "NOT_COLLECTED",
        },
        "artifacts": [{
            "artifact_type": "source_content", "uri": stored.uri, "sha256": stored.sha256,
            "bytes": stored.bytes, "media_type": "text/plain",
            "schema_version": "defence-studio.source-artifact.v1",
        }],
        "original_metadata": {}, "provenance": None, "warnings": [], "error": None,
    }


def _store_acquisition(store, job_id, sources):
    import json
    payload = {
        "job_id": job_id, "acquired_at": "2026-10-09T00:00:00.000000+00:00", "sources": sources,
        "requested": len(sources), "succeeded": sum(1 for s in sources if s["status"] == "ACQUIRED"),
        "failed": 0, "skipped_duplicate": 0, "warnings": [], "schema_version": ACQUISITION_RESULT_SCHEMA,
    }
    data = json.dumps(payload).encode("utf-8")
    uri = f"jobs/{job_id}/research/acquisition_result.json"
    stored = store.write_bytes(job_id, uri, data, "application/json")
    return stored.uri, stored.sha256


def _advance(jobs: JobService, job_id: str, stage_key: str):
    jobs.transition(job_id, OWNER, stage_key, "RUNNING")


def _complete(jobs: JobService, job_id: str, stage_key: str):
    jobs.transition(job_id, OWNER, stage_key, "COMPLETED")


def _basic_job(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "review-basic", "General", "en")
    job_id = job["job_id"]
    jobs.transition(job_id, OWNER, JOB_STAGES[0], "SKIPPED")
    jobs.transition(job_id, OWNER, JOB_STAGES[1], "SKIPPED")
    _advance(jobs, job_id, ACQUISITION_STAGE)
    source = _source(store, job_id, "src-001", "https://example.com/a",
                     "Officials reportedly confirmed the unit is fully operational in 2026.")
    acq_uri, acq_sha = _store_acquisition(store, job_id, [source])
    jobs.add_artifact(job_id, OWNER, ACQUISITION_STAGE, ACQUISITION_RESULT_ARTIFACT,
                      acq_uri, acq_sha, ACQUISITION_RESULT_SCHEMA)
    _complete(jobs, job_id, ACQUISITION_STAGE)
    _advance(jobs, job_id, ANALYSIS_STAGE)
    analysis = AnalysisService(database_file, storage_root)
    # Empty topic: the extractor's topic-relevance filter only applies when a
    # topic is given, so this keeps the fixture's claim (no military
    # designation token) from being filtered out as topic-irrelevant.
    ref, result = analysis.run_and_store(job_id, OWNER, acq_uri, acq_sha, topic="")
    jobs.add_artifact(job_id, OWNER, ANALYSIS_STAGE, ref["artifact_type"], ref["uri"],
                      ref["sha256"], ref["schema_version"])
    _complete(jobs, job_id, ANALYSIS_STAGE)
    review = ReviewService(database_file, storage_root)
    assert result.claims, "fixture must produce at least one claim"
    return database_file, storage_root, jobs, store, job_id, review, result, ref, acq_uri, acq_sha


# ---------------------------------------------------------------------------
# 1. Review record creation and retrieval
# ---------------------------------------------------------------------------


def test_claim_review_decision_is_created_and_retrievable(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id

    record = review.decide_claim(job_id, OWNER, claim_id, "APPROVED_FOR_USE",
                                 ref["sha256"], 0, "Looks solid", "idem-1")
    assert record["decision"] == "APPROVED_FOR_USE"
    assert record["revision"] == 1
    assert record["reviewer_id"] == OWNER
    assert record["note"] == "Looks solid"

    fetched = review.get_claim(job_id, OWNER, claim_id)
    assert fetched["review"]["decision"] == "APPROVED_FOR_USE"
    assert fetched["review_status"] == "APPROVED_FOR_USE"
    assert fetched["is_stale"] is False


def test_source_review_decision_is_created_and_retrievable(tmp_path):
    _, _, _, _, job_id, review, result, ref, acq_uri, acq_sha = _basic_job(tmp_path)

    record = review.decide_source(job_id, OWNER, "src-001", "NEEDS_MORE_EVIDENCE",
                                  acq_sha, 0, "Need a second outlet", "idem-src-1")
    assert record["decision"] == "NEEDS_MORE_EVIDENCE"

    fetched = review.get_source(job_id, OWNER, "src-001")
    assert fetched["review"]["decision"] == "NEEDS_MORE_EVIDENCE"
    assert fetched["review_status"] == "NEEDS_MORE_EVIDENCE"


def test_pending_claim_has_no_review_until_decided(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id
    fetched = review.get_claim(job_id, OWNER, claim_id)
    assert fetched["review"] is None
    assert fetched["review_status"] == "PENDING"


# ---------------------------------------------------------------------------
# 2. Decision value validation (invalid decisions rejected at the boundary)
# ---------------------------------------------------------------------------


def test_unsupported_decision_value_is_rejected(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id
    with pytest.raises(ReviewValidationError):
        review.decide_claim(job_id, OWNER, claim_id, "VERIFIED_TRUE",
                            ref["sha256"], 0, "Reviewed test evidence", "idem-bad")


def test_unknown_claim_id_is_rejected(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    with pytest.raises(ReviewValidationError):
        review.decide_claim(job_id, OWNER, "claim-does-not-exist", "APPROVED_FOR_USE",
                            ref["sha256"], 0, "Reviewed test evidence", "idem-unknown")


# ---------------------------------------------------------------------------
# 3. Owner authorization and cross-user access rejection
# ---------------------------------------------------------------------------


def test_cross_owner_access_is_rejected_as_not_found(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id
    with pytest.raises(ReviewNotFound):
        review.decide_claim(job_id, "someone-else", claim_id, "APPROVED_FOR_USE",
                            ref["sha256"], 0, "Reviewed test evidence", "idem-x")
    with pytest.raises(ReviewNotFound):
        review.list_claims(job_id, "someone-else")


# ---------------------------------------------------------------------------
# 4/5. Approval tied to the exact analysis revision; stale revision rejection
# ---------------------------------------------------------------------------


def test_decision_against_a_superseded_analysis_revision_is_rejected_as_stale(tmp_path):
    database_file, storage_root, jobs, store, job_id, review, result, ref, acq_uri, acq_sha = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id
    stale_sha = ref["sha256"]

    # A new acquisition + re-analysis supersedes the artifact the reviewer saw
    # (different topic => different input_digest => a new analysis_result version).
    extra_source = _source(store, job_id, "src-002", "https://example.com/b",
                           "A second, unrelated statement entirely about logistics in 2027.")
    import json
    combined_payload = json.loads(store.read_text(job_id, acq_uri))
    combined_payload["sources"].append(extra_source)
    combined_payload["requested"] = 2
    combined_payload["succeeded"] = 2
    new_acq_bytes = json.dumps(combined_payload).encode("utf-8")
    new_acq_uri = f"jobs/{job_id}/research/acquisition_result_v2.json"
    stored = store.write_bytes(job_id, new_acq_uri, new_acq_bytes, "application/json")
    jobs.add_artifact(job_id, OWNER, ACQUISITION_STAGE, ACQUISITION_RESULT_ARTIFACT,
                      stored.uri, stored.sha256, ACQUISITION_RESULT_SCHEMA)

    analysis = AnalysisService(database_file, storage_root)
    new_ref, _ = analysis.run_and_store(job_id, OWNER, stored.uri, stored.sha256, topic="")
    jobs.add_artifact(job_id, OWNER, ANALYSIS_STAGE, new_ref["artifact_type"], new_ref["uri"],
                      new_ref["sha256"], new_ref["schema_version"])

    assert new_ref["sha256"] != stale_sha
    with pytest.raises(StaleRevision):
        review.decide_claim(job_id, OWNER, claim_id, "APPROVED_FOR_USE", stale_sha, 0, "Reviewed test evidence", "idem-stale")

    # Deciding against the current revision succeeds.
    record = review.decide_claim(job_id, OWNER, claim_id, "APPROVED_FOR_USE",
                                 new_ref["sha256"], 0, "Reviewed test evidence", "idem-current")
    assert record["reviewed_artifact_sha256"] == new_ref["sha256"]


def test_decide_without_any_analysis_result_is_rejected(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "review-empty", "General", "en")
    job_id = job["job_id"]
    review = ReviewService(database_file, storage_root)
    with pytest.raises(ReviewValidationError):
        review.decide_claim(job_id, OWNER, "claim-x", "APPROVED_FOR_USE", "0" * 64, 0, "Reviewed test evidence", "idem-empty")
    listing = review.list_claims(job_id, OWNER)
    assert listing["claims"] == []
    assert "empty_reason" in listing


# ---------------------------------------------------------------------------
# 6. Concurrent decision conflicts (optimistic concurrency)
# ---------------------------------------------------------------------------


def test_stale_expected_revision_is_rejected_as_conflict(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id
    review.decide_claim(job_id, OWNER, claim_id, "APPROVED_FOR_USE", ref["sha256"], 0, "Reviewed test evidence", "idem-a")

    # A second submission still claiming expected_revision=0 (as if it never saw
    # the first reviewer's change) must not silently overwrite it.
    with pytest.raises(RevisionConflict):
        review.decide_claim(job_id, OWNER, claim_id, "REJECTED", ref["sha256"], 0, "Reviewed test evidence", "idem-b")

    # Resubmitting with the correct current revision succeeds.
    record = review.decide_claim(job_id, OWNER, claim_id, "REJECTED", ref["sha256"], 1, "Reviewed test evidence", "idem-c")
    assert record["decision"] == "REJECTED"
    assert record["revision"] == 2


# ---------------------------------------------------------------------------
# 7. Idempotent submissions
# ---------------------------------------------------------------------------


def test_duplicate_submission_with_same_idempotency_key_does_not_duplicate_history(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id

    first = review.decide_claim(job_id, OWNER, claim_id, "APPROVED_FOR_USE",
                                ref["sha256"], 0, "note", "idem-once")
    second = review.decide_claim(job_id, OWNER, claim_id, "APPROVED_FOR_USE",
                                 ref["sha256"], 0, "note", "idem-once")
    assert first == second
    history = review.claim_history(job_id, OWNER, claim_id)
    assert len(history) == 1


# ---------------------------------------------------------------------------
# 8. Append-only audit history
# ---------------------------------------------------------------------------


def test_history_is_append_only_and_tracks_every_change(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id

    review.decide_claim(job_id, OWNER, claim_id, "NEEDS_MORE_EVIDENCE", ref["sha256"], 0, "first", "k1")
    review.decide_claim(job_id, OWNER, claim_id, "CONFLICT_UNRESOLVED", ref["sha256"], 1, "second", "k2")
    review.decide_claim(job_id, OWNER, claim_id, "APPROVED_FOR_USE", ref["sha256"], 2, "third", "k3")

    history = review.claim_history(job_id, OWNER, claim_id)
    assert [h["decision"] for h in history] == ["NEEDS_MORE_EVIDENCE", "CONFLICT_UNRESOLVED", "APPROVED_FOR_USE"]
    assert [h["revision"] for h in history] == [1, 2, 3]
    assert history[1]["previous_decision"] == "NEEDS_MORE_EVIDENCE"
    assert history[2]["previous_decision"] == "CONFLICT_UNRESOLVED"
    # Current state reflects only the latest decision.
    current = review.get_claim(job_id, OWNER, claim_id)
    assert current["review"]["decision"] == "APPROVED_FOR_USE"
    assert current["review"]["revision"] == 3


# ---------------------------------------------------------------------------
# 9. Conflicting claims and duplicate-source relationships surface in listings
# ---------------------------------------------------------------------------


def test_conflicting_claims_are_surfaced_with_both_sides_and_flagged(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "review-conflict", "General", "en")
    job_id = job["job_id"]
    jobs.transition(job_id, OWNER, JOB_STAGES[0], "SKIPPED")
    jobs.transition(job_id, OWNER, JOB_STAGES[1], "SKIPPED")
    _advance(jobs, job_id, ACQUISITION_STAGE)
    positive = _source(store, job_id, "src-x", "https://outlet-x.example/1",
                       "Officials confirmed the Su-30 squadron is fully operational at the eastern base in 2026.")
    negative = _source(store, job_id, "src-y", "https://outlet-y.example/2",
                       "Officials confirmed the Su-30 squadron is not operational at the eastern base in 2026.")
    acq_uri, acq_sha = _store_acquisition(store, job_id, [positive, negative])
    jobs.add_artifact(job_id, OWNER, ACQUISITION_STAGE, ACQUISITION_RESULT_ARTIFACT,
                      acq_uri, acq_sha, ACQUISITION_RESULT_SCHEMA)
    _complete(jobs, job_id, ACQUISITION_STAGE)
    _advance(jobs, job_id, ANALYSIS_STAGE)
    analysis = AnalysisService(database_file, storage_root)
    ref, result = analysis.run_and_store(job_id, OWNER, acq_uri, acq_sha, topic="General")
    jobs.add_artifact(job_id, OWNER, ANALYSIS_STAGE, ref["artifact_type"], ref["uri"],
                      ref["sha256"], ref["schema_version"])
    _complete(jobs, job_id, ANALYSIS_STAGE)
    assert result.conflicts

    review = ReviewService(database_file, storage_root)
    listing = review.list_claims(job_id, OWNER)
    flagged = [c for c in listing["claims"] if c["has_conflict"]]
    assert len(flagged) == 2
    texts = {c["claim_text"] for c in flagged}
    assert any("not operational" in t for t in texts)
    assert any("fully operational" in t for t in texts)

    only_conflicts = review.list_claims(job_id, OWNER, conflict_only=True)
    assert len(only_conflicts["claims"]) == 2


def test_filtering_claims_by_review_status_and_source(tmp_path):
    _, _, _, _, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id
    review.decide_claim(job_id, OWNER, claim_id, "REJECTED", ref["sha256"], 0, "Reviewed test evidence", "idem-filter")

    pending = review.list_claims(job_id, OWNER, status="PENDING")
    assert pending["claims"] == []
    rejected = review.list_claims(job_id, OWNER, status="REJECTED")
    assert len(rejected["claims"]) == 1
    by_source = review.list_claims(job_id, OWNER, source_id="src-001")
    assert len(by_source["claims"]) == 1
    by_other_source = review.list_claims(job_id, OWNER, source_id="does-not-exist")
    assert by_other_source["claims"] == []


# ---------------------------------------------------------------------------
# 10. Migration from the real previous schema, and persistence across restart
# ---------------------------------------------------------------------------


_LEGACY_V2_SCHEMA = """
CREATE TABLE schema_info (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL);
INSERT INTO schema_info(singleton, version) VALUES(1, 2);
CREATE TABLE workflow_jobs (
    job_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    request_schema TEXT NOT NULL, request_hash TEXT NOT NULL, topic TEXT NOT NULL,
    language TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, queued_at TEXT, UNIQUE(owner_id, idempotency_key)
);
CREATE TABLE workflow_stages (
    job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id) ON DELETE CASCADE,
    stage_key TEXT NOT NULL, ordinal INTEGER NOT NULL, status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '', attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3, retry_after TEXT, PRIMARY KEY(job_id, stage_key)
);
CREATE TABLE workflow_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, stage_key TEXT NOT NULL,
    from_status TEXT NOT NULL, to_status TEXT NOT NULL, stage_version INTEGER NOT NULL,
    occurred_at TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE workflow_artifacts (
    artifact_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage_key TEXT NOT NULL,
    artifact_type TEXT NOT NULL, version INTEGER NOT NULL, uri TEXT NOT NULL,
    sha256 TEXT NOT NULL, schema_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE worker_claims (
    claim_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage_key TEXT NOT NULL, worker_id TEXT NOT NULL,
    attempt INTEGER NOT NULL, status TEXT NOT NULL, lease_expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, finished_at TEXT, failure_detail TEXT NOT NULL DEFAULT ''
);
INSERT INTO workflow_jobs VALUES(
    'legacy-job-1', 'legacy-owner', 'legacy-key', 'defence-studio.job-create.v1', 'hash',
    'Legacy topic', 'en', 'DRAFT', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', NULL
);
"""


def test_migration_from_a_real_v2_database_adds_review_tables_without_disturbing_existing_rows(tmp_path):
    database_file = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(database_file)) as connection:
        connection.executescript(_LEGACY_V2_SCHEMA)
        connection.commit()

    initialize_database(database_file)  # new code upgrading a real pre-existing v2 file

    with closing(connect(database_file)) as connection:
        version = connection.execute("SELECT version FROM schema_info WHERE singleton = 1").fetchone()[0]
        assert version == 13
        for table in ("claim_reviews", "claim_review_history", "source_reviews", "source_review_history"):
            connection.execute(f"SELECT COUNT(*) FROM {table}")  # does not raise
        preserved = connection.execute(
            "SELECT owner_id, topic FROM workflow_jobs WHERE job_id = 'legacy-job-1'"
        ).fetchone()
        assert tuple(preserved) == ("legacy-owner", "Legacy topic")


def test_unsupported_schema_version_fails_closed(tmp_path):
    database_file = tmp_path / "future.sqlite3"
    with closing(sqlite3.connect(database_file)) as connection:
        connection.execute(
            "CREATE TABLE schema_info (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO schema_info VALUES(1, 999)")
        connection.commit()
    with pytest.raises(RuntimeError):
        initialize_database(database_file)


def test_decisions_persist_across_a_fresh_service_instance(tmp_path):
    database_file, storage_root, jobs, store, job_id, review, result, ref, _, _ = _basic_job(tmp_path)
    claim_id = result.claims[0].claim_id
    review.decide_claim(job_id, OWNER, claim_id, "APPROVED_FOR_USE", ref["sha256"], 0, "Reviewed test evidence", "idem-restart")

    reopened = ReviewService(database_file, storage_root)  # simulates app restart
    fetched = reopened.get_claim(job_id, OWNER, claim_id)
    assert fetched["review"]["decision"] == "APPROVED_FOR_USE"


# ---------------------------------------------------------------------------
# 11. API-level integration: auth, persistence, stale/conflict status codes
# ---------------------------------------------------------------------------


SECRET = "phase-one-test-signing-secret-32-bytes-minimum"


def access_token(subject: str, secret: str = SECRET) -> str:
    import base64
    import hashlib
    import hmac
    import json as _json
    import time

    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    header = encode(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = encode(_json.dumps({"sub": subject, "exp": int(time.time()) + 600}).encode())
    signing_input = f"{header}.{payload}"
    signature = encode(hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


@pytest.fixture
def api_job(tmp_path):
    database_file, storage_root, jobs, store, job_id, review, result, ref, acq_uri, acq_sha = _basic_job(tmp_path)
    app = create_app(database_file, SECRET, storage_root)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {access_token(OWNER, SECRET)}"}
    other_headers = {"Authorization": f"Bearer {access_token('someone-else', SECRET)}"}
    return client, headers, other_headers, job_id, result.claims[0].claim_id, ref["sha256"]


def test_api_claim_review_round_trip_and_persistence(api_job):
    client, headers, _other, job_id, claim_id, sha = api_job

    listing = client.get(f"/api/jobs/{job_id}/review/claims", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["claims"][0]["review_status"] == "PENDING"

    decide = client.post(
        f"/api/jobs/{job_id}/review/claims/{claim_id}/decisions",
        headers={**headers, "Idempotency-Key": "api-decision-1"},
        json={"decision": "APPROVED_FOR_USE", "reviewed_artifact_sha256": sha,
              "expected_revision": 0, "note": "Looks good"},
    )
    assert decide.status_code == 201
    assert decide.json()["decision"] == "APPROVED_FOR_USE"

    refetched = client.get(f"/api/jobs/{job_id}/review/claims/{claim_id}", headers=headers)
    assert refetched.json()["review"]["decision"] == "APPROVED_FOR_USE"

    history = client.get(f"/api/jobs/{job_id}/review/claims/{claim_id}/history", headers=headers)
    assert len(history.json()) == 1


def test_api_rejects_cross_owner_and_unauthenticated_access(api_job):
    client, headers, other_headers, job_id, claim_id, sha = api_job

    unauthenticated = client.get(f"/api/jobs/{job_id}/review/claims")
    assert unauthenticated.status_code == 401

    cross_owner = client.get(f"/api/jobs/{job_id}/review/claims", headers=other_headers)
    assert cross_owner.status_code == 404

    cross_owner_decide = client.post(
        f"/api/jobs/{job_id}/review/claims/{claim_id}/decisions",
        headers={**other_headers, "Idempotency-Key": "cross-owner-1"},
        json={"decision": "APPROVED_FOR_USE", "reviewed_artifact_sha256": sha,
              "expected_revision": 0, "note": "Reviewed test evidence"},
    )
    assert cross_owner_decide.status_code == 404


def test_api_rejects_stale_revision_and_concurrency_conflict(api_job):
    client, headers, _other, job_id, claim_id, sha = api_job

    bad_sha = "f" * 64
    stale = client.post(
        f"/api/jobs/{job_id}/review/claims/{claim_id}/decisions",
        headers={**headers, "Idempotency-Key": "stale-1"},
        json={"decision": "APPROVED_FOR_USE", "reviewed_artifact_sha256": bad_sha,
              "expected_revision": 0, "note": "Reviewed test evidence"},
    )
    assert stale.status_code == 409

    first = client.post(
        f"/api/jobs/{job_id}/review/claims/{claim_id}/decisions",
        headers={**headers, "Idempotency-Key": "conflict-1"},
        json={"decision": "APPROVED_FOR_USE", "reviewed_artifact_sha256": sha,
              "expected_revision": 0, "note": "Reviewed test evidence"},
    )
    assert first.status_code == 201

    conflict = client.post(
        f"/api/jobs/{job_id}/review/claims/{claim_id}/decisions",
        headers={**headers, "Idempotency-Key": "conflict-2"},
        json={"decision": "REJECTED", "reviewed_artifact_sha256": sha,
              "expected_revision": 0, "note": "Reviewed test evidence"},
    )
    assert conflict.status_code == 409


def test_api_rejects_invalid_decision_value(api_job):
    client, headers, _other, job_id, claim_id, sha = api_job
    response = client.post(
        f"/api/jobs/{job_id}/review/claims/{claim_id}/decisions",
        headers={**headers, "Idempotency-Key": "invalid-1"},
        json={"decision": "TOTALLY_TRUE", "reviewed_artifact_sha256": sha,
              "expected_revision": 0, "note": "Reviewed test evidence"},
    )
    assert response.status_code == 422
