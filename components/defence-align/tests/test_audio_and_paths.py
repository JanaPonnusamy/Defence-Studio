"""Audio inspection (real measured duration) and input-path safety."""
import pytest

from align_engine.audio import probe_audio, resolve_input_path
from align_engine.audio.inspect import AudioInfo
from align_engine.errors import (
    MissingAudioError, UnsafePathError, UnsupportedAudioFormatError,
)
from conftest import make_config, make_wav


def test_probe_returns_real_measured_duration(tmp_path):
    wav = make_wav(tmp_path / "a.wav", seconds=2.0, rate=8000)
    info = probe_audio(wav, max_bytes=10_000_000)
    assert isinstance(info, AudioInfo)
    assert info.duration_seconds == pytest.approx(2.0)
    assert info.sample_rate == 8000 and info.channels == 1 and info.codec == "pcm_wav"


def test_probe_missing_file(tmp_path):
    with pytest.raises(MissingAudioError):
        probe_audio(tmp_path / "nope.wav", max_bytes=10_000_000)


def test_probe_rejects_non_wav_format(tmp_path):
    fake = tmp_path / "a.mp3"
    fake.write_bytes(b"ID3\x00\x00\x00\x00")
    with pytest.raises(UnsupportedAudioFormatError, match="soundfile"):
        probe_audio(fake, max_bytes=10_000_000)


def test_probe_rejects_corrupt_wav(tmp_path):
    bad = tmp_path / "a.wav"
    bad.write_bytes(b"not really a wav")
    with pytest.raises(UnsupportedAudioFormatError):
        probe_audio(bad, max_bytes=10_000_000)


def test_probe_rejects_oversize(tmp_path):
    wav = make_wav(tmp_path / "a.wav", seconds=1.0)
    with pytest.raises(UnsupportedAudioFormatError):
        probe_audio(wav, max_bytes=10)


def test_resolve_valid_relative_path(tmp_path):
    config = make_config(tmp_path)
    (config.input_root / "audio").mkdir(parents=True)
    (config.input_root / "audio" / "x.wav").write_bytes(b"x")
    resolved = resolve_input_path(config.input_root, "audio/x.wav")
    assert resolved.name == "x.wav"


@pytest.mark.parametrize("bad", ["../secret.wav", "audio/../../secret.wav", "/etc/passwd",
                                 "audio\\x.wav", "./.."])
def test_resolve_rejects_traversal_and_absolute(tmp_path, bad):
    config = make_config(tmp_path)
    config.input_root.mkdir(parents=True, exist_ok=True)
    with pytest.raises(UnsafePathError):
        resolve_input_path(config.input_root, bad)
