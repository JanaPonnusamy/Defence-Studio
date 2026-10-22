"""Provider request shape, structured-output handling and key configuration.

All HTTP here is `httpx.MockTransport`: this proves the request we SEND and how we
react to each response shape. It does not prove live OpenAI compatibility.
"""
import json

import httpx
import pytest

from defence_studio.providers import openai_text as ot
from defence_studio.providers.openai_text import OpenAiTextProvider, ProviderError
from defence_studio.services.script_prompt import draft_response_schema
SCRIPT_RESPONSE_JSON_SCHEMA = draft_response_schema()

KEY = "sk-test-NOT-A-REAL-KEY-0123456789"


def make(handler, **kw):
    seen = []

    def wrapped(request):
        seen.append(json.loads(request.content))
        return handler(request)

    provider = OpenAiTextProvider(api_key=kw.pop("api_key", KEY), max_retries=0,
                                  client_factory=lambda **k: httpx.Client(transport=httpx.MockTransport(wrapped), **k), **kw)
    return provider, seen


def ok(content="{}", **choice):
    return lambda _r: httpx.Response(200, json={
        "choices": [{"message": {"content": content}, **choice}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


def gen(provider, model="gpt-4o-mini", **kw):
    return provider.generate(model=model, system_prompt="s", user_prompt="u", **kw)


def test_schema_request_is_strict_json_schema_with_max_completion_tokens():
    provider, seen = make(ok())
    gen(provider, response_schema=SCRIPT_RESPONSE_JSON_SCHEMA, response_schema_name="script_revision_body")
    body = seen[0]
    assert body["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "script_revision_body", "strict": True, "schema": SCRIPT_RESPONSE_JSON_SCHEMA}}
    assert body["max_completion_tokens"] == 4096 and "max_tokens" not in body
    assert body["temperature"] == 0.2


def test_without_schema_uses_json_object_mode():
    provider, seen = make(ok())
    gen(provider)
    assert seen[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("model", ["o3-mini", "o1", "gpt-5", "gpt-5-mini"])
def test_reasoning_models_get_no_temperature_and_no_max_tokens(model):
    provider, seen = make(ok())
    gen(provider, model=model)
    assert "temperature" not in seen[0] and "max_tokens" not in seen[0]
    assert seen[0]["max_completion_tokens"] == 4096


def test_custom_base_url_keeps_legacy_token_parameter():
    provider, seen = make(ok(), base_url="http://compat.invalid/v1")
    gen(provider)
    assert seen[0]["max_tokens"] == 4096 and "max_completion_tokens" not in seen[0]


def test_refusal_is_an_explicit_error_not_empty_success():
    provider, _ = make(lambda _r: httpx.Response(200, json={
        "choices": [{"message": {"content": None, "refusal": "I can't help with that."}, "finish_reason": "stop"}]}))
    with pytest.raises(ProviderError) as exc:
        gen(provider)
    assert exc.value.code == "refusal" and not exc.value.retryable


def test_truncated_output_is_rejected_even_if_it_looks_like_json():
    provider, _ = make(ok('{"title": "cut off', finish_reason="length"))
    with pytest.raises(ProviderError) as exc:
        gen(provider)
    assert exc.value.code == "truncated_output"


def test_content_filter_and_malformed_shapes():
    provider, _ = make(ok("{}", finish_reason="content_filter"))
    with pytest.raises(ProviderError) as exc:
        gen(provider)
    assert exc.value.code == "content_filtered"
    provider, _ = make(lambda _r: httpx.Response(200, json={"choices": []}))
    with pytest.raises(ProviderError) as exc:
        gen(provider)
    assert exc.value.code == "malformed_response"
    provider, _ = make(ok("   "))
    with pytest.raises(ProviderError) as exc:
        gen(provider)
    assert exc.value.code == "malformed_response"


def test_script_schema_is_strict_mode_compatible():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])
    walk(SCRIPT_RESPONSE_JSON_SCHEMA)


# -- key configuration (never reveals values) ------------------------------------

def _clear(monkeypatch):
    for name in ot.API_KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_missing_key_is_detected_and_blocks_before_any_request(monkeypatch):
    _clear(monkeypatch)
    assert ot.api_key_env_source() is None and ot.configured_api_key() == ""
    provider, seen = make(ok(), api_key="")
    with pytest.raises(ProviderError) as exc:
        gen(provider)
    assert exc.value.code == "missing_api_key" and seen == []


def test_documented_and_standard_env_names_are_both_honoured(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert ot.api_key_env_source() == "OPENAI_API_KEY" and ot.configured_api_key() == KEY
    monkeypatch.setenv("DEFENCE_STUDIO_OPENAI_API_KEY", KEY + "-primary")
    assert ot.api_key_env_source() == "DEFENCE_STUDIO_OPENAI_API_KEY"   # project name wins
    assert OpenAiTextProvider().api_key == KEY + "-primary"


def test_readiness_credential_status_and_provider_agree(monkeypatch, tmp_path):
    from defence_studio.database import initialize_database
    from defence_studio.services import credentials
    from defence_studio.verification.providers import capability_registry
    db = tmp_path / "c.sqlite3"
    initialize_database(db)
    _clear(monkeypatch)
    assert credentials.status(db, "owner")[0]["configured"] is False
    row = next(r for r in capability_registry() if r.get("id", r.get("name")) == "openai-text-adapter"
               or "openai" in json.dumps(r).lower())
    assert "No OpenAI key configured" in json.dumps(row)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    st = credentials.status(db, "owner")[0]
    assert st["configured"] is True and st["source"] == "server_environment"
    assert KEY not in json.dumps(st) and KEY not in json.dumps(capability_registry())
    assert OpenAiTextProvider().api_key == KEY


def test_provider_error_text_never_contains_the_key():
    provider, _ = make(lambda _r: httpx.Response(401, text=f"bad key {KEY}"))
    with pytest.raises(ProviderError) as exc:
        gen(provider)
    assert KEY not in exc.value.message
