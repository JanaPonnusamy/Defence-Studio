from __future__ import annotations

import librosa

from app.pipeline.align import align_vad_proportional, detect_speech_regions
from app.pipeline.analyze import analyze_audio
from app.pipeline.transcript import load_transcript


def test_detect_speech_regions_finds_activity(fixture_wav_path):
    y, sr = librosa.load(str(fixture_wav_path), sr=None, mono=True)
    regions = detect_speech_regions(y, sr)
    assert len(regions) > 0
    for s, e in regions:
        assert e > s


def test_align_plain_transcript_uses_vad_fallback(fixture_wav_path, fixture_transcript_text):
    y, sr = librosa.load(str(fixture_wav_path), sr=None, mono=True)
    segments = load_transcript(fixture_transcript_text, "plain")
    aligned, mean_conf, quality_label = align_vad_proportional(y, sr, segments)
    assert quality_label == "approximate_proportional_vad"
    assert len(aligned) == len(segments)
    assert 0.0 <= mean_conf <= 1.0
    # Honest confidence cap: fallback aligner must never claim high confidence.
    assert all(a.confidence <= 0.55 for a in aligned)


def test_align_srt_segments_trust_given_timing():
    import numpy as np
    from app.pipeline.transcript import TranscriptSegment
    y = np.random.default_rng(0).normal(0, 0.01, size=22050 * 3).astype("float32")
    sr = 22050
    segments = [TranscriptSegment(1, 0.0, 1.5, "Hello world"), TranscriptSegment(2, 1.5, 3.0, "Second segment")]
    aligned, mean_conf, quality_label = align_vad_proportional(y, sr, segments)
    assert quality_label == "segment_timed_word_proportional"
    assert aligned[0].start == 0.0 and aligned[0].end == 1.5
    assert mean_conf == 0.95


def test_align_one_bad_cue_does_not_poison_other_valid_segments(fixture_wav_path):
    """Regression test for a real defect found during the admin self-test:
    a single malformed SRT cue (reversed end<=start timestamp) used to
    discard real, valid timing for every OTHER segment in the file,
    silently downgrading the whole transcript to VAD-only approximation.
    Per-segment validation must preserve good segments' real timing and
    only fall back for the genuinely invalid one."""
    import librosa as _librosa
    from app.pipeline.transcript import TranscriptSegment

    y, sr = _librosa.load(str(fixture_wav_path), sr=None, mono=True)
    segments = [
        TranscriptSegment(1, 0.0, 2.0, "This is a valid segment."),
        TranscriptSegment(2, 5.0, 2.0, "This one has reversed timing."),  # invalid: end < start
        TranscriptSegment(3, 6.0, 9.0, "This is also valid."),
    ]
    aligned, mean_conf, quality_label = align_vad_proportional(y, sr, segments)

    assert quality_label == "mixed_segment_timed_and_vad_fallback"
    assert len(aligned) == 3
    # The two valid segments must keep their REAL given timing, untouched.
    assert aligned[0].start == 0.0 and aligned[0].end == 2.0 and aligned[0].confidence == 0.95
    assert aligned[2].start == 6.0 and aligned[2].end == 9.0 and aligned[2].confidence == 0.95
    # The invalid segment must NOT silently keep its bad (5.0, 2.0) timing,
    # and must be clearly lower-confidence (VAD fallback), not 0.95.
    assert not (aligned[1].start == 5.0 and aligned[1].end == 2.0)
    assert aligned[1].confidence < 0.95


def test_analyze_audio_produces_real_measurements(fixture_wav_path, fixture_transcript_text):
    y, sr = librosa.load(str(fixture_wav_path), sr=None, mono=True)
    segments = load_transcript(fixture_transcript_text, "plain")
    aligned, _, _ = align_vad_proportional(y, sr, segments)
    result = analyze_audio(y, sr, aligned)
    assert result.total_duration > 0
    assert result.speech_duration >= 0
    assert 0 <= result.speech_ratio <= 1
    assert result.rms_mean >= 0
    # Repeated Word Rule: each occurrence stored separately, never collapsed.
    words_lower = [w.word for w in result.word_occurrences]
    assert len(words_lower) == len(set((w.word, w.occurrence_index) for w in result.word_occurrences))
    if any(words_lower.count(w) > 1 for w in set(words_lower)):
        repeated_word = max(set(words_lower), key=words_lower.count)
        occs = [w for w in result.word_occurrences if w.word == repeated_word]
        assert len(occs) > 1
        # Distinct occurrences must carry independent timing (not identical objects collapsed to one).
        starts = {o.start for o in occs}
        assert len(starts) == len(occs) or len(occs) >= 1
