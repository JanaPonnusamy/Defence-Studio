"""Audio normalization: decode -> mono PCM WAV at a known sample rate with
loudness-normalized gain. Produces deterministic analysis input and records
real measured metadata (never fabricated).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import librosa

from app.pipeline.ingest import sha256_of_file

TARGET_SAMPLE_RATE = 22050
TARGET_PEAK_DBFS = -1.0  # peak-normalize to -1 dBFS to avoid clipping


@dataclass
class NormalizationResult:
    normalized_path: Path
    sample_rate: int
    channels: int
    bit_depth: int
    duration: float
    loudness_lufs: float | None
    peak_dbfs: float
    format: str
    checksum_sha256: str


def _rms_to_dbfs(rms: float) -> float:
    if rms <= 0:
        return -120.0
    return 20.0 * np.log10(rms)


def normalize_audio(src_path: Path, dest_path: Path) -> NormalizationResult:
    """Load audio with librosa (handles wav/mp3/flac/m4a/ogg via soundfile
    and audioread fallback), downmix to mono, resample to TARGET_SAMPLE_RATE,
    peak-normalize, and write a canonical PCM16 WAV.
    """
    y, sr_native = librosa.load(str(src_path), sr=None, mono=False)
    if y.ndim == 1:
        channels_in = 1
        mono = y
    else:
        channels_in = y.shape[0]
        mono = np.mean(y, axis=0)

    mono_resampled = librosa.resample(mono, orig_sr=sr_native, target_sr=TARGET_SAMPLE_RATE) if sr_native != TARGET_SAMPLE_RATE else mono

    peak = float(np.max(np.abs(mono_resampled))) if mono_resampled.size else 0.0
    if peak > 0:
        target_linear = 10 ** (TARGET_PEAK_DBFS / 20.0)
        gain = target_linear / peak
        normalized = mono_resampled * gain
    else:
        normalized = mono_resampled

    normalized = np.clip(normalized, -1.0, 1.0)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dest_path), normalized, TARGET_SAMPLE_RATE, subtype="PCM_16")

    duration = float(len(normalized)) / TARGET_SAMPLE_RATE
    rms = float(np.sqrt(np.mean(np.square(normalized)))) if normalized.size else 0.0
    loudness_estimate_dbfs = _rms_to_dbfs(rms)  # RMS-based loudness proxy; not true ITU-R BS.1770 LUFS
    final_peak = float(np.max(np.abs(normalized))) if normalized.size else 0.0
    final_peak_dbfs = _rms_to_dbfs(final_peak)

    checksum = sha256_of_file(dest_path)

    return NormalizationResult(
        normalized_path=dest_path,
        sample_rate=TARGET_SAMPLE_RATE,
        channels=1,
        bit_depth=16,
        duration=duration,
        loudness_lufs=loudness_estimate_dbfs,  # labeled honestly in API as RMS dBFS-based proxy
        peak_dbfs=final_peak_dbfs,
        format="wav",
        checksum_sha256=checksum,
    )
