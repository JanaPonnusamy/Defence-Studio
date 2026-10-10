"""Acquisition orchestration service.

Runs an `AcquisitionRequestV1` over its targets and returns an
`AcquisitionResultV1`. Design guarantees:

* Per-source isolation: one target failing never aborts the others; the result
  reports explicit partial success (succeeded / failed / skipped counts).
* No silent substitution: a target either acquires the thing it named or is
  recorded as a failure with an honest, retryable-tagged error.
* Idempotent reuse: an already-acquired source (same canonical URL) whose stored
  bytes still verify is reused instead of re-downloaded.
* Clamped limits: a request may only ask for the same or less than the hard
  ceilings; it can never widen them.
* Full provenance from request to stored bytes is preserved on every source.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone

from defence_studio.acquisition.http_client import GuardedFetcher
from defence_studio.acquisition.provider import (
    AcquisitionProvider,
    ProviderContext,
    ProviderError,
    ProviderRegistry,
    ProviderResult,
    StoredOutput,
)
from defence_studio.acquisition.providers.http_page import HttpPageProvider
from defence_studio.acquisition.providers.youtube import YoutubeSubtitleProvider
from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError, normalize_url
from defence_studio.contracts.acquisition import (
    AcquiredSourceV1,
    AcquisitionError,
    AcquisitionLimits,
    AcquisitionRequestV1,
    AcquisitionResultV1,
    ArtifactRef,
    DeclaredMetadata,
    MetadataStatus,
    Provenance,
    SourceStatus,
)
from defence_studio.storage.artifacts import ArtifactStore, StoragePathError


# Hard ceilings. A request's limits are clamped to these and can never exceed them.
HARD_MAX_BYTES = 100 * 1024 * 1024
HARD_MAX_SOURCES = 50
HARD_TIMEOUT_SECONDS = 120.0
HARD_MAX_REDIRECTS = 10

_REUSE_SIDECAR = "acquired.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def default_registry(youtube_backend=None) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(HttpPageProvider())
    if youtube_backend is not None:
        registry.register(YoutubeSubtitleProvider(backend=youtube_backend))
    else:
        registry.register(YoutubeSubtitleProvider())
    return registry


def clamp_limits(limits: AcquisitionLimits) -> AcquisitionLimits:
    return AcquisitionLimits(
        max_bytes_per_source=max(1, min(limits.max_bytes_per_source, HARD_MAX_BYTES)),
        timeout_seconds=max(0.1, min(limits.timeout_seconds, HARD_TIMEOUT_SECONDS)),
        max_sources=max(0, min(limits.max_sources, HARD_MAX_SOURCES)),
        max_redirects=max(0, min(limits.max_redirects, HARD_MAX_REDIRECTS)),
    )


class AcquisitionService:
    def __init__(self, store: ArtifactStore, guard: UrlGuard | None = None,
                 registry: ProviderRegistry | None = None, client_factory=None):
        self.store = store
        self.guard = guard or UrlGuard()
        self.registry = registry or default_registry()
        self.client_factory = client_factory

    def run(self, request: AcquisitionRequestV1,
            cancel: threading.Event | None = None) -> AcquisitionResultV1:
        cancel = cancel or threading.Event()
        limits = clamp_limits(request.limits)
        acquired_at = _now_iso()
        sources: list[AcquiredSourceV1] = []
        warnings: list[str] = []
        seen: dict[str, str] = {}  # source_id -> first target_id

        selected = request.targets[: limits.max_sources]
        if len(request.targets) > limits.max_sources:
            warnings.append(
                f"Request named {len(request.targets)} targets; capped at {limits.max_sources}."
            )

        for target in selected:
            if cancel.is_set():
                warnings.append("Acquisition cancelled before all targets were processed.")
                break
            sources.append(self._acquire_one(request.job_id, target, limits, seen, cancel))

        succeeded = sum(s.status == SourceStatus.ACQUIRED for s in sources)
        failed = sum(s.status == SourceStatus.FAILED for s in sources)
        skipped = sum(s.status == SourceStatus.SKIPPED_DUPLICATE for s in sources)
        return AcquisitionResultV1(
            job_id=request.job_id,
            acquired_at=acquired_at,
            sources=tuple(sources),
            requested=len(request.targets),
            succeeded=succeeded,
            failed=failed,
            skipped_duplicate=skipped,
            warnings=tuple(warnings),
        )

    def _acquire_one(self, job_id, target, limits, seen, cancel) -> AcquiredSourceV1:
        timestamp = _now_iso()
        # 1. Normalize + derive the stable source id (also the dedup key).
        try:
            canonical = normalize_url(target.url)
        except UrlValidationError as exc:
            return self._failed(job_id, target, target.url, target.url, timestamp,
                                AcquisitionError("invalid_url", str(exc), retryable=False))
        source_id = hashlib.sha256(canonical.encode()).hexdigest()[:40]

        # 2. In-request duplicate?
        if source_id in seen:
            return AcquiredSourceV1(
                source_id=source_id, target_id=target.target_id,
                status=SourceStatus.SKIPPED_DUPLICATE, method=target.method,
                source_type=None, requested_url=target.url, canonical_url=canonical,
                final_url=None, acquisition_timestamp=timestamp,
                declared_metadata=DeclaredMetadata(), artifacts=(), original_metadata={},
                provenance=None,
                warnings=(f"Duplicate of target '{seen[source_id]}' in the same request.",),
            )
        seen[source_id] = target.target_id

        # 3. Reusable prior acquisition? (idempotency across retries/requests)
        reuse = self._reuse(job_id, source_id)
        if reuse is not None:
            provider_name, result = reuse
            return self._success(job_id, target, canonical, source_id, timestamp,
                                 provider_name, "reused", result,
                                 warnings=("Reused previously acquired, hash-verified artifacts.",))

        # 4. SSRF / URL validation.
        try:
            validated = self.guard.validate(canonical)
        except UrlValidationError as exc:
            code = "blocked_address" if exc.__class__.__name__ == "BlockedAddressError" else "invalid_url"
            return self._failed(job_id, target, target.url, canonical, timestamp,
                                AcquisitionError(code, str(exc), retryable=False))

        # 5. Provider selection.
        try:
            provider = self.registry.resolve(target.method, validated)
        except ProviderError as exc:
            return self._failed(job_id, target, target.url, canonical, timestamp,
                                AcquisitionError(exc.code, exc.message, exc.retryable))

        # 6. Acquire.
        fetcher = GuardedFetcher(
            self.guard, limits.max_bytes_per_source, limits.timeout_seconds,
            limits.max_redirects, client_factory=self.client_factory,
        )
        context = ProviderContext(
            job_id=job_id, target=target, validated_url=validated, limits=limits,
            store=self.store, guard=self.guard, fetcher=fetcher, cancel=cancel,
            source_id=source_id,
        )
        try:
            result = provider.acquire(context)
        except ProviderError as exc:
            return self._failed(job_id, target, target.url, canonical, timestamp,
                                AcquisitionError(exc.code, exc.message, exc.retryable),
                                source_type=None, provider=provider)
        except StoragePathError as exc:
            return self._failed(job_id, target, target.url, canonical, timestamp,
                                AcquisitionError("storage_error", str(exc), retryable=False),
                                provider=provider)

        self._write_reuse_sidecar(job_id, source_id, provider.name, result)
        return self._success(job_id, target, canonical, source_id, timestamp,
                             provider.name, provider.source_component, result)

    # -- result builders ---------------------------------------------------

    def _success(self, job_id, target, canonical, source_id, timestamp, provider_name,
                 source_component, result: ProviderResult, warnings=()) -> AcquiredSourceV1:
        artifacts = tuple(
            ArtifactRef(a.artifact_type, a.uri, a.sha256, a.bytes, a.media_type,
                        result_schema_for(result.source_type))
            for a in result.artifacts
        )
        provenance = Provenance(
            provider=provider_name, provider_version=getattr(result, "version", "0.1.0"),
            source_component=source_component or provider_name,
            requested_url=target.url, canonical_url=canonical, final_url=result.final_url,
            acquired_at=timestamp, request_target_id=target.target_id,
        )
        return AcquiredSourceV1(
            source_id=source_id, target_id=target.target_id, status=SourceStatus.ACQUIRED,
            method=target.method, source_type=result.source_type, requested_url=target.url,
            canonical_url=canonical, final_url=result.final_url, acquisition_timestamp=timestamp,
            declared_metadata=result.declared_metadata, artifacts=artifacts,
            original_metadata=dict(result.original_metadata), provenance=provenance,
            warnings=tuple(warnings) + tuple(result.warnings),
        )

    def _failed(self, job_id, target, requested_url, canonical, timestamp, error,
                source_type=None, provider=None) -> AcquiredSourceV1:
        source_id = hashlib.sha256(canonical.encode()).hexdigest()[:40]
        provenance = None
        if provider is not None:
            provenance = Provenance(
                provider=provider.name, provider_version=provider.version,
                source_component=provider.source_component or provider.name,
                requested_url=requested_url, canonical_url=canonical, final_url=None,
                acquired_at=timestamp, request_target_id=target.target_id,
            )
        return AcquiredSourceV1(
            source_id=source_id, target_id=target.target_id, status=SourceStatus.FAILED,
            method=target.method, source_type=source_type, requested_url=requested_url,
            canonical_url=canonical, final_url=None, acquisition_timestamp=timestamp,
            declared_metadata=DeclaredMetadata(), artifacts=(), original_metadata={},
            provenance=provenance, error=error,
        )

    # -- reuse / idempotency ----------------------------------------------

    def _sidecar_uri(self, job_id: str, source_id: str) -> str:
        return f"jobs/{job_id}/research/sources/{source_id}/{_REUSE_SIDECAR}"

    def _write_reuse_sidecar(self, job_id, source_id, provider_name, result: ProviderResult) -> None:
        payload = {
            "provider": provider_name,
            "source_type": result.source_type,
            "final_url": result.final_url,
            "artifacts": [
                {"artifact_type": a.artifact_type, "uri": a.uri, "sha256": a.sha256,
                 "bytes": a.bytes, "media_type": a.media_type}
                for a in result.artifacts
            ],
        }
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.store.write_bytes(job_id, self._sidecar_uri(job_id, source_id), data, "application/json")

    def _reuse(self, job_id, source_id):
        uri = self._sidecar_uri(job_id, source_id)
        try:
            if not self.store.exists(job_id, uri):
                return None
            payload = json.loads(self.store.read_text(job_id, uri))
        except (StoragePathError, json.JSONDecodeError, OSError):
            return None
        outputs: list[StoredOutput] = []
        for entry in payload.get("artifacts", []):
            file_uri = entry.get("uri", "")
            # Demonstrably reusable only if the bytes still exist and hash-match.
            try:
                if not self.store.exists(job_id, file_uri):
                    return None
                if self.store.sha256_of(job_id, file_uri) != entry.get("sha256"):
                    return None
            except StoragePathError:
                return None
            outputs.append(StoredOutput(
                artifact_type=entry["artifact_type"], uri=file_uri, sha256=entry["sha256"],
                bytes=int(entry.get("bytes", 0)), media_type=entry.get("media_type", ""),
            ))
        if not outputs:
            return None
        result = ProviderResult(
            source_type=payload.get("source_type") or "unknown",
            artifacts=tuple(outputs),
            declared_metadata=DeclaredMetadata(),
            original_metadata={"reused": True},
            final_url=payload.get("final_url"),
        )
        return payload.get("provider", "cache"), result


def result_schema_for(source_type: str | None) -> str:
    return "defence-studio.source-artifact.v1"
