"""Discovery orchestration service.

Runs a `DiscoveryRequestV1` across its enabled providers and returns a
`DiscoveryResultV1`. Design guarantees mirror F3's acquisition service:

* Per-provider isolation: one provider failing (missing credentials, network
  error, malformed response) never blocks the others; the result reports an
  honest `ProviderOutcome` per provider plus explicit partial success.
* Canonical-URL deduplication only: two providers returning the same exact
  URL are merged into one candidate (provenance of all contributing
  providers preserved); candidates with merely similar titles from different
  publishers are never merged.
* Deterministic, explainable ranking (defence_studio.discovery.ranking);
  ties broken by candidate_id so repeat runs over the same inputs order
  identically.
* Clamped limits: a request may only ask for the same or less than the hard
  ceilings.
* Nothing is fabricated: a candidate exists only if some provider actually
  returned it.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError, normalize_url
from defence_studio.contracts.acquisition import MetadataStatus
from defence_studio.contracts.discovery import (
    DeclaredCandidateMetadata,
    DiscoveryCandidateV1,
    DiscoveryLimits,
    DiscoveryRequestV1,
    DiscoveryResultV1,
)
from defence_studio.discovery import ranking as ranking_mod
from defence_studio.discovery import taxonomy as tx
from defence_studio.discovery.providers import DiscoveryContext, DiscoveryProviderRegistry, default_registry

HARD_MAX_RESULTS = 50
HARD_MAX_FEEDS_PER_PROVIDER = 10
HARD_TIMEOUT_SECONDS = 60.0
HARD_MAX_REDIRECTS = 10
HARD_MAX_BYTES_PER_FETCH = 10 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def clamp_limits(limits: DiscoveryLimits) -> DiscoveryLimits:
    return DiscoveryLimits(
        max_results=max(1, min(limits.max_results, HARD_MAX_RESULTS)),
        max_feeds_per_provider=max(1, min(limits.max_feeds_per_provider, HARD_MAX_FEEDS_PER_PROVIDER)),
        timeout_seconds=max(0.1, min(limits.timeout_seconds, HARD_TIMEOUT_SECONDS)),
        max_redirects=max(0, min(limits.max_redirects, HARD_MAX_REDIRECTS)),
        max_bytes_per_fetch=max(1, min(limits.max_bytes_per_fetch, HARD_MAX_BYTES_PER_FETCH)),
    )


class DiscoveryService:
    def __init__(self, guard: UrlGuard | None = None, registry: DiscoveryProviderRegistry | None = None,
                 client_factory=None):
        self.guard = guard or UrlGuard()
        self.registry = registry or default_registry(client_factory=client_factory)

    def run(self, request: DiscoveryRequestV1, cancel: threading.Event | None = None) -> DiscoveryResultV1:
        cancel = cancel or threading.Event()
        limits = clamp_limits(request.limits)
        providers = self.registry.enabled(request.filters.providers)
        if not request.filters.providers:
            # manual_url runs only for seeded requests (alone, by default); requests
            # without seed_urls behave exactly as before.
            providers = ([p for p in providers if p.name == "manual_url"] if request.seed_urls
                         else [p for p in providers if p.name != "manual_url"])
        outcomes = []
        merged: dict[str, DiscoveryCandidateV1] = {}
        warnings: list[str] = []
        dedup_notes: list[str] = []
        discovered_at = _now_iso()

        if not providers:
            warnings.append("No discovery providers matched the requested provider filter.")

        for provider in providers:
            if cancel.is_set():
                warnings.append("Discovery cancelled before all providers ran.")
                break
            context = DiscoveryContext(
                job_id=request.job_id, topic=request.topic, search_terms=request.search_terms,
                language=request.language, filters=request.filters, limits=limits,
                guard=self.guard, cancel=cancel, seed_urls=request.seed_urls,
            )
            try:
                outcome, raw_candidates = provider.discover(context)
            except Exception as exc:  # a provider must never abort the whole run
                from defence_studio.contracts.discovery import ProviderOutcome, ProviderStatus
                outcome = ProviderOutcome(
                    provider=provider.name, status=ProviderStatus.ERROR,
                    message=f"Unhandled provider error: {type(exc).__name__}: {exc}",
                    candidates_returned=0, requires_credentials=provider.requires_credentials,
                    optional_dependency=provider.optional_dependency,
                )
                raw_candidates = []
            outcomes.append(outcome)

            for raw in raw_candidates:
                self._merge_candidate(
                    merged, raw, provider.name, provider.version, discovered_at,
                    request, warnings, dedup_notes,
                )

        candidates = sorted(
            merged.values(),
            key=lambda c: (-c.ranking.total_score, c.candidate_id),
        )
        requested_limit = limits.max_results
        truncated = candidates[:requested_limit]
        if len(candidates) > requested_limit:
            warnings.append(f"{len(candidates)} candidates found; returning the top {requested_limit}.")

        ran_ok = any(o.status.value in ("OK", "PARTIAL", "COMPLETED_NO_RESULTS") for o in outcomes)
        failed_any = any(o.status.value in ("ERROR",) for o in outcomes)
        if not truncated and ran_ok and not failed_any:
            status = "COMPLETED_NO_RESULTS"
        elif failed_any and truncated:
            status = "COMPLETED_WITH_FAILURES"
        elif failed_any and not truncated:
            status = "COMPLETED_WITH_FAILURES"
        else:
            status = "COMPLETED"

        return DiscoveryResultV1(
            job_id=request.job_id, topic=request.topic, language=request.language,
            discovered_at=discovered_at, candidates=tuple(truncated), providers=tuple(outcomes),
            requested_limit=requested_limit, returned=len(truncated), status=status,
            dedup_notes=tuple(dedup_notes), warnings=tuple(warnings),
        )

    def _merge_candidate(self, merged, raw, provider_name, provider_version, discovered_at,
                         request: DiscoveryRequestV1, warnings, dedup_notes):
        try:
            canonical = normalize_url(raw.requested_url)
        except UrlValidationError as exc:
            warnings.append(f"{provider_name}: discarded candidate with invalid URL: {exc}")
            return
        candidate_id = hashlib.sha256(canonical.encode()).hexdigest()[:40]

        if request.filters.date_from or request.filters.date_to:
            published_date = ranking_mod.parse_date(raw.published_at) if raw.published_at else None
            if published_date is not None:
                if request.filters.date_from and published_date.isoformat() < request.filters.date_from:
                    return
                if request.filters.date_to and published_date.isoformat() > request.filters.date_to:
                    return

        if request.filters.source_types and raw.source_type not in request.filters.source_types:
            return

        category, category_basis = tx.classify(raw.title, raw.snippet, raw.feed_category, raw.provider_tag)
        regions, region_basis = tx.classify_regions(raw.title, raw.snippet, raw.feed_region)
        source_class = raw.feed_source_class or "UNKNOWN"
        flags: list[str] = []
        f = request.filters
        # Server-side taxonomy filters. A candidate whose metadata cannot be
        # determined is kept and flagged (never silently dropped or kept).
        if f.categories:
            if category == tx.UNCLASSIFIED and tx.UNCLASSIFIED not in f.categories:
                flags.append("category_filter_unverifiable")
            elif category not in f.categories:
                return
        if f.regions:
            if not regions:
                flags.append("region_filter_unverifiable")
            elif not set(regions) & set(f.regions):
                return
        if f.source_classes:
            if source_class == "UNKNOWN" and "UNKNOWN" not in f.source_classes:
                flags.append("source_class_filter_unverifiable")
            elif source_class not in f.source_classes:
                return
        if (f.date_from or f.date_to) and not raw.published_at:
            flags.append("date_filter_unverifiable")

        factors = ranking_mod.score_candidate(
            title=raw.title, published_at=raw.published_at, candidate_language=raw.language,
            candidate_type=raw.source_type, topic=request.topic, search_terms=request.search_terms,
            requested_language=request.language, preferred_source_types=request.filters.source_types,
        )

        if candidate_id in merged:
            existing = merged[candidate_id]
            if provider_name not in existing.merged_from_providers and provider_name != existing.provider:
                dedup_notes.append(
                    f"{canonical} seen from both '{existing.provider}' and '{provider_name}'; "
                    f"kept the '{existing.provider}' record."
                )
                merged[candidate_id] = DiscoveryCandidateV1(
                    **{**existing.__dict__, "merged_from_providers": existing.merged_from_providers + (provider_name,)}
                )
            return

        # Near-duplicate: same publisher, same normalized title, same day
        # (spec §9). Merge, never delete; syndication across publishers is F4's job.
        near_key = _near_key(raw.title, raw.publisher, raw.published_at)
        if near_key is not None:
            for existing in merged.values():
                meta = existing.declared_metadata
                if _near_key(meta.title, meta.publisher, meta.published_at) == near_key:
                    dedup_notes.append(
                        f"{canonical} merged into {existing.canonical_url}: same publisher, title and day."
                    )
                    merged[existing.candidate_id] = DiscoveryCandidateV1(**{
                        **existing.__dict__,
                        "warnings": existing.warnings + (f"near-duplicate URL merged: {canonical}",),
                    })
                    return

        merged[candidate_id] = DiscoveryCandidateV1(
            candidate_id=candidate_id, requested_url=raw.requested_url, canonical_url=canonical,
            source_type=raw.source_type, provider=provider_name, provider_version=provider_version,
            discovered_at=discovered_at,
            declared_metadata=DeclaredCandidateMetadata(
                title=raw.title, title_status=MetadataStatus.COLLECTED if raw.title else MetadataStatus.UNAVAILABLE,
                publisher=raw.publisher,
                publisher_status=MetadataStatus.COLLECTED if raw.publisher else MetadataStatus.UNAVAILABLE,
                published_at=raw.published_at,
                published_at_status=MetadataStatus.COLLECTED if raw.published_at else MetadataStatus.UNAVAILABLE,
                language=raw.language,
                language_status=MetadataStatus.COLLECTED if raw.language else MetadataStatus.UNAVAILABLE,
            ),
            ranking=factors, original_metadata=dict(raw.original_metadata),
            category=category, category_basis=category_basis, regions=tuple(regions),
            region_basis=region_basis, source_class=source_class, retrieved_at=discovered_at,
            snippet=raw.snippet, provider_rank=raw.provider_rank, filter_flags=tuple(flags),
        )


def _near_key(title, publisher, published_at):
    if not title or not publisher or not published_at or publisher.startswith("http"):
        return None  # a URL fallback is not a declared publisher
    parsed = ranking_mod.parse_date(published_at)
    if parsed is None:
        return None
    import re
    norm = re.sub(r"[^\w]+", " ", title.lower(), flags=re.UNICODE).strip()
    return (norm, publisher.strip().lower(), parsed.isoformat()) if norm else None
