"""Permitted image-search providers and explicit unsupported sources.

Wikimedia Commons and Openverse use their public APIs. Social platforms are
reported as unsupported: public visibility is not permission to download.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from defence_studio.acquisition.http_client import GuardedFetcher, HttpFetchError
from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError
from defence_studio.image_research.rights import classify_rights, openverse_license, strip_html


_USER_AGENT = "DefenceStudio-ImageResearch/0.1 (https://github.com/JanaPonnusamy/Defence-Studio)"
_JSON_LIMIT = 2_000_000
_IMAGE_LIMIT = 5_000_000


class ProviderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class GuardedJsonClient:
    def __init__(self, get_json=None):
        self._get_json = get_json

    def get_json(self, url: str) -> dict:
        if self._get_json is not None:
            return self._get_json(url)
        fetcher = GuardedFetcher(
            UrlGuard(), _JSON_LIMIT, timeout=40, max_redirects=3,
            extra_headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        try:
            response = fetcher.get(url)
        except UrlValidationError as exc:
            raise ProviderError("blocked_url", str(exc)) from exc
        except HttpFetchError as exc:
            raise ProviderError(exc.code, exc.message) from exc
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("malformed_response", "Provider returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError("malformed_response", "Provider JSON was not an object")
        return payload


class GuardedImageDownloader:
    def __init__(self, fetch=None, max_bytes: int = _IMAGE_LIMIT):
        self._fetch = fetch
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> tuple[str, bytes | None, str | None]:
        if self._fetch is not None:
            return self._fetch(url)
        fetcher = GuardedFetcher(
            UrlGuard(), self.max_bytes, timeout=25, max_redirects=3,
            extra_headers={"User-Agent": _USER_AGENT, "Accept": "image/*"},
        )
        try:
            response = fetcher.get(url)
        except UrlValidationError as exc:
            return "BLOCKED", None, str(exc)
        except HttpFetchError as exc:
            return "FAILED", None, f"{exc.code}: {exc.message}"
        media = response.media_type or ""
        if media and not media.startswith("image/") and not media.startswith("application/octet-stream"):
            return "FAILED", None, f"Response is not an image ({media})"
        if not response.content:
            return "FAILED", None, "Empty image response"
        return "OK", response.content, None


class WikimediaCommonsProvider:
    name = "wikimedia_commons"

    def __init__(self, client: GuardedJsonClient | None = None):
        self.client = client or GuardedJsonClient()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": "Wikimedia Commons",
            "status": "AVAILABLE",
            "credential": "NOT_REQUIRED",
            "detail": "Public Commons API. A result is not a completed search until a job runs.",
        }

    def search(self, query: str, limit: int) -> list[dict]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrlimit": str(max(1, min(limit, 50))),
            "gsrsearch": query,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": "640",
        }
        payload = self.client.get_json("https://commons.wikimedia.org/w/api.php?" + urlencode(params))
        if payload.get("error"):
            info = payload["error"].get("info") if isinstance(payload.get("error"), dict) else str(payload.get("error"))
            raise ProviderError("provider_error", f"Wikimedia Commons: {info}")
        return parse_wikimedia(payload)


class OpenverseProvider:
    name = "openverse"

    def __init__(self, client: GuardedJsonClient | None = None):
        self.client = client or GuardedJsonClient()

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": "Openverse",
            "status": "AVAILABLE",
            "credential": "NOT_REQUIRED",
            "detail": "Public Openverse image API. Results are not invented when the API is unreachable.",
        }

    def search(self, query: str, limit: int) -> list[dict]:
        params = {"q": query, "page_size": str(max(1, min(limit, 20)))}
        payload = self.client.get_json("https://api.openverse.org/v1/images/?" + urlencode(params))
        return parse_openverse(payload)


class SocialMediaSource:
    name = "social_media"

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": "Social media",
            "status": "UNSUPPORTED",
            "credential": "NOT_CONFIGURED",
            "detail": (
                "No official API acquisition is configured. Public posts are not downloaded. "
                "Public visibility is not permission to reuse."
            ),
        }

    def search(self, query: str, limit: int) -> list[dict]:
        return []


def parse_wikimedia(payload: dict) -> list[dict]:
    pages = ((payload or {}).get("query") or {}).get("pages") or {}
    page_list = list(pages.values()) if isinstance(pages, dict) else list(pages)
    drafts = []
    for page in page_list:
        info_list = page.get("imageinfo") or []
        if not info_list:
            continue
        info = info_list[0]
        url = info.get("url")
        if not url:
            continue
        meta = info.get("extmetadata") or {}
        license_name = _meta(meta, "LicenseShortName")
        license_url = _meta(meta, "LicenseUrl")
        rights = classify_rights(license_name=license_name, license_url=license_url)
        title = page.get("title") or ""
        drafts.append({
            "provider": WikimediaCommonsProvider.name,
            "original_image_url": url,
            "thumbnail_url": info.get("thumburl") or url,
            "page_url": info.get("descriptionurl"),
            "source_website": "commons.wikimedia.org",
            "creator": _meta(meta, "Artist"),
            "title": title,
            "caption": _meta(meta, "ImageDescription"),
            "publication_date": _date(_meta(meta, "DateTimeOriginal") or _meta(meta, "DateTime")),
            "license_name": license_name or None,
            "license_url": license_url or None,
            "attribution": _meta(meta, "Credit") or _meta(meta, "Artist"),
            "rights": rights,
            "width": info.get("width"),
            "height": info.get("height"),
            "file_format": _format_from_mime(info.get("mime")),
            "category": None,
        })
    return drafts


def parse_openverse(payload: dict) -> list[dict]:
    drafts = []
    for item in (payload or {}).get("results") or []:
        url = item.get("url")
        if not url:
            continue
        rights = openverse_license(item.get("license"), item.get("license_version"), item.get("license_url"))
        drafts.append({
            "provider": OpenverseProvider.name,
            "original_image_url": url,
            "thumbnail_url": item.get("thumbnail") or url,
            "page_url": item.get("foreign_landing_url"),
            "source_website": item.get("source") or "openverse",
            "creator": item.get("creator") or "",
            "title": item.get("title") or "",
            "caption": item.get("title") or "",
            "publication_date": None,
            "license_name": rights.get("license_name") or _openverse_display(item),
            "license_url": item.get("license_url") or None,
            "attribution": item.get("attribution") or "",
            "rights": rights,
            "width": item.get("width"),
            "height": item.get("height"),
            "file_format": (item.get("filetype") or "").lower() or None,
            "category": item.get("category"),
        })
    return drafts


def _openverse_display(item: dict) -> str | None:
    code = item.get("license")
    version = item.get("license_version")
    if not code:
        return None
    return f"{code} {version}".strip() if version else str(code)


def _meta(meta: dict, key: str) -> str:
    raw = meta.get(key)
    if isinstance(raw, dict):
        raw = raw.get("value")
    return strip_html(str(raw or ""))


def _date(value: str) -> str | None:
    text = (value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def _format_from_mime(mime: str | None) -> str | None:
    if not mime or "/" not in mime:
        return None
    return mime.split("/", 1)[1].lower()
