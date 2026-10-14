"""MOCK TTS adapter.

THIS ADAPTER IS UNMISTAKABLY FAKE. It does not call any real speech model.
It synthesizes a short, valid, silent-ish sine-wave placeholder WAV file so
that the rest of the pipeline (validation, job system, UI playback) can be
exercised end-to-end without any cloud credentials or heavy local model.

Every result from this adapter reports provider="mock-tts" and
is_mock=True, both in describe() and in the TTSResult/provider_metadata, so
callers can never mistake it for a real synthesis.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from app.tts.base import TTSAdapter, TTSRequest, TTSResult

SAMPLE_RATE = 22050
WORDS_PER_SECOND_ESTIMATE = 2.5  # rough mock pacing only, not a real measurement


class MockTTSAdapter(TTSAdapter):
    provider_name = "mock-tts"
    is_mock = True

    def describe(self) -> dict:
        return {
            "provider": self.provider_name,
            "model": "mock-sine-synth-v1",
            "capabilities": ["duration_estimate_only"],
            "output_format": "wav",
            "is_mock": True,
            "notes": "MOCK ADAPTER: produces a placeholder tone, not real speech. For testing only.",
        }

    def synthesize(self, request: TTSRequest, dest_path: Path) -> TTSResult:
        word_count = max(len(request.text.split()), 1)
        duration = word_count / WORDS_PER_SECOND_ESTIMATE
        duration = max(duration, 0.3)

        t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
        # Gentle warbling tone with an envelope so it is clearly audible but
        # obviously not real speech -- a deliberate, labeled placeholder.
        base_freq = 220.0
        tone = 0.15 * np.sin(2 * np.pi * base_freq * t) * np.sin(2 * np.pi * 2.0 * t) ** 2
        envelope = np.clip(np.sin(np.pi * t / duration), 0, 1)
        samples = (tone * envelope).astype(np.float32)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(dest_path), samples, SAMPLE_RATE, subtype="PCM_16")

        return TTSResult(
            provider=self.provider_name,
            model="mock-sine-synth-v1",
            voice=request.voice_id or "mock-voice",
            language=request.language,
            capabilities=["duration_estimate_only"],
            output_format="wav",
            request_id=str(uuid.uuid4()),
            result_path=dest_path,
            estimated_cost=0.0,
            provider_metadata={"is_mock": True, "word_count": word_count},
        )
