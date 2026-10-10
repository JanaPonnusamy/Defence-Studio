"""Typed request/result contracts for F3 Content Acquisition.

These sit on top of the locked F1/F2 job, stage and artifact model. Acquisition
runs as the `topic_search_and_source_collection` worker stage: an approved
upstream request (stored as an artifact of a completed predecessor stage) names
explicit targets, and the stage acquires real bytes with traceable provenance.

Design rules enforced by these shapes:

* Acquisition collects *evidence*, never verified facts. Source-declared
  metadata (title, publisher, publication time, language) is preserved verbatim
  and labelled as declared, never promoted to truth here.
* Unavailable metadata (the source genuinely does not provide it) is explicitly
  distinguished from metadata that was simply not collected, via
  `MetadataStatus` rather than a bare ``None``.
* Nothing is fabricated: a field that was not supplied stays absent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


ACQUISITION_REQUEST_SCHEMA = "defence-studio.acquisition-request.v1"
ACQUISITION_RESULT_SCHEMA = "defence-studio.acquisition-result.v1"

# The locked pipeline stage this module executes under (see contracts/jobs.py).
ACQUISITION_STAGE = "topic_search_and_source_collection"

# Acquisition methods supported by registered providers. `auto` lets the service
# pick a provider from the normalized URL; an explicit method pins one provider
# and fails closed rather than silently substituting another.
SUPPORTED_METHODS = ("auto", "http_page", "youtube_subtitles")


class MetadataStatus(str, Enum):
    """Why a declared-metadata field has (or lacks) a value.

    COLLECTED      -> the source supplied it and it is present.
    UNAVAILABLE    -> the source was reached but genuinely did not supply it.
    NOT_COLLECTED  -> acquisition did not reach the point of collecting it
                      (e.g. the download failed first).
    """

    COLLECTED = "COLLECTED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_COLLECTED = "NOT_COLLECTED"


class SourceStatus(str, Enum):
    ACQUIRED = "ACQUIRED"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionLimits:
    """Per-request resource bounds. Values are clamped by the service against
    hard ceilings; a request can only ask for the same or less."""

    max_bytes_per_source: int = 25 * 1024 * 1024
    timeout_seconds: float = 30.0
    max_sources: int = 20
    max_redirects: int = 5


@dataclass(frozen=True)
class AcquisitionTarget:
    """One approved acquisition target.

    `target_id` is the caller-stable dedup/idempotency identifier for this entry
    in the request; repeating a request with the same `target_id` and URL must
    not re-download if a reusable artifact already exists.
    """

    target_id: str
    url: str
    method: str = "auto"
    language_preferences: tuple[str, ...] = ()
    # Optional hint from the approved upstream package. Recorded as provenance,
    # never treated as the authoritative source title.
    title_hint: str | None = None


@dataclass(frozen=True)
class AcquisitionRequestV1:
    job_id: str
    targets: tuple[AcquisitionTarget, ...]
    project_id: str | None = None
    limits: AcquisitionLimits = field(default_factory=AcquisitionLimits)
    schema_version: str = ACQUISITION_REQUEST_SCHEMA


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRef:
    """A reference to real bytes written under ``jobs/<job_id>/``.

    Unlike the F1 reference boundary, these are produced only after the bytes
    exist on disk and `sha256` is computed over those exact bytes.
    """

    artifact_type: str
    uri: str
    sha256: str
    bytes: int
    media_type: str
    schema_version: str


@dataclass(frozen=True)
class AcquisitionError:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class DeclaredMetadata:
    """Source-declared, unverified metadata plus its collection status.

    A value of ``None`` is always paired with a status explaining whether the
    source withheld it (UNAVAILABLE) or acquisition never got to it
    (NOT_COLLECTED).
    """

    title: str | None = None
    title_status: MetadataStatus = MetadataStatus.NOT_COLLECTED
    publisher: str | None = None
    publisher_status: MetadataStatus = MetadataStatus.NOT_COLLECTED
    published_at: str | None = None
    published_at_status: MetadataStatus = MetadataStatus.NOT_COLLECTED
    language: str | None = None
    language_status: MetadataStatus = MetadataStatus.NOT_COLLECTED


@dataclass(frozen=True)
class Provenance:
    provider: str
    provider_version: str
    source_component: str
    requested_url: str
    canonical_url: str
    final_url: str | None
    acquired_at: str
    request_target_id: str


@dataclass(frozen=True)
class AcquiredSourceV1:
    source_id: str
    target_id: str
    status: SourceStatus
    method: str
    source_type: str | None
    requested_url: str
    canonical_url: str
    final_url: str | None
    acquisition_timestamp: str
    declared_metadata: DeclaredMetadata
    artifacts: tuple[ArtifactRef, ...]
    original_metadata: dict
    provenance: Provenance | None
    warnings: tuple[str, ...] = ()
    error: AcquisitionError | None = None


@dataclass(frozen=True)
class AcquisitionResultV1:
    job_id: str
    acquired_at: str
    sources: tuple[AcquiredSourceV1, ...]
    requested: int
    succeeded: int
    failed: int
    skipped_duplicate: int
    warnings: tuple[str, ...] = ()
    schema_version: str = ACQUISITION_RESULT_SCHEMA


def _clean(value):
    """asdict() post-processing: Enums -> their value; drop nothing else so the
    absent-vs-unavailable distinction survives serialization."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def result_to_dict(result: AcquisitionResultV1) -> dict:
    return _clean(asdict(result))


class RequestValidationError(ValueError):
    """The stored acquisition request is malformed."""


def request_from_dict(payload: dict) -> AcquisitionRequestV1:
    """Parse and validate a stored acquisition-request document.

    Rejects malformed shapes rather than guessing. Unknown methods are caught
    here so a bad request fails before any network access.
    """
    if not isinstance(payload, dict):
        raise RequestValidationError("Acquisition request must be a JSON object")
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise RequestValidationError("Acquisition request requires a string job_id")
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise RequestValidationError("Acquisition request requires a non-empty targets list")

    targets: list[AcquisitionTarget] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, dict):
            raise RequestValidationError(f"Target {index} must be an object")
        target_id = raw.get("target_id")
        url = raw.get("url")
        if not isinstance(target_id, str) or not target_id.strip():
            raise RequestValidationError(f"Target {index} requires a string target_id")
        if target_id in seen_ids:
            raise RequestValidationError(f"Duplicate target_id {target_id!r}")
        seen_ids.add(target_id)
        if not isinstance(url, str) or not url.strip():
            raise RequestValidationError(f"Target {target_id} requires a string url")
        method = raw.get("method", "auto")
        if method not in SUPPORTED_METHODS:
            raise RequestValidationError(f"Target {target_id} has unsupported method {method!r}")
        langs = raw.get("language_preferences", [])
        if not isinstance(langs, list) or not all(isinstance(item, str) for item in langs):
            raise RequestValidationError(f"Target {target_id} language_preferences must be strings")
        targets.append(AcquisitionTarget(
            target_id=target_id, url=url, method=method,
            language_preferences=tuple(langs),
            title_hint=raw.get("title_hint") if isinstance(raw.get("title_hint"), str) else None,
        ))

    raw_limits = payload.get("limits") or {}
    if not isinstance(raw_limits, dict):
        raise RequestValidationError("limits must be an object")
    defaults = AcquisitionLimits()
    try:
        limits = AcquisitionLimits(
            max_bytes_per_source=int(raw_limits.get("max_bytes_per_source", defaults.max_bytes_per_source)),
            timeout_seconds=float(raw_limits.get("timeout_seconds", defaults.timeout_seconds)),
            max_sources=int(raw_limits.get("max_sources", defaults.max_sources)),
            max_redirects=int(raw_limits.get("max_redirects", defaults.max_redirects)),
        )
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(f"Invalid numeric limit: {exc}") from None

    project_id = payload.get("project_id")
    return AcquisitionRequestV1(
        job_id=job_id, targets=tuple(targets),
        project_id=project_id if isinstance(project_id, str) else None,
        limits=limits,
    )
