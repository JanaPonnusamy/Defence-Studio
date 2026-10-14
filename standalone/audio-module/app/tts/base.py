"""Provider-neutral TTS adapter interface (spec section 13/14).

Core pipeline/API code must depend only on this interface, never on any
provider-specific class or module.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TTSRequest:
    text: str
    voice_id: str | None
    language: str
    performance_plan: list[dict] = field(default_factory=list)
    output_format: str = "wav"


@dataclass
class TTSResult:
    provider: str
    model: str
    voice: str
    language: str
    capabilities: list[str]
    output_format: str
    request_id: str
    result_path: Path
    estimated_cost: float | None
    provider_metadata: dict


class TTSAdapter(ABC):
    provider_name: str = "unset"
    is_mock: bool = False

    @abstractmethod
    def synthesize(self, request: TTSRequest, dest_path: Path) -> TTSResult:
        ...

    @abstractmethod
    def describe(self) -> dict:
        """Return provider/model/voice/capability metadata without secrets."""
        ...
