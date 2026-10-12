"""Timeout safety: a billable generation request is never resent when the provider may already have
processed (and billed) it. All HTTP is mocked (httpx.MockTransport); no request can reach the network."""
from __future__ import annotations

import httpx
import pytest

from app.api.costs import project_costs
from app.config import get_settings
from app.db.models import Asset, CostRecord, GenerationRecord
from app.errors import VideoModuleError
from app.generation.service import budget_commitment, generate_image
from tests.test_openai_provider import _req, err, ok_response, openai_mock  # noqa: F401  (fixture re-export)

UNCERTAIN_ERRORS = [
    httpx.ReadTimeout("read timed out"),
    httpx.WriteTimeout("write timed out"),
    httpx.ReadError("connection reset"),
    httpx.RemoteProtocolError("server disconnected"),
]


def _posts(calls):
    return [c for c in calls if c.method == "POST" and c.url.path.endswith("/images/generations")]


@pytest.fixture()
def retries_enabled(monkeypatch):
    """Even with billable retries explicitly configured, an uncertain outcome must not be resent."""
    s = get_settings()
    monkeypatch.setattr(s, "billable_generation_max_retries", 3)
    monkeypatch.setattr(s, "generation_retry_backoff_s", 0.0)


def test_default_config_disables_billable_retries():
    assert get_settings().billable_generation_max_retries == 0


@pytest.mark.parametrize("exc", UNCERTAIN_ERRORS, ids=lambda e: type(e).__name__)
def test_uncertain_failure_sends_exactly_one_request(db, project, openai_mock, retries_enabled, exc):
    calls, script = openai_mock
    script(exc, ok_response())  # a resend would succeed -- it must never happen
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert len(_posts(calls)) == 1
    assert getattr(e.value, "outcome_uncertain", False) is True
    assert "UNCERTAIN" in e.value.message

    g = db.query(GenerationRecord).filter(GenerationRecord.project_id == project).one()
    assert g.status == "FAILED" and g.status != "SUCCEEDED"
    assert g.provider_state == "UNCERTAIN" and g.attempts == 1 and g.cost_status == "UNKNOWN"
    assert db.query(Asset).filter(Asset.project_id == project).count() == 0


def test_timeout_message_does_not_claim_request_never_reached_provider(db, project, openai_mock):
    _, script = openai_mock
    script(httpx.ReadTimeout("timed out"))
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    msg = e.value.message.lower()
    assert "may have processed and billed" in msg and "not sent" not in msg


@pytest.mark.parametrize("exc", UNCERTAIN_ERRORS[:2], ids=lambda e: type(e).__name__)
def test_uncertain_failure_never_falls_back_to_placeholder(db, project, openai_mock, retries_enabled, exc):
    calls, script = openai_mock
    script(exc)
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name="openai-images", request=_req(), allow_fallback=True)
    assert e.value.code in ("PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE")
    assert len(_posts(calls)) == 1
    gens = db.query(GenerationRecord).filter(GenerationRecord.project_id == project).all()
    assert len(gens) == 1 and gens[0].provider == "openai-images"  # no placeholder generation was attempted
    assert db.query(Asset).filter(Asset.project_id == project).count() == 0


def test_uncertain_cost_stays_unknown_and_is_counted(db, project, openai_mock):
    _, script = openai_mock
    script(httpx.ReadTimeout("timed out"))
    with pytest.raises(VideoModuleError):
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())

    rec = db.query(CostRecord).filter(CostRecord.project_id == project).one()
    assert rec.cost_status == "UNKNOWN" and rec.estimated_cost is None and rec.asset_id is None
    assert rec.generation_kind == "AI_GENERATED" and rec.pre_call_estimate_usd and rec.pre_call_estimate_usd > 0
    committed, unknown_n = budget_commitment(db, project)
    assert unknown_n == 1 and committed == pytest.approx(rec.pre_call_estimate_usd)  # not treated as free

    report = project_costs(project, db)
    assert report["uncertain_outcome_count"] == 1 and report["unknown_cost_count"] == 1
    assert report["totals_complete"] is False and report["ai_generation_cost_is_lower_bound"] is True
    assert report["ai_image_count"] == 0 and report["ai_generation_cost"] == 0.0


def test_success_after_uncertain_timeout_requires_a_new_explicit_request(db, project, openai_mock):
    calls, script = openai_mock
    script(httpx.ReadTimeout("timed out"), ok_response())
    with pytest.raises(VideoModuleError):
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert len(_posts(calls)) == 1
    asset, gen = generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert len(_posts(calls)) == 2 and gen.status == "SUCCEEDED" and asset.generation_kind == "AI_GENERATED"


@pytest.mark.parametrize("first", [httpx.ConnectError("refused"), httpx.ConnectTimeout("connect timed out"),
                                   err(429, "rate_limit_exceeded", "Rate limit reached")],
                         ids=["ConnectError", "ConnectTimeout", "429-rate-limit"])
def test_not_processed_errors_retry_only_when_explicitly_configured(db, project, openai_mock, retries_enabled, first):
    calls, script = openai_mock
    script(first, ok_response())
    asset, gen = generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert len(_posts(calls)) == 2 and gen.status == "SUCCEEDED" and gen.attempts == 2


@pytest.mark.parametrize("first", [httpx.ConnectError("refused"), err(429, "rate_limit_exceeded", "Rate limit")],
                         ids=["ConnectError", "429-rate-limit"])
def test_not_processed_errors_not_retried_by_default(db, project, openai_mock, first):
    calls, script = openai_mock
    script(first, ok_response())
    with pytest.raises(VideoModuleError):
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert len(_posts(calls)) == 1


def test_server_error_not_retried_even_when_configured(db, project, openai_mock, retries_enabled):
    calls, script = openai_mock
    script(err(503, None, "overloaded"), ok_response())
    with pytest.raises(VideoModuleError) as e:
        generate_image(db, project_id=project, provider_name="openai-images", request=_req())
    assert e.value.code == "PROVIDER_UNAVAILABLE" and len(_posts(calls)) == 1
