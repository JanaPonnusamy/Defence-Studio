"""Provider-neutral alignment engine interface.

An engine maps spoken tokens to timing against a decoded audio file. It must:
- advertise capabilities, distinguishing *declared* language support (the
  codes it accepts) from *tested* support (codes actually verified to produce
  reliable alignment) — see ``EngineCapabilities``;
- report whether it is currently available (libraries + model present) without
  downloading anything or contacting a network at import/startup;
- return the granularity it actually produced (never claim word-level when it
  only did sentence-level);
- surface engine-provided confidence without inventing values;
- support cooperative cancellation via a ``threading.Event``.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EngineCapabilities:
    engine_name: str
    engine_version: str
    available: bool
    is_synthetic: bool
    declared_languages: tuple[str, ...]   # codes the engine accepts
    tested_languages: tuple[str, ...]     # codes verified to actually align reliably
    audio_formats: tuple[str, ...]
    granularities: tuple[str, ...]        # e.g. ("word",) or ("sentence",)
    requires_model: bool
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "engine_name": self.engine_name, "engine_version": self.engine_version,
            "available": self.available, "is_synthetic": self.is_synthetic,
            "declared_languages": list(self.declared_languages),
            "tested_languages": list(self.tested_languages),
            "audio_formats": list(self.audio_formats),
            "granularities": list(self.granularities),
            "requires_model": self.requires_model, "notes": self.notes,
        }


@dataclass(frozen=True)
class EngineTokenInput:
    token_id: str
    original_text: str
    normalized_text: str
    segment_id: str
    index: int


@dataclass(frozen=True)
class EngineTokenResult:
    token_id: str
    aligned: bool
    start: float | None = None
    end: float | None = None
    confidence: float | None = None
    is_uncertain: bool = False
    kind: str = "word"          # "word" or "segment" (sentence-level)
    reason: str = ""            # populated when aligned is False


@dataclass(frozen=True)
class EngineAlignResult:
    actual_granularity: str     # "word" | "sentence"
    tokens: list[EngineTokenResult] = field(default_factory=list)


class AlignmentEngine(ABC):
    name: str = "abstract"
    version: str = "0"

    @abstractmethod
    def capabilities(self) -> EngineCapabilities: ...

    def is_available(self) -> bool:
        return self.capabilities().available

    @abstractmethod
    def align(
        self,
        *,
        audio_path,
        duration_seconds: float,
        language: str,
        tokens: list[EngineTokenInput],
        requested_granularity: str,
        cancel: threading.Event | None = None,
    ) -> EngineAlignResult:
        """Return per-token timing. Must raise
        ``align_engine.errors.UnsupportedCapabilityError`` when it cannot
        honestly perform the requested alignment (e.g. model absent, language
        not supported) rather than returning fabricated timings."""
