"""Generates a self-contained synthetic test-fixture WAV file plus a matching
plain-text transcript, for use in tests and manual verification. This is
clearly a synthetic TEST FIXTURE (sine-wave speech-like amplitude envelope),
not a claim of real recorded speech.

Run: python fixtures/generate_fixture.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 22050
OUT_WAV = Path(__file__).parent / "test_fixture_speech_like.wav"
OUT_TXT = Path(__file__).parent / "test_fixture_transcript.txt"

TRANSCRIPT = (
    "This is a test fixture. It is generated synthetically for automated testing. "
    "The audio module should process this file end to end. "
    "Alignment and analysis results on this fixture are approximate by design."
)


def build_fixture_audio() -> np.ndarray:
    """Builds a synthetic 'speech-like' waveform: several words-as-tones
    separated by silence gaps, each tone frequency-modulated slightly and
    amplitude-enveloped, so VAD/energy/pitch code has real signal structure
    to operate on."""
    segments = []
    rng = np.random.default_rng(42)
    sentences = TRANSCRIPT.split(". ")
    for sentence in sentences:
        words = sentence.split()
        for w in words:
            dur = max(0.12 + 0.03 * len(w), 0.15)
            t = np.linspace(0, dur, int(SR * dur), endpoint=False)
            base_freq = 140 + rng.uniform(-20, 40)
            vibrato = 6 * np.sin(2 * np.pi * 5 * t)
            tone = 0.25 * np.sin(2 * np.pi * (base_freq + vibrato) * t)
            tone += 0.05 * np.sin(2 * np.pi * (base_freq * 2) * t)
            envelope = np.sin(np.pi * np.linspace(0, 1, len(t))) ** 0.5
            segments.append(tone * envelope)
            gap = int(SR * 0.08)
            segments.append(np.zeros(gap))
        sentence_gap = int(SR * 0.45)
        segments.append(np.zeros(sentence_gap))
    audio = np.concatenate(segments).astype(np.float32)
    noise = rng.normal(0, 0.002, size=audio.shape).astype(np.float32)
    return np.clip(audio + noise, -1.0, 1.0)


def main() -> None:
    audio = build_fixture_audio()
    sf.write(str(OUT_WAV), audio, SR, subtype="PCM_16")
    OUT_TXT.write_text(TRANSCRIPT, encoding="utf-8")
    print(f"Wrote {OUT_WAV} ({len(audio) / SR:.2f}s) and {OUT_TXT}")


if __name__ == "__main__":
    main()
