"""Discovery provider interface and implementations.

Mirrors F3's `acquisition/provider.py` registry pattern (register/get/names)
but narrowed to discovery's job: a provider looks for *candidate* sources for
a topic, never acquires full content. Every provider must report an honest
`ProviderOutcome` -- `OK`, `PARTIAL`, `UNAVAILABLE_*`, or `ERROR` -- and must
never fabricate a candidate it did not actually receive from a real source.

Two providers ship:

* `rss_feed` -- a *working* provider requiring no credentials. It reads a
  configured list of RSS/Atom feed URLs (one set per language) from
  `DEFENCE_STUDIO_DISCOVERY_RSS_FEEDS` and filters entries by topic/search-term
  keyword match. If no feeds are configured for the requested language it
  reports `UNAVAILABLE_NOT_CONFIGURED` rather than returning nothing silently.
* `configured_search_api` -- a provider *requiring credentials*. Without
  `DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY` / `..._URL` configured it reports
  `UNAVAILABLE_MISSING_CREDENTIALS` and makes no network call. When configured
  it expects the endpoint to return `{"results": [{"title", "url", "snippet",
  "published_at"}, ...]}` and performs one guarded GET through the same SSRF
  guard F3 uses -- no new fetch path, no new vulnerability surface.

All network access goes through `defence_studio.acquisition.http_client` and
`url_guard`, reusing F3's SSRF protection rather than re-implementing it.
"""

from __future__ import annotations

import json
import os
import threading
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from defence_studio.acquisition.http_client import GuardedFetcher, HttpFetchError
from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError
from defence_studio.contracts.discovery import DiscoveryFilters, DiscoveryLimits, ProviderOutcome, ProviderStatus

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class RawCandidate:
    requested_url: str
    title: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    language: str | None = None
    source_type: str = "news_feed"
    original_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryContext:
    job_id: str
    topic: str
    search_terms: tuple[str, ...]
    language: str
    filters: DiscoveryFilters
    limits: DiscoveryLimits
    guard: UrlGuard
    cancel: threading.Event
    client_factory = None


class DiscoveryProvider(ABC):
    name: str
    version: str = "0.1.0"
    requires_credentials: bool = False
    optional_dependency: bool = False

    @abstractmethod
    def discover(self, context: DiscoveryContext) -> tuple[ProviderOutcome, list[RawCandidate]]:
        """Return an honest outcome plus whatever real candidates were found."""


class DiscoveryProviderRegistry:
    def __init__(self):
        self._providers: dict[str, DiscoveryProvider] = {}

    def register(self, provider: DiscoveryProvider) -> DiscoveryProvider:
        self._providers[provider.name] = provider
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)

    def get(self, name: str) -> DiscoveryProvider | None:
        return self._providers.get(name)

    def enabled(self, requested: tuple[str, ...]) -> list[DiscoveryProvider]:
        """Providers to run for a request: the explicit subset if given,
        otherwise every registered provider."""
        if not requested:
            return [self._providers[name] for name in self.names()]
        return [self._providers[name] for name in requested if name in self._providers]


def _matches_topic(text: str, topic: str, search_terms: tuple[str, ...]) -> bool:
    haystack = text.lower()
    needles = [topic.lower()] + [t.lower() for t in search_terms]
    return any(needle in haystack for needle in needles if needle)


class RssFeedProvider(DiscoveryProvider):
    name = "rss_feed"
    requires_credentials = False

    def __init__(self, feeds_by_language: dict[str, tuple[str, ...]] | None = None, client_factory=None):
        self._feeds_by_language = feeds_by_language if feeds_by_language is not None else _feeds_from_env()
        self._client_factory = client_factory

    def discover(self, context: DiscoveryContext) -> tuple[ProviderOutcome, list[RawCandidate]]:
        feeds = self._feeds_by_language.get(context.language, ())
        if not feeds:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.UNAVAILABLE_NOT_CONFIGURED,
                message=(
                    f"No RSS/Atom feed URLs configured for language {context.language!r}; "
                    "set DEFENCE_STUDIO_DISCOVERY_RSS_FEEDS (e.g. 'en:https://example.com/feed.xml,"
                    "ta:https://example.com/ta-feed.xml')."
                ),
                candidates_returned=0,
            ), []

        fetcher = GuardedFetcher(
            context.guard, context.limits.max_bytes_per_fetch, context.limits.timeout_seconds,
            context.limits.max_redirects, client_factory=self._client_factory,
        )
        candidates: list[RawCandidate] = []
        errors: list[str] = []
        feeds = feeds[: context.limits.max_feeds_per_provider]
        for feed_url in feeds:
            if context.cancel.is_set():
                errors.append("Discovery cancelled before all feeds were read.")
                break
            try:
                response = fetcher.get(feed_url, cancel=context.cancel)
            except HttpFetchError as exc:
                errors.append(f"{feed_url}: {exc.message}")
                continue
            except UrlValidationError as exc:
                errors.append(f"{feed_url}: {exc}")
                continue
            try:
                entries = _parse_feed(response.content)
            except ET.ParseError as exc:
                errors.append(f"{feed_url}: could not parse feed XML: {exc}")
                continue
            for entry in entries:
                haystack = f"{entry.get('title') or ''} {entry.get('summary') or ''}"
                if not _matches_topic(haystack, context.topic, context.search_terms):
                    continue
                link = entry.get("link")
                if not link:
                    continue
                candidates.append(RawCandidate(
                    requested_url=link,
                    title=entry.get("title"),
                    publisher=entry.get("source") or feed_url,
                    published_at=entry.get("published_at"),
                    language=context.language,
                    source_type="news_feed",
                    original_metadata=entry,
                ))

        if not candidates and errors and len(errors) == len(feeds):
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.ERROR,
                message="All configured feeds failed: " + "; ".join(errors),
                candidates_returned=0,
            ), []
        if errors:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.PARTIAL,
                message=f"{len(errors)} of {len(feeds)} feed(s) failed: " + "; ".join(errors),
                candidates_returned=len(candidates),
            ), candidates
        if not candidates:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.COMPLETED_NO_RESULTS,
                message="Feeds were read successfully but no entry matched the topic/search terms.",
                candidates_returned=0,
            ), []
        return ProviderOutcome(
            provider=self.name, status=ProviderStatus.OK,
            message=f"Matched {len(candidates)} entr{'y' if len(candidates) == 1 else 'ies'} across {len(feeds)} feed(s).",
            candidates_returned=len(candidates),
        ), candidates


class ConfiguredSearchApiProvider(DiscoveryProvider):
    name = "configured_search_api"
    requires_credentials = True

    def __init__(self, api_url: str | None = None, api_key: str | None = None, client_factory=None):
        self._api_url = api_url if api_url is not None else os.environ.get("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL", "")
        self._api_key = api_key if api_key is not None else os.environ.get("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY", "")
        self._client_factory = client_factory

    def discover(self, context: DiscoveryContext) -> tuple[ProviderOutcome, list[RawCandidate]]:
        if not self._api_url or not self._api_key:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.UNAVAILABLE_MISSING_CREDENTIALS,
                message=(
                    "Set DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL and "
                    "DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY to enable this provider."
                ),
                candidates_returned=0, requires_credentials=True,
            ), []

        fetcher = GuardedFetcher(
            context.guard, context.limits.max_bytes_per_fetch, context.limits.timeout_seconds,
            context.limits.max_redirects, client_factory=self._client_factory,
        )
        query = " ".join([context.topic, *context.search_terms]).strip()
        separator = "&" if "?" in self._api_url else "?"
        url = f"{self._api_url}{separator}q={_url_quote(query)}&lang={_url_quote(context.language)}"
        try:
            response = fetcher.get(url, cancel=context.cancel)
        except (HttpFetchError, UrlValidationError) as exc:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.ERROR,
                message=f"Search API request failed: {exc}",
                candidates_returned=0, requires_credentials=True,
            ), []
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.ERROR,
                message=f"Search API response was not valid JSON: {exc}",
                candidates_returned=0, requires_credentials=True,
            ), []
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.ERROR,
                message="Search API response did not contain a 'results' list.",
                candidates_returned=0, requires_credentials=True,
            ), []
        candidates = []
        for item in results:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            candidates.append(RawCandidate(
                requested_url=item["url"],
                title=item.get("title"),
                publisher=item.get("publisher") or self._api_url,
                published_at=item.get("published_at"),
                language=context.language,
                source_type="search_result",
                original_metadata=item,
            ))
        if not candidates:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.COMPLETED_NO_RESULTS,
                message="The configured search API returned no results for this query.",
                candidates_returned=0, requires_credentials=True,
            ), []
        return ProviderOutcome(
            provider=self.name, status=ProviderStatus.OK,
            message=f"Search API returned {len(candidates)} result(s).",
            candidates_returned=len(candidates), requires_credentials=True,
        ), candidates


def _url_quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


def _feeds_from_env() -> dict[str, tuple[str, ...]]:
    raw = os.environ.get("DEFENCE_STUDIO_DISCOVERY_RSS_FEEDS", "")
    feeds: dict[str, list[str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        lang, _, url = entry.partition(":")
        lang = lang.strip()
        url = url.strip()
        if not lang or not url:
            continue
        feeds.setdefault(lang, []).append(url)
    return {lang: tuple(urls) for lang, urls in feeds.items()}


def _parse_feed(content: bytes) -> list[dict]:
    root = ET.fromstring(content)
    entries: list[dict] = []
    channel_title = None
    channel = root.find("channel")
    if channel is not None:
        title_el = channel.find("title")
        channel_title = title_el.text if title_el is not None else None
        for item in channel.findall("item"):
            entries.append(_rss_item(item, channel_title))
        return entries
    if root.tag == f"{_ATOM_NS}feed" or root.tag == "feed":
        for entry in root.findall(f"{_ATOM_NS}entry") or root.findall("entry"):
            entries.append(_atom_entry(entry))
        return entries
    return entries


def _text(el, tag: str) -> str | None:
    found = el.find(tag)
    return found.text.strip() if found is not None and found.text else None


def _rss_item(item, channel_title) -> dict:
    return {
        "title": _text(item, "title"),
        "link": _text(item, "link"),
        "summary": _text(item, "description"),
        "published_at": _text(item, "pubDate"),
        "source": channel_title,
    }


def _atom_entry(entry) -> dict:
    link_el = entry.find(f"{_ATOM_NS}link")
    if link_el is None:
        link_el = entry.find("link")
    href = link_el.get("href") if link_el is not None else None
    return {
        "title": _text(entry, f"{_ATOM_NS}title") or _text(entry, "title"),
        "link": href,
        "summary": _text(entry, f"{_ATOM_NS}summary") or _text(entry, "summary"),
        "published_at": _text(entry, f"{_ATOM_NS}updated") or _text(entry, "updated")
        or _text(entry, f"{_ATOM_NS}published") or _text(entry, "published"),
        "source": None,
    }


def default_registry(client_factory=None) -> DiscoveryProviderRegistry:
    registry = DiscoveryProviderRegistry()
    registry.register(RssFeedProvider(client_factory=client_factory))
    registry.register(ConfiguredSearchApiProvider(client_factory=client_factory))
    return registry
