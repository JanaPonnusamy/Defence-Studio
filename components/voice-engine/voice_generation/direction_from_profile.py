"""Optional bridge: Voice Style Profile (plain dict) -> Voice Direction. Contract-only; no analyzer import.
Uses measured delivery numbers to steer an independently owned voice; never copies identity."""
from __future__ import annotations


def direction_from_profile(profile: dict, *, voice_id: str, language: str, reference_rate: float | None = None) -> dict:
    sr = profile["speaking_rate"]
    base = sr.get("normal_rate_syllables_per_sec") or 0
    tempo = 1.0 if not base or not reference_rate else base / reference_rate
    shares = profile.get("emotion_profile", {}).get("label_share", {})
    label = max(shares, key=shares.get) if shares else "NEUTRAL"
    pv = (profile["pitch"].get("variation_semitones_std") or 2.0) / 6.0
    en = profile["energy_profile"]
    emphasis_pauses = profile["pause_profile"].get("max_ms") or 0
    return {
        "voice_id": voice_id, "language": language, "style_profile_id": profile["voice_style_id"],
        "emotion": {"label": label, "intensity": round(min(1.0, max(0.0, shares.get(label, 0.5))), 3)},
        "tempo": round(min(2.0, max(0.5, tempo)), 3),
        "pitch_variation": round(min(1.0, max(0.0, pv)), 3),
        "energy": round(min(1.0, max(0.0, 1 + en["mean_dbfs"] / 40)), 3),
        "emphasis": [],
        "pauses": [{"position": "after_sentence", "duration_ms": int(min(profile["pause_profile"].get("mean_ms") or 300, 5000))},
                   {"position": "before_emphasis", "duration_ms": int(min(emphasis_pauses, 5000) // 2)}],
    }
