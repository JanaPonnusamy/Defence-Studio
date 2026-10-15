"""Reproducible output measurements, without inferring identity or word timestamps."""
import hashlib
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def measure(path, text=''):
    data, sr = sf.read(path, dtype='float32')
    if not data.size or sr <= 0 or not np.isfinite(data).all():
        raise ValueError('Audio must contain finite samples')
    mono = data.mean(axis=1) if data.ndim == 2 else data
    duration = len(mono) / sr
    y = librosa.resample(mono, orig_sr=sr, target_sr=16000)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=256)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-10))
    threshold = max(-55., float(np.percentile(db, 95)) - 30.)
    active = db > threshold
    step = 256 / 16000
    regions, pauses = [], []
    start = 0
    for end in range(1, len(active) + 1):
        if end == len(active) or active[end] != active[start]:
            length = min(end * step, duration) - start * step
            if active[start]:
                regions.append(length)
            elif length >= .12:
                pauses.append(length)
            start = end
    f0, voiced, probability = librosa.pyin(y, fmin=60, fmax=500, sr=16000,
                                         frame_length=1024, hop_length=256)
    valid = np.isfinite(f0) & voiced & (probability >= .5) & active[:len(f0)]
    pitch = f0[valid]
    warnings = ['Energy activity is not a speech classifier; music/noise can contaminate it.',
                'Pitch uses pYIN with probability >= 0.5; octave errors remain possible.',
                'WPM uses supplied whitespace-delimited words / complete duration, not recognized speech.',
                'No word alignment or native emotion measurement; listening review required.']
    if len(pitch) < 5:
        warnings.append('Insufficient confident voiced frames; pitch statistics unavailable.')
    return {
        'analysis_version': 'output-measurement-1.0',
        'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        'bytes': Path(path).stat().st_size, 'sample_rate': sr,
        'channels': 1 if data.ndim == 1 else data.shape[1], 'duration_seconds': duration,
        'word_count': len(text.split()), 'speaking_rate_wpm': len(text.split()) * 60 / duration,
        'activity_ratio': float(np.mean(active)), 'voiced_ratio': float(np.mean(valid)),
        'pitch_probability_mean': float(np.mean(probability)),
        'pitch_hz': ({'median': float(np.median(pitch)), 'p05': float(np.percentile(pitch, 5)),
                      'p95': float(np.percentile(pitch, 95)), 'std': float(np.std(pitch))}
                     if len(pitch) >= 5 else None),
        'rms_dbfs': float(20 * np.log10(max(float(np.sqrt(np.mean(mono ** 2))), 1e-10))),
        'peak_dbfs': float(20 * np.log10(max(float(np.max(np.abs(mono))), 1e-10))),
        'dynamic_range_p95_p10_db': float(np.percentile(db, 95) - np.percentile(db, 10)),
        'pause_seconds': pauses, 'mean_phrase_seconds': float(np.mean(regions)) if regions else None,
        'warnings': warnings,
    }
