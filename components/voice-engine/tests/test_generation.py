import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(__file__))
from voice_generation import (VoiceGenerationEngine, GenStore, SyntheticModel, GenerationError,
                              DirectionError, validate_direction, ModelError)
from voice_generation.models import VoiceModel
from voice_generation import wavutil

LIC = {"license_id": "LIC-TEST", "speaker_license_ref": "TEST-SYNTH", "training_allowed": False,
       "commercial_use_allowed": True, "youtube_use_allowed": True, "valid_from": "2020-01-01", "valid_until": None}
VID = "DEFENCE_ERA_FEMALE_001"
SCRIPT = "இது ஒரு சோதனை. புதிய ஏவுகணை தாக்குதல் நடந்தது. முடிந்தது."


@pytest.fixture
def eng(tmp_path):
    e = VoiceGenerationEngine(GenStore(), str(tmp_path / "audio"))
    e.register_voice(VID, SyntheticModel(), display_name="synthetic", license=LIC)
    return e


def D(**kw):
    d = {"voice_id": VID, "language": "ta-IN", "style_profile_id": "VSP-001",
         "emotion": {"label": "SERIOUS", "intensity": 0.72}, "tempo": 1.08, "pitch_variation": 0.64, "energy": 0.81,
         "emphasis": [{"text": "புதிய ஏவுகணை", "strength": 0.88}],
         "pauses": [{"position": "before_emphasis", "duration_ms": 420}, {"position": "after_emphasis", "duration_ms": 650}]}
    d.update(kw)
    return d


def test_valid_generation_contract(eng):
    g = eng.generate(SCRIPT, D())
    assert g["status"] == "COMPLETED" and g["audio_format"] == "wav" and g["sample_rate"] == 48000
    assert g["duration_ms"] > 1000 and os.path.getsize(g["audio_reference"]) > 1000
    assert g["generation_hash"] and g["style_profile_id"] == "VSP-001" and g["language"] == "ta-IN"
    assert len(g["segments"]) == 3
    x, sr = wavutil.read_wav(open(g["audio_reference"], "rb").read())
    assert sr == 48000 and abs(len(x) / sr * 1000 - g["duration_ms"]) < 5


@pytest.mark.parametrize("script", ["", "   ", None])
def test_empty_script(eng, script):
    with pytest.raises(GenerationError) as e:
        eng.generate(script, D())
    assert e.value.code == "EMPTY_SCRIPT"


def test_unsupported_language(eng):
    with pytest.raises(GenerationError) as e:
        eng.generate("hello.", D(language="xx-XX"))
    assert e.value.code == "UNSUPPORTED_LANGUAGE"


def test_invalid_voice(eng):
    with pytest.raises(GenerationError) as e:
        eng.generate("hello.", D(voice_id="NOPE"))
    assert e.value.code == "UNKNOWN_VOICE"


@pytest.mark.parametrize("patch", [
    {"tempo": 0.4}, {"tempo": 2.1}, {"tempo": "fast"}, {"pitch_variation": 1.2}, {"pitch_variation": -0.1},
    {"energy": 2}, {"emotion": {"label": "HAPPY", "intensity": 0.5}}, {"emotion": {"label": "CALM", "intensity": 3}},
    {"pauses": [{"position": "after_sentence", "duration_ms": 6000}]},
    {"pauses": [{"position": "after_sentence", "duration_ms": -1}]},
    {"pauses": [{"position": "nowhere", "duration_ms": 100}]},
    {"emphasis": [{"text": "x", "strength": 1.5}]}, {"emphasis": [{"text": " ", "strength": 0.5}]},
])
def test_invalid_direction_bounds(eng, patch):
    with pytest.raises(DirectionError):
        eng.generate("hello there.", D(**patch))


def test_bounds_accepted_at_edges():
    d = validate_direction(D(tempo=0.5, pitch_variation=0, energy=1,
                             pauses=[{"position": "after_sentence", "duration_ms": 5000}]))
    assert d["tempo"] == 0.5 and d["pauses"][0]["duration_ms"] == 5000


def test_tempo_changes_duration(eng):
    slow = eng.generate("one two three. four five six.", D(tempo=0.7, emphasis=[], pauses=[]))
    fast = eng.generate("one two three. four five six.", D(tempo=1.6, emphasis=[], pauses=[]))
    assert slow["duration_ms"] > fast["duration_ms"] * 1.5


def test_emphasis_and_pauses_applied(eng):
    plain = eng.generate(SCRIPT, D(emphasis=[], pauses=[]))
    emph = eng.generate(SCRIPT, D())
    assert emph["duration_ms"] >= plain["duration_ms"] + 420 + 650 - 100
    s = eng.store.segments(emph["generation_id"])
    import json
    assert json.loads(s[1]["controls_json"])["emphasis"] == pytest.approx(0.88)
    assert json.loads(s[0]["controls_json"])["emphasis"] == 0.0
    # pause before emphasised sentence is real silence in the audio
    gap = s[1]["start_ms"] - s[0]["end_ms"]
    assert gap >= 420 + 300 - 60


def test_idempotent_replay_and_hash(eng):
    a = eng.generate(SCRIPT, D())
    b = eng.generate(SCRIPT, D())
    assert a["generation_id"] == b["generation_id"] and a["generation_hash"] == b["generation_hash"]
    assert eng.store.db.execute("SELECT COUNT(*) FROM voice_generations").fetchone()[0] == 1
    c = eng.generate(SCRIPT, D(tempo=1.2))
    assert c["generation_id"] != a["generation_id"] and c["generation_hash"] != a["generation_hash"]


def test_deterministic_audio_bytes(tmp_path):
    out = []
    for i in range(2):
        e = VoiceGenerationEngine(GenStore(), str(tmp_path / f"a{i}"))
        e.register_voice(VID, SyntheticModel(), display_name="s", license=LIC)
        g = e.generate(SCRIPT, D())
        out.append(g["generation_hash"])
    assert out[0] == out[1]


def test_failure_recorded_and_retry(tmp_path):
    m = SyntheticModel(fail_on="boom")
    e = VoiceGenerationEngine(GenStore(), str(tmp_path / "a"))
    e.register_voice(VID, m, display_name="s", license=LIC)
    with pytest.raises(GenerationError) as ex:
        e.generate("this goes boom.", D(emphasis=[], pauses=[]))
    assert ex.value.code == "GENERATION_FAILED"
    row = e.store.db.execute("SELECT generation_id,status,error,attempt FROM voice_generations").fetchone()
    assert row["status"] == "FAILED" and "synthetic failure" in row["error"]
    m.fail_on = None                                  # transient fault cleared -> retry same request
    g = e.generate("this goes boom.", D(emphasis=[], pauses=[]))
    assert g["generation_id"] == row["generation_id"] and g["status"] == "COMPLETED" and g["attempt"] == 2
    assert e.store.db.execute("SELECT COUNT(*) FROM voice_generations").fetchone()[0] == 1


def test_license_window_and_commercial(tmp_path):
    e = VoiceGenerationEngine(GenStore(), str(tmp_path / "a"))
    e.register_voice(VID, SyntheticModel(), display_name="s",
                     license={**LIC, "valid_until": "2021-01-01", "commercial_use_allowed": False})
    with pytest.raises(GenerationError) as ex:
        e.generate("hi.", D(emphasis=[], pauses=[]))
    assert ex.value.code == "LICENSE_INVALID"
    e.register_voice(VID, SyntheticModel(), display_name="s", license={**LIC, "commercial_use_allowed": False})
    with pytest.raises(GenerationError):
        e.generate("hi.", D(emphasis=[], pauses=[]), require_commercial=True)
    assert e.generate("hi.", D(emphasis=[], pauses=[]))["status"] == "COMPLETED"


def test_license_fields_required(tmp_path):
    e = VoiceGenerationEngine(GenStore(), str(tmp_path / "a"))
    with pytest.raises(ValueError):
        e.register_voice(VID, SyntheticModel(), display_name="s", license={"license_id": "x"})


def test_alignment_measured_and_monotonic(eng):
    g = eng.generate(SCRIPT, D())
    a = eng.alignment(g["generation_id"])
    assert a["alignment_version"] == "1.0" and len(a["segments"]) == 3
    assert all(s["measured"] for s in a["segments"]) and not any(w["measured"] for w in a["words"])
    ends = [(s["start_ms"], s["end_ms"]) for s in a["segments"]]
    assert all(s < e for s, e in ends) and all(ends[i][1] <= ends[i + 1][0] for i in range(2))
    assert ends[-1][1] <= g["duration_ms"]
    # measured sentence timing agrees with the real waveform energy
    x, sr = wavutil.read_wav(open(g["audio_reference"], "rb").read())
    s0, e0 = ends[0]
    inside = np.abs(x[int(s0 / 1000 * sr):int(e0 / 1000 * sr)]).mean()
    gap = np.abs(x[int(e0 / 1000 * sr) + 50:int(ends[1][0] / 1000 * sr) - 50]).mean()
    assert inside > 20 * max(gap, 1e-6)


def test_capabilities_and_health(eng):
    m = SyntheticModel()
    assert m.health_check()["ok"] and "ta-IN" in m.capabilities()["languages"]
    assert m.capabilities()["natural_speech"] is False
    with pytest.raises(ModelError):
        m.validate("fr-FR")


def test_model_interface_is_abstract():
    with pytest.raises(TypeError):
        VoiceModel()


def test_api(eng):
    from fastapi.testclient import TestClient
    from voice_generation.api import create_app
    c = TestClient(create_app(eng))
    r = c.post("/voice-generation/generate", json={"script": SCRIPT, "direction": D()})
    assert r.status_code == 200
    gid = r.json()["generation_id"]
    assert c.get(f"/voice-generation/generations/{gid}").json()["status"] == "COMPLETED"
    assert len(c.get(f"/voice-generation/generations/{gid}/alignment").json()["segments"]) == 3
    assert c.post("/voice-generation/generate", json={"script": "x.", "direction": D(tempo=9)}).status_code == 422
    assert c.get("/voice-generation/generations/none").status_code == 404
