import sqlite3, sys, os
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(__file__))
from helpers import speechlike, write_wav, tone_phrase, build, convert
from voice_analyzer import VoiceAnalyzerService, Store
from voice_analyzer.audio import InvalidAudioError, NoSpeechError


@pytest.fixture
def svc():
    return VoiceAnalyzerService(Store())


def src(svc, path, **kw):
    return svc.register_source(kind="LOCAL_FILE", location=str(path), authorization_ref="TEST-AUTH-1", **kw)


@pytest.fixture
def wav(tmp_path):
    return speechlike(str(tmp_path / "s.wav"))


def run(svc, wav, **kw):
    return svc.analyze(src(svc, wav), profile_name="t", language="ta-IN", **kw)


def test_valid_audio_profile_shape(svc, wav):
    p = run(svc, wav)
    for k in ("pitch", "speaking_rate", "tempo", "pause_profile", "energy_profile", "intonation_profile",
              "rhythm_profile", "emphasis_profile", "emotion_profile", "confidence", "source_segments",
              "analysis_hash", "voice_style_id", "analysis_version"):
        assert k in p
    assert p["voice_style_id"].startswith("VSP-")


def test_segmentation_boundaries(svc, wav):
    p = run(svc, wav)
    seg = svc.segments(p["voice_style_id"])
    assert len(seg) == 4
    assert abs(seg[0]["start_ms"] - 300) <= 60            # 300 ms lead silence
    assert all(a["end_ms"] <= b["start_ms"] for a, b in zip(seg, seg[1:]))


def test_pitch_extraction_accuracy(svc, wav):
    p = run(svc, wav)
    assert 180 <= p["pitch"]["median_hz"] <= 240
    assert p["pitch"]["unit"] == "Hz" and p["pitch"]["max_hz"] >= p["pitch"]["min_hz"]


def test_pause_extraction_ms_and_types(svc, wav):
    p = run(svc, wav)
    pa = p["pause_profile"]["pauses"]
    assert len(pa) == 3
    assert abs(pa[1]["duration_ms"] - 700) <= 70
    assert pa[1]["pause_type"] == "DRAMATIC"
    assert pa[0]["pause_type"] == "SHORT"
    assert abs(p["pause_profile"]["pause_before_speech_ms"] - 300) <= 60


def test_tempo_detects_fast_segment(svc, wav):
    p = run(svc, wav)
    seg = svc.segments(p["voice_style_id"])
    assert seg[2]["tempo"]["tempo"] > seg[0]["tempo"]["tempo"]
    assert seg[2]["tempo"]["tempo_class"] == "FAST"


def test_energy_extraction(svc, wav):
    e = run(svc, wav)["energy_profile"]
    assert e["peak_dbfs"] <= 0 and e["mean_dbfs"] < 0 and e["dynamic_range_db"] >= 0
    assert e["note"] == "loudness is not emotion"


def test_speaking_rate_measured_vs_estimated(svc, wav):
    a = run(svc, wav)["speaking_rate"]
    assert a["words_per_minute"] is None and a["words_per_minute_kind"] == "UNAVAILABLE"
    assert a["syllables_kind"] == "ESTIMATED"
    b = svc.analyze(src(svc, wav), profile_name="t", language="ta-IN", transcript="one two three four five six")
    assert b["speaking_rate"]["words_per_minute_kind"] == "MEASURED_FROM_TRANSCRIPT"
    assert b["speaking_rate"]["words_per_minute"] > 0


def test_emotion_is_inference_with_confidence(svc, wav):
    seg = svc.segments(run(svc, wav)["voice_style_id"])
    for s in seg:
        em = s["emotion"]
        assert em["kind"] == "INFERENCE" and 0 <= em["score"] <= 1 and 0 <= em["confidence"] <= 1
        assert em["analysis_version"] == "1.0"


def test_segment_contract_fields(svc, wav):
    s = svc.segments(run(svc, wav)["voice_style_id"])[0]
    for k in ("segment_id", "style_id", "start_ms", "end_ms", "pitch", "speaking_rate", "tempo", "energy",
              "pause_before_ms", "pause_after_ms", "intonation", "emphasis", "emotion", "confidence"):
        assert k in s
    assert 0 <= s["confidence"] <= 1


def test_deterministic_and_idempotent(svc, wav):
    a, b = run(svc, wav), run(svc, wav)   # separate source registrations, identical audio
    assert a["analysis_hash"] == b["analysis_hash"]
    sid = src(svc, wav)
    x = svc.analyze(sid, profile_name="t", language="ta-IN")
    y = svc.analyze(sid, profile_name="t", language="ta-IN")
    assert x["voice_style_id"] == y["voice_style_id"] and x["version"] == y["version"] == 1


def test_versioning_no_overwrite(svc, wav):
    sid = src(svc, wav)
    v1 = svc.analyze(sid, profile_name="t", language="ta-IN")
    v2 = svc.analyze(sid, profile_name="t", language="ta-IN", base_voice_style_id=v1["voice_style_id"],
                     transcript="a b c d", note="correction")
    assert v2["voice_style_id"] == v1["voice_style_id"] and v2["version"] == 2
    old = svc.profile(v1["voice_style_id"], 1)
    assert old["analysis_hash"] == v1["analysis_hash"] and old["speaking_rate"]["words_per_minute"] is None
    with pytest.raises(sqlite3.DatabaseError):
        svc.store.db.execute("UPDATE voice_profile_versions SET profile_json='{}'")
    with pytest.raises(sqlite3.DatabaseError):
        svc.store.db.execute("DELETE FROM voice_style_segments")


def test_invalid_audio(svc, tmp_path):
    bad = tmp_path / "bad.wav"; bad.write_bytes(b"not audio at all")
    with pytest.raises(InvalidAudioError):
        svc.analyze(src(svc, bad), profile_name="t", language="ta-IN")
    empty = tmp_path / "e.wav"; empty.write_bytes(b"")
    with pytest.raises(InvalidAudioError):
        svc.analyze(src(svc, empty), profile_name="t", language="ta-IN")
    with pytest.raises(InvalidAudioError):
        svc.analyze(src(svc, tmp_path / "missing.wav"), profile_name="t", language="ta-IN")
    txt = tmp_path / "a.txt"; txt.write_text("x")
    with pytest.raises(InvalidAudioError):
        svc.analyze(src(svc, txt), profile_name="t", language="ta-IN")


def test_silent_audio(svc, tmp_path):
    p = write_wav(str(tmp_path / "sil.wav"), np.zeros(16000 * 3, np.float32))
    with pytest.raises(NoSpeechError):
        svc.analyze(src(svc, p), profile_name="t", language="ta-IN")


def test_short_audio_warns(svc, tmp_path):
    p = write_wav(str(tmp_path / "short.wav"), build([tone_phrase(4)], [], 100, 100))
    prof = svc.analyze(src(svc, p), profile_name="t", language="ta-IN")
    assert "SHORT_AUDIO" in prof["confidence"]["warnings"]


def test_noisy_audio_still_segments_with_lower_confidence(svc, tmp_path):
    clean = run(svc, speechlike(str(tmp_path / "c.wav")))
    noisy = run(svc, speechlike(str(tmp_path / "n.wav"), noise=0.02))
    assert len(svc.segments(noisy["voice_style_id"])) >= 3
    assert noisy["confidence"]["snr_proxy_db"] <= clean["confidence"]["snr_proxy_db"]


def test_long_audio(svc, tmp_path):
    ph = [tone_phrase(10, 200 + 5 * (i % 4)) for i in range(40)]
    p = write_wav(str(tmp_path / "long.wav"), build(ph, [400] * 40))
    prof = svc.analyze(src(svc, p), profile_name="t", language="ta-IN")
    assert prof["audio"]["duration_ms"] > 60000 and prof["audio"]["segment_count"] == 40


def test_unvoiced_noise_has_no_invented_pitch(svc, tmp_path):
    rng = np.random.default_rng(1)
    n = rng.normal(0, 0.2, 16000)
    x = np.concatenate([np.zeros(8000), n, np.zeros(8000), n, np.zeros(8000)]).astype(np.float32)
    prof = svc.analyze(src(svc, write_wav(str(tmp_path / "wn.wav"), x)), profile_name="t", language="ta-IN")
    assert prof["pitch"].get("value", 1) is None or prof["pitch"]["voiced_ratio"] < 0.5


@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".webm"])
def test_other_formats(svc, wav, tmp_path, ext):
    f = convert(wav, str(tmp_path / ("x" + ext)))
    p = run(svc, f)
    assert p["audio"]["segment_count"] == 4


def test_mp4_video_container(svc, wav, tmp_path):
    import subprocess
    out = str(tmp_path / "v.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=12", "-i", wav,
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", out], check=True)
    assert run(svc, out)["audio"]["segment_count"] == 4


def test_source_requires_authorization(svc):
    with pytest.raises(ValueError):
        svc.register_source(kind="LOCAL_FILE", location="x.wav", authorization_ref=" ")
    with pytest.raises(ValueError):
        svc.register_source(kind="LOCAL_FILE", location="x.wav", authorization_ref="r", provenance_state="UNKNOWN")


def test_url_source_needs_acquired_path(svc, wav):
    sid = svc.register_source(kind="URL", location="https://example.invalid/a", authorization_ref="r")
    with pytest.raises(InvalidAudioError):
        svc.analyze(sid, profile_name="t", language="ta-IN")
    assert svc.analyze(sid, profile_name="t", language="ta-IN", audio_path=wav)["voice_style_id"]


def test_no_raw_audio_persisted(svc, wav):
    run(svc, wav)
    dump = "\n".join(svc.store.db.iterdump())
    assert "RIFF" not in dump and len(dump) < 2_000_000


def test_api_roundtrip(wav):
    from fastapi.testclient import TestClient
    from voice_analyzer.api import create_app
    c = TestClient(create_app(VoiceAnalyzerService(Store())))
    s = c.post("/voice-analyzer/sources", json={"kind": "LOCAL_FILE", "location": wav, "authorization_ref": "A"}).json()
    r = c.post("/voice-analyzer/analyze", json={"source_id": s["source_id"], "profile_name": "p", "language": "ta-IN"})
    assert r.status_code == 200
    vid = r.json()["voice_style_id"]
    assert c.get(f"/voice-analyzer/profiles/{vid}").status_code == 200
    assert len(c.get(f"/voice-analyzer/profiles/{vid}/segments").json()["segments"]) == 4
    assert c.get("/voice-analyzer/profiles/nope").status_code == 404
    bad = c.post("/voice-analyzer/analyze", json={"source_id": s["source_id"], "profile_name": "p",
                                                  "language": "ta-IN", "audio_path": wav + ".missing"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "INVALID_AUDIO"
