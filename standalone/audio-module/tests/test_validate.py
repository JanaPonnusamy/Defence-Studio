from __future__ import annotations

import numpy as np
import soundfile as sf

from app.pipeline.validate import validate_generated_audio


def test_validate_missing_file(tmp_path):
    result = validate_generated_audio(tmp_path / "nope.wav")
    assert result["status"] == "FAILED"
    assert result["reason"] == "file_missing"


def test_validate_silent_audio_fails(tmp_path):
    path = tmp_path / "silent.wav"
    sf.write(str(path), np.zeros(22050), 22050)
    result = validate_generated_audio(path)
    assert result["status"] == "FAILED"
    assert result["reason"] == "mostly_silent"


def test_validate_normal_audio_passes(tmp_path):
    path = tmp_path / "ok.wav"
    t = np.linspace(0, 1, 22050)
    data = 0.3 * np.sin(2 * np.pi * 220 * t)
    sf.write(str(path), data, 22050)
    result = validate_generated_audio(path)
    assert result["status"] == "PASSED"
    assert result["details"]["duration"] > 0.9


def test_validate_clipping_detected(tmp_path):
    path = tmp_path / "clip.wav"
    data = np.ones(22050) * 0.9999
    sf.write(str(path), data, 22050)
    result = validate_generated_audio(path)
    assert result["status"] == "WARNING"
    assert result["details"]["clipping_detected"] is True
