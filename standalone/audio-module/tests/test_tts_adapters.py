from __future__ import annotations

from pathlib import Path

from app.tts.base import TTSRequest
from app.tts.mock_adapter import MockTTSAdapter


def test_mock_adapter_is_clearly_labeled():
    adapter = MockTTSAdapter()
    desc = adapter.describe()
    assert desc["provider"] == "mock-tts"
    assert desc["is_mock"] is True
    assert "MOCK" in desc["notes"]


def test_mock_adapter_synthesizes_valid_wav(tmp_path):
    adapter = MockTTSAdapter()
    dest = tmp_path / "out.wav"
    req = TTSRequest(text="Hello world this is mock speech", voice_id=None, language="en")
    result = adapter.synthesize(req, dest)
    assert dest.exists()
    assert result.provider == "mock-tts"
    assert result.provider_metadata["is_mock"] is True
    import soundfile as sf
    data, sr = sf.read(str(dest))
    assert len(data) > 0
    assert sr == 22050


def test_local_pyttsx3_describe_is_not_mock():
    from app.tts.local_pyttsx3 import LocalPyttsx3Adapter
    adapter = LocalPyttsx3Adapter()
    desc = adapter.describe()
    assert desc["is_mock"] is False
    assert desc["provider"] == "local-pyttsx3"
