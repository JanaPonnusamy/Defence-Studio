from __future__ import annotations

from pathlib import Path

from app.pipeline.normalize import normalize_audio


def test_normalize_produces_canonical_wav(fixture_wav_path, tmp_path):
    dest = tmp_path / "normalized.wav"
    result = normalize_audio(fixture_wav_path, dest)
    assert dest.exists()
    assert result.sample_rate == 22050
    assert result.channels == 1
    assert result.bit_depth == 16
    assert result.duration > 0
    assert result.checksum_sha256 and len(result.checksum_sha256) == 64
    assert result.peak_dbfs <= 0.5  # peak-normalized, should not exceed ~0dBFS
