"""OPT-IN live provider test. SKIPPED unless BOTH are set:
    VVM_ENABLE_PAID_PROVIDER_TESTS=1   and   OPENAI_API_KEY (or VIDEO_MODULE_OPENAI_API_KEY)

WARNING: when enabled, test_live_generation makes ONE billable OpenAI image request
(quality=draft -> "low", 1024x1024; documented price at the time of writing USD 0.006 for
gpt-image-2, see docs/PROVIDER_SETUP.md). test_live_connectivity is non-generating (GET /models).
Never enabled by default; never run in this build (no key, no authorisation for paid calls).
"""
from __future__ import annotations

import os

import pytest
from pydantic import SecretStr

from tests import conftest

pytestmark = pytest.mark.skipif(
    not (conftest.PAID_TESTS_ENABLED and conftest._SAVED_OPENAI_KEY),
    reason="paid provider tests are opt-in: set VVM_ENABLE_PAID_PROVIDER_TESTS=1 and OPENAI_API_KEY",
)


@pytest.fixture()
def live(monkeypatch):
    from app.config import get_settings
    from app.generation.openai_images import OpenAIImagesProvider

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr(conftest._SAVED_OPENAI_KEY))
    monkeypatch.setattr(OpenAIImagesProvider, "_transport", None)  # real network for this module only
    yield


def test_live_connectivity(client, live):
    st = client.get("/providers/openai-images/status", params={"probe": True}).json()
    assert st["state"] == "AVAILABLE", st


@pytest.mark.skipif(os.environ.get("VVM_LIVE_GENERATE") != "1",
                    reason="the billable generation additionally requires VVM_LIVE_GENERATE=1")
def test_live_generation(client, project, live):
    from tests.conftest import wait_job

    done = wait_job(client, client.post("/assets/generate", json={
        "project_id": project, "prompt": "Abstract illustration of a calm harbour at dawn, no text",
        "provider": "openai-images", "quality": "draft", "resolution": "1024x1024",
        "allow_placeholder_fallback": False}).json()["job_id"], timeout=300)
    assert done["status"] == "SUCCEEDED", done
    a = client.get(f"/assets/{done['result_id']}").json()
    assert a["generation_kind"] == "AI_GENERATED" and a["is_real_ai"] is True and a["provider_request_id"]
    assert a["cost_status"] in ("KNOWN", "UNKNOWN")
