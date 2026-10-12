from __future__ import annotations

from app.errors import VideoModuleError
from app.generation.base import AIImageProvider
from app.generation.mock_local import MockLocalProvider
from app.generation.openai_images import OpenAIImagesProvider
from app.generation.placeholder_procedural import PlaceholderProceduralProvider

PLACEHOLDER_PROVIDER = "placeholder-procedural"
_BUILTIN = ("placeholder-procedural", "mock-local", "openai-images")

_PROVIDERS: dict[str, AIImageProvider] = {
    "placeholder-procedural": PlaceholderProceduralProvider(),
    "mock-local": MockLocalProvider(),
    "openai-images": OpenAIImagesProvider(),
}


def _aliases() -> dict[str, str]:
    return {alias: name for name, p in _PROVIDERS.items() for alias in getattr(p, "aliases", ())}


def canonical_name(name: str) -> str:
    return _aliases().get(name, name)


def _enabled() -> set[str]:
    from app.config import get_settings

    return {canonical_name(n) for n in get_settings().enabled_image_providers}


def get_provider(name: str) -> AIImageProvider:
    name = canonical_name(name)
    if name not in _PROVIDERS or name not in _enabled() and not getattr(_PROVIDERS[name], "_test_only", False):
        raise VideoModuleError("VALIDATION_ERROR", f"Unknown or disabled image provider: {name!r}. "
                               f"Available: {sorted(p['name'] for p in list_providers())}")
    return _PROVIDERS[name]


def list_providers() -> list[dict]:
    enabled = _enabled()
    return [p.describe() for n, p in _PROVIDERS.items() if n in enabled or getattr(p, "_test_only", False)]


def register_provider(provider: AIImageProvider, *, test_only: bool = True) -> None:
    """Used by the test-suite to plug in failure-injecting providers."""
    provider._test_only = test_only  # type: ignore[attr-defined]
    _PROVIDERS[provider.name] = provider


def unregister_provider(name: str) -> None:
    if name not in _BUILTIN:
        _PROVIDERS.pop(name, None)
