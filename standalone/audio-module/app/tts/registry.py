from __future__ import annotations

from app.config import get_settings
from app.tts.base import TTSAdapter
from app.tts.edge_neural import EdgeNeuralAdapter
from app.tts.local_pyttsx3 import LocalPyttsx3Adapter
from app.tts.mock_adapter import MockTTSAdapter

_ADAPTERS: dict[str, TTSAdapter] = {
    "edge-neural": EdgeNeuralAdapter(),
    "local-pyttsx3": LocalPyttsx3Adapter(),
    "mock-tts": MockTTSAdapter(),
}


def get_adapter(provider: str | None) -> TTSAdapter:
    settings = get_settings()
    name = provider or settings.default_tts_provider
    if name not in settings.enabled_tts_providers:
        raise ValueError(f"TTS provider '{name}' is not enabled in this deployment")
    if name not in _ADAPTERS:
        raise ValueError(f"Unknown TTS provider '{name}'")
    return _ADAPTERS[name]


def list_adapters() -> list[dict]:
    settings = get_settings()
    return [a.describe() for name, a in _ADAPTERS.items() if name in settings.enabled_tts_providers]
