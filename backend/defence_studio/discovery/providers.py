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

import hashlib
import json
import os
import random
import re
import threading
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from defence_studio.acquisition.http_client import GuardedFetcher, HttpFetchError
from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError, normalize_url
from defence_studio.contracts.discovery import DiscoveryFilters, DiscoveryLimits, ProviderOutcome, ProviderStatus
from defence_studio.discovery.taxonomy import FeedConfig, load_feed_configs

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
    snippet: str | None = None
    feed_category: str | None = None
    feed_source_class: str | None = None
    feed_region: str | None = None
    provider_tag: str | None = None
    provider_rank: int | None = None


MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = (0.5, 1.0)
SNIPPET_MAX = 500


def fetch_with_retry(fetcher, url, cancel, sleep=time.sleep, max_retries=MAX_RETRIES):
    """Idempotent GET with bounded retry: only connection errors / 5xx / 429
    (``HttpFetchError.retryable``) are retried; 4xx are not. Returns
    ``(response, attempts, rate_limited)``; on failure raises the last
    HttpFetchError with ``attempts`` / ``rate_limited`` attached."""
    attempts = 0
    rate_limited = False
    while True:
        attempts += 1
        try:
            return fetcher.get(url, cancel=cancel), attempts, rate_limited
        except HttpFetchError as exc:
            if "429" in exc.message:
                rate_limited = True
            exc.attempts, exc.rate_limited = attempts, rate_limited
            cancelled = exc.code == "cancelled" or (cancel is not None and cancel.is_set())
            if cancelled or not exc.retryable or attempts > max_retries:
                raise
            delay = RETRY_BACKOFF_SECONDS[min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            sleep(delay + random.uniform(0, delay / 4))


def _clip(text, limit=SNIPPET_MAX):
    if not text:
        return None
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit] or None


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
    seed_urls: tuple[str, ...] = ()


class DiscoveryProvider(ABC):
    name: str
    version: str = "0.1.0"
    requires_credentials: bool = False
    optional_dependency: bool = False

    @abstractmethod
    def discover(self, context: DiscoveryContext) -> tuple[ProviderOutcome, list[RawCandidate]]:
        """Return an honest outcome plus whatever real candidates were found."""

    def describe(self) -> dict:
        """Static, secret-free descriptor for GET /api/discovery/providers."""
        return {"name": self.name, "configured": True, "requires_credentials": self.requires_credentials,
                "supported_filters": [], "status_message": ""}


class DiscoveryProviderRegistry:
    def __init__(self):
        self._providers: dict[str, DiscoveryProvider] = {}

    def register(self, provider: DiscoveryProvider) -> DiscoveryProvider:
        self._providers[provider.name] = provider
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)

    def describe_all(self) -> list[dict]:
        return [self._providers[name].describe() for name in self.names()]

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

    def __init__(self, feeds_by_language: dict[str, tuple[str, ...]] | None = None, client_factory=None,
                 feed_configs: list[FeedConfig] | None = None, sleep=time.sleep):
        self._sleep = sleep
        configs: list[FeedConfig] = []
        if feeds_by_language is not None:
            for lang, urls in feeds_by_language.items():
                configs.extend(FeedConfig(url=url, language=lang) for url in urls)
        elif feed_configs is not None:
            configs = list(feed_configs)
        else:
            configs = load_feed_configs()
            known = {c.url for c in configs}
            for lang, urls in _feeds_from_env().items():
                configs.extend(FeedConfig(url=u, language=lang) for u in urls if u not in known)
        self._configs_by_language: dict[str, list[FeedConfig]] = {}
        for config in configs:
            self._configs_by_language.setdefault(config.language, []).append(config)
        self._client_factory = client_factory

    def describe(self) -> dict:
        count = sum(len(v) for v in self._configs_by_language.values())
        return {
            "name": self.name, "kind": "rss", "configured": count > 0, "requires_credentials": False,
            "supported_filters": ["language", "topic", "date_from", "date_to", "source_types",
                                  "categories", "regions", "source_classes"],
            "rate_limit": "sequential requests, bounded retries",
            "feeds_configured": count, "languages": sorted(self._configs_by_language),
            "status_message": (f"{count} feed(s) configured." if count else
                               "No feeds configured: set DEFENCE_STUDIO_DISCOVERY_CONFIG or DEFENCE_STUDIO_DISCOVERY_RSS_FEEDS."),
        }

    def discover(self, context: DiscoveryContext) -> tuple[ProviderOutcome, list[RawCandidate]]:
        started = time.monotonic()
        attempts_total = 0
        rate_limited = False
        feed_configs = self._configs_by_language.get(context.language, [])
        feeds = tuple(c.url for c in feed_configs)
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
        feed_configs = feed_configs[: context.limits.max_feeds_per_provider]
        feeds = tuple(c.url for c in feed_configs)
        for feed in feed_configs:
            feed_url = feed.url
            if context.cancel.is_set():
                errors.append("Discovery cancelled before all feeds were read.")
                break
            try:
                response, attempts, limited = fetch_with_retry(fetcher, feed_url, context.cancel, self._sleep)
                attempts_total += attempts
                rate_limited = rate_limited or limited
            except HttpFetchError as exc:
                attempts_total += getattr(exc, "attempts", 1)
                rate_limited = rate_limited or getattr(exc, "rate_limited", False)
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
                    publisher=feed.publisher or entry.get("source") or feed_url,
                    published_at=entry.get("published_at"),
                    language=context.language,
                    source_type="news_feed",
                    original_metadata=entry,
                    snippet=_clip(entry.get("summary")),
                    feed_category=feed.category,
                    feed_source_class=feed.source_class,
                    feed_region=feed.region,
                ))

        stats = dict(
            duration_ms=int((time.monotonic() - started) * 1000), http_attempts=attempts_total,
            rate_limited=rate_limited, filters_applied=("language", "topic"),
        )

        if not candidates and errors and len(errors) == len(feeds):
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.ERROR,
                message="All configured feeds failed: " + "; ".join(errors),
                candidates_returned=0, **stats,
            ), []
        if errors:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.PARTIAL,
                message=f"{len(errors)} of {len(feeds)} feed(s) failed: " + "; ".join(errors),
                candidates_returned=len(candidates), **stats,
            ), candidates
        if not candidates:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.COMPLETED_NO_RESULTS,
                message="Feeds were read successfully but no entry matched the topic/search terms.",
                candidates_returned=0, **stats,
            ), []
        return ProviderOutcome(
            provider=self.name, status=ProviderStatus.OK,
            message=f"Matched {len(candidates)} entr{'y' if len(candidates) == 1 else 'ies'} across {len(feeds)} feed(s).",
            candidates_returned=len(candidates), **stats,
        ), candidates


class ConfiguredSearchApiProvider(DiscoveryProvider):
    name = "configured_search_api"
    requires_credentials = True

    def __init__(self, api_url: str | None = None, api_key: str | None = None, client_factory=None,
                 sleep=time.sleep):
        self._api_url = api_url if api_url is not None else os.environ.get("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL", "")
        self._api_key = api_key if api_key is not None else os.environ.get("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY", "")
        self._client_factory = client_factory
        self._sleep = sleep

    def describe(self) -> dict:
        configured = bool(self._api_url and self._api_key)
        return {
            "name": self.name, "kind": "search_api", "configured": configured, "requires_credentials": True,
            "supported_filters": ["language", "topic", "source_types"],
            "rate_limit": "one request per run, bounded retries",
            "status_message": ("Configured (no call is made until a search runs)." if configured else
                               "Missing DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL and/or _KEY; no network call is made."),
        }

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
            extra_headers={"Authorization": f"Bearer {self._api_key}"},
        )
        query = " ".join([context.topic, *context.search_terms]).strip()
        separator = "&" if "?" in self._api_url else "?"
        url = f"{self._api_url}{separator}q={_url_quote(query)}&lang={_url_quote(context.language)}"
        started = time.monotonic()
        try:
            response, attempts, rate_limited = fetch_with_retry(fetcher, url, context.cancel, self._sleep)
        except (HttpFetchError, UrlValidationError) as exc:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.ERROR,
                message=f"Search API request failed: {_redact(str(exc), self._api_key)}",
                candidates_returned=0, requires_credentials=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                http_attempts=getattr(exc, "attempts", 1), rate_limited=getattr(exc, "rate_limited", False),
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
                snippet=_clip(item.get("snippet")),
                provider_tag=item.get("category") if isinstance(item.get("category"), str) else None,
                provider_rank=len(candidates) + 1,
            ))
        if not candidates:
            return ProviderOutcome(
                provider=self.name, status=ProviderStatus.COMPLETED_NO_RESULTS,
                message="The configured search API returned no results for this query.",
                candidates_returned=0, requires_credentials=True, http_attempts=attempts,
                rate_limited=rate_limited, duration_ms=int((time.monotonic() - started) * 1000),
            ), []
        return ProviderOutcome(
            provider=self.name, status=ProviderStatus.OK,
            message=f"Search API returned {len(candidates)} result(s).",
            candidates_returned=len(candidates), requires_credentials=True, http_attempts=attempts,
            rate_limited=rate_limited, duration_ms=int((time.monotonic() - started) * 1000),
            filters_applied=("language", "topic"),
        ), candidates


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


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
    # Untrusted XML: refuse DTDs/entities outright (entity-expansion bombs, external entities).
    head = content[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in content.lower():
        raise ET.ParseError("DTD/entity declarations are not accepted in feeds")
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
    # A 200 response that is neither RSS nor Atom (HTML error page, JSON-as-XML) is a feed
    # error, not an empty successful feed.
    raise ET.ParseError(f"unrecognized feed root element <{root.tag}>")


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


class _PageMetaParser(HTMLParser):
    """Collects <title>, meta tags, canonical link, <time> and visible text."""

    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.title: str | None = None
        self.time_values: list[str] = []
        self.text: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "link" and a.get("rel", "").lower() == "canonical" and a.get("href"):
            self.canonical = a["href"].strip()
        elif tag == "time" and a.get("datetime"):
            self.time_values.append(a["datetime"].strip())

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data.strip() or None
        if not self._skip_depth and data.strip():
            self.text.append(data.strip())


_DATE_META_KEYS = ("article:published_time", "citation_publication_date", "citation_date", "dc.date",
                   "dcterms.created", "datepublished", "date")
MIN_EXTRACTED_CHARS = 200


def decode_html(content: bytes, content_type: str = "") -> str:
    """Decode with the declared charset (header, then <meta>), else strict UTF-8,
    else cp1252 -- never silently replace undecodable bytes with U+FFFD."""
    import codecs
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(
        rb"charset=[\"']?([\w-]+)", content[:4096], re.I)
    declared = m.group(1) if m else None
    if isinstance(declared, bytes):
        declared = declared.decode("ascii", "ignore")
    if declared:
        try:
            codecs.lookup(declared)
            return content.decode(declared, errors="replace")
        except LookupError:
            pass
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def extract_page(content: bytes, base_url: str, content_type: str = "") -> dict:
    """Pure HTML metadata/text extraction. A date is returned only when the page
    itself declares one; nothing is inferred."""
    parser = _PageMetaParser()
    try:
        parser.feed(decode_html(content, content_type))
        parser.close()
    except Exception:  # malformed markup must not abort discovery
        pass
    text = re.sub(r"\s+", " ", " ".join(parser.text)).strip()
    published = next((parser.meta[k] for k in _DATE_META_KEYS if k in parser.meta), None)
    if not published and parser.time_values:
        published = parser.time_values[0]
    canonical = None
    if parser.canonical:
        try:
            joined = urljoin(base_url, parser.canonical)
            # A declared canonical is honoured only when it stays on the fetched host.
            if urlsplit(joined).hostname == urlsplit(base_url).hostname:
                canonical = joined
        except ValueError:
            canonical = None
    return {
        "title": parser.meta.get("og:title") or parser.title,
        "publisher": parser.meta.get("og:site_name") or parser.meta.get("application-name"),
        "published_at": published, "canonical_url": canonical, "text": text,
    }


class ManualUrlProvider(DiscoveryProvider):
    """Operator-supplied URLs. Every fetch goes through the SSRF-guarded fetcher
    (each redirect hop re-validated). A 200 proves reachability only, never that
    the page's claims are accurate."""

    name = "manual_url"
    requires_credentials = False

    def __init__(self, client_factory=None, sleep=time.sleep):
        self._client_factory = client_factory
        self._sleep = sleep

    def describe(self) -> dict:
        return {"name": self.name, "kind": "manual", "configured": True, "requires_credentials": False,
                "supported_filters": ["source_types"], "rate_limit": "sequential requests, bounded retries",
                "status_message": "Fetches operator-supplied seed_urls through the guarded fetcher."}

    def discover(self, context: DiscoveryContext) -> tuple[ProviderOutcome, list[RawCandidate]]:
        started = time.monotonic()
        if not context.seed_urls:
            return ProviderOutcome(provider=self.name, status=ProviderStatus.COMPLETED_NO_RESULTS,
                                   message="No seed_urls were supplied.", candidates_returned=0), []
        fetcher = GuardedFetcher(context.guard, context.limits.max_bytes_per_fetch, context.limits.timeout_seconds,
                                 context.limits.max_redirects, client_factory=self._client_factory)
        out: list[RawCandidate] = []
        errors: list[str] = []
        seen_urls: set[str] = set()
        seen_text: dict[str, str] = {}
        attempts_total = 0
        for url in context.seed_urls:
            if context.cancel.is_set():
                errors.append("Discovery cancelled before all seed URLs were fetched.")
                break
            try:
                resp, attempts, _ = fetch_with_retry(fetcher, url, context.cancel, self._sleep)
                attempts_total += attempts
            except HttpFetchError as exc:
                attempts_total += getattr(exc, "attempts", 1)
                errors.append(f"{url}: {exc.message}")
                continue
            except UrlValidationError as exc:
                errors.append(f"{url}: {exc}")
                continue
            if resp.media_type not in ("text/html", "application/xhtml+xml", "text/plain"):
                errors.append(f"{url}: unsupported media type {resp.media_type or 'unknown'}; no text extracted")
                continue
            page = extract_page(resp.content, resp.final_url, resp.headers.get("content-type", ""))
            if len(page["text"]) < MIN_EXTRACTED_CHARS:
                errors.append(f"{url}: extraction failed (only {len(page['text'])} characters of text)")
                continue
            try:
                final = normalize_url(page["canonical_url"] or resp.final_url)
            except UrlValidationError as exc:
                errors.append(f"{url}: {exc}")
                continue
            digest = hashlib.sha256(page["text"].encode("utf-8")).hexdigest()
            if final in seen_urls:
                continue
            if digest in seen_text:
                errors.append(f"{url}: duplicate of {seen_text[digest]} (identical extracted text); skipped")
                continue
            seen_urls.add(final)
            seen_text[digest] = final
            out.append(RawCandidate(
                requested_url=final, title=page["title"],
                publisher=page["publisher"] or (urlsplit(final).hostname or None),
                published_at=page["published_at"], language=context.language, source_type="web_page",
                original_metadata={
                    "seed_url": url, "final_url": resp.final_url, "http_status": resp.status_code,
                    "media_type": resp.media_type, "fetch_status": "REACHABLE", "reachability_only": True,
                    "text_sha256": digest, "text_chars": len(page["text"]),
                    "declared_canonical_url": page["canonical_url"],
                    "publisher_declared": bool(page["publisher"]),
                },
                snippet=page["text"][:SNIPPET_MAX], provider_tag="manual",
            ))
        stats = dict(duration_ms=int((time.monotonic() - started) * 1000), http_attempts=attempts_total,
                     filters_applied=("seed_urls",))
        if not out and errors:
            return ProviderOutcome(provider=self.name, status=ProviderStatus.ERROR,
                                   message="All seed URLs failed: " + "; ".join(errors),
                                   candidates_returned=0, **stats), []
        if errors:
            return ProviderOutcome(provider=self.name, status=ProviderStatus.PARTIAL,
                                   message=f"{len(errors)} issue(s): " + "; ".join(errors),
                                   candidates_returned=len(out), **stats), out
        return ProviderOutcome(provider=self.name, status=ProviderStatus.OK,
                               message=f"Fetched {len(out)} seed URL(s); HTTP 200 shows reachability only.",
                               candidates_returned=len(out), **stats), out


def default_registry(client_factory=None) -> DiscoveryProviderRegistry:
    registry = DiscoveryProviderRegistry()
    registry.register(RssFeedProvider(client_factory=client_factory))
    registry.register(ConfiguredSearchApiProvider(client_factory=client_factory))
    registry.register(ManualUrlProvider(client_factory=client_factory))
    return registry
