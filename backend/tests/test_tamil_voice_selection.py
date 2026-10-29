"""Tamil voice selection on the canonical TTS path (backend -> audio module edge-neural) and the
military-designation / lexicon resolution order with the real config files."""
import httpx
import pytest

from defence_studio.services import tts_text
from defence_studio.services.audio import AudioService, EVALUATION_VOICES, PROVIDER_TERMS
from defence_studio.services.integration import IntegrationValidationError
from test_audio_rm4 import FakeAudioModule, _client, _service
from test_scene_service_m1 import OWNER, SCENE_CONFIG, _plan_sha, approved_script  # noqa: F401

DESCRIBED = [
    {"provider": "edge-neural", "available": True, "network": True,
     "voices": [{"id": "ta-IN-PallaviNeural", "languages": ["ta-in"]}, {"id": "ta-LK-SaranyaNeural", "languages": ["ta-lk"]},
                {"id": "ta-IN-ValluvarNeural", "languages": ["ta-in"]}, {"id": "en-IN-NeerjaNeural", "languages": ["en-in"]}]},
    {"provider": "local-pyttsx3", "available": True, "voices": []},
]


class FakeWithProviders(FakeAudioModule):
    def handler(self, request):
        if request.url.path == "/tts/providers":
            return httpx.Response(200, json={"providers": DESCRIBED})
        return super().handler(request)


class Down:
    def providers(self):
        raise ConnectionError("audio module unreachable")


def test_provider_voices_come_from_the_module_and_filter_by_language(tmp_path):
    svc = AudioService(tmp_path / "db.sqlite3", tmp_path, audio_client=_client(FakeWithProviders()))
    got = svc.provider_voices("ta")
    assert got["error"] is None
    assert [v["voice_id"] for v in got["voices"]] == ["ta-IN-PallaviNeural", "ta-LK-SaranyaNeural", "ta-IN-ValluvarNeural"]
    assert {v["locale"] for v in got["voices"]} == {"ta-in", "ta-lk"}
    assert all(v["provider"] == "edge-neural" and v["network"] for v in got["voices"])


def test_unreachable_module_reports_error_and_no_invented_voices(tmp_path):
    got = AudioService(tmp_path / "db.sqlite3", tmp_path, audio_client=Down()).provider_voices("ta")
    assert got == {"voices": [], "error": "ConnectionError: audio module unreachable"}


def test_suggested_voice_and_terms_are_explicit():
    assert EVALUATION_VOICES[("edge-neural", "ta")] == "ta-IN-PallaviNeural"
    assert PROVIDER_TERMS["edge-neural"].startswith("EVALUATION_ONLY")


def _tamil_plan(s):
    plan = s["scene"].generate(s["job"], OWNER, SCENE_CONFIG, "scene-ta")
    s["scene"].approve(s["job"], OWNER, "ta", plan["version"], _plan_sha(s["scene"], s["job"], plan["version"]), "ok", "p-ta")


def test_undeclared_voice_is_refused_before_any_synthesis(approved_script, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES", "edge-neural:ta,local-pyttsx3:en")
    s = approved_script
    _tamil_plan(s)
    fake = FakeWithProviders()
    with pytest.raises(IntegrationValidationError, match="VOICE_UNSUPPORTED"):
        _service(s, fake).start_audio(s["job"], OWNER, "ta", "a-1", provider="edge-neural",
                                      voice_id="en-IN-NeerjaNeural", background=False)
    assert fake.synth_calls == 0


def test_audio_status_reports_voices_default_and_terms(approved_script, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES", "edge-neural:ta,local-pyttsx3:en")
    s = approved_script
    st = _service(s, FakeWithProviders()).audio_status(s["job"], OWNER, "ta")
    assert st["default_provider"] == "edge-neural" and st["suggested_voice"] == "ta-IN-PallaviNeural"
    assert "ta-LK-SaranyaNeural" in {v["voice_id"] for v in st["voices"]} and st["voices_error"] is None
    assert st["provider_terms"]["edge-neural"].startswith("EVALUATION_ONLY")


# ---- pronunciation resolution with the real config files (no injected tables) -----------------
def spoken(text):
    return tts_text.prepare(text, "ta")


def test_real_config_designations_win_over_the_lexicon_for_s400():
    """Current committed behaviour: the designation table is applied before the lexicon, so the
    ASR-checked lexicon form "எஸ்-400" is shadowed by the unchecked "S four hundred". Pinned so a
    change is deliberate; the choice itself is UNRESOLVED (needs a listening/ASR check)."""
    lexicon, _ = tts_text.load_lexicon()
    assert lexicon["S-400"] == "எஸ்-400"
    out = spoken("S-400 அமைப்பு.")
    assert out["spoken"] == "S four hundred அமைப்பு."
    assert [r["source"] for r in out["replacements"]] == ["designation"]


def test_real_config_su57_mig29_drdo_and_numbers():
    out = spoken("Su-57-ல் புதிய ரேடார். MiG-29 விமானம். DRDO சோதனை. 2026 அக்டோபர் 29 அன்று 400 km, 96%.")
    assert out["spoken"] == ("Su fifty seven விமானத்தில் புதிய ரேடார். MiG twenty nine விமானம். டிஆர்டிஓ சோதனை. "
                             "2026 அக்டோபர் 29 அன்று 400 கிலோமீட்டர், 96 சதவீதம்.")
    assert out["warnings"] == []


def test_unreviewed_suffix_on_another_designation_is_reported_separately():
    out = spoken("MiG-29-இல் மாற்றம்.")
    assert out["spoken"].startswith("MiG twenty nine இல்")
    assert any("needs a reviewed speech rewrite" in w and "MiG-29" in w for w in out["warnings"])
