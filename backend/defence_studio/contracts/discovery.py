"""Typed request/result contracts for Phase 1A Feed Discovery.

Discovery runs as the locked `news_feed_and_topic_discovery` worker stage
(`contracts/jobs.py`'s second stage, immediately before F3's
`topic_search_and_source_collection`). Its *input* artifact
(`discovery_request`) is produced by the owner against the predecessor stage
`application_foundation`; its *output* artifact (`discovery_result`) is the
set of article URLs the acquisition stage fetches. The owner may still
confirm a subset as an `acquisition_request` before that stage starts.
Discovery never acquires bytes itself and never marks a candidate as
acquired, verified, corroborated, or approved -- that remains F3/F5's job.

Design rules enforced by these shapes (mirrors F3's contracts/acquisition.py):

* A candidate is evidence to consider, never a verified fact. Source-declared
  metadata is preserved verbatim and labelled as declared.
* Unavailable metadata is explicitly distinguished from metadata that was
  simply not collected, via the same `MetadataStatus` tri-state F3 uses.
* Ranking factors are explicit and explainable; a missing date never becomes
  a fabricated one.
* One provider's failure is reported, never silently hidden or substituted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum

from defence_studio.contracts.acquisition import MetadataStatus

DISCOVERY_REQUEST_SCHEMA = "defence-studio.discovery-request.v1"
DISCOVERY_RESULT_SCHEMA = "defence-studio.discovery-result.v2"
DISCOVERY_RESULT_SCHEMA_V1 = "defence-studio.discovery-result.v1"  # still readable (additive v2)

# The locked pipeline stage this module executes under (see contracts/jobs.py).
# Its predecessor, `application_foundation`, carries the owner-submitted
# discovery_request artifact this stage consumes.
DISCOVERY_STAGE = "news_feed_and_topic_discovery"
DISCOVERY_PREDECESSOR_STAGE = "application_foundation"

SUPPORTED_PROVIDERS = ("rss_feed", "configured_search_api", "manual_url")
MAX_SEED_URLS = 20
SUPPORTED_LANGUAGES_HINT = ("en", "ta")  # documentation only; any 2-20 char tag is accepted


class ProviderStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    COMPLETED_NO_RESULTS = "COMPLETED_NO_RESULTS"
    UNAVAILABLE_MISSING_CREDENTIALS = "UNAVAILABLE_MISSING_CREDENTIALS"
    UNAVAILABLE_NOT_CONFIGURED = "UNAVAILABLE_NOT_CONFIGURED"
    UNAVAILABLE_OPTIONAL_DEPENDENCY_MISSING = "UNAVAILABLE_OPTIONAL_DEPENDENCY_MISSING"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveryLimits:
    """Per-request resource bounds, clamped by the service against hard
    ceilings (defence_studio/discovery/service.py); a request can only ask
    for the same or less."""

    max_results: int = 10
    max_feeds_per_provider: int = 5
    timeout_seconds: float = 20.0
    max_redirects: int = 5
    max_bytes_per_fetch: int = 2 * 1024 * 1024


@dataclass(frozen=True)
class DiscoveryFilters:
    """Optional, provider-genuine filters. A filter the selected providers do
    not actually support must not be silently ignored by the caller claiming
    it worked -- callers should check `ProviderOutcome` for what ran."""

    source_types: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()  # empty/absent means "all enabled providers"
    date_from: str | None = None  # ISO date (YYYY-MM-DD)
    date_to: str | None = None
    categories: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    source_classes: tuple[str, ...] = ()


VIDEO_FORMATS = ("standard", "short")


@dataclass(frozen=True)
class DiscoveryRequestV1:
    job_id: str
    topic: str
    language: str = "en"
    search_terms: tuple[str, ...] = ()
    filters: DiscoveryFilters = field(default_factory=DiscoveryFilters)
    limits: DiscoveryLimits = field(default_factory=DiscoveryLimits)
    schema_version: str = DISCOVERY_REQUEST_SCHEMA
    # Operator-supplied source URLs (manual_url provider); empty keeps old behaviour.
    seed_urls: tuple[str, ...] = ()
    # "short" (<= ~90 s video) selects the evidence policy's short readiness profile.
    video_format: str = "standard"


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderOutcome:
    provider: str
    status: ProviderStatus
    message: str
    candidates_returned: int
    requires_credentials: bool = False
    optional_dependency: bool = False
    duration_ms: int = 0
    http_attempts: int = 0
    rate_limited: bool = False
    filters_applied: tuple[str, ...] = ()
    filters_not_supported: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class RankingFactors:
    relevance_score: float
    recency_score: float
    language_match_score: float
    source_type_score: float
    total_score: float
    explanation: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeclaredCandidateMetadata:
    title: str | None = None
    title_status: MetadataStatus = MetadataStatus.NOT_COLLECTED
    publisher: str | None = None
    publisher_status: MetadataStatus = MetadataStatus.NOT_COLLECTED
    published_at: str | None = None
    published_at_status: MetadataStatus = MetadataStatus.NOT_COLLECTED
    language: str | None = None
    language_status: MetadataStatus = MetadataStatus.NOT_COLLECTED


@dataclass(frozen=True)
class DiscoveryCandidateV1:
    candidate_id: str  # stable SHA-256-derived dedup key over the canonical URL
    requested_url: str
    canonical_url: str
    source_type: str
    provider: str
    provider_version: str
    discovered_at: str
    declared_metadata: DeclaredCandidateMetadata
    ranking: RankingFactors
    original_metadata: dict
    merged_from_providers: tuple[str, ...] = ()  # other providers that returned the same canonical URL
    warnings: tuple[str, ...] = ()
    # --- v2 additive fields (spec §8) ---
    category: str = "UNCLASSIFIED"
    category_basis: str = "UNCLASSIFIED"
    regions: tuple[str, ...] = ()
    region_basis: str = "UNCLASSIFIED"
    source_class: str = "UNKNOWN"
    retrieved_at: str | None = None
    snippet: str | None = None
    provider_rank: int | None = None
    filter_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoveryResultV1:
    job_id: str
    topic: str
    language: str
    discovered_at: str
    candidates: tuple[DiscoveryCandidateV1, ...]
    providers: tuple[ProviderOutcome, ...]
    requested_limit: int
    returned: int
    status: str  # COMPLETED | COMPLETED_WITH_FAILURES | COMPLETED_NO_RESULTS
    dedup_notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: str = DISCOVERY_RESULT_SCHEMA


def _clean(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def result_to_dict(result: DiscoveryResultV1) -> dict:
    return _clean(asdict(result))


def request_to_dict(request: DiscoveryRequestV1) -> dict:
    return _clean(asdict(request))


class RequestValidationError(ValueError):
    """The discovery request payload is malformed, unsafe, or out of range."""


def request_from_dict(payload: dict, job_id: str | None = None) -> DiscoveryRequestV1:
    """Parse and validate a discovery request. Rejects malformed/unsafe input
    with a specific message rather than guessing a default; never rewrites
    the topic text."""
    if not isinstance(payload, dict):
        raise RequestValidationError("Discovery request must be a JSON object")

    effective_job_id = payload.get("job_id", job_id)
    if not isinstance(effective_job_id, str) or not effective_job_id.strip():
        raise RequestValidationError("Discovery request requires a string job_id")

    topic = payload.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise RequestValidationError("Discovery request requires a non-empty string topic")
    if len(topic) > 1000:
        raise RequestValidationError("topic must be at most 1000 characters")
    topic = topic.strip()

    language = payload.get("language", "en")
    if not isinstance(language, str) or not (2 <= len(language.strip()) <= 20):
        raise RequestValidationError("language must be a 2-20 character string")

    raw_terms = payload.get("search_terms", [])
    if not isinstance(raw_terms, list) or len(raw_terms) > 10:
        raise RequestValidationError("search_terms must be a list of at most 10 strings")
    terms = []
    for term in raw_terms:
        if not isinstance(term, str) or not term.strip() or len(term) > 200:
            raise RequestValidationError("Each search_term must be a non-empty string up to 200 characters")
        terms.append(term.strip())

    raw_filters = payload.get("filters") or {}
    if not isinstance(raw_filters, dict):
        raise RequestValidationError("filters must be an object")
    source_types = _string_tuple(raw_filters.get("source_types", []), "filters.source_types")
    providers = _string_tuple(raw_filters.get("providers", []), "filters.providers")
    for provider in providers:
        if provider not in SUPPORTED_PROVIDERS:
            raise RequestValidationError(f"Unsupported provider {provider!r}")
    date_from = _validate_date(raw_filters.get("date_from"), "filters.date_from")
    date_to = _validate_date(raw_filters.get("date_to"), "filters.date_to")
    if date_from and date_to and date_from > date_to:
        raise RequestValidationError("filters.date_from must not be after filters.date_to")
    from defence_studio.discovery import taxonomy as tx
    categories = _string_tuple(raw_filters.get("categories", []), "filters.categories")
    regions = _string_tuple(raw_filters.get("regions", []), "filters.regions")
    source_classes = _string_tuple(raw_filters.get("source_classes", []), "filters.source_classes")
    for value, allowed, name in (
        (categories, tx.CATEGORIES + (tx.UNCLASSIFIED,), "category"),
        (regions, tx.REGIONS, "region"),
        (source_classes, tx.SOURCE_CLASSES, "source class"),
    ):
        for item in value:
            if item not in allowed:
                raise RequestValidationError(f"Unknown {name} {item!r}")
    known = {"source_types", "providers", "date_from", "date_to", "categories", "regions", "source_classes"}
    unknown = sorted(set(raw_filters) - known)
    if unknown:
        raise RequestValidationError(f"Unknown filter key(s): {unknown}")
    filters = DiscoveryFilters(
        source_types=source_types, providers=providers, date_from=date_from, date_to=date_to,
        categories=categories, regions=regions, source_classes=source_classes,
    )

    raw_limits = payload.get("limits") or {}
    if not isinstance(raw_limits, dict):
        raise RequestValidationError("limits must be an object")
    defaults = DiscoveryLimits()
    try:
        limits = DiscoveryLimits(
            max_results=int(raw_limits.get("max_results", defaults.max_results)),
            max_feeds_per_provider=int(raw_limits.get("max_feeds_per_provider", defaults.max_feeds_per_provider)),
            timeout_seconds=float(raw_limits.get("timeout_seconds", defaults.timeout_seconds)),
            max_redirects=int(raw_limits.get("max_redirects", defaults.max_redirects)),
            max_bytes_per_fetch=int(raw_limits.get("max_bytes_per_fetch", defaults.max_bytes_per_fetch)),
        )
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(f"Invalid numeric limit: {exc}") from None
    if limits.max_results <= 0:
        raise RequestValidationError("limits.max_results must be a positive integer")

    raw_seeds = payload.get("seed_urls", [])
    if not isinstance(raw_seeds, list) or len(raw_seeds) > MAX_SEED_URLS:
        raise RequestValidationError(f"seed_urls must be a list of at most {MAX_SEED_URLS} URLs")
    from defence_studio.acquisition.url_guard import UrlValidationError, normalize_url
    seeds: list[str] = []
    for item in raw_seeds:
        if not isinstance(item, str) or len(item) > 2000:
            raise RequestValidationError("Each seed URL must be a string of at most 2000 characters")
        try:
            norm = normalize_url(item)
        except UrlValidationError as exc:
            raise RequestValidationError(f"Invalid seed URL: {exc}") from None
        if not norm.lower().startswith(("http://", "https://")):
            raise RequestValidationError("Seed URLs must use http or https")
        if norm not in seeds:
            seeds.append(norm)

    video_format = payload.get("video_format", "standard")
    if video_format not in VIDEO_FORMATS:
        raise RequestValidationError(f"video_format must be one of {list(VIDEO_FORMATS)}")

    return DiscoveryRequestV1(
        job_id=effective_job_id, topic=topic, language=language.strip(),
        search_terms=tuple(terms), filters=filters, limits=limits, seed_urls=tuple(seeds),
        video_format=video_format,
    )


def _string_tuple(value, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise RequestValidationError(f"{field_name} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _validate_date(value, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestValidationError(f"{field_name} must be an ISO date string (YYYY-MM-DD)")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise RequestValidationError(f"{field_name} is not a valid ISO date: {exc}") from None
    return value
