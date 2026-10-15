"""Generated-audio validation (spec section 15)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def validate_generated_audio(path: Path, expected_language: str | None = None, provider_metadata: dict | None = None) -> dict:
    details: dict = {}
    if not path.exists():
        return {"status": "FAILED", "reason": "file_missing", "details": details}

    try:
        data, sr = sf.read(str(path))
    except Exception as exc:  # noqa: BLE001
        return {"status": "FAILED", "reason": f"corrupted_output: {exc}", "details": details}

    if data.size == 0:
        return {"status": "FAILED", "reason": "empty_audio", "details": details}
    if not np.isfinite(data).all():
        return {"status": "FAILED", "reason": "nonfinite_audio", "details": details}

    mono = data if data.ndim == 1 else np.mean(data, axis=1)
    duration = float(len(mono)) / sr
    channels = 1 if data.ndim == 1 else data.shape[1]
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0

    clipping = peak >= 0.999
    silence_ratio = float(np.mean(np.abs(mono) < 1e-4))
    is_mostly_silent = silence_ratio > 0.98

    details.update({
        "duration": duration,
        "sample_rate": sr,
        "channels": channels,
        "peak": peak,
        "rms": rms,
        "clipping_detected": clipping,
        "silence_ratio": silence_ratio,
        "provider_metadata": provider_metadata or {},
    })

    if is_mostly_silent:
        return {"status": "FAILED", "reason": "mostly_silent", "details": details}
    if duration < 0.05:
        return {"status": "FAILED", "reason": "duration_too_short", "details": details}

    status = "WARNING" if clipping else "PASSED"
    return {"status": status, "reason": "clipping_detected" if clipping else "ok", "details": details}
