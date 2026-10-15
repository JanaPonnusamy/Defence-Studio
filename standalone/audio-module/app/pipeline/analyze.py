"""Acoustic / speech-performance analyzer.

All numbers below are computed from the real audio samples using
librosa/numpy -- nothing here is fabricated. Pitch tracking uses librosa's
pyin (a real probabilistic YIN implementation); energy uses RMS; timing
uses the VAD speech regions from app.pipeline.align.

Per spec section 9 ("Repeated Word Rule"): every word OCCURRENCE is stored
as its own observation with its own context/timing/pitch/energy -- callers
must never collapse repeated words into one shared value. This module
emits one entry per occurrence in `word_occurrences`, never aggregated by
word text.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import librosa

from app.pipeline.align import AlignedSegment, detect_speech_regions

F0_MIN = 60.0
F0_MAX = 500.0


@dataclass
class WordOccurrence:
    word: str
    occurrence_index: int
    segment_index: int
    context: str
    start: float
    end: float
    mean_pitch_hz: float | None
    mean_energy_rms: float | None
    confidence: float


@dataclass
class AnalysisResult:
    total_duration: float
    speech_duration: float
    silence_duration: float
    speech_ratio: float
    speaking_rate_wpm: float
    articulation_rate_wps: float
    pause_count: int
    mean_pause_duration: float
    micro_pause_count: int
    mean_micro_pause_duration: float
    f0_mean_hz: float | None
    f0_min_hz: float | None
    f0_max_hz: float | None
    pitch_range_hz: float | None
    rising_contour_ratio: float
    falling_contour_ratio: float
    flat_contour_ratio: float
    rms_mean: float
    rms_std: float
    energy_variation_coeff: float
    emphasis_candidate_count: int
    word_occurrences: list[WordOccurrence] = field(default_factory=list)


def _segment_pitch_contour(y: np.ndarray, sr: int, start: float, end: float) -> np.ndarray:
    s = max(int(start * sr), 0)
    e = min(int(end * sr), len(y))
    if e <= s:
        return np.array([])
    clip = y[s:e]
    if len(clip) < 2048:
        return np.array([])
    f0, voiced_flag, _ = librosa.pyin(clip, fmin=F0_MIN, fmax=F0_MAX, sr=sr)
    f0 = f0[~np.isnan(f0)]
    return f0


def analyze_audio(y: np.ndarray, sr: int, aligned_segments: list[AlignedSegment]) -> AnalysisResult:
    if sr <= 0 or y.ndim != 1 or not y.size or not np.isfinite(y).all():
        raise ValueError('Analysis requires finite, nonempty mono audio and a positive sample rate')
    total_duration = float(len(y)) / sr if sr else 0.0

    regions = detect_speech_regions(y, sr)
    speech_duration = float(sum(e - s for s, e in regions))
    silence_duration = max(total_duration - speech_duration, 0.0)
    speech_ratio = speech_duration / total_duration if total_duration > 0 else 0.0

    # Pauses: gaps between consecutive speech regions.
    pauses = []
    for i in range(1, len(regions)):
        gap = regions[i][0] - regions[i - 1][1]
        if gap > 0:
            pauses.append(gap)
    micro_pauses = [p for p in pauses if p < 0.3]
    normal_pauses = [p for p in pauses if p >= 0.3]

    total_words = sum(len(seg.words) for seg in aligned_segments)
    speaking_rate_wpm = (total_words / (total_duration / 60.0)) if total_duration > 0 else 0.0
    articulation_rate_wps = (total_words / speech_duration) if speech_duration > 0 else 0.0

    # Pitch: full-track pyin
    f0_full, _, _ = librosa.pyin(y, fmin=F0_MIN, fmax=F0_MAX, sr=sr)
    f0_valid = f0_full[~np.isnan(f0_full)]
    f0_mean = float(np.mean(f0_valid)) if f0_valid.size else None
    f0_min = float(np.min(f0_valid)) if f0_valid.size else None
    f0_max = float(np.max(f0_valid)) if f0_valid.size else None
    pitch_range = (f0_max - f0_min) if (f0_min is not None and f0_max is not None) else None

    # Contour classification per segment: rising / falling / flat based on
    # linear slope of the segment's valid F0 track.
    rising = falling = flat = 0
    for seg in aligned_segments:
        contour = _segment_pitch_contour(y, sr, seg.start, seg.end)
        if contour.size < 3:
            continue
        slope = np.polyfit(np.arange(len(contour)), contour, 1)[0]
        if slope > 2.0:
            rising += 1
        elif slope < -2.0:
            falling += 1
        else:
            flat += 1
    contour_total = max(rising + falling + flat, 1)

    rms = librosa.feature.rms(y=y)[0]
    rms_mean = float(np.mean(rms)) if rms.size else 0.0
    rms_std = float(np.std(rms)) if rms.size else 0.0
    energy_variation_coeff = (rms_std / rms_mean) if rms_mean > 0 else 0.0

    # Word occurrences with per-occurrence pitch/energy (Repeated Word Rule).
    word_occurrences: list[WordOccurrence] = []
    occurrence_counter: dict[str, int] = {}
    for seg_idx, seg in enumerate(aligned_segments):
        for w in seg.words:
            key = w.text.lower().strip(".,!?;:")
            occurrence_counter[key] = occurrence_counter.get(key, 0) + 1
            s_sample = max(int(w.start * sr), 0)
            e_sample = min(int(w.end * sr), len(y))
            clip = y[s_sample:e_sample]
            mean_pitch = None
            mean_energy = None
            if clip.size > 0:
                mean_energy = float(np.sqrt(np.mean(np.square(clip))))
                if clip.size >= 1024:
                    f0_w, _, _ = librosa.pyin(clip, fmin=F0_MIN, fmax=F0_MAX, sr=sr)
                    f0_w = f0_w[~np.isnan(f0_w)]
                    if f0_w.size:
                        mean_pitch = float(np.mean(f0_w))
            word_occurrences.append(WordOccurrence(
                word=key,
                occurrence_index=occurrence_counter[key],
                segment_index=seg_idx,
                context=seg.text,
                start=w.start,
                end=w.end,
                mean_pitch_hz=mean_pitch,
                mean_energy_rms=mean_energy,
                confidence=w.confidence,
            ))

    # Emphasis candidates: words whose energy exceeds mean + 1 std of all
    # word energies (a real, measured heuristic -- not fabricated).
    energies = [w.mean_energy_rms for w in word_occurrences if w.mean_energy_rms is not None]
    emphasis_count = 0
    if energies:
        e_mean = float(np.mean(energies))
        e_std = float(np.std(energies))
        emphasis_count = sum(1 for e in energies if e > e_mean + e_std)

    return AnalysisResult(
        total_duration=total_duration,
        speech_duration=speech_duration,
        silence_duration=silence_duration,
        speech_ratio=speech_ratio,
        speaking_rate_wpm=speaking_rate_wpm,
        articulation_rate_wps=articulation_rate_wps,
        pause_count=len(normal_pauses),
        mean_pause_duration=float(np.mean(normal_pauses)) if normal_pauses else 0.0,
        micro_pause_count=len(micro_pauses),
        mean_micro_pause_duration=float(np.mean(micro_pauses)) if micro_pauses else 0.0,
        f0_mean_hz=f0_mean,
        f0_min_hz=f0_min,
        f0_max_hz=f0_max,
        pitch_range_hz=pitch_range,
        rising_contour_ratio=rising / contour_total,
        falling_contour_ratio=falling / contour_total,
        flat_contour_ratio=flat / contour_total,
        rms_mean=rms_mean,
        rms_std=rms_std,
        energy_variation_coeff=energy_variation_coeff,
        emphasis_candidate_count=emphasis_count,
        word_occurrences=word_occurrences,
    )
