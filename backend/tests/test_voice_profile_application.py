"""A Voice Style Profile must change real synthesis inputs (or be reported as having no effect).

Deterministic test voice (synthetic tones, env-gated) — no OS voice, no network, no paid provider.
Real-audio comparison on a Windows OS voice lives in the 2026-10-15 audit notes, not in CI.
"""
import io
import sys
import wave
from pathlib import Path

import pytest

from conftest import SECRET  # noqa: F401  (fixture module import side effects)
from test_voice_integration import H, direction, env, speech_wav, upload  # noqa: F401
from defence_studio.services import voice_engine as ve

SCRIPT = "One two three four. Five six seven eight. Nine ten eleven twelve."


def _profile(env, key="a1", **form):
    assert upload(env, key=key, **form).status_code == 202
    assert env["worker"].run_once() is True
    return env["c"].get("/api/voice/profiles", headers=H()).json()["profiles"][0]["voice_style_id"]


def _generate(env, key, drop=(), **dir_kw):
    d = direction(**dir_kw)
    for k in drop:                       # fields the caller did not set explicitly
        d.pop(k)
    r = env["c"].post("/api/voice/generations", headers=H(key=key), json={"script": SCRIPT, "direction": d})
    assert r.status_code == 202, r.text
    assert env["worker"].run_once() is True
    job = env["c"].get(f"/api/voice/jobs/{r.json()['job_id']}", headers=H()).json()
    assert job["status"] == "COMPLETED", job
    return env["c"].get(f"/api/voice/generations/{job['result_ref']}", headers=H()).json(), job


def test_no_profile_preserves_baseline(env):
    g, job = _generate(env, "g0")
    assert g["style_profile_id"] is None and g["profile_application"] is None
    assert g["effective_direction"]["pauses"] == []          # engine default sentence pause only
    assert job["result"].get("profile_application") is None


def test_profile_changes_effective_direction_and_audio(env):
    vid = _profile(env)
    base, _ = _generate(env, "g0")
    prof, job = _generate(env, "g1", style_profile_id=vid)

    pa = prof["profile_application"]
    assert pa["effect"] == "APPLIED" and pa["style_profile_id"] == vid and pa["profile_version"] == 1
    applied = {a["control"]: a for a in pa["applied"]}
    assert "pauses.after_sentence" in applied and applied["pauses.after_sentence"]["basis"] == "MEASURED"
    ms = applied["pauses.after_sentence"]["value_ms"]
    assert 100 <= ms <= 1500

    eff = {p["position"]: p["duration_ms"] for p in prof["effective_direction"]["pauses"]}
    assert eff["after_sentence"] == ms and eff["after_sentence"] != 300          # engine default is 300 ms
    # the measurable consequence: longer inter-sentence silence => longer rendered audio
    assert prof["duration_ms"] > base["duration_ms"]
    assert job["result"]["profile_application"]["effect"] == "APPLIED"            # persisted with the result
    assert prof["generation_hash"] != base["generation_hash"]


def test_unsupported_characteristics_are_disclosed_not_applied(env):
    vid = _profile(env)
    g, _ = _generate(env, "g1", drop=("tempo", "energy", "pitch_variation"), style_profile_id=vid)
    na = {n["control"]: n for n in g["profile_application"]["not_applied"]}
    for control in ("tempo", "energy", "emotion", "emphasis"):
        assert control in na and na[control]["reason"]
    assert "INFERENCE" in na["emotion"]["reason"] or "heuristic" in na["emotion"]["reason"]
    # none of the not-applied controls leaked into the direction
    assert g["effective_direction"]["tempo"] == 1.0 and g["effective_direction"]["energy"] == 0.7  # defaults
    assert g["effective_direction"]["emotion"] == {"label": "SERIOUS", "intensity": 0.7}


def test_explicit_pause_overrides_profile(env):
    vid = _profile(env)
    g, _ = _generate(env, "g1", style_profile_id=vid,
                     pauses=[{"position": "after_sentence", "duration_ms": 123}])
    assert "pauses.after_sentence" in g["profile_application"]["explicit_overrides"]
    eff = {p["position"]: p["duration_ms"] for p in g["effective_direction"]["pauses"]}
    assert eff["after_sentence"] == 123


def test_missing_or_incompatible_profile_is_rejected(env):
    vid = _profile(env, language="ta-IN")
    c = env["c"]
    r = c.post("/api/voice/generations", headers=H(key="m"),
               json={"script": SCRIPT, "direction": direction(style_profile_id="VSP-doesnotexist")})
    assert r.status_code == 404
    r = c.post("/api/voice/generations", headers=H(key="l"),
               json={"script": SCRIPT, "direction": direction(language="en-US", style_profile_id=vid)})
    assert r.status_code == 422 and "cannot be applied" in r.json()["detail"]
    r = c.post("/api/voice/generations", headers=H("user-2", "o"),     # another owner's profile
               json={"script": SCRIPT, "direction": direction(style_profile_id=vid)})
    assert r.status_code == 404


def test_no_raw_reference_audio_is_retained(env):
    _profile(env)
    _generate(env, "g1", style_profile_id=env["c"].get("/api/voice/profiles", headers=H()).json()["profiles"][0]["voice_style_id"])
    assert not list(env["voice"].upload_dir.glob("*"))


def test_provider_that_ignores_controls_is_not_reported_as_applied():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "components" / "voice-engine"))
    from voice_generation.profile_application import ProfileApplicationError, apply_profile

    profile = {"voice_style_id": "VSP-x", "language": "en-US", "version": 1, "analysis_hash": "h",
               "pause_profile": {"pauses": [], "pause_before_speech_ms": 0},
               "pitch": {"variation_semitones_std": 4.2}, "speaking_rate": {}, "emotion_profile": {}}
    d = {"voice_id": "V", "language": "en-US", "style_profile_id": "VSP-x", "tempo": 1.0, "pitch_variation": 0.5,
         "energy": 0.7, "emotion": {"label": "NEUTRAL", "intensity": 0.5}, "emphasis": [], "pauses": []}
    eff, rep = apply_profile(profile, {"voice_id": "V", "language": "en-US"}, d, supported_controls={"tempo"})
    assert rep["effect"] == "NONE" and rep["applied"] == []
    assert eff["pitch_variation"] == 0.5 and eff["pauses"] == []
    assert any(n["control"] == "pitch_variation" and "ignores" in n["reason"] for n in rep["not_applied"])
    # a voice that DOES honour pitch variation gets the measured value
    eff2, rep2 = apply_profile(profile, {"voice_id": "V", "language": "en-US"}, d,
                               supported_controls={"pitch_variation"})
    assert rep2["effect"] == "APPLIED" and eff2["pitch_variation"] == 0.7 and \
        rep2["applied"][0]["control"] == "pitch_variation"
    with pytest.raises(ProfileApplicationError):
        apply_profile({}, {}, d, supported_controls=())
