"""Performance Profile Builder.

A profile describes HOW narration should be performed (tempo, pause style,
energy, emphasis, pitch style) -- never WHOSE voice it is (spec section 10
and 12). Profiles are derived from one or more Analysis results by bucketing
real measured statistics into qualitative labels.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProfileResult:
    profile_name: str
    tempo: str
    pause_style: str
    energy: str
    emphasis: str
    pitch_style: str
    transition_pause: str
    derived_from_metrics: dict


def _bucket(value: float, low: float, high: float, labels: tuple[str, str, str]) -> str:
    if value < low:
        return labels[0]
    if value > high:
        return labels[2]
    return labels[1]


def build_profile(profile_name: str, analyses: list[dict]) -> ProfileResult:
    """analyses: list of analysis dicts (as produced by AnalysisResult
    serialization) to aggregate into one profile."""
    if not analyses:
        raise ValueError("At least one analysis is required to build a profile")

    def avg(key: str) -> float:
        vals = [a[key] for a in analyses if a.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    speaking_rate = avg("speaking_rate_wpm")
    mean_pause = avg("mean_pause_duration")
    rms_mean = avg("rms_mean")
    energy_variation = avg("energy_variation_coeff")
    pitch_range = avg("pitch_range_hz")
    emphasis_count = avg("emphasis_candidate_count")

    tempo = _bucket(speaking_rate, 110, 160, ("slow", "moderate", "fast"))
    pause_style = _bucket(mean_pause, 0.3, 0.8, ("tight", "medium", "long"))
    energy = _bucket(rms_mean, 0.03, 0.12, ("low", "controlled-high", "high"))
    emphasis = _bucket(emphasis_count, 2, 8, ("minimal", "selective", "frequent"))
    pitch_style = _bucket(pitch_range or 0.0, 40, 120, ("flat", "moderately dynamic", "highly dynamic"))
    transition_pause = _bucket(mean_pause, 0.25, 0.6, ("short", "medium", "long"))

    return ProfileResult(
        profile_name=profile_name,
        tempo=tempo,
        pause_style=pause_style,
        energy=energy,
        emphasis=emphasis,
        pitch_style=pitch_style,
        transition_pause=transition_pause,
        derived_from_metrics={
            "speaking_rate_wpm": speaking_rate,
            "mean_pause_duration": mean_pause,
            "rms_mean": rms_mean,
            "energy_variation_coeff": energy_variation,
            "pitch_range_hz": pitch_range,
            "emphasis_candidate_count": emphasis_count,
            "num_analyses_aggregated": len(analyses),
        },
    )
