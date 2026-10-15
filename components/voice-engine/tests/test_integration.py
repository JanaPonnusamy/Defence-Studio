import json, os, sys
import pytest
sys.path.insert(0, os.path.dirname(__file__))
from helpers import speechlike
from voice_analyzer import VoiceAnalyzerService, Store
from voice_generation import VoiceGenerationEngine, GenStore, SyntheticModel, SapiModel, validate_direction
from voice_generation.direction_from_profile import direction_from_profile

LIC = {"license_id": "LIC-TEST", "speaker_license_ref": "TEST", "training_allowed": False,
       "commercial_use_allowed": False, "youtube_use_allowed": False}
SCRIPT = "This is the first sentence. The new missile strike was confirmed. That is all for now."


def pipeline(tmp_path, model, language, voice="DEFENCE_ERA_FEMALE_001", persist=None):
    ref = speechlike(str(tmp_path / "ref.wav"))
    an = VoiceAnalyzerService(Store())
    sid = an.register_source(kind="LOCAL_FILE", location=ref, authorization_ref="TEST-AUTH")
    profile = an.analyze(sid, profile_name="ref", language=language)
    eng = VoiceGenerationEngine(GenStore(), str(persist or tmp_path / "gen"))
    eng.register_voice(voice, model, display_name="narrator", license=LIC)
    direction = direction_from_profile(profile, voice_id=voice, language=language, reference_rate=4.0)
    direction["emphasis"] = [{"text": "new missile strike", "strength": 0.88}]
    gen = eng.generate(SCRIPT, direction)
    return profile, direction, gen, eng


def test_end_to_end_synthetic(tmp_path):
    profile, direction, gen, eng = pipeline(tmp_path, SyntheticModel(), "ta-IN")
    validate_direction(direction)
    assert direction["style_profile_id"] == profile["voice_style_id"]
    assert gen["status"] == "COMPLETED" and gen["style_profile_id"] == profile["voice_style_id"]
    al = eng.alignment(gen["generation_id"])
    assert len(al["segments"]) == 3 and al["segments"][-1]["end_ms"] <= gen["duration_ms"]
    # generated audio can itself be re-analysed (closes the loop; measured, not assumed)
    an = VoiceAnalyzerService(Store())
    s = an.register_source(kind="LOCAL_FILE", location=gen["audio_reference"], authorization_ref="OWN-OUTPUT",
                           provenance_state="OWNED")
    again = an.analyze(s, profile_name="gen", language="ta-IN")
    assert again["audio"]["segment_count"] == 3


@pytest.mark.skipif(not SapiModel().health_check()["ok"], reason="Windows SAPI voice not available")
def test_end_to_end_real_os_voice_english(tmp_path):
    m = SapiModel("Microsoft Zira Desktop")
    profile, direction, gen, eng = pipeline(tmp_path, m, "en-US")
    assert gen["status"] == "COMPLETED" and gen["duration_ms"] > 2000
    al = eng.alignment(gen["generation_id"])
    assert len(al["segments"]) == 3
    assert all(a["end_ms"] <= b["start_ms"] for a, b in zip(al["segments"], al["segments"][1:]))


def test_sapi_rejects_tamil():
    m = SapiModel()
    if not m.health_check()["ok"]:
        pytest.skip("SAPI unavailable")
    with pytest.raises(Exception):
        m.validate("ta-IN")
