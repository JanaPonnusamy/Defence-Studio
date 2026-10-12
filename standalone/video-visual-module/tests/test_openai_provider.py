"""Phase 2 / B: OpenAI Images adapter, unit-tested with MOCKED HTTP only (httpx.MockTransport).
No test in this file can reach the network: the transport is always a local mock, and the key is fake."""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from app.config import get_settings
from app.errors import VideoModuleError
from app.generation.base import ImageGenerationRequest
from app.generation.openai_images import OpenAIImagesProvider, preset_size_for
from app.generation.openai_pricing import cost_from_usage
from app.generation.service import generate_image
from tests.conftest import wait_job

FAKE_KEY = "sk-test-FAKE0123456789abcdefSECRETTAIL"


def _png_b64(w=1536, h=1024) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 160)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


USAGE = {"input_tokens": 50, "input_tokens_details": {"text_tokens": 50, "image_tokens": 0},
         "output_tokens": 1000, "output_tokens_details": {"image_tokens": 1000, "text_tokens": 0}, "total_tokens": 1050}


@pytest.fixture()
def openai_mock(monkeypatch):
    """Configure a FAKE key and route the adapter through a scripted MockTransport."""
    calls: list[httpx.Request] = []
    script: list = []

    def handler(request: httpx.Request):
        calls.append(request)
        step = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(step, Exception):
            raise step
        return step(request) if callable(step) else step

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr(FAKE_KEY))
    monkeypatch.setattr(OpenAIImagesProvider, "_transport", httpx.MockTransport(handler))

    def set_script(*steps):
        script.clear()
        script.extend(steps)
    return calls, set_script


def ok_response(usage=USAGE, w=1536, h=1024):
    body = {"created": 1760000000, "data": [{"b64_json": _png_b64(w, h), "revised_prompt": "rp"}],
            "size": f"{w}x{h}", "quality": "medium", "output_format": "png", "background": "opaque"}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body, headers={"x-request-id": "req_abc123"})


def err(status, code=None, message="error", typ=None):
    return httpx.Response(status, json={"error": {"code": code, "message": message, "type": typ}},
                          headers={"x-request-id": "req_err"})


def _req(**kw):
    return ImageGenerationRequest(prompt="Illustrative coastal patrol at dusk", **kw)


def test_success_records_provenance_and_known_cost(db, project, openai_mock):
    calls, script = openai_mock
    script(ok_response())
    asset, gen = generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert len(calls) == 1
    sent = json.loads(calls[0].content)
    assert calls[0].url.path.endswith("/images/generations") and calls[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert sent == {"model": "gpt-image-2", "prompt": "Illustrative coastal patrol at dusk", "n": 1,
                    "size": "1536x1024", "quality": "medium", "output_format": "png"}
    assert asset.generation_kind == "AI_GENERATED" and asset.source_type == "AI_GENERATED" and not asset.is_mock
    assert asset.asset_type == "AI_IMAGE" and asset.editorial_category == "AI_GENERATED"
    assert (asset.width, asset.height) == (1536, 1024) and asset.provider == "openai-images" and asset.model == "gpt-image-2"
    assert asset.provider_request_id == "req_abc123" and gen.provider_state == "SUCCEEDED"
    # cost = 50 text tokens * $5/M + 1000 image-output tokens * $30/M (documented gpt-image-2 rates)
    expected = round((50 * 5.0 + 1000 * 30.0) / 1e6, 6)
    assert asset.cost_status == "KNOWN" and asset.generation_cost == pytest.approx(expected)
    assert gen.pre_call_estimate_usd == 0.041
    meta = asset.extra_metadata
    assert meta["revised_prompt"] == "rp" and meta["provider_created"].startswith("2025-") and meta["usage"] == USAGE
    assert meta["c2pa_manifest_present"] is False  # detection only: our synthetic PNG has no manifest


def test_success_without_usage_is_unknown_cost(db, project, openai_mock):
    _, script = openai_mock
    script(ok_response(usage=None))
    asset, gen = generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert asset.cost_status == "UNKNOWN" and asset.generation_cost is None and gen.estimated_cost is None


@pytest.mark.parametrize("response,code,state,calls_expected", [
    (err(401, "invalid_api_key", f"Incorrect API key provided: {FAKE_KEY[:8]}****TAIL"), "PROVIDER_AUTH_FAILED", "FAILED", 1),
    (err(429, "insufficient_quota", "You exceeded your current quota"), "PROVIDER_QUOTA", "FAILED", 1),
    (err(400, "moderation_blocked", "blocked", "image_generation_user_error"), "PROVIDER_CONTENT_REJECTED", "FAILED", 1),
    # billable provider: no automatic retries by default (billable_generation_max_retries=0)
    (err(429, "rate_limit_exceeded", "Rate limit reached"), "PROVIDER_RATE_LIMITED", "UNAVAILABLE", 1),
    (err(503, None, "overloaded"), "PROVIDER_UNAVAILABLE", "UNAVAILABLE", 1),
    (httpx.ReadTimeout("timed out"), "PROVIDER_TIMEOUT", "UNCERTAIN", 1),
    (httpx.ConnectError("refused"), "PROVIDER_UNAVAILABLE", "UNAVAILABLE", 1),
])
def test_failures_map_to_explicit_codes_and_states(db, project, openai_mock, response, code, state, calls_expected):
    calls, script = openai_mock
    script(response)
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert e.value.code == code and len(calls) == calls_expected
    from app.db.models import GenerationRecord

    g = db.query(GenerationRecord).filter(GenerationRecord.project_id == project).one()
    assert g.status == "FAILED" and g.provider_state == state and g.error_code == code
    assert FAKE_KEY not in (g.error_message or "") and FAKE_KEY[:8] not in (g.error_message or "")
    assert FAKE_KEY not in e.value.message


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b"<html>not json</html>"),
    httpx.Response(200, json={"data": []}),
    httpx.Response(200, json={"data": [{"url": "https://example.invalid/x.png"}]}),
    httpx.Response(200, json={"data": [{"b64_json": "!!!not-base64!!!"}]}),
    httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]}),
])
def test_malformed_responses_fail_explicitly(db, project, openai_mock, response):
    _, script = openai_mock
    script(response)
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert e.value.code == "PROVIDER_RESPONSE_INVALID"
    from app.db.models import Asset

    assert db.query(Asset).filter(Asset.project_id == project).count() == 0  # nothing fabricated


def test_not_configured_makes_no_request_and_fails_clearly(client, project, monkeypatch):
    calls = []
    monkeypatch.setattr(OpenAIImagesProvider, "_transport", httpx.MockTransport(lambda r: calls.append(r)))
    assert get_settings().openai_api_key is None
    r = client.post("/assets/generate", json={"project_id": project, "prompt": "x", "provider": "openai-images"})
    assert r.status_code == 409 and r.json()["error_code"] == "PROVIDER_NOT_CONFIGURED"
    r = client.post("/visual-plans/build", json={"project_id": project, "options": {"generate_ai": True, "ai_provider": "openai-images"}})
    assert r.status_code in (409, 422)  # 422 if the project has no scenes yet; never accepted silently
    st = client.get("/providers/openai-images/status", params={"probe": True}).json()
    assert st["state"] == "NOT_CONFIGURED" and st["probe"]["probed"] is False
    assert calls == []


def test_explicit_fallback_is_labelled_and_counted_as_placeholder(client, project):
    body = {"project_id": project, "prompt": "Patrol boats at dawn", "provider": "openai-images",
            "resolution": "320x180", "allow_placeholder_fallback": True}
    done = wait_job(client, client.post("/assets/generate", json=body).json()["job_id"])
    assert done["status"] == "SUCCEEDED", done
    a = client.get(f"/assets/{done['result_id']}").json()
    assert a["generation_kind"] == "PLACEHOLDER" and a["is_fallback"] is True and a["fallback_from"] == "openai-images"
    assert a["title"].startswith("[FALLBACK PLACEHOLDER]") and "PROVIDER_NOT_CONFIGURED" in a["fallback_reason"]
    assert a["is_real_ai"] is False
    gens = client.get("/generations", params={"project_id": project}).json()
    states = sorted((g["provider"], g["provider_state"]) for g in gens)
    assert states == [("openai-images", "NOT_CONFIGURED"), ("placeholder-procedural", "SUCCEEDED")]
    c = client.get(f"/costs/{project}").json()
    assert c["ai_image_count"] == 0 and c["fallback_placeholder_count"] == 1
    assert c["failed_by_provider_state"] == {"NOT_CONFIGURED": 1}


def test_configured_fallback_setting(client, project, monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_placeholder_fallback", True)
    done = wait_job(client, client.post("/assets/generate", json={"project_id": project, "prompt": "x", "resolution": "320x180",
                                                                  "provider": "openai-images"}).json()["job_id"])
    assert done["status"] == "SUCCEEDED"
    assert client.get(f"/assets/{done['result_id']}").json()["is_fallback"] is True
    # explicit false overrides the operator setting
    r = client.post("/assets/generate", json={"project_id": project, "prompt": "x", "provider": "openai-images",
                                              "allow_placeholder_fallback": False})
    assert r.status_code == 409


def test_auth_failure_without_fallback_never_produces_placeholder(db, project, openai_mock):
    _, script = openai_mock
    script(err(401, "invalid_api_key", "bad key"))
    with pytest.raises(VideoModuleError):
        generate_image(db, project_id=project, provider_name="openai-images", request=_req(), allow_fallback=False)
    from app.db.models import Asset

    assert db.query(Asset).filter(Asset.project_id == project).count() == 0


def test_connectivity_probe_with_key_is_non_generating(client, openai_mock):
    calls, script = openai_mock
    script(httpx.Response(200, json={"id": "gpt-image-2", "object": "model"}))
    st = client.get("/providers/openai-images/status", params={"probe": True}).json()
    assert st["state"] == "AVAILABLE" and st["probe"]["probed"] is True
    assert [(c.method, c.url.path) for c in calls] == [("GET", "/v1/models/gpt-image-2")]
    script(err(401, "invalid_api_key", "no"))
    assert client.get("/providers/openai-images/status", params={"probe": True}).json()["state"] == "FAILED"
    script(httpx.ConnectError("down"))
    assert client.get("/providers/openai-images/status", params={"probe": True}).json()["state"] == "UNAVAILABLE"
    # without probe: configuration only, no request
    n = len(calls)
    assert client.get("/providers/openai-images/status").json()["state"] == "AVAILABLE" and len(calls) == n
    assert FAKE_KEY not in client.get("/providers").text and FAKE_KEY not in client.get("/diagnostics").text


def test_key_never_logged(db, project, openai_mock):
    from app.logging_conf import tail_log

    _, script = openai_mock
    script(err(401, "invalid_api_key", f"Incorrect API key provided: {FAKE_KEY}"))
    with pytest.raises(VideoModuleError):
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert not any(FAKE_KEY in line or FAKE_KEY[-10:] in line for line in tail_log(2000))


def test_pricing_rules_never_guess():
    assert cost_from_usage("gpt-image-2", None)[0] is None
    assert cost_from_usage("unknown-model", USAGE)[0] is None
    assert cost_from_usage("gpt-image-2", {"input_tokens": 10})[0] is None  # no output tokens -> unknown
    # gpt-image-1.5 has text output pricing, so an unsplit output count is ambiguous -> unknown
    assert cost_from_usage("gpt-image-1.5", {"input_tokens": 10, "output_tokens": 100})[0] is None
    assert cost_from_usage("gpt-image-2-2026-04-21", USAGE)[0] == pytest.approx((50 * 5 + 1000 * 30) / 1e6)
    assert preset_size_for("1280x720") == "1536x1024" and preset_size_for("720x1280") == "1024x1536"
    assert preset_size_for("1024x1024") == "1024x1024"
