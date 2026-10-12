"""Generation tests (spec §33): provider adapter, cost recording, failure handling, retry."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.errors import VideoModuleError
from app.generation.base import AIImageProvider, ImageGenerationRequest, ImageGenerationResult, ProviderError
from app.generation.local_procedural import LocalProceduralProvider
from app.generation.registry import register_provider, unregister_provider
from app.generation.service import generate_image
from app.db.models import CostRecord, GenerationRecord
from fixtures.synthetic import SAMPLE_NARRATION
from tests.conftest import wait_job


class FlakyProvider(AIImageProvider):
    """Test-only provider: fails with PROVIDER_TIMEOUT `fail_times` times, then delegates to the real local renderer."""
    name = "test-flaky"
    model = "flaky-v1"
    is_mock = True

    def __init__(self, fail_times: int, code: str = "PROVIDER_TIMEOUT"):
        self.fail_times, self.code, self.calls = fail_times, code, 0

    def generate(self, request, out_dir):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError(self.code, f"simulated {self.code}")
        r = LocalProceduralProvider().generate(request, out_dir)
        r.provider, r.model, r.is_mock = self.name, self.model, True
        return r


class LyingProvider(AIImageProvider):
    """Claims success but never writes a file -> must be FAILED, never SUCCEEDED."""
    name = "test-lying"
    model = "liar-v1"
    is_mock = True

    def generate(self, request, out_dir):
        return ImageGenerationResult(provider=self.name, is_mock=True, model=self.model, request_id="x",
                                     file_path=Path(out_dir) / "does_not_exist.png", width=10, height=10,
                                     generation_time_seconds=0.0, estimated_cost=0.0)


@pytest.fixture()
def providers():
    made = []

    def add(p):
        register_provider(p)
        made.append(p.name)
        return p
    yield add
    for n in made:
        unregister_provider(n)


def test_placeholder_provider_is_labelled_placeholder_not_ai(client, project, test_root):
    """Phase 2 (replaces v1.0 'local procedural is real AI' test): the old local-procedural name still works
    as an alias but the output is classified PLACEHOLDER, never AI, never evidence, and has no cost."""
    job = client.post("/assets/generate", json={"project_id": project, "prompt": "Naval task force at dawn",
                                                "provider": "local-procedural", "purpose": "HOOK", "resolution": "640x360"}).json()
    assert job["status"] == "QUEUED"
    done = wait_job(client, job["job_id"])
    assert done["status"] == "SUCCEEDED", done
    a = client.get(f"/assets/{done['result_id']}").json()
    assert a["provider"] == "placeholder-procedural" and a["model"] == "procedural-composition-v1"
    assert a["generation_kind"] == "PLACEHOLDER" and a["provenance_class"] == "PLACEHOLDER"
    assert a["asset_type"] == "PLACEHOLDER_IMAGE" and a["source_type"] == "SYSTEM_GENERATED"
    assert a["editorial_category"] == "PLACEHOLDER" and a["is_real_ai"] is False and a["evidence_eligible"] is False
    assert a["is_mock"] is False and a["title"].startswith("[PLACEHOLDER]")
    assert a["generation_cost"] is None and a["cost_status"] == "NOT_APPLICABLE"
    with Image.open(test_root / a["local_path"]) as im:
        assert im.size == (640, 360)
        assert len(set(im.convert("RGB").getdata())) > 50  # real pixels were rendered
    costs = client.get(f"/costs/{project}").json()
    assert costs["ai_image_count"] == 0 and costs["placeholder_image_count"] == 1
    rec = costs["records"][0]
    assert rec["generation_kind"] == "PLACEHOLDER" and rec["is_real_ai"] is False
    assert rec["estimated_cost"] is None and rec["cost_status"] == "NOT_APPLICABLE"
    assert rec["request_id"] and rec["quality"] == "standard" and rec["resolution"] == "640x360" and rec["currency"] == "USD"


def test_mock_provider_is_visibly_labelled(client, project):
    done = wait_job(client, client.post("/assets/generate", json={"project_id": project, "prompt": "test", "provider": "mock-local"}).json()["job_id"])
    assert done["status"] == "SUCCEEDED"
    a = client.get(f"/assets/{done['result_id']}").json()
    assert a["is_mock"] is True and a["title"].startswith("[MOCK]") and a["extra_metadata"]["is_mock"] is True
    assert a["source_type"] == "AI_GENERATED"
    costs = client.get(f"/costs/{project}").json()
    assert costs["mock_generation_count"] == 1 and costs["ai_image_count"] == 0 and costs["ai_generation_cost"] == 0
    assert costs["by_provider"][0]["is_mock"] is True
    providers = client.get("/providers").json()["providers"]
    assert any(p["name"] == "mock-local" and p["is_mock"] and "MOCK" in p["label"] for p in providers)


def test_retry_on_transient_timeout_then_success(db, project, providers):
    p = providers(FlakyProvider(fail_times=2))
    asset, gen = generate_image(db, project_id=project, provider_name=p.name, request=ImageGenerationRequest(prompt="retry me", resolution="320x180"))
    assert p.calls == 3 and gen.attempts == 3 and gen.status == "SUCCEEDED"
    assert asset.source_type == "AI_GENERATED"


def test_retry_exhausted_fails_explicitly(db, project, providers):
    p = providers(FlakyProvider(fail_times=10))
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name=p.name, request=ImageGenerationRequest(prompt="x", resolution="320x180"))
    assert e.value.code == "PROVIDER_TIMEOUT" and p.calls == 3
    rec = db.query(GenerationRecord).filter(GenerationRecord.project_id == project).one()
    assert rec.status == "FAILED" and rec.error_code == "PROVIDER_TIMEOUT"
    assert db.query(CostRecord).filter(CostRecord.project_id == project).count() == 0


def test_quota_is_not_retried(db, project, providers):
    p = providers(FlakyProvider(fail_times=1, code="PROVIDER_QUOTA"))
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name=p.name, request=ImageGenerationRequest(prompt="x", resolution="320x180"))
    assert e.value.code == "PROVIDER_QUOTA" and p.calls == 1


def test_provider_claiming_success_without_output_fails(client, project, providers):
    providers(LyingProvider())
    done = wait_job(client, client.post("/assets/generate", json={"project_id": project, "prompt": "lie", "provider": "test-lying"}).json()["job_id"])
    assert done["status"] == "FAILED" and done["error_code"] == "GENERATION_FAILED"
    assert done["result_id"] is None


def test_budget_exceeded_blocks_generation(client, monkeypatch):
    """Billable provider with a FAKE key: the documented-price estimate exceeds the budget, so the request is
    never sent (the conftest network guard would fail the test if it were)."""
    from pydantic import SecretStr

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("sk-test-FAKE-not-a-real-key-000000"))
    pid = client.post("/projects", json={"name": "tiny budget", "ai_image_budget_usd": 0.001}).json()["project_id"]
    body = {"project_id": pid, "prompt": "x", "provider": "openai-images"}
    done = wait_job(client, client.post("/assets/generate", json=body).json()["job_id"])
    assert done["status"] == "FAILED" and done["error_code"] == "BUDGET_EXCEEDED"
    est = client.post("/assets/generate/estimate", json=body).json()
    # documented gpt-image-2 medium 1536x1024 per-image price (openai_pricing.py, retrieved 2026-10-12)
    assert est["within_budget"] is False and est["estimated_cost"] == 0.041 and est["estimate_status"] == "ESTIMATED"
    # placeholder: cost not applicable, never "free AI"
    est2 = client.post("/assets/generate/estimate", json={"project_id": pid, "prompt": "x"}).json()
    assert est2["estimated_cost"] is None and est2["estimate_status"] == "NOT_APPLICABLE" and est2["is_real_ai"] is False


@pytest.mark.parametrize("body,code", [
    ({"prompt": "   "}, "VALIDATION_ERROR"),
    ({"prompt": "use api_key=abcd1234 please"}, "VALIDATION_ERROR"),
    ({"prompt": "x", "provider": "openai-not-wired"}, "VALIDATION_ERROR"),
    ({"prompt": "x", "quality": "ultra"}, "VALIDATION_ERROR"),
    ({"prompt": "x", "resolution": "99999x1"}, "VALIDATION_ERROR"),
])
def test_generation_request_validation(client, project, body, code):
    r = client.post("/assets/generate", json={"project_id": project, **body})
    assert r.status_code == 422 and r.json()["error_code"] == code


def test_plan_generates_at_most_cap_and_never_for_evidence(client, project):
    client.post("/scenes", json={"project_id": project, "narration": SAMPLE_NARRATION + "\n\n" +
                                 "Strikes escalated along the coast. The missile launchers moved again. Attacks continued overnight.",
                                 "replace_existing": True})
    j = client.post("/visual-plans/build", json={"project_id": project, "options": {
        "generate_ai": True, "ai_provider": "mock-local", "max_ai_images": 2}}).json()
    done = wait_job(client, j["job_id"])
    assert done["status"] == "SUCCEEDED", done
    plan = client.get(f"/visual-plans/{done['result_id']}").json()
    assert plan["stats"]["ai_images_generated_in_plan"] <= 2
    shots = client.get(f"/shots/{done['result_id']}").json()["shots"]
    for s in shots:
        if s["source_type"] == "AI_GENERATED":
            assert s["intent"] not in ("EVIDENCE", "STATISTIC", "GEOGRAPHIC_CONTEXT", "LOCATION", "TIMELINE", "COMPARISON")
            assert s["editorial_label"] == "AI_GENERATED"
            assert any(o["type"] == "editorial_label" and "MOCK" in o["text"] for o in s["overlays"])
    assert any("MOCK" in w for w in plan["warnings"])
    costs = client.get(f"/costs/{project}").json()
    assert costs["mock_generation_count"] == plan["stats"]["ai_images_generated_in_plan"]


def test_core_pipeline_has_no_provider_specific_calls():
    root = Path(__file__).resolve().parent.parent / "app" / "pipeline"
    for f in root.glob("*.py"):
        text = f.read_text(encoding="utf-8").lower()
        for needle in ("openai", "local_procedural", "mock_local", "requests.post", "httpx"):
            assert needle not in text, f"{f.name} references {needle}"
