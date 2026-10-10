from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from defence_studio.acquisition.handler import (
    ACQUISITION_MANIFEST_ARTIFACT_TYPE,
    build_acquisition_handler,
)
from defence_studio.acquisition.providers.youtube import SubtitleTrack, YoutubeSubtitles
from defence_studio.acquisition.service import default_registry
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.analysis.ai_schema import AiOutputError, validate_ai_payload
from defence_studio.contracts.acquisition import ACQUISITION_RESULT_SCHEMA
from defence_studio.contracts.analysis import (
    ACQUISITION_RESULT_ARTIFACT,
    ANALYSIS_RESULT_ARTIFACT,
    ANALYSIS_STAGE,
    AnalysisLimits,
    ConflictBasis,
    DuplicateBasis,
    NormalizationStatus,
)
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import initialize_database
from defence_studio.services.analysis import AnalysisInputError, AnalysisService, make_analysis_handler
from defence_studio.services.jobs import JobNotFound, JobService
from defence_studio.services.worker import Worker, WorkerService
from defence_studio.storage.artifacts import ArtifactStore

OWNER = "analysis-test-user"
ACQUISITION_STAGE = JOB_STAGES[2]
assert ANALYSIS_STAGE == JOB_STAGES[3]
# ACQUISITION_RESULT_ARTIFACT (F4's contract) must track the real F3 output type.
assert ACQUISITION_RESULT_ARTIFACT == ACQUISITION_MANIFEST_ARTIFACT_TYPE


def _setup(tmp_path: Path):
    database_file = tmp_path / "analysis.sqlite3"
    storage_root = tmp_path / "storage"
    initialize_database(database_file)
    return database_file, storage_root, JobService(database_file), ArtifactStore(storage_root)


def _source(store, job_id, source_id, url, media_type, text, *, status="ACQUIRED",
           title=None, published_at=None, artifacts_override=None, extra_media_type=None):
    entries = []
    if status == "ACQUIRED" and artifacts_override is None:
        data = text.encode("utf-8")
        uri = f"jobs/{job_id}/sources/{source_id}.bin"
        stored = store.write_bytes(job_id, uri, data, extra_media_type or media_type)
        entries.append({
            # Matches the real F3 artifact_type/schema (see
            # defence_studio.acquisition.providers.http_page / .youtube and
            # acquisition.service.result_schema_for).
            "artifact_type": "source_content", "uri": stored.uri, "sha256": stored.sha256,
            "bytes": stored.bytes, "media_type": extra_media_type or media_type,
            "schema_version": "defence-studio.source-artifact.v1",
        })
    elif artifacts_override is not None:
        entries = artifacts_override
    declared = {
        "title": title, "title_status": "COLLECTED" if title else "NOT_COLLECTED",
        "publisher": None, "publisher_status": "NOT_COLLECTED",
        "published_at": published_at, "published_at_status": "COLLECTED" if published_at else "NOT_COLLECTED",
        "language": None, "language_status": "NOT_COLLECTED",
    }
    return {
        "source_id": source_id, "target_id": source_id, "status": status,
        "method": "http_page", "source_type": "article",
        "requested_url": url, "canonical_url": url, "final_url": url,
        "acquisition_timestamp": "2026-10-09T00:00:00.000000+00:00",
        "declared_metadata": declared, "artifacts": entries,
        "original_metadata": {}, "provenance": None, "warnings": [], "error": None,
    }


def _store_acquisition(store, job_id, sources):
    payload = {
        "job_id": job_id, "acquired_at": "2026-10-09T00:00:00.000000+00:00", "sources": sources,
        "requested": len(sources),
        "succeeded": sum(1 for s in sources if s["status"] == "ACQUIRED"),
        "failed": sum(1 for s in sources if s["status"] == "FAILED"),
        "skipped_duplicate": sum(1 for s in sources if s["status"] == "SKIPPED_DUPLICATE"),
        "warnings": [], "schema_version": ACQUISITION_RESULT_SCHEMA,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    uri = f"jobs/{job_id}/research/acquisition_result.json"
    stored = store.write_bytes(job_id, uri, blob, "application/json")
    return stored.uri, stored.sha256


RICH_HTML = (
    "<html><body><script>var x=1;</script>"
    "<p>India reportedly inducted 12 fighters on October 9, 2026, but officials "
    "said the Su-30 deal is not finalized.</p></body></html>"
)


# ---------------------------------------------------------------------------
# 1. Valid F3 artifact, provenance, traceability, qualifiers/negation/entities
# ---------------------------------------------------------------------------


def test_analysis_processes_valid_artifact_with_full_provenance_and_traceability(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-valid", "General", "en")
    job_id = job["job_id"]
    source = _source(store, job_id, "src-001", "https://example.com/a", "text/html", RICH_HTML,
                     title="Induction report", published_at="2026-10-08")
    uri, sha = _store_acquisition(store, job_id, [source])

    service = AnalysisService(database_file, storage_root)
    result = service.analyze(job_id, OWNER, uri, sha, topic="General")

    assert result.sources_total == 1
    assert result.sources_normalized == 1
    assert not result.failures

    record = result.normalized_sources[0]
    assert record.status == NormalizationStatus.NORMALIZED
    assert record.source_id == "src-001"
    assert record.target_id == "src-001"
    assert record.original_artifact is not None
    assert record.original_sha256_verified is True
    assert record.dates.published_at == "2026-10-08"
    assert record.dates.acquired_at == "2026-10-09T00:00:00.000000+00:00"
    assert "<script>" not in record.normalized_text
    assert "var x=1" not in record.normalized_text
    assert "Su-30" in record.normalized_text

    assert result.claims, "expected at least one extracted claim"
    claim = result.claims[0]
    assert claim.source_id == "src-001"
    assert claim.source_published_at == "2026-10-08"
    assert claim.acquisition_timestamp == "2026-10-09T00:00:00.000000+00:00"
    # Qualifiers/negation/entities are preserved verbatim, never stripped.
    assert "reportedly" in claim.qualifiers
    assert claim.has_negation is True
    assert "not finalized" in claim.claim_text
    assert "Su-30" in claim.entities.designations
    assert any("fighters" in q for q in claim.entities.quantities)
    assert any("2026" in d for d in claim.entities.dates)

    # Claim-to-passage traceability: offsets slice back to the exact claim text.
    passage = claim.passage
    assert record.normalized_text[passage.start_offset:passage.end_offset] == claim.claim_text
    assert passage.text == claim.claim_text
    assert passage.source_id == "src-001"


def test_subtitle_passage_preserves_cue_timestamps(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-subs", "General", "en")
    job_id = job["job_id"]
    vtt = (
        "WEBVTT\n\n"
        "1\n00:00:01.000 --> 00:00:04.000\n"
        "The Su-30 squadron completed 8 sorties on October 9, 2026.\n"
    )
    source = _source(store, job_id, "src-vtt", "https://example.com/v", "text/vtt", vtt)
    uri, sha = _store_acquisition(store, job_id, [source])

    result = AnalysisService(database_file, storage_root).analyze(job_id, OWNER, uri, sha, topic="General")
    assert result.claims
    claim = result.claims[0]
    assert claim.passage.timestamp_start == 1.0
    assert claim.passage.timestamp_end == 4.0


# ---------------------------------------------------------------------------
# 2. Missing / malformed / unsupported / corrupted artifacts
# ---------------------------------------------------------------------------


def test_failed_malformed_unsupported_and_corrupted_sources_are_recorded_not_fabricated(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-failures", "General", "en")
    job_id = job["job_id"]

    not_acquired = _source(store, job_id, "src-failed", "https://example.com/f", "text/plain",
                           "", status="FAILED")

    unsupported = _source(store, job_id, "src-unsupported", "https://example.com/u",
                          "application/pdf", "binary content here", extra_media_type="application/pdf")

    good_data = "Plain readable statement about the ministry in 2026.".encode("utf-8")
    good_stored = store.write_bytes(job_id, f"jobs/{job_id}/sources/src-corrupt.bin",
                                     good_data, "text/plain")
    corrupted = _source(store, job_id, "src-corrupt", "https://example.com/c", "text/plain", "",
                       status="ACQUIRED", artifacts_override=[])
    bad_sha = good_stored.sha256[:-1] + ("0" if good_stored.sha256[-1] != "0" else "1")
    corrupted["artifacts"] = [{
        "artifact_type": "source_content", "uri": good_stored.uri, "sha256": bad_sha,
        "bytes": good_stored.bytes, "media_type": "text/plain",
        "schema_version": "defence-studio.source-artifact.v1",
    }]

    missing_stored = store.write_bytes(job_id, f"jobs/{job_id}/sources/src-missing.bin",
                                        b"will be deleted", "text/plain")
    missing_stored.path.unlink()
    missing = _source(store, job_id, "src-missing", "https://example.com/m", "text/plain", "",
                      status="ACQUIRED", artifacts_override=[])
    missing["artifacts"] = [{
        "artifact_type": "source_content", "uri": missing_stored.uri, "sha256": missing_stored.sha256,
        "bytes": missing_stored.bytes, "media_type": "text/plain",
        "schema_version": "defence-studio.source-artifact.v1",
    }]

    uri, sha = _store_acquisition(store, job_id, [not_acquired, unsupported, corrupted, missing])
    result = AnalysisService(database_file, storage_root).analyze(job_id, OWNER, uri, sha)

    assert result.status == "COMPLETED_WITH_FAILURES"
    assert result.sources_total == 4
    assert result.sources_normalized == 0
    codes = {f.code for f in result.failures}
    assert "NO_SUPPORTED_ARTIFACT" in codes
    assert "ARTIFACT_CORRUPT" in codes
    assert "ARTIFACT_UNREADABLE" in codes
    assert any(w.code == "SOURCE_NOT_ACQUIRED" for w in result.warnings)
    assert result.claims == ()


def test_load_acquisition_failures_fail_closed_without_fabricating_content(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-bad-input", "General", "en")
    job_id = job["job_id"]
    service = AnalysisService(database_file, storage_root)

    with pytest.raises(AnalysisInputError, match="missing"):
        service.analyze(job_id, OWNER, f"jobs/{job_id}/research/nope.json", "a" * 64)

    uri, sha = _store_acquisition(store, job_id, [])
    with pytest.raises(AnalysisInputError, match="integrity check"):
        service.analyze(job_id, OWNER, uri, "b" * 64)

    bad_uri = f"jobs/{job_id}/research/../../escape.json"
    with pytest.raises(AnalysisInputError, match="unsafe"):
        service.analyze(job_id, OWNER, bad_uri, "c" * 64)

    malformed = store.write_bytes(job_id, f"jobs/{job_id}/research/bad.json", b"{not json", "application/json")
    with pytest.raises(AnalysisInputError, match="not valid JSON"):
        service.analyze(job_id, OWNER, malformed.uri, malformed.sha256)


# ---------------------------------------------------------------------------
# 4/5. Deduplication: exact, identical-content, near-duplicate, independent
# ---------------------------------------------------------------------------


def test_exact_and_identical_content_duplicates_are_grouped_without_losing_sources(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-dedup-exact", "General", "en")
    job_id = job["job_id"]
    shared_text = "Officials announced new training exercises near the eastern border in 2026."
    a = _source(store, job_id, "src-a", "https://news.example/story", "text/plain", shared_text)
    b = _source(store, job_id, "src-b", "https://news.example/story", "text/plain", shared_text)

    other_text = "A new naval exercise began near the coast in 2026."
    c = _source(store, job_id, "src-c", "https://siteone.example/x", "text/plain", other_text)
    d = _source(store, job_id, "src-d", "https://sitetwo.example/y", "text/plain", other_text)

    uri, sha = _store_acquisition(store, job_id, [a, b, c, d])
    result = AnalysisService(database_file, storage_root).analyze(job_id, OWNER, uri, sha)

    assert result.sources_normalized == 4
    bases = {g.basis for g in result.duplicate_groups}
    assert DuplicateBasis.SAME_CANONICAL_URL in bases
    assert DuplicateBasis.IDENTICAL_CONTENT_HASH in bases
    url_group = next(g for g in result.duplicate_groups if g.basis == DuplicateBasis.SAME_CANONICAL_URL)
    assert set(url_group.member_source_ids) == {"src-a", "src-b"}
    hash_group = next(g for g in result.duplicate_groups if g.basis == DuplicateBasis.IDENTICAL_CONTENT_HASH)
    assert set(hash_group.member_source_ids) == {"src-c", "src-d"}
    # Every original source record is preserved even though it was grouped.
    assert {rec.source_id for rec in result.normalized_sources} == {"src-a", "src-b", "src-c", "src-d"}


def test_near_duplicates_grouped_but_independent_sources_never_merged(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-dedup-near", "General", "en")
    job_id = job["job_id"]

    base_sentence = (
        "The ministry released a detailed statement about the new submarine "
        "construction program this week following a closed door press briefing "
        "with senior defence officials and regional commanders stationed nearby."
    )
    near_copy = base_sentence + " in the capital."
    e = _source(store, job_id, "src-e", "https://outlet-one.example/1", "text/plain", base_sentence)
    f = _source(store, job_id, "src-f", "https://outlet-two.example/2", "text/plain", near_copy)

    independent_g = ("Local fishermen reported unusual naval activity near the coastline "
                     "early Monday morning according to residents.")
    independent_h = ("A coastal patrol unit confirmed increased maritime movement in the "
                     "region following reports from nearby villages.")
    g = _source(store, job_id, "src-g", "https://outlet-three.example/3", "text/plain", independent_g)
    h = _source(store, job_id, "src-h", "https://outlet-four.example/4", "text/plain", independent_h)

    uri, sha = _store_acquisition(store, job_id, [e, f, g, h])
    result = AnalysisService(database_file, storage_root).analyze(
        job_id, OWNER, uri, sha, limits=AnalysisLimits(near_duplicate_threshold=0.82)
    )

    near_groups = [grp for grp in result.duplicate_groups if grp.basis == DuplicateBasis.NEAR_DUPLICATE_TEXT]
    assert len(near_groups) == 1
    assert set(near_groups[0].member_source_ids) == {"src-e", "src-f"}
    # Independent sources discussing a related subject must stay ungrouped.
    for grp in result.duplicate_groups:
        assert not ({"src-g", "src-h"} <= set(grp.member_source_ids))
    assert {rec.source_id for rec in result.normalized_sources} == {"src-e", "src-f", "src-g", "src-h"}


# ---------------------------------------------------------------------------
# 7. Conflicting claims remain distinct and flagged, not resolved/merged
# ---------------------------------------------------------------------------


def test_conflicting_claims_stay_distinct_and_are_flagged_for_review(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-conflict", "General", "en")
    job_id = job["job_id"]
    positive = "Officials confirmed the Su-30 squadron is fully operational at the eastern base in 2026."
    negative = "Officials confirmed the Su-30 squadron is not operational at the eastern base in 2026."
    x = _source(store, job_id, "src-x", "https://outlet-x.example/1", "text/plain", positive)
    y = _source(store, job_id, "src-y", "https://outlet-y.example/2", "text/plain", negative)

    uri, sha = _store_acquisition(store, job_id, [x, y])
    result = AnalysisService(database_file, storage_root).analyze(job_id, OWNER, uri, sha, topic="General")

    assert len(result.claims) == 2
    assert result.conflicts, "expected a flagged conflict between opposing statements"
    conflict = result.conflicts[0]
    assert conflict.basis == ConflictBasis.NEGATION_MISMATCH
    assert conflict.flagged_for_review is True
    assert conflict.claim_id_a != conflict.claim_id_b
    claim_ids = {c.claim_id for c in result.claims}
    assert conflict.claim_id_a in claim_ids and conflict.claim_id_b in claim_ids
    # Neither side was discarded or merged into the other.
    texts = {c.claim_text for c in result.claims}
    assert any("not operational" in t for t in texts)
    assert any("fully operational" in t for t in texts)


# ---------------------------------------------------------------------------
# 8/9. Strict AI-output schema validation (no live provider; seam only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    "not a list",
    [{"claim_text": ""}],
    [{"claim_text": "ok", "start_offset": "0", "end_offset": 5}],
    [{"claim_text": "ok", "start_offset": 5, "end_offset": 2}],
    [{"claim_text": "ok", "start_offset": 0, "end_offset": 999}],
    [{"claim_text": "ok", "start_offset": 0, "end_offset": 2, "qualifiers": "not-a-list"}],
    [{"claim_text": "ok", "start_offset": 0, "end_offset": 2, "has_negation": "nope"}],
    [{}],
    ["not-a-dict"],
])
def test_malformed_ai_output_is_rejected_not_silently_coerced(payload):
    with pytest.raises(AiOutputError):
        validate_ai_payload(payload, text_length=10)


def test_well_formed_ai_output_is_accepted():
    validated = validate_ai_payload(
        [{"claim_text": "A valid claim", "start_offset": 0, "end_offset": 13,
          "qualifiers": ["reportedly"], "has_negation": False}],
        text_length=50,
    )
    assert len(validated) == 1
    assert validated[0].claim_text == "A valid claim"
    assert validated[0].qualifiers == ("reportedly",)


# ---------------------------------------------------------------------------
# 10. Repeat processing / idempotency
# ---------------------------------------------------------------------------


def test_repeat_processing_over_unchanged_inputs_is_idempotent(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-idempotent", "General", "en")
    job_id = job["job_id"]
    source = _source(store, job_id, "src-001", "https://example.com/a", "text/html", RICH_HTML)
    uri, sha = _store_acquisition(store, job_id, [source])

    service = AnalysisService(database_file, storage_root)
    ref1, result1 = service.run_and_store(job_id, OWNER, uri, sha, topic="General")
    ref2, result2 = service.run_and_store(job_id, OWNER, uri, sha, topic="General")

    assert ref1 == ref2
    assert result1.input_digest == result2.input_digest
    assert store.read_text(job_id, ref1["uri"]) == store.read_text(job_id, ref2["uri"])


# ---------------------------------------------------------------------------
# 11/12/13. Durable worker execution: retry without duplicate versions,
# predecessor stages preserved, cross-user rejection.
# ---------------------------------------------------------------------------


def _complete_prerequisite_stages_via_worker(database_file, storage_root, job_id, acquisition_sha):
    store = ArtifactStore(storage_root)

    def noop(_context, _cancel):
        return None

    def produce_acquisition(context, _cancel):
        source = _source(store, context.job_id, "src-001", "https://example.com/a",
                         "text/html", RICH_HTML)
        uri, sha = _store_acquisition(store, context.job_id, [source])
        sha_to_emit = acquisition_sha if acquisition_sha is not None else sha
        return [{
            "artifact_type": ACQUISITION_RESULT_ARTIFACT, "uri": uri,
            "sha256": sha_to_emit, "schema_version": ACQUISITION_RESULT_SCHEMA,
        }]

    handlers = {JOB_STAGES[0]: noop, JOB_STAGES[1]: noop, JOB_STAGES[2]: produce_acquisition}
    worker = Worker(database_file, "prereq-worker", handlers, retry_safe_stages=set(handlers))
    for _ in range(3):
        assert worker.run_once() is True


def test_interrupted_processing_retries_through_worker_without_duplicate_versions(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-worker-retry", "General", "en")
    job_id = job["job_id"]
    WorkerService(database_file).queue(job_id, OWNER)

    # Deliberately emit a wrong (but well-formed) sha256 so the first analysis
    # attempt fails closed on the integrity check, simulating an interrupted/
    # inconsistent upstream result rather than fabricating a transcript.
    bad_sha = "0" * 64
    _complete_prerequisite_stages_via_worker(database_file, storage_root, job_id, bad_sha)

    handler = make_analysis_handler(database_file, storage_root)
    analysis_worker = Worker(database_file, "analysis-worker-1", {ANALYSIS_STAGE: handler},
                             retry_safe_stages={ANALYSIS_STAGE})
    assert analysis_worker.run_once() is True  # attempt 1: fails closed

    state = jobs.get(job_id, OWNER)
    stage = next(s for s in state["stages"] if s["stage_key"] == ANALYSIS_STAGE)
    assert stage["status"] == "FAILED"
    assert stage["attempt_count"] == 1
    assert "integrity check" in stage["error"]
    # Predecessor stages/artifacts are untouched by the failure.
    for key in JOB_STAGES[:3]:
        assert next(s for s in state["stages"] if s["stage_key"] == key)["status"] == "COMPLETED"
    acquisition_artifacts = [a for a in state["artifacts"] if a["artifact_type"] == ACQUISITION_RESULT_ARTIFACT]
    assert len(acquisition_artifacts) == 1

    # A corrected acquisition reference becomes available (e.g. an operator
    # fix), and after the backoff window the worker retries successfully.
    good_uri = next(a["uri"] for a in acquisition_artifacts)
    good_sha = store.sha256_of(job_id, good_uri)
    jobs.add_artifact(job_id, OWNER, JOB_STAGES[2], ACQUISITION_RESULT_ARTIFACT,
                      good_uri, good_sha, ACQUISITION_RESULT_SCHEMA)
    time.sleep(1.1)

    analysis_worker_2 = Worker(database_file, "analysis-worker-2", {ANALYSIS_STAGE: handler},
                               retry_safe_stages={ANALYSIS_STAGE})
    assert analysis_worker_2.run_once() is True  # attempt 2: succeeds

    state2 = jobs.get(job_id, OWNER)
    stage2 = next(s for s in state2["stages"] if s["stage_key"] == ANALYSIS_STAGE)
    assert stage2["status"] == "COMPLETED"
    assert stage2["attempt_count"] == 2
    analysis_artifacts = [a for a in state2["artifacts"] if a["artifact_type"] == ANALYSIS_RESULT_ARTIFACT]
    assert len(analysis_artifacts) == 1  # the failed attempt committed nothing
    for key in JOB_STAGES[:3]:
        assert next(s for s in state2["stages"] if s["stage_key"] == key)["status"] == "COMPLETED"


def test_cross_user_access_to_analysis_is_rejected(tmp_path):
    database_file, storage_root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "f4-cross-user", "General", "en")
    job_id = job["job_id"]
    source = _source(store, job_id, "src-001", "https://example.com/a", "text/plain",
                     "A plain statement about the ministry in 2026.")
    uri, sha = _store_acquisition(store, job_id, [source])

    service = AnalysisService(database_file, storage_root)
    with pytest.raises(JobNotFound):
        service.analyze(job_id, "someone-else", uri, sha)


# ---------------------------------------------------------------------------
# End-to-end: the real F3 acquisition handler feeds the real F4 analysis
# handler, both driven only through the F2 durable worker. This is the actual
# pipeline wiring, not a stand-in for it: F4 reads exactly what F3's real
# providers (http_page here) write, under F3's real artifact_type/schema.
# ---------------------------------------------------------------------------

_PUBLIC_IP = "93.184.216.34"
_ARTICLE_HTML = (
    "<html lang='en'><head><title>S-400 induction update</title>"
    "<meta property='og:site_name' content='PIB'>"
    "<meta property='article:published_time' content='2026-10-08'></head>"
    "<body><p>Officials reportedly confirmed the S-400 squadron is not yet "
    "fully operational at the northern base in 2026.</p></body></html>"
)


def _dns_resolver(_host, _port):
    return [_PUBLIC_IP]


def _mock_http_factory():
    def handler(_request):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=_ARTICLE_HTML.encode("utf-8"))

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def test_real_f3_acquisition_output_feeds_real_f4_analysis_through_worker(tmp_path):
    database_file = tmp_path / "pipeline.sqlite3"
    storage_root = tmp_path / "storage"
    initialize_database(database_file)
    store = ArtifactStore(storage_root)
    jobs = JobService(database_file)

    job = jobs.create(OWNER, "f4-real-pipeline", "S-400 induction", "en")
    job_id = job["job_id"]
    WorkerService(database_file).queue(job_id, OWNER)

    def trivial(_context, _cancel):
        return None

    def produce_request(context, _cancel):
        request_doc = {
            "schema_version": "defence-studio.acquisition-request.v1",
            "job_id": context.job_id,
            "targets": [{"target_id": "a", "url": "https://pib.example/s400", "method": "http_page"}],
        }
        data = json.dumps(request_doc).encode("utf-8")
        uri = f"jobs/{context.job_id}/research/acquisition_request.json"
        stored = store.write_bytes(context.job_id, uri, data, "application/json")
        return [{"artifact_type": "acquisition_request", "uri": stored.uri,
                "sha256": stored.sha256, "schema_version": "defence-studio.acquisition-request.v1"}]

    acquire = build_acquisition_handler(
        store, registry=default_registry(youtube_backend=lambda *a, **k: YoutubeSubtitles(
            video_id="x", title=None, channel=None, upload_date=None, webpage_url=None)),
        guard=UrlGuard(resolver=_dns_resolver), client_factory=_mock_http_factory(),
    )
    analyze = make_analysis_handler(database_file, storage_root)

    handlers = {JOB_STAGES[0]: trivial, JOB_STAGES[1]: produce_request,
               JOB_STAGES[2]: acquire, ANALYSIS_STAGE: analyze}
    worker = Worker(database_file, "pipeline-worker", handlers, retry_safe_stages=set(handlers))
    while worker.run_once():
        pass

    state = jobs.get(job_id, OWNER)
    statuses = {s["stage_key"]: s["status"] for s in state["stages"]}
    assert statuses[JOB_STAGES[0]] == "COMPLETED"
    assert statuses[JOB_STAGES[1]] == "COMPLETED"
    assert statuses[JOB_STAGES[2]] == "COMPLETED"
    assert statuses[ANALYSIS_STAGE] == "COMPLETED"

    manifest_ref = next(a for a in state["artifacts"] if a["artifact_type"] == ACQUISITION_MANIFEST_ARTIFACT_TYPE)
    manifest = json.loads(store.read_text(job_id, manifest_ref["uri"]))
    assert manifest["sources"][0]["source_type"] == "web_page"
    assert manifest["sources"][0]["artifacts"][0]["artifact_type"] == "source_content"

    analysis_ref = next(a for a in state["artifacts"] if a["artifact_type"] == ANALYSIS_RESULT_ARTIFACT)
    analysis = json.loads(store.read_text(job_id, analysis_ref["uri"]))
    assert analysis["sources_normalized"] == 1
    assert analysis["normalized_sources"][0]["original_sha256_verified"] is True
    assert analysis["claims"], "expected a claim extracted from the real acquired HTML"
    claim = analysis["claims"][0]
    assert "reportedly" in claim["qualifiers"]
    assert claim["has_negation"] is True
    assert claim["source_published_at"] == "2026-10-08"
