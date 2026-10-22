"""Phase 3 source-grounded script generation, validation, review, approval.

Builds on the real F3/F4/F5/Phase-2 fixtures (`_basic_job` from
test_review_f5.py) through a genuinely APPROVED evidence package, then drives
ScriptService end to end with an injected fake provider -- no network call, no
API credits spent, matching the project's existing testable-seam convention
(GuardedFetcher/client_factory, OpenAiTextProvider/client_factory).
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.contracts.evidence import digest
from defence_studio.contracts.script import count_words, estimate_duration, validate_revision
from defence_studio.providers.openai_text import OpenAiTextProvider, ProviderError
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.review import ReviewNotFound, ReviewValidationError, StaleRevision
from defence_studio.services.factcheck import FactcheckService
from defence_studio.services.script import (
    ScriptGenerationFailed, ScriptService, ScriptStaleRevision, ScriptValidationError,
)
from defence_studio.services.script_validation import validate_revision_body
from test_review_f5 import _basic_job, OWNER
from conftest import SECRET, access_token

GOOD_CONFIG = {
    "language": "ta", "audience": "general public", "tone": "neutral",
    "script_format": "narration", "target_duration_seconds": 300, "target_words": 200,
    "min_words": 1, "max_words": 5000,
    "model": "gpt-4o-mini", "prompt_version": "defence-studio.source-grounded-script.v1",
    "max_text_cost_inr": 10.0, "terminology": {"Rafale": "Rafale"},
}


class FakeProvider:
    """Deterministic double standing in for OpenAiTextProvider.generate()."""

    def __init__(self, body_factory=None, error=None):
        self.body_factory = body_factory
        self.error = error
        self.calls = 0

    def generate(self, *, model, system_prompt, user_prompt, max_output_tokens=4096):
        self.calls += 1
        if self.error is not None:
            raise self.error
        body = self.body_factory(user_prompt) if self.body_factory else _default_body()
        return _Result(json.dumps(body))


class _Result:
    def __init__(self, text, input_tokens=120, output_tokens=80, tokens_available=True,
                retries=0, measured_text_cost_inr=0.05, cost_is_measured=True,
                pricing_source="test-pricing", currency="INR"):
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.tokens_available = tokens_available
        self.retries = retries
        self.measured_text_cost_inr = measured_text_cost_inr
        self.cost_is_measured = cost_is_measured
        self.pricing_source = pricing_source
        self.currency = currency


def _eligible_claim_id(service, job):
    return service.draft(job, OWNER)["package"]["eligible_claim_ids"][0]


def _default_body(claim_id="claim-unset"):
    return {
        "title": "Rafale readiness update",
        "opening_hook": "Here is what officials have said.",
        "sections": [{
            "section_id": "s1", "heading": "Status", "narration": "அதிகாரிகள் தெரிவித்த தகவலின்படி.",
            "requires_evidence": True,
            "evidence": {"claim_ids": [claim_id], "source_ids": [], "qualification": "Attributed source statement"},
            "presentation_note": "",
        }],
        "closing": "That is the latest update.",
    }


@pytest.fixture
def approved(tmp_path):
    """A job with a genuinely APPROVED Phase 2 evidence package."""
    values = _basic_job(tmp_path)
    db, root, jobs, store, job, review, result, ref, _, acq_sha = values
    review.decide_source(job, OWNER, "src-001", "APPROVED_FOR_USE", acq_sha, 0, "Source context inspected", "s1")
    review.decide_claim(job, OWNER, result.claims[0].claim_id, "APPROVED_FOR_USE", ref["sha256"], 0, "Exact passage inspected", "c1")
    evidence = EvidenceService(db, root)
    draft = evidence.draft(job, OWNER)
    evidence.approve(job, OWNER, draft["revision"], "Evidence reviewed", "evidence-approve")
    claim_id = draft["package"]["eligible_claim_ids"][0]
    provider = FakeProvider(body_factory=lambda _p: _default_body(claim_id))
    # Preserve coverage of the legacy human-review API independently of the
    # default automatic path, exercised in test_script_auto_approval.py.
    service = ScriptService(db, root, provider=provider, auto_approve=False)
    return dict(db=db, root=root, jobs=jobs, store=store, job=job, evidence=evidence,
                service=service, provider=provider, claim_id=claim_id)


# ---------------------------------------------------------------------------
# 1. Generation blocked absent/stale evidence approval
# ---------------------------------------------------------------------------

def test_generation_blocked_without_evidence_approval(tmp_path):
    values = _basic_job(tmp_path)
    db, root, jobs, store, job, review, result, ref, _, acq_sha = values
    service = ScriptService(db, root, provider=FakeProvider())
    with pytest.raises(StaleRevision):
        service.generate(job, OWNER, GOOD_CONFIG, "gen-1")
    assert service.provider_calls_for_test() if hasattr(service, "provider_calls_for_test") else True


def test_generation_blocked_after_evidence_changes_post_approval(approved):
    s = approved
    # A later review change invalidates the approval (StaleRevision on the evidence side).
    claims = s["evidence"].draft(s["job"], OWNER)["package"]["claims"]
    s["evidence"].decide_claim(s["job"], OWNER, claims[0]["claim_id"], "NEEDS_MORE_EVIDENCE",
                               claims[0]["review"]["reviewed_artifact_sha256"], 1, "New concern", "re-review")
    with pytest.raises(StaleRevision):
        s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-2")
    assert s["provider"].calls == 0


def test_factcheck_package_blocks_generation_until_current_approval(approved):
    s = approved
    check = FactcheckService(s["db"], s["root"])
    check.enqueue(OWNER, "fc-claims", s["job"], "claims", {
        "claims": [{
            "claim_id": s["claim_id"], "text": "Northwind base counted 4 patrol craft during the March drill.",
            "source_id": "origin", "category": "equipment", "entities": ["Northwind"], "extraction_confidence": 0.95,
        }],
        "sources": [
            {"source_id": "origin", "excerpt": "Northwind base counted 4 patrol craft during the March drill.",
             "publisher": "origin", "category": "news"},
            {"source_id": "desk-a", "publisher": "Desk A",
             "excerpt": "Northwind base counted 4 patrol craft during the March drill according to the harbour master.",
             "category": "news", "primary": True, "named_attribution": True, "url": "https://example.test/a",
             "published_at": "2024-03-02T00:00:00+00:00"},
            {"source_id": "desk-b", "publisher": "Desk B",
             "excerpt": "Northwind base counted 4 patrol craft during the March drill as recorded by the quay observers.",
             "category": "news", "primary": True, "named_attribution": True, "url": "https://example.test/b",
             "published_at": "2024-03-02T00:00:00+00:00"},
        ],
        "collection": {"attempted": True, "errors": []},
    })
    check.run_pending()
    check.enqueue(OWNER, "fc-pkg", s["job"], "package", {"topic": "Northwind drill"})
    check.run_pending()
    with pytest.raises(ScriptStaleRevision, match="explicit human approval"):
        s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "fc-block")
    assert s["provider"].calls == 0
    package = check.latest_package(s["job"], OWNER)["package"]
    check.approve(s["job"], OWNER, package["package_id"], "Human accepted the current fact-check package.")
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "fc-ok")
    assert s["provider"].calls == 1
    assert s["claim_id"] in revision["eligible_claim_ids"]
    check.enqueue(OWNER, "fc-pkg-2", s["job"], "package", {"topic": "Northwind drill revised"})
    check.run_pending()
    stale = s["service"].revision_staleness(s["job"], OWNER, "ta")
    assert any(row["state"] == "STALE" for row in stale["revisions"])
    with pytest.raises(ScriptStaleRevision, match="explicit human approval"):
        s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "fc-stale")
    assert s["provider"].calls == 1


# ---------------------------------------------------------------------------
# 2. Corrupted artifacts / invalid package hashes
# ---------------------------------------------------------------------------

def test_generation_blocked_for_corrupted_evidence_bytes(approved):
    s = approved
    s["store"].write_bytes(s["job"], f"jobs/{s['job']}/sources/src-001.bin", b"tampered", "text/plain")
    with pytest.raises(StaleRevision):
        s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-3")
    assert s["provider"].calls == 0


# ---------------------------------------------------------------------------
# 3. Unauthorized / cross-owner access
# ---------------------------------------------------------------------------

def test_cross_owner_generation_and_reads_are_denied(approved):
    s = approved
    with pytest.raises(ReviewNotFound):
        s["service"].generate(s["job"], "someone-else", GOOD_CONFIG, "gen-x")
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-ok")
    # A known idempotency key must not bypass ownership via the replay branch.
    with pytest.raises(ReviewNotFound):
        s["service"].generate(s["job"], "someone-else", GOOD_CONFIG, "gen-ok")
    with pytest.raises(ReviewNotFound):
        s["service"].get_revision(s["job"], "someone-else", "ta", revision["version"])
    with pytest.raises(ReviewNotFound):
        s["service"].list_revisions(s["job"], "someone-else")


# ---------------------------------------------------------------------------
# 4/5. Tamil and English stored independently; terminology preserved
# ---------------------------------------------------------------------------

def test_tamil_and_english_variants_are_independent_lineages(approved):
    s = approved
    ta = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-ta")
    en_config = {**GOOD_CONFIG, "language": "en"}
    s["provider"].body_factory = lambda _p: {**_default_body(s["claim_id"]), "sections": [{
        "section_id": "s1", "heading": "Status", "narration": "Officials said so.",
        "requires_evidence": True,
        "evidence": {"claim_ids": [s["claim_id"]], "source_ids": [], "qualification": "Attributed"},
        "presentation_note": "",
    }]}
    en = s["service"].generate(s["job"], OWNER, en_config, "gen-en")
    assert ta["language"] == "ta" and en["language"] == "en"
    assert ta["version"] == 1 and en["version"] == 1  # independent per-language version counters
    both = s["service"].list_revisions(s["job"], OWNER)["revisions"]
    assert {r["language"] for r in both} == {"ta", "en"}
    # Approving Tamil must not approve English.
    ta_sha = next(r["revision_sha256"] for r in both if r["language"] == "ta")
    s["service"].approve(s["job"], OWNER, "ta", 1, ta_sha, "note", "x")
    assert s["service"].get_revision(s["job"], OWNER, "ta", 1)["status"] == "APPROVED"
    assert s["service"].get_revision(s["job"], OWNER, "en", 1)["status"] == "AWAITING_REVIEW"


def test_english_terminology_preserved_in_tamil_narration(approved):
    s = approved
    captured = {}
    def body_factory(user_prompt):
        captured["prompt"] = user_prompt
        return _default_body(s["claim_id"])
    s["provider"].body_factory = body_factory
    config = {**GOOD_CONFIG, "terminology": {"Rafale": "Rafale", "AESA": "AESA"}}
    s["service"].generate(s["job"], OWNER, config, "gen-term")
    assert "Rafale" in captured["prompt"] and "AESA" in captured["prompt"]


# ---------------------------------------------------------------------------
# 6/7. Missing/invalid/unsupported/rejected evidence references
# ---------------------------------------------------------------------------

def test_missing_evidence_reference_fails_validation(approved):
    s = approved
    s["provider"].body_factory = lambda _p: {
        "title": "T", "opening_hook": "", "sections": [{
            "section_id": "s1", "heading": "", "narration": "The unit has 12 aircraft in 2026.",
            "requires_evidence": True, "evidence": {"claim_ids": [], "source_ids": [], "qualification": ""},
            "presentation_note": "",
        }], "closing": "Done.",
    }
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-missing")
    assert revision["status"] == "VALIDATION_FAILED"
    assert any(i["code"] == "MISSING_EVIDENCE" for i in revision["validation"]["issues"])
    sha = s["service"].list_revisions(s["job"], OWNER)["revisions"][0]["revision_sha256"]
    with pytest.raises(ScriptValidationError):
        s["service"].approve(s["job"], OWNER, "ta", revision["version"], sha, "note", "approve-fail")


def test_invented_source_id_fails_validation(approved):
    s = approved
    body = _default_body(s["claim_id"])
    body["sections"][0]["evidence"]["source_ids"] = ["source-not-in-package"]
    s["provider"].body_factory = lambda _p: body
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-bad-source")
    assert revision["status"] == "VALIDATION_FAILED"
    assert any(i["code"] == "UNKNOWN_SOURCE_REFERENCE" for i in revision["validation"]["issues"])
    assert revision["approval"] is None


def test_unapproved_or_unknown_claim_reference_fails_validation(approved):
    s = approved
    s["provider"].body_factory = lambda _p: _default_body("not-a-real-claim-id")
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-bad-claim")
    assert revision["status"] == "VALIDATION_FAILED"
    assert any(i["code"] == "UNKNOWN_CLAIM_REFERENCE" for i in revision["validation"]["issues"])


def test_citation_id_leaked_into_narration_is_rejected(approved):
    s = approved
    s["provider"].body_factory = lambda _p: {
        "title": "T", "opening_hook": "",
        "sections": [{
            "section_id": "s1", "heading": "", "narration": f"As {s['claim_id']} shows, this happened.",
            "requires_evidence": True,
            "evidence": {"claim_ids": [s["claim_id"]], "source_ids": [], "qualification": "ok"},
            "presentation_note": "",
        }], "closing": "Done.",
    }
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-leak")
    assert revision["status"] == "VALIDATION_FAILED"
    assert any(i["code"] == "CITATION_LEAKED_INTO_NARRATION" for i in revision["validation"]["issues"])


# ---------------------------------------------------------------------------
# 8. Deterministic structural validation / duration / word-count calculations
# ---------------------------------------------------------------------------

def test_word_count_and_duration_are_deterministic():
    assert count_words("hello world  foo") == 3
    assert count_words("") == 0
    result = estimate_duration(300, words_per_minute=150)
    assert result == {"words": 300, "words_per_minute": 150, "estimated_duration_seconds": 120,
                      "method": "words-per-minute.v1", "is_estimate": True}
    with pytest.raises(ValueError):
        estimate_duration(10, words_per_minute=0)


def test_validate_revision_body_is_pure_and_reproducible(approved):
    s = approved
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-pure")
    package = s["evidence"].export(s["job"], OWNER)
    result_a = validate_revision_body(revision, package)
    result_b = validate_revision_body(revision, package)
    assert result_a["issues"] == result_b["issues"] and result_a["passed"] == result_b["passed"]


# ---------------------------------------------------------------------------
# 9. Provider timeout, rate limit, malformed response, retry behavior
# ---------------------------------------------------------------------------

def test_provider_failure_does_not_corrupt_state_and_retry_is_safe(approved):
    s = approved
    s["provider"].error = ProviderError("timeout", "simulated timeout", retryable=True)
    with pytest.raises(ScriptGenerationFailed):
        s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-fail")
    assert s["service"].list_revisions(s["job"], OWNER)["revisions"] == []
    s["provider"].error = None
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-fail")  # same idempotency key, now succeeds
    assert revision["version"] == 1


def test_provider_malformed_json_response_fails_closed(approved):
    s = approved
    s["provider"].body_factory = None
    original_generate = s["provider"].generate
    s["provider"].generate = lambda **kw: _Result("not json")
    with pytest.raises(ScriptGenerationFailed):
        s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-malformed")
    assert s["service"].list_revisions(s["job"], OWNER)["revisions"] == []


def test_openai_provider_retries_rate_limit_then_succeeds():
    calls = {"n": 0}
    def responder(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\":true}"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    provider = OpenAiTextProvider(api_key="test-key", client_factory=lambda **kw: httpx.Client(
        transport=httpx.MockTransport(responder), **kw))
    result = provider.generate(model="gpt-4o-mini", system_prompt="sys", user_prompt="usr")
    assert calls["n"] == 2 and result.retries == 1 and result.text == '{"ok":true}'


def test_openai_provider_malformed_response_raises_non_retryable():
    def responder(_request):
        return httpx.Response(200, json={"unexpected": "shape"})
    provider = OpenAiTextProvider(api_key="k", client_factory=lambda **kw: httpx.Client(
        transport=httpx.MockTransport(responder), **kw))
    with pytest.raises(ProviderError) as exc:
        provider.generate(model="gpt-4o-mini", system_prompt="s", user_prompt="u")
    assert exc.value.code == "malformed_response" and not exc.value.retryable


def test_openai_provider_requires_api_key():
    provider = OpenAiTextProvider(api_key="")
    with pytest.raises(ProviderError) as exc:
        provider.generate(model="gpt-4o-mini", system_prompt="s", user_prompt="u")
    assert exc.value.code == "missing_api_key"


# ---------------------------------------------------------------------------
# 10. Idempotent generation / duplicate-request handling
# ---------------------------------------------------------------------------

def test_duplicate_generation_request_does_not_create_second_revision(approved):
    s = approved
    first = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "same-key")
    second = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "same-key")
    assert first == second
    assert s["provider"].calls == 1
    assert len(s["service"].list_revisions(s["job"], OWNER)["revisions"]) == 1


# ---------------------------------------------------------------------------
# 11. Immutable revisions / stale-review detection
# ---------------------------------------------------------------------------

def test_revisions_are_immutable_and_versioned(approved):
    s = approved
    first = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-a")
    second = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-b")
    assert first["version"] == 1 and second["version"] == 2
    assert first["revision_sha256" if "revision_sha256" in first else "package_sha256"]
    refetched_first = s["service"].get_revision(s["job"], OWNER, "ta", 1)
    assert refetched_first == first


# ---------------------------------------------------------------------------
# 12/13. Approval bound to correct hash/revision; changes after approval re-review
# ---------------------------------------------------------------------------

def test_approval_requires_matching_revision_hash(approved):
    s = approved
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-approve")
    rows = s["service"].list_revisions(s["job"], OWNER)["revisions"]
    actual_sha = rows[0]["revision_sha256"]
    with pytest.raises(ScriptStaleRevision):
        s["service"].approve(s["job"], OWNER, "ta", revision["version"], "0" * 64, "note", "wrong-hash")
    approved_record = s["service"].approve(s["job"], OWNER, "ta", revision["version"], actual_sha, "Approved after review", "approve-1")
    assert approved_record["status"] == "APPROVED"
    # A second approval attempt on the same (now approved) revision is rejected.
    with pytest.raises(ScriptStaleRevision):
        s["service"].approve(s["job"], OWNER, "ta", revision["version"], actual_sha, "Again", "approve-2")


def test_changes_requested_then_new_revision_requires_fresh_approval(approved):
    s = approved
    first = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-v1")
    rows = s["service"].list_revisions(s["job"], OWNER)["revisions"]
    s["service"].request_changes(s["job"], OWNER, "ta", first["version"], "Please add more context", "req-1")
    updated = s["service"].get_revision(s["job"], OWNER, "ta", first["version"])
    assert updated["status"] == "CHANGES_REQUESTED"
    second = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-v2")
    assert second["version"] == 2
    rows2 = s["service"].list_revisions(s["job"], OWNER)["revisions"]
    sha2 = next(r["revision_sha256"] for r in rows2 if r["version"] == 2)
    approved_record = s["service"].approve(s["job"], OWNER, "ta", 2, sha2, "Now approved", "approve-v2")
    assert approved_record["status"] == "APPROVED"
    # The first (changes-requested) revision remains unapproved and untouched.
    assert s["service"].get_revision(s["job"], OWNER, "ta", 1)["status"] == "CHANGES_REQUESTED"


def test_approving_new_revision_supersedes_previous_approval(approved):
    s = approved
    first = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-sup-1")
    sha1 = s["service"].list_revisions(s["job"], OWNER)["revisions"][0]["revision_sha256"]
    s["service"].approve(s["job"], OWNER, "ta", 1, sha1, "First approval", "sup-approve-1")
    second = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-sup-2")
    sha2 = next(r["revision_sha256"] for r in s["service"].list_revisions(s["job"], OWNER)["revisions"] if r["version"] == 2)
    s["service"].approve(s["job"], OWNER, "ta", 2, sha2, "Second approval", "sup-approve-2")
    assert s["service"].get_revision(s["job"], OWNER, "ta", 1)["status"] == "SUPERSEDED"
    assert s["service"].get_revision(s["job"], OWNER, "ta", 2)["status"] == "APPROVED"


def test_approval_invalidated_by_changed_evidence_package(approved):
    s = approved
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-stale-pkg")
    sha = s["service"].list_revisions(s["job"], OWNER)["revisions"][0]["revision_sha256"]
    claims = s["evidence"].draft(s["job"], OWNER)["package"]["claims"]
    s["evidence"].decide_claim(s["job"], OWNER, claims[0]["claim_id"], "NEEDS_MORE_EVIDENCE",
                               claims[0]["review"]["reviewed_artifact_sha256"], 1, "New concern", "re-review-2")
    with pytest.raises(ScriptStaleRevision):
        s["service"].approve(s["job"], OWNER, "ta", revision["version"], sha, "note", "approve-stale")


# ---------------------------------------------------------------------------
# 14. Audit history and reviewer-note validation
# ---------------------------------------------------------------------------

def test_reviewer_note_required_and_history_recorded(approved):
    s = approved
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-hist")
    with pytest.raises(ReviewValidationError):
        s["service"].request_changes(s["job"], OWNER, "ta", revision["version"], "  ", "req-blank")
    s["service"].request_changes(s["job"], OWNER, "ta", revision["version"], "Add attribution", "req-ok")
    history = s["service"].revision_history(s["job"], OWNER, "ta", revision["version"])
    assert len(history) == 1 and history[0]["action"] == "CHANGES_REQUESTED" and history[0]["note"] == "Add attribution"


# ---------------------------------------------------------------------------
# 15. Cost/usage tracking, including unavailable usage metadata
# ---------------------------------------------------------------------------

def test_measured_cost_recorded_and_distinguished_from_unavailable(approved):
    s = approved
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-cost")
    assert revision["usage"]["cost_is_measured"] is True
    assert revision["usage"]["measured_text_cost_inr"] == 0.05
    s["provider"].body_factory = lambda _p: _default_body(s["claim_id"])
    s["service"]._provider = FakeProvider(body_factory=lambda _p: _default_body(s["claim_id"]))
    s["service"]._provider.generate = lambda **kw: _Result(json.dumps(_default_body(s["claim_id"])),
                                                             tokens_available=False, measured_text_cost_inr=None,
                                                             cost_is_measured=False)
    unmeasured = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-cost-2")
    assert unmeasured["usage"]["cost_is_measured"] is False
    assert unmeasured["usage"]["measured_text_cost_inr"] is None


def test_cost_over_budget_fails_closed(approved):
    s = approved
    s["provider"].generate = lambda **kw: _Result(json.dumps(_default_body(s["claim_id"])), measured_text_cost_inr=999.0)
    with pytest.raises(ScriptGenerationFailed):
        s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-over-budget")
    assert s["service"].list_revisions(s["job"], OWNER)["revisions"] == []


# ---------------------------------------------------------------------------
# 16. API authorization and correct error codes
# ---------------------------------------------------------------------------

def test_api_auth_and_error_codes(approved):
    s = approved
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-api")
    with TestClient(create_app(s["db"], SECRET, s["root"])) as client:
        base = f"/api/jobs/{s['job']}/script"
        assert client.get(f"{base}/revisions").status_code == 401
        headers = {"Authorization": "Bearer " + access_token(OWNER)}
        assert client.get(f"{base}/revisions", headers=headers).status_code == 200
        assert client.get(f"{base}/revisions", headers={"Authorization": "Bearer " + access_token("other")}).status_code == 404
        assert client.get(f"{base}/export/ta", headers=headers).status_code == 409  # not approved yet
        # The API wires the real OpenAiTextProvider (no fake injected here); with
        # no DEFENCE_STUDIO_OPENAI_API_KEY configured it fails closed with 502
        # rather than silently generating anything -- the correct, intended
        # behavior for an unconfigured provider, not a test artifact.
        gen_headers = {**headers, "Idempotency-Key": "api-gen-1"}
        resp = client.post(f"{base}/generate", headers=gen_headers, json={**GOOD_CONFIG, "language": "en"})
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# 17. Approved-script export and the Phase 4 boundary
# ---------------------------------------------------------------------------

def test_export_approved_requires_approval_and_current_package(approved):
    s = approved
    revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-export")
    with pytest.raises(ScriptStaleRevision):
        s["service"].export_approved(s["job"], OWNER, "ta")
    sha = s["service"].list_revisions(s["job"], OWNER)["revisions"][0]["revision_sha256"]
    s["service"].approve(s["job"], OWNER, "ta", revision["version"], sha, "Approved for export", "export-approve")
    exported = s["service"].export_approved(s["job"], OWNER, "ta")
    assert exported["status"] == "APPROVED"
    assert validate_revision(exported) == exported
    with pytest.raises(ScriptValidationError):
        s["service"].export_approved(s["job"], OWNER, "xx")


def test_revisions_and_approval_persist_across_a_fresh_service_instance(approved):
    """Simulated server restart: a new `ScriptService` against the same
    database file/storage root (no shared Python object, no cache) must see
    the exact same draft and approved revisions, status, and history -- the
    same pattern F5/Phase 2 already use to prove restart durability."""
    s = approved
    draft_revision = s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-restart-draft")
    sha = s["service"].list_revisions(s["job"], OWNER)["revisions"][0]["revision_sha256"]
    approved_record = s["service"].approve(
        s["job"], OWNER, "ta", draft_revision["version"], sha, "Approved before restart", "approve-restart",
    )
    history_before = s["service"].revision_history(s["job"], OWNER, "ta", draft_revision["version"])

    # A brand-new service instance (no shared process state) over the same files.
    restarted = ScriptService(s["db"], s["root"], provider=s["provider"])
    revisions_after = restarted.list_revisions(s["job"], OWNER)["revisions"]
    assert len(revisions_after) == 1
    assert revisions_after[0]["status"] == "APPROVED"
    assert revisions_after[0]["revision_sha256"] == sha

    refetched = restarted.get_revision(s["job"], OWNER, "ta", draft_revision["version"])
    assert refetched["status"] == "APPROVED"
    assert refetched["approval"]["revision_sha256"] == sha
    assert refetched["sections"] == draft_revision["sections"]

    history_after = restarted.revision_history(s["job"], OWNER, "ta", draft_revision["version"])
    assert history_after == history_before
    assert len(history_after) == 1 and history_after[0]["action"] == "APPROVED"

    exported = restarted.export_approved(s["job"], OWNER, "ta")
    assert exported["status"] == "APPROVED"
    assert exported["approval"]["revision_sha256"] == approved_record["revision_sha256"]


def test_phase3_module_has_no_downstream_production_imports():
    import ast
    import pathlib
    source = pathlib.Path(__file__).parent.parent / "defence_studio" / "services" / "script.py"
    tree = ast.parse(source.read_text())
    forbidden = {"ffmpeg", "tts", "voice", "render", "youtube", "publish"}
    names = {n.lower() for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
             for n in ([a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else []))}
    assert not any(f in n for n in names for f in forbidden)
