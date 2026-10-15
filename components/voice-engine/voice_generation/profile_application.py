"""Apply a measured Voice Style Profile to a Voice Direction — honestly.

Only delivery characteristics that (a) were MEASURED in the profile and (b) map to a control
that is really honoured at render time are written into the direction. Everything else is
reported in ``not_applied`` with the reason. The result is deterministic and inspectable;
no speaker identity, embedding or audio is used or copied (only numbers from the profile).

Contract-only: takes plain dicts, imports nothing from the application or the analyzer.
"""
from __future__ import annotations

APPLICATION_VERSION = "1.0"

# Direction keys a caller may set explicitly. An explicit value always wins over the profile.
PAUSE_MIN_MS, PAUSE_MAX_APPLIED_MS = 100, 1500
BEFORE_SPEECH_MAX_MS = 1000


class ProfileApplicationError(ValueError):
    """The profile cannot be applied to this direction (clear, user-facing message)."""


def _primary(lang: str) -> str:
    return (lang or "").split("-")[0].lower()


def _explicit_pause(raw: dict, position: str) -> bool:
    return any(isinstance(p, dict) and p.get("position") == position for p in (raw.get("pauses") or []))


def apply_profile(profile: dict, raw_direction: dict, direction: dict, *, supported_controls) -> tuple[dict, dict]:
    """Return ``(effective_direction, report)``.

    ``raw_direction``  – what the caller sent (used only to know which fields were explicit).
    ``direction``      – the validated/defaulted direction.
    ``supported_controls`` – controls the selected voice really applies (e.g. {"tempo","energy"}).
    Pauses are inserted by the engine itself (silence), so they are honoured for every voice.
    """
    if not isinstance(profile, dict) or "voice_style_id" not in profile:
        raise ProfileApplicationError("style profile is missing or malformed")
    plang, dlang = _primary(profile.get("language", "")), _primary(direction.get("language", ""))
    if plang and dlang and plang != dlang:
        raise ProfileApplicationError(
            f"style profile {profile['voice_style_id']} was measured on {profile.get('language')} audio "
            f"and cannot be applied to {direction.get('language')} narration")
    pp = profile.get("pause_profile") or {}
    out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in direction.items()}
    applied, not_applied, overridden = [], [], []
    supported = set(supported_controls or ())

    # -- pauses (engine-level silence; measured durations, classification is heuristic) ----------
    sent = [p["duration_ms"] for p in (pp.get("pauses") or [])
            if p.get("context") == "after_sentence_candidate" and isinstance(p.get("duration_ms"), (int, float))]
    pauses = {p["position"]: p for p in out.get("pauses", [])}
    if _explicit_pause(raw_direction, "after_sentence"):
        overridden.append("pauses.after_sentence")
    elif sent:
        ms = int(round(min(PAUSE_MAX_APPLIED_MS, max(PAUSE_MIN_MS, sum(sent) / len(sent)))))
        pauses["after_sentence"] = {"position": "after_sentence", "duration_ms": ms}
        applied.append({"control": "pauses.after_sentence", "value_ms": ms, "basis": "MEASURED",
                        "source": f"mean of {len(sent)} sentence-boundary pauses, clamped "
                                  f"[{PAUSE_MIN_MS},{PAUSE_MAX_APPLIED_MS}] ms"})
    else:
        not_applied.append({"control": "pauses.after_sentence", "reason": "profile has no sentence-boundary pauses"})
    bs = pp.get("pause_before_speech_ms")
    if _explicit_pause(raw_direction, "before_speech"):
        overridden.append("pauses.before_speech")
    elif isinstance(bs, (int, float)) and bs > 0:
        ms = int(round(min(BEFORE_SPEECH_MAX_MS, bs)))
        pauses["before_speech"] = {"position": "before_speech", "duration_ms": ms}
        applied.append({"control": "pauses.before_speech", "value_ms": ms, "basis": "MEASURED",
                        "source": "leading silence before first speech"})
    out["pauses"] = list(pauses.values())

    # -- pitch variation: only when the voice truly honours it -----------------------------------
    std = (profile.get("pitch") or {}).get("variation_semitones_std")
    if "pitch_variation" in (raw_direction or {}):
        overridden.append("pitch_variation")
    elif "pitch_variation" in supported and isinstance(std, (int, float)):
        out["pitch_variation"] = round(min(1.0, max(0.0, std / 6.0)), 3)
        applied.append({"control": "pitch_variation", "value": out["pitch_variation"], "basis": "MEASURED",
                        "source": f"pitch std {std} semitones / 6"})
    else:
        not_applied.append({"control": "pitch_variation", "measured": std,
                            "reason": "selected voice does not apply pitch_variation (provider ignores it)"})

    # -- never derived: reasons are part of the contract ----------------------------------------
    sr = profile.get("speaking_rate") or {}
    if "tempo" in (raw_direction or {}):
        overridden.append("tempo")
    else:
        not_applied.append({"control": "tempo", "measured": sr.get("normal_rate_syllables_per_sec"),
                            "reason": "reference rate is an ESTIMATED syllable rate and the selected voice's own "
                                      "baseline rate is not measured, so no honest ratio exists; set tempo explicitly"})
    if "energy" in (raw_direction or {}):
        overridden.append("energy")
    else:
        not_applied.append({"control": "energy",
                            "reason": "profile loudness (dBFS) reflects recording level, not delivery"})
    emo = (profile.get("emotion_profile") or {})
    not_applied.append({"control": "emotion", "label_share": emo.get("label_share"),
                        "reason": "heuristic INFERENCE, and the selected voice has no emotion control"})
    not_applied.append({"control": "emphasis",
                        "reason": "emphasis candidates carry no text (word alignment unavailable)"})

    report = {
        "application_version": APPLICATION_VERSION,
        "style_profile_id": profile["voice_style_id"],
        "profile_version": profile.get("version"),
        "profile_analysis_hash": profile.get("analysis_hash"),
        "applied": applied,
        "not_applied": not_applied,
        "explicit_overrides": sorted(set(overridden)),
        "effect": "APPLIED" if applied else "NONE",
        "note": ("Only the controls listed in `applied` changed synthesis; all other profile features are "
                 "recorded for transparency and had no effect."),
    }
    out["style_profile_id"] = profile["voice_style_id"]
    return out, report
