"""Waveform/timeline peak-data generation for the UI's waveform renderer."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def generate_waveform_peaks(y: np.ndarray, sr: int, buckets: int = 1000) -> dict:
    if y.size == 0:
        return {"sample_rate": sr, "buckets": buckets, "peaks": []}
    bucket_size = max(len(y) // buckets, 1)
    peaks = []
    for i in range(0, len(y), bucket_size):
        chunk = y[i:i + bucket_size]
        if chunk.size == 0:
            continue
        peaks.append([float(np.min(chunk)), float(np.max(chunk))])
    return {"sample_rate": sr, "buckets": len(peaks), "duration": float(len(y)) / sr, "peaks": peaks}


def write_waveform_json(y: np.ndarray, sr: int, dest_path: Path) -> Path:
    data = generate_waveform_peaks(y, sr)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(data), encoding="utf-8")
    return dest_path
