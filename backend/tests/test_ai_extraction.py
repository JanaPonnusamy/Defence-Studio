"""AI-assisted claim selection over normalized text. Provider is a mock: this proves
grounding/validation/persistence behaviour, not live model quality."""
import json

import pytest

from defence_studio.analysis.ai_schema import AiOutputError, ValidatedAiClaim, ground_ai_claim
from defence_studio.database import connect
from defence_studio.providers.openai_text import ProviderError, ProviderResult
from defence_studio.services.review import ReviewNotFound, ReviewService
from defence_studio.services.script import ScriptGenerationFailed, ScriptService
from test_content_flow import TEXT, build_content_fixture


def result(payload, cost=0.01):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return ProviderResult(text=text, input_tokens=100, output_tokens=20, tokens_available=True, retries=0,
                          measured_text_cost_inr=cost, cost_is_measured=True, pricing_source="test")


class Scripted:
    name = "mock-scripted"
    is_paid = False
    supports_response_schema = True

    def __init__(self, responder):
        self.responder, self.calls = responder, []

    def generate(self, **kw):
        self.calls.append(kw)
        out = self.responder(kw, len(self.calls))
        if isinstance(out, Exception):
            raise out
        return out


def span(sentence):
    i = TEXT.find(sentence)
    return {"claim_text": sentence, "start_offset": i, "end_offset": i + len(sentence)}


S1 = "According to the Ministry of Defence, GSAT-7C is intended for the Indian Air Force."


@pytest.fixture
def job(tmp_path):
    db, root, job_id, owner = build_content_fixture(tmp_path)
    return db, root, job_id, owner


def run(job, provider, **kw):
    db, root, job_id, owner = job
    service = ScriptService(db, root, provider=provider)
    return service, service.extract_claims_ai(job_id, owner, model="gpt-4o-mini", max_cost_inr=5, **kw)


def test_grounding_unit_rules():
    text = "alpha beta. gamma delta. alpha beta."
    ok, how = ground_ai_claim(ValidatedAiClaim("gamma delta.", 12, 24, (), False), text)
    assert how == "offsets"
    fixed, how = ground_ai_claim(ValidatedAiClaim("gamma delta.", 0, 5, (), False), text)
    assert how == "text_search" and text[fixed.start_offset:fixed.end_offset] == "gamma delta."
    with pytest.raises(AiOutputError):
        ground_ai_claim(ValidatedAiClaim("not in source", 0, 5, (), False), text)
    with pytest.raises(AiOutputError):                       # ambiguous: occurs twice, offsets wrong
        ground_ai_claim(ValidatedAiClaim("alpha beta.", 13, 20, (), False), text)


def test_real_normalized_text_is_sent_and_only_verbatim_spans_are_kept(job):
    db, root, job_id, owner = job
    provider = Scripted(lambda kw, n: result({"claims": [
        span(S1),
        {"claim_text": "The Ministry announced a 500 crore GSAT-7C deal.", "start_offset": 0, "end_offset": 30},  # invented
        {"claim_text": S1.replace("Air Force", "Navy"), "start_offset": 0, "end_offset": 20},                   # altered
    ]}))
    _, out = run(job, provider)
    sent = provider.calls[0]
    assert "Varunastra" in sent["user_prompt"] and S1 in sent["user_prompt"]            # real normalized text
    assert sent["response_schema"]["required"] == ["claims"]
    source = out["sources"][0]
    assert [c["claim_text"] for c in source["claims"]] == [S1]
    assert len(source["rejected_claims"]) == 2
    claim = source["claims"][0]
    known = ReviewService(db, root).list_sources(job_id, owner)["sources"][0]["source_id"]
    assert claim["source_id"] == known and claim["extraction_method"] == "ai-assisted-span-selection"
    assert claim["passage"]["text"] == S1 and claim["claim_id"].startswith("claim-")
    assert claim["duplicates_deterministic_claim"] is True       # same span the deterministic extractor found
    assert out["review_status"] == "NOT_REVIEWED" and out["provenance"]["response_format"] == "json_schema"


def test_wrong_offsets_are_repaired_only_by_exact_text(job):
    wrong = {**span(S1), "start_offset": 3, "end_offset": 9}
    _, out = run(job, Scripted(lambda kw, n: result({"claims": [wrong]})))
    c = out["sources"][0]["claims"][0]
    assert c["offset_resolution"] == "text_search"
    p = c["passage"]
    assert (p["start_offset"], p["end_offset"]) != (3, 9) and p["end_offset"] - p["start_offset"] == len(S1)
    assert c["claim_text"] == p["text"] == S1


@pytest.mark.parametrize("payload", [
    "not json", "[]", '{"claims": "x"}', '{"claims": [], "extra": 1}',
    {"claims": [{"claim_text": "", "start_offset": 0, "end_offset": 3}]},
    {"claims": [{"claim_text": "abc", "start_offset": 5, "end_offset": 2}]},
    {"claims": [{"claim_text": "abc", "start_offset": 0, "end_offset": 10 ** 9}]},
    {"claims": [{"claim_text": "abc", "start_offset": True, "end_offset": 3}]},
])
def test_structural_violations_quarantine_the_source_and_persist_nothing(job, payload):
    db, root, job_id, owner = job
    service = ScriptService(db, root, provider=Scripted(lambda kw, n: result(payload)))
    with pytest.raises(ScriptGenerationFailed, match="SCHEMA_INVALID"):
        service.extract_claims_ai(job_id, owner, model="gpt-4o-mini", max_cost_inr=5)
    with pytest.raises(ReviewNotFound):
        service.latest_ai_extraction(job_id, owner)


@pytest.mark.parametrize("code", ["refusal", "truncated_output", "malformed_response", "missing_api_key"])
def test_provider_failures_are_explicit_not_false_success(job, code):
    provider = Scripted(lambda kw, n: ProviderError(code, "boom", retryable=False))
    db, root, job_id, owner = job
    with pytest.raises(ScriptGenerationFailed, match=code):
        ScriptService(db, root, provider=provider).extract_claims_ai(job_id, owner, model="m", max_cost_inr=5)


def test_idempotent_reuse_means_no_second_paid_call_and_reopen_matches(job):
    db, root, job_id, owner = job
    provider = Scripted(lambda kw, n: result({"claims": [span(S1)]}))
    service, first = run(job, provider)
    again = service.extract_claims_ai(job_id, owner, model="gpt-4o-mini", max_cost_inr=5)
    assert len(provider.calls) == 1 and again["reused"] is True and first["reused"] is False
    reopened = ScriptService(db, root, provider=Scripted(lambda kw, n: 1 / 0)).latest_ai_extraction(job_id, owner)
    assert reopened["sources"][0]["claims"] == first["sources"][0]["claims"]


def test_budget_guard_blocks_unknown_model_and_overspend_before_any_call(job):
    db, root, job_id, owner = job

    class Paid(Scripted):
        is_paid = True
    provider = Paid(lambda kw, n: result({"claims": []}))
    service = ScriptService(db, root, provider=provider)
    with pytest.raises(ScriptGenerationFailed, match="no pricing"):
        service.extract_claims_ai(job_id, owner, model="gpt-9-unknown", max_cost_inr=5)
    with pytest.raises(ScriptGenerationFailed, match="exceeds the configured budget"):
        service.extract_claims_ai(job_id, owner, model="gpt-4o", max_cost_inr=0.0001)
    assert provider.calls == []


def test_other_owner_is_denied_and_approval_state_is_untouched(job):
    db, root, job_id, owner = job

    def counts():
        with connect(db) as c:
            return [c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("claim_reviews", "evidence_packages", "script_revisions", "workflow_artifacts")]
    before = counts()
    provider = Scripted(lambda kw, n: result({"claims": [span(S1)]}))
    service = ScriptService(db, root, provider=provider)
    with pytest.raises(ReviewNotFound):
        service.extract_claims_ai(job_id, "someone-else", model="gpt-4o-mini", max_cost_inr=5)
    assert provider.calls == []
    service.extract_claims_ai(job_id, owner, model="gpt-4o-mini", max_cost_inr=5)
    assert counts() == before
