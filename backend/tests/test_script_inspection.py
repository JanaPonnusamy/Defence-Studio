"""Request/response inspection for Phase 3 generation (real OpenAiTextProvider
over httpx.MockTransport: zero network, zero credits)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.providers.openai_text import OpenAiTextProvider
from defence_studio.services.script import ScriptGenerationFailed, ScriptService
from defence_studio.services.script_inspection import sanitize
from test_review_f5 import OWNER
from test_script_phase3 import GOOD_CONFIG, _default_body, approved  # noqa: F401  (fixture)
from conftest import SECRET, access_token

KEY = "sk-test-SECRETKEY1234567890"


def _service(s, content, status=200, sent=None):
    def responder(request):
        if sent is not None:
            sent.append(json.loads(request.content))
            sent.append(dict(request.headers))
        return httpx.Response(status, headers={"x-request-id": "req_abc"}, json={
            "model": "gpt-4o-mini", "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 120}})
    provider = OpenAiTextProvider(api_key=KEY, client_factory=lambda **kw: httpx.Client(
        transport=httpx.MockTransport(responder), **kw))
    return ScriptService(s["db"], s["root"], provider=provider)


def _only_record(service, s, lang="ta"):
    rows = service.list_inspections(s["job"], OWNER, lang)["records"]
    assert len(rows) == 1
    return service.get_inspection(s["job"], OWNER, lang, rows[0]["record_id"])


def test_record_matches_actual_wire_request_and_hides_secrets(approved):
    s = approved
    sent = []
    svc = _service(s, json.dumps(_default_body(s["claim_id"])), sent=sent)
    svc.generate(s["job"], OWNER, GOOD_CONFIG, "insp-1")
    rec = _only_record(svc, s)
    assert rec["request"]["wire_body"] == sent[0]                      # exactly what the client sent
    assert rec["request"]["structure"]["wire_messages_match_client_input"] is True
    assert rec["request"]["structure"]["evidence_references"]["claim_ids"] == [s["claim_id"]]
    assert rec["outcome"] == "AWAITING_REVIEW" and rec["script_accepted"] is True
    assert rec["raw_response"]["http"]["provider_request_id"] == "req_abc"
    assert rec["raw_response"]["usage"]["input_tokens"] == 400
    assert rec["clean_output"]["status_label"] == "ACCEPTED_FOR_REVIEW"
    assert "authorization" in {k.lower() for k in sent[1]}              # sent on the wire...
    assert KEY not in json.dumps(rec) and "Bearer" not in json.dumps(rec)  # ...never stored


def test_invalid_reference_is_preserved_but_never_labelled_success(approved):
    s = approved
    svc = _service(s, json.dumps(_default_body("claim-does-not-exist")))
    rev = svc.generate(s["job"], OWNER, GOOD_CONFIG, "insp-2")
    rec = _only_record(svc, s)
    assert rev["status"] == "VALIDATION_FAILED"
    assert rec["outcome"] == "VALIDATION_FAILED" and rec["script_accepted"] is False
    assert rec["clean_output"]["status_label"] == "REJECTED_NOT_A_SCRIPT"
    refs = rec["parsed_response"]["evidence_reference_checks"]
    assert refs and refs[0]["in_eligible_set"] is False
    assert rec["validation_report"]["passed"] is False and rec["validation_report"]["issues"]


def test_malformed_json_keeps_raw_text_and_field_errors_are_reported(approved):
    s = approved
    svc = _service(s, "not json at all")
    with pytest.raises(ScriptGenerationFailed):
        svc.generate(s["job"], OWNER, GOOD_CONFIG, "insp-3")
    rec = _only_record(svc, s)
    assert rec["outcome"] == "MALFORMED_JSON" and rec["raw_response"]["returned_text"] == "not json at all"
    assert rec["parsed_response"]["parsed"] is False and rec["clean_output"] is None
    # Extra / missing fields are named, not papered over.
    body = _default_body(s["claim_id"]); body["bogus"] = 1; del body["closing"]
    svc2 = _service(s, json.dumps(body))
    with pytest.raises(ScriptGenerationFailed):
        svc2.generate(s["job"], OWNER, {**GOOD_CONFIG, "language": "en"}, "insp-4")
    rec2 = _only_record(svc2, s, "en")
    assert rec2["outcome"] == "SCHEMA_INVALID" and rec2["script_accepted"] is False
    assert rec2["parsed_response"]["schema_errors"]
    errs = rec2["parsed_response"]["field_errors"]
    assert any("bogus" in e for e in errs) and any("closing" in e for e in errs)


def test_budget_block_makes_zero_provider_calls(approved):
    s = approved
    sent = []
    svc = _service(s, json.dumps(_default_body(s["claim_id"])), sent=sent)
    with pytest.raises(ScriptGenerationFailed):
        svc.generate(s["job"], OWNER, {**GOOD_CONFIG, "max_text_cost_inr": 0.0001}, "insp-5")
    assert sent == []
    rec = _only_record(svc, s)
    assert rec["outcome"] == "BLOCKED_BUDGET" and rec["provider_calls"] == 0 and rec["raw_response"]["available"] is False


def test_provider_error_has_no_automatic_retry_of_validation_and_records_attempts(approved):
    s = approved
    calls = []
    def responder(request):
        calls.append(1)
        return httpx.Response(400, text=f"bad key {KEY}")
    provider = OpenAiTextProvider(api_key=KEY, client_factory=lambda **kw: httpx.Client(
        transport=httpx.MockTransport(responder), **kw))
    svc = ScriptService(s["db"], s["root"], provider=provider)
    with pytest.raises(ScriptGenerationFailed):
        svc.generate(s["job"], OWNER, GOOD_CONFIG, "insp-6")
    rec = _only_record(svc, s)
    assert len(calls) == 1 and rec["outcome"] == "PROVIDER_ERROR"
    assert KEY not in json.dumps(rec)


def test_inspection_api_requires_owner(approved):
    s = approved
    svc = _service(s, json.dumps(_default_body(s["claim_id"])))
    svc.generate(s["job"], OWNER, GOOD_CONFIG, "insp-7")
    rid = svc.list_inspections(s["job"], OWNER, "ta")["records"][0]["record_id"]
    with TestClient(create_app(s["db"], SECRET, s["root"])) as client:
        url = f"/api/jobs/{s['job']}/script/inspections/ta"
        assert client.get(url).status_code == 401
        mine = {"Authorization": "Bearer " + access_token(OWNER)}
        other = {"Authorization": "Bearer " + access_token("other")}
        assert client.get(url, headers=other).status_code == 404
        assert client.get(f"{url}/{rid}", headers=other).status_code == 404
        r = client.get(f"{url}/{rid}", headers=mine)
        assert r.status_code == 200 and r.json()["record_id"] == rid
        assert client.get(f"{url}/../../x", headers=mine).status_code in (404, 422)
        assert client.get(f"{url}/..%2F..%2Fsecret", headers=mine).status_code == 404


def test_sanitize_redacts_keys_and_local_paths():
    out = sanitize({"a": r"Bearer abcdefgh12345678 at C:\Users\me\x.json and /home/u/y", "k": KEY}, KEY)
    flat = json.dumps(out)
    assert KEY not in flat and "Bearer abcdefgh" not in flat and "Users" not in flat and "/home/u" not in flat


# --- single-outbound-request guarantee for the script-generation path ---------

def _counting_provider(status=None, exc=None, **kw):
    calls = []
    def responder(request):
        calls.append(request)
        if exc is not None:
            raise exc
        return httpx.Response(status, text="boom " + KEY)
    p = OpenAiTextProvider(api_key=KEY, client_factory=lambda **k: httpx.Client(
        transport=httpx.MockTransport(responder), **k), **kw)
    return p, calls


def _script_provider(monkeypatch, tmp_path, env=None):
    from defence_studio.providers import openai_text
    monkeypatch.delenv("DEFENCE_STUDIO_SCRIPT_PROVIDER", raising=False)
    monkeypatch.setenv("DEFENCE_STUDIO_OPENAI_API_KEY", KEY)
    if env is None:
        monkeypatch.delenv("DEFENCE_STUDIO_SCRIPT_MAX_RETRIES", raising=False)
    else:
        monkeypatch.setenv("DEFENCE_STUDIO_SCRIPT_MAX_RETRIES", env)
    return ScriptService(tmp_path / "x.sqlite3", tmp_path)._resolve_provider(None)


def test_script_path_defaults_to_zero_retries_and_env_is_clamped(monkeypatch, tmp_path):
    assert _script_provider(monkeypatch, tmp_path).max_retries == 0
    assert _script_provider(monkeypatch, tmp_path, "2").max_retries == 2
    assert _script_provider(monkeypatch, tmp_path, "99").max_retries == 3
    assert _script_provider(monkeypatch, tmp_path, "-1").max_retries == 0
    assert _script_provider(monkeypatch, tmp_path, "junk").max_retries == 0


@pytest.mark.parametrize("status", [429, 500, 503])
def test_no_second_request_on_retryable_status(monkeypatch, tmp_path, status):
    real = _script_provider(monkeypatch, tmp_path)           # production-configured instance
    p, calls = _counting_provider(status=status, max_retries=real.max_retries)
    from defence_studio.providers.openai_text import ProviderError
    with pytest.raises(ProviderError) as ei:
        p.generate(model="gpt-4o-mini", system_prompt="s", user_prompt="u")
    assert len(calls) == 1 and ei.value.retryable is True
    assert KEY not in str(ei.value) and KEY not in json.dumps(p.last_exchange)


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("t"), httpx.ConnectError("c")])
def test_no_second_request_on_timeout_or_connection_failure(monkeypatch, tmp_path, exc):
    real = _script_provider(monkeypatch, tmp_path)
    p, calls = _counting_provider(exc=exc, max_retries=real.max_retries)
    from defence_studio.providers.openai_text import ProviderError
    with pytest.raises(ProviderError):
        p.generate(model="gpt-4o-mini", system_prompt="s", user_prompt="u")
    assert len(calls) == 1
    assert p.last_exchange["attempts"] and KEY not in json.dumps(p.last_exchange)


def test_success_is_exactly_one_request(approved):
    s = approved
    p_calls = []
    def responder(request):
        p_calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(_default_body(s["claim_id"]))},
                                                       "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    prov = OpenAiTextProvider(api_key=KEY, max_retries=0, client_factory=lambda **k: httpx.Client(
        transport=httpx.MockTransport(responder), **k))
    ScriptService(s["db"], s["root"], provider=prov).generate(s["job"], OWNER, GOOD_CONFIG, "one-req")
    assert len(p_calls) == 1
