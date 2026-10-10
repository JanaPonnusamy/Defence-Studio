"""Acquisition provider interface and registry.

Adapted from QVault's `integrations/acquisition/provider.py` registry pattern
(register / get / list) but narrowed to F3's job: no provider-specific type is
allowed past this boundary. A provider receives a `ProviderContext` (a validated
target plus the byte store, URL guard, HTTP client factory and a cancellation
event) and returns a `ProviderResult` of real stored bytes and declared
metadata. Failures are raised as `ProviderError` with an honest retryable flag;
a provider must never substitute a different source or fabricate success.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from defence_studio.acquisition.http_client import GuardedFetcher
from defence_studio.acquisition.url_guard import UrlGuard, ValidatedUrl
from defence_studio.contracts.acquisition import (
    AcquisitionLimits,
    AcquisitionTarget,
    DeclaredMetadata,
)
from defence_studio.storage.artifacts import ArtifactStore


class ProviderError(Exception):
    """A provider could not acquire the requested source.

    `retryable` reflects whether a later attempt through the durable worker
    could plausibly succeed (timeouts, transient 5xx) versus a permanent
    condition (blocked address, 404, unsupported content).
    """

    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderContext:
    job_id: str
    target: AcquisitionTarget
    validated_url: ValidatedUrl
    limits: AcquisitionLimits
    store: ArtifactStore
    guard: UrlGuard
    # Guarded fetcher centralises SSRF revalidation, redirect bounding and size
    # limits; providers must download through it rather than any raw client.
    fetcher: GuardedFetcher
    cancel: threading.Event
    source_id: str


@dataclass(frozen=True)
class StoredOutput:
    artifact_type: str
    uri: str
    sha256: str
    bytes: int
    media_type: str


@dataclass(frozen=True)
class ProviderResult:
    source_type: str
    artifacts: tuple[StoredOutput, ...]
    declared_metadata: DeclaredMetadata
    original_metadata: dict = field(default_factory=dict)
    final_url: str | None = None
    warnings: tuple[str, ...] = ()


class AcquisitionProvider(ABC):
    name: str
    version: str
    source_component: str = ""
    # A fallback provider is only chosen under `auto` when no specific provider
    # claims the URL (e.g. the generic HTTP page provider yields to YouTube).
    fallback: bool = False

    @abstractmethod
    def supports(self, validated_url: ValidatedUrl) -> bool:
        """Whether this provider should handle the URL under `auto` selection."""

    @abstractmethod
    def acquire(self, context: ProviderContext) -> ProviderResult:
        """Acquire bytes for the target, writing them through `context.store`."""


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, AcquisitionProvider] = {}

    def register(self, provider: AcquisitionProvider) -> AcquisitionProvider:
        self._providers[provider.name] = provider
        return provider

    def get(self, name: str) -> AcquisitionProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderError("unknown_method", f"No provider registered for '{name}'", retryable=False)
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)

    def resolve(self, method: str, validated_url: ValidatedUrl) -> AcquisitionProvider:
        """Pick a provider for a target.

        An explicit method pins exactly one provider (and fails closed if its
        own `supports` rejects the URL, so a mismatched method never silently
        acquires the wrong thing). `auto` selects the first registered provider
        whose `supports` returns True.
        """
        if method and method != "auto":
            provider = self.get(method)
            if not provider.supports(validated_url):
                raise ProviderError(
                    "method_mismatch",
                    f"Provider '{method}' does not support {validated_url.url}",
                    retryable=False,
                )
            return provider
        fallback = None
        for name in self.names():
            provider = self._providers[name]
            if not provider.supports(validated_url):
                continue
            if provider.fallback:
                fallback = fallback or provider
            else:
                return provider  # a specific provider wins over any fallback
        if fallback is not None:
            return fallback
        raise ProviderError(
            "no_provider", f"No acquisition provider supports {validated_url.url}", retryable=False
        )
