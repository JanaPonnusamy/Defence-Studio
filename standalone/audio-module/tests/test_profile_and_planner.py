from __future__ import annotations

from app.pipeline.planner import build_performance_plan
from app.pipeline.profile import build_profile


def _sample_analysis(speaking_rate=140, pause=0.4, rms=0.08, pitch_range=70, emphasis=4):
    return {
        "speaking_rate_wpm": speaking_rate, "mean_pause_duration": pause,
        "rms_mean": rms, "energy_variation_coeff": 0.3, "pitch_range_hz": pitch_range,
        "emphasis_candidate_count": emphasis,
    }


def test_build_profile_buckets_metrics():
    result = build_profile("Test Profile", [_sample_analysis()])
    assert result.profile_name == "Test Profile"
    assert result.tempo == "moderate"
    assert result.pause_style == "medium"
    assert result.energy == "controlled-high"
    assert result.derived_from_metrics["num_analyses_aggregated"] == 1


def test_build_profile_requires_analyses():
    import pytest
    with pytest.raises(ValueError):
        build_profile("Empty", [])


def test_build_performance_plan_semantic_and_provider_neutral():
    profile = {
        "tempo": "moderate", "pause_style": "medium", "energy": "controlled-high",
        "emphasis": "selective", "pitch_style": "moderately dynamic", "transition_pause": "medium",
    }
    script = "This is a major escalation. The situation is critical."
    plan = build_performance_plan(script, profile)
    assert len(plan) == 2
    assert plan[0]["sentence_index"] == 1
    assert plan[0]["pace"] == "moderate"
    assert "provider" not in plan[0]  # must stay provider-neutral
    assert plan[0]["emphasis"] is not None
