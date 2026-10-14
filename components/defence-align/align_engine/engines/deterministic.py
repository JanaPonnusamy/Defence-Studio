"""Deterministic SYNTHETIC test aligner — for automated tests only.

CRITICAL: this engine does NOT measure speech. It produces reproducible,
length-weighted synthetic timings purely so the pipeline (validation,
serialization, storage, jobs, API) can be exercised offline without a real
model. Every result it produces is flagged ``is_synthetic=True`` and the
engine name is ``deterministic-test-aligner``; the service refuses it unless
the request sets ``config.allow_synthetic=true``. Its timestamps must never be
presented as measured alignment of real audio.

It is explicitly NOT an "even division across the transcript": each token's
span is proportional to its normalized length, within the measured duration,
non-overlapping and ordered. Confidence is intentionally ``None`` — a synthetic
engine has no measured confidence to report and will not invent one.
"""
from __future__ import annotations

import threading

from align_engine.engines.base import (
    AlignmentEngine, EngineAlignResult, EngineCapabilities, EngineTokenInput,
    EngineTokenResult,
)

ENGINE_NAME = "deterministic-test-aligner"
ENGINE_VERSION = "synthetic-1"


class DeterministicTestAligner(AlignmentEngine):
    name = ENGINE_NAME
    version = ENGINE_VERSION

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            engine_name=ENGINE_NAME, engine_version=ENGINE_VERSION,
            available=True, is_synthetic=True,
            declared_languages=("ta", "en"),
            # "tested" here means the synthetic path is exercised by tests; it is
            # NOT a claim of real-speech alignment capability.
            tested_languages=("ta", "en"),
            audio_formats=("wav",), granularities=("word", "sentence"),
            requires_model=False,
            notes="Synthetic test aligner. Timings are reproducible test data, "
                  "NOT measured alignment of real speech.",
        )

    def align(self, *, audio_path, duration_seconds, language, tokens,
              requested_granularity, cancel: threading.Event | None = None) -> EngineAlignResult:
        if requested_granularity == "sentence":
            return self._sentence(tokens, duration_seconds, cancel)
        return self._word(tokens, duration_seconds, cancel)

    def _word(self, tokens: list[EngineTokenInput], duration: float,
              cancel: threading.Event | None) -> EngineAlignResult:
        weights = [max(1, len(t.normalized_text)) for t in tokens]
        total = sum(weights) or 1
        cursor = 0.0
        results: list[EngineTokenResult] = []
        for token, weight in zip(tokens, weights):
            if cancel is not None and cancel.is_set():
                break
            span = duration * (weight / total)
            start = cursor
            end = min(duration, cursor + span)
            cursor = end
            results.append(EngineTokenResult(
                token_id=token.token_id, aligned=True, start=round(start, 3),
                end=round(end, 3), confidence=None, kind="word",
            ))
        return EngineAlignResult(actual_granularity="word", tokens=results)

    def _sentence(self, tokens: list[EngineTokenInput], duration: float,
                  cancel: threading.Event | None) -> EngineAlignResult:
        # Group tokens by segment and emit one span per segment; granularity is
        # honestly reported as "sentence".
        order: list[str] = []
        by_segment: dict[str, list[EngineTokenInput]] = {}
        for token in tokens:
            if token.segment_id not in by_segment:
                by_segment[token.segment_id] = []
                order.append(token.segment_id)
            by_segment[token.segment_id].append(token)
        weights = [sum(max(1, len(t.normalized_text)) for t in by_segment[s]) for s in order]
        total = sum(weights) or 1
        cursor = 0.0
        results: list[EngineTokenResult] = []
        for segment_id, weight in zip(order, weights):
            span = duration * (weight / total)
            start, end = cursor, min(duration, cursor + span)
            cursor = end
            results.append(EngineTokenResult(
                token_id=segment_id, aligned=True, start=round(start, 3),
                end=round(end, 3), confidence=None, kind="segment",
            ))
        return EngineAlignResult(actual_granularity="sentence", tokens=results)
