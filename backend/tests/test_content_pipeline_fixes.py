"""Audit fixes: malformed draft handling, output-token limit, idempotency, coverage.

Mocked/offline only: no network, no paid call, no production database."""
import json

import httpx
import pytest

from defence_studio.providers.openai_text import OpenAiTextProvider, ProviderError, output_token_limit
from defence_studio.services.research_monitor import build_coverage
from defence_studio.services.review import RevisionConflict
from defence_studio.services.script import ScriptGenerationFailed, ScriptService, draft_shape_problems
from test_evidence_policy import OWNER as POLICY_OWNER  # noqa: F401
from test_research_monitor import ran  # noqa: F401
from test_script_phase3 import FakeProvider, GOOD_CONFIG, OWNER, _default_body, approved  # noqa: F401


def _service(approved, factory):
    return ScriptService(approved["db"], approved["root"], provider=FakeProvider(body_factory=factory),
                         auto_approve=False)


def _good(approved):
    return lambda _p: _default_body(approved["claim_id"])


# -- 1. malformed model output ---------------------------------------------------------------

def _mutate(approved, fn):
    def factory(_p):
        body = _default_body(approved["claim_id"])
        fn(body)
        return body
    return factory


MALFORMED = {
    "sections_is_string": lambda b: b.update(sections="oops"),
    "sections_null": lambda b: b.update(sections=None),
    "section_is_string": lambda b: b.update(sections=["just text"]),
    "narration_is_list": lambda b: b["sections"][0].update(narration=["a", "b"]),
    "narration_missing": lambda b: b["sections"][0].pop("narration"),
    "narration_is_number": lambda b: b["sections"][0].update(narration=12),
    "closing_is_dict": lambda b: b.update(closing={"x": 1}),
    "opening_missing": lambda b: b.pop("opening_hook"),
    "title_is_int": lambda b: b.update(title=5),
    "evidence_is_list": lambda b: b["sections"][0].update(evidence=["c1"]),
    "claim_ids_wrong_type": lambda b: b["sections"][0]["evidence"].update(claim_ids="c1"),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_structure_is_a_recorded_failure_not_a_crash(approved, name):
    service = _service(approved, _mutate(approved, MALFORMED[name]))
    with pytest.raises(ScriptGenerationFailed, match="script schema"):
        service.generate(approved["job"], OWNER, GOOD_CONFIG, "k-malformed")
    assert service.list_revisions(approved["job"], OWNER)["revisions"] == []
    records = service.list_inspections(approved["job"], OWNER, "ta")["records"]
    assert any(r.get("outcome") == "SCHEMA_INVALID" for r in records)


@pytest.mark.parametrize("body", [[], "text", 3, None])
def test_non_object_draft_has_shape_problem(body):
    assert draft_shape_problems(body)


def test_valid_shape_passes_and_generates(approved):
    service = _service(approved, _good(approved))
    assert service.generate(approved["job"], OWNER, GOOD_CONFIG, "k-ok")["version"] == 1


# -- 2. output-token limit ---------------------------------------------------------------------

def test_limit_covers_tamil_and_reasoning_models():
    assert output_token_limit("gpt-4o-mini", "ta", 1300) >= 1300 * 8
    assert output_token_limit("gpt-4o-mini", "en", 1300) < output_token_limit("gpt-4o-mini", "ta", 1300)
    assert output_token_limit("gpt-5", "en", 1300) > output_token_limit("gpt-4o", "en", 1300) + 7000
    assert output_token_limit("gpt-5", "ta", 5000) <= 32000


def test_service_passes_explicit_limit_to_provider(approved):
    seen = {}

    class Spy(FakeProvider):
        def generate(self, *, model, system_prompt, user_prompt, max_output_tokens=4096):
            seen["limit"] = max_output_tokens
            return super().generate(model=model, system_prompt=system_prompt, user_prompt=user_prompt)

    service = ScriptService(approved["db"], approved["root"],
                            provider=Spy(body_factory=_good(approved)), auto_approve=False)
    service.generate(approved["job"], OWNER, GOOD_CONFIG, "k-limit")
    assert seen["limit"] == output_token_limit(GOOD_CONFIG["model"], "ta", GOOD_CONFIG["max_words"])
    assert seen["limit"] > 4096


def _client(payload, bodies):
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=payload)
    return lambda **k: httpx.Client(transport=httpx.MockTransport(handler), **k)


def test_truncated_completion_is_provider_failure_not_content(approved):
    payload = {"choices": [{"finish_reason": "length", "message": {"content": "{\"title\": \"cut"}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
    bodies = []
    provider = OpenAiTextProvider(api_key="sk-test-key-0000", max_retries=0, client_factory=_client(payload, bodies))
    with pytest.raises(ProviderError) as err:
        provider.generate(model="gpt-4o-mini", system_prompt="s", user_prompt="u", max_output_tokens=9000)
    assert err.value.code == "truncated_output"
    service = ScriptService(approved["db"], approved["root"], provider=provider, auto_approve=False)
    with pytest.raises(ScriptGenerationFailed, match="truncated_output"):
        service.generate(approved["job"], OWNER, GOOD_CONFIG, "k-trunc")
    assert service.list_revisions(approved["job"], OWNER)["revisions"] == []


@pytest.mark.parametrize("model,absent", [("gpt-5", "temperature"), ("gpt-4o-mini", None)])
def test_api_parameter_matches_model(model, absent):
    payload = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
    bodies = []
    provider = OpenAiTextProvider(api_key="sk-test-key-0000", client_factory=_client(payload, bodies))
    provider.generate(model=model, system_prompt="s", user_prompt="u", max_output_tokens=12345)
    assert bodies[0]["max_completion_tokens"] == 12345 and "max_tokens" not in bodies[0]
    if absent:
        assert absent not in bodies[0]


def test_budget_guard_still_blocks_with_larger_limit(approved):
    cfg = dict(GOOD_CONFIG, model="gpt-4o", max_text_cost_inr=0.01)
    service = _service(approved, _good(approved))
    service._provider.is_paid = True
    with pytest.raises(ScriptGenerationFailed, match="Budget guard"):
        service.generate(approved["job"], OWNER, cfg, "k-budget")
    assert service._provider.calls == 0


# -- 4. idempotency ----------------------------------------------------------------------------

def test_same_key_same_settings_replays(approved):
    service = _service(approved, _good(approved))
    a = service.generate(approved["job"], OWNER, GOOD_CONFIG, "k-same")
    b = service.generate(approved["job"], OWNER, GOOD_CONFIG, "k-same")
    assert a["version"] == b["version"] == 1 and service._provider.calls == 1


@pytest.mark.parametrize("change", [{"min_words": 2}, {"max_words": 4000}, {"model": "gpt-4o"},
                                    {"tone": "formal"}])
def test_same_key_different_settings_cannot_replay(approved, change):
    service = _service(approved, _good(approved))
    service.generate(approved["job"], OWNER, GOOD_CONFIG, "k-diff")
    with pytest.raises(RevisionConflict):
        service.generate(approved["job"], OWNER, dict(GOOD_CONFIG, **change), "k-diff")
    assert len(service.list_revisions(approved["job"], OWNER)["revisions"]) == 1


def test_word_range_changes_the_request_digest(approved):
    service = _service(approved, _good(approved))
    a = service.generate(approved["job"], OWNER, GOOD_CONFIG, "k-a")
    b = service.generate(approved["job"], OWNER, dict(GOOD_CONFIG, max_words=4000), "k-b")
    assert a["request_sha256"] != b["request_sha256"]


# -- 3. coverage ---------------------------------------------------------------------------------

REQUEST = {"search_terms": ["S-400", "delivery"]}
DISC = {"candidates": [{"candidate_id": str(i)} for i in range(6)], "providers": [
    {"provider": "rss", "status": "OK", "message": "ok", "candidates_returned": 6, "http_attempts": 3, "duration_ms": 40},
    {"provider": "configured_search_api", "status": "UNAVAILABLE_MISSING_CREDENTIALS", "message": "no key",
     "candidates_returned": 0, "requires_credentials": True},
    {"provider": "weird", "status": "SOMETHING_NEW", "message": "?", "candidates_returned": 0}]}
ACQ = {"skipped_duplicate": 1, "sources": [
    {"source_id": "a", "requested_url": "https://pib.example/1", "status": "ACQUIRED"},
    {"source_id": "b", "requested_url": "https://thehindu.example/2", "status": "ACQUIRED"},
    {"source_id": "c", "requested_url": "https://blog.invalid/3", "status": "FAILED", "error": "SOURCE_NOT_ALLOWLISTED"},
    {"source_id": "d", "requested_url": "https://pib.example/4", "status": "FAILED", "error": "HTTP 503"}]}
ANA = {"sources_normalized": 2, "normalized_sources": [{}, {}], "duplicate_groups": [{"g": 1}],
       "claims": [{"claim_id": str(i)} for i in range(5)]}
POL = {"sources": [{"publisher": "PIB", "independence_group": "g-pib"},
                   {"publisher": "The Hindu", "independence_group": "g-hindu"},
                   {"publisher": "PIB", "independence_group": "g-pib"}],
       "counts": {"claims": {"AUTO_ACCEPTED": 3, "NEEDS_REVIEW": 1, "REJECTED": 1}},
       "ready_for_generation": False, "policy_criteria_failed": [{"criterion": "x"}]}


def test_coverage_keeps_stages_separate_and_counts_real_records():
    c = build_coverage(REQUEST, DISC, ACQ, ANA, POL)
    assert (c["candidate_urls_discovered"], c["unique_urls_attempted"], c["sources_fetched"]) == (6, 4, 2)
    assert (c["sources_rejected_by_policy"], c["sources_errors_or_unsupported"], c["sources_normalized"]) == (1, 1, 2)
    assert c["duplicate_urls_removed"] == 1 and c["duplicate_document_groups"] == 1
    assert c["unique_publishers"] == ["PIB", "The Hindu"] and c["independent_source_groups"] == ["g-hindu", "g-pib"]
    assert (c["claims_extracted"], c["claims_auto_accepted"], c["claims_needs_review"], c["claims_rejected"]) == (5, 3, 1, 1)
    assert c["search_terms_count"] == 2 and c["provider_http_attempts"] == 3
    assert "search_queries_executed" not in c


def test_coverage_provider_configuration_is_unknown_when_not_established():
    c = build_coverage(REQUEST, DISC, ACQ, ANA, POL)
    by = {p["name"]: p for p in c["providers"]}
    assert by["rss"]["configured"] is True
    assert by["configured_search_api"]["configured"] is False
    assert by["configured_search_api"]["status"].startswith("UNAVAILABLE")
    assert by["weird"]["configured"] is None and by["weird"]["http_attempts"] is None
    assert c["providers_not_configured"] == 1


def test_coverage_with_nothing_stored_reports_unknown_not_zero_queries():
    c = build_coverage({}, {}, {}, {}, {})
    assert c["search_terms"] is None and c["search_terms_count"] is None
    assert c["candidate_urls_discovered"] is None and c["provider_http_attempts"] is None
    assert c["sources_fetched"] == c["sources_normalized"] == c["claims_extracted"] == 0
    assert "NOT_IMPLEMENTED" in c["images_and_assets"]


def test_coverage_service_path_uses_stored_artifacts(ran):  # noqa: F811
    mon, job, *_ = ran
    cov = mon.coverage_report(job, POLICY_OWNER)
    assert cov["sources_fetched"] == 2
    assert cov["sources_rejected_by_policy"] + cov["sources_errors_or_unsupported"] == 1
    assert cov["unique_urls_attempted"] == 3
    assert cov["sources_normalized"] >= 1 and cov["claims_extracted"] >= 1
    assert mon.monitor(job, POLICY_OWNER)["coverage"]["sources_fetched"] == 2
