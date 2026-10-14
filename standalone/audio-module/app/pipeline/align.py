"""Forced alignment.

HONEST LIMITATION NOTICE
-------------------------
This standalone build does NOT install a full neural forced-alignment model
(e.g. whisperx/MFA/wav2vec2-CTC), because such models require large
downloads and often GPU acceleration that cannot be assumed to exist on an
arbitrary Windows machine. Installing one is impractical in this
environment.

Instead this module implements an honest, documented FALLBACK aligner:

  1. Energy/VAD-based speech-segmentation: the normalized mono audio is
     analyzed with librosa to find speech-active regions (using a short-time
     RMS energy threshold relative to the track's noise floor).
  2. Each transcript segment (sentence/subtitle cue) is assigned to the
     corresponding detected speech region in chronological order.
  3. Within a segment, word-level timing is produced by PROPORTIONAL
     distribution across the segment's active-speech duration, weighted by
     each word's character length (a crude proxy for spoken duration).

This is clearly WORSE than true forced alignment and is labeled as such:
  - quality_label = "approximate_proportional"
  - every word/segment carries a confidence score that is deliberately
    capped well below what a true CTC aligner would report, and
  - the API and docs state plainly that word-level timing is an estimate,
    not a measurement.

If transcript segments already carry real timings (e.g. ingested from an
SRT/VTT file with real timestamps), those timings are used directly for
segment-level timing (confidence 1.0 at the segment level), and only
word-level timing within each segment remains proportional/estimated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import librosa

from app.pipeline.transcript import TranscriptSegment

FRAME_LENGTH = 1024
HOP_LENGTH = 256


@dataclass
class WordTiming:
    text: str
    start: float
    end: float
    confidence: float


@dataclass
class AlignedSegment:
    text: str
    start: float
    end: float
    confidence: float
    words: list[WordTiming] = field(default_factory=list)


def detect_speech_regions(y: np.ndarray, sr: int, min_region_sec: float = 0.15) -> list[tuple[float, float]]:
    """Simple energy-based VAD: compute short-time RMS, threshold relative
    to the signal's noise floor (10th percentile), and merge contiguous
    active frames into regions."""
    rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH)[0]
    if rms.size == 0:
        return []
    noise_floor = np.percentile(rms, 10)
    peak = np.max(rms)
    threshold = noise_floor + 0.15 * (peak - noise_floor)
    active = rms > threshold

    times = librosa.frames_to_time(np.arange(len(active)), sr=sr, hop_length=HOP_LENGTH)
    regions: list[tuple[float, float]] = []
    start = None
    for i, is_active in enumerate(active):
        if is_active and start is None:
            start = times[i]
        elif not is_active and start is not None:
            end = times[i]
            if end - start >= min_region_sec:
                regions.append((float(start), float(end)))
            start = None
    if start is not None:
        regions.append((float(start), float(times[-1])))
    return regions


def _distribute_words(text: str, start: float, end: float, base_confidence: float) -> list[WordTiming]:
    words = text.split()
    if not words:
        return []
    weights = [max(len(w), 1) for w in words]
    total_weight = sum(weights)
    duration = max(end - start, 0.01)
    cursor = start
    out: list[WordTiming] = []
    for w, wt in zip(words, weights):
        span = duration * (wt / total_weight)
        w_start = cursor
        w_end = cursor + span
        out.append(WordTiming(text=w, start=round(w_start, 3), end=round(w_end, 3), confidence=round(base_confidence, 3)))
        cursor = w_end
    return out


# Word-level confidence is deliberately capped below segment-level confidence
# because proportional-by-character-length timing is an approximation, not a
# measurement of actual phoneme boundaries.
WORD_CONFIDENCE_CAP = 0.55


def align_vad_proportional(y: np.ndarray, sr: int, segments: list[TranscriptSegment]) -> tuple[list[AlignedSegment], float, str]:
    """Produce the fallback alignment described in this module's docstring.

    IMPORTANT: real segment timing (e.g. from an SRT/VTT cue) is trusted
    PER SEGMENT, not all-or-nothing. A single malformed cue (e.g. a reversed
    end<=start timestamp, which can happen with hand-edited or corrupted
    subtitle files) must not discard the valid, real timing of every other
    segment in the file -- it only disables trust for that one segment,
    which falls back to a VAD-derived region instead. (This was a real bug
    found during the admin self-test: one bad cue silently threw away good
    data for the whole transcript. Fixed by validating per segment.)

    Returns (aligned_segments, mean_confidence, quality_label).
    """
    valid_flags = [s.end > s.start for s in segments]
    any_valid = any(valid_flags)
    all_valid = all(valid_flags) and len(segments) > 0

    # VAD speech regions are only computed (and only consumed) for segments
    # that do NOT have trustworthy real timing.
    regions = detect_speech_regions(y, sr) if not all_valid else []
    region_cursor = 0

    aligned: list[AlignedSegment] = []
    for seg, is_valid in zip(segments, valid_flags):
        if is_valid:
            # Segment timing came from a real SRT/VTT cue -- trust it.
            words = _distribute_words(seg.text, seg.start, seg.end, WORD_CONFIDENCE_CAP)
            aligned.append(AlignedSegment(text=seg.text, start=seg.start, end=seg.end, confidence=0.95, words=words))
            continue

        if region_cursor < len(regions):
            start, end = regions[region_cursor]
            region_cursor += 1
            conf = 0.4  # whole-segment timing itself is only VAD-derived, so cap lower
            words = _distribute_words(seg.text, start, end, min(conf, WORD_CONFIDENCE_CAP))
            aligned.append(AlignedSegment(text=seg.text, start=start, end=end, confidence=conf, words=words))
        else:
            # No VAD region left to assign -- honest zero-span placeholder
            # rather than silently dropping the segment or fabricating timing.
            aligned.append(AlignedSegment(text=seg.text, start=0.0, end=0.0, confidence=0.0, words=[]))

    mean_conf = float(np.mean([a.confidence for a in aligned])) if aligned else 0.0

    if all_valid:
        quality_label = "segment_timed_word_proportional"
    elif any_valid:
        # Mix of real per-cue timing and VAD-derived fallback for the
        # specific segment(s) whose given timestamps were invalid.
        quality_label = "mixed_segment_timed_and_vad_fallback"
    else:
        quality_label = "approximate_proportional_vad"

    return aligned, mean_conf, quality_label
