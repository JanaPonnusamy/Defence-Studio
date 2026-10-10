"""Guarded HTTP fetcher used by HTTP-based acquisition providers.

Centralises the controls the F3 spec requires so no provider can accidentally
bypass them:

* every hop (initial URL and each redirect) is re-validated by `UrlGuard`
  before the request, closing redirect-to-internal SSRF;
* redirects are followed manually and bounded by `limits.max_redirects`;
* the body is streamed and aborted the instant it exceeds
  `limits.max_bytes_per_source`, so an attacker cannot exhaust disk/memory by
  advertising a small (or no) Content-Length;
* a single total timeout bounds slow-loris style stalls;
* no cookies, Authorization, or other credential headers are ever sent, and
  only a safe allow-list of response headers is surfaced.

The `client_factory` seam lets tests inject an ``httpx.MockTransport`` so the
adapter boundary is exercised with realistic responses and zero network.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError


_USER_AGENT = "DefenceStudio-Acquisition/0.1 (+contact: configure per deployment)"
_SAFE_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "last-modified",
    "etag",
    "date",
    "content-language",
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class HttpFetchError(Exception):
    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class FetchResponse:
    requested_url: str
    final_url: str
    status_code: int
    media_type: str
    headers: dict
    content: bytes


class GuardedFetcher:
    def __init__(self, guard: UrlGuard, max_bytes: int, timeout: float, max_redirects: int,
                 client_factory=None):
        self.guard = guard
        self.max_bytes = int(max_bytes)
        self.timeout = float(timeout)
        self.max_redirects = int(max_redirects)
        self._client_factory = client_factory or (lambda **kw: httpx.Client(**kw))

    def get(self, url: str, cancel: threading.Event | None = None) -> FetchResponse:
        requested = url
        current = self.guard.validate(url)  # raises for blocked/invalid
        hops = 0
        with self._client_factory(timeout=self.timeout, follow_redirects=False) as client:
            while True:
                if cancel is not None and cancel.is_set():
                    raise HttpFetchError("cancelled", "Acquisition was cancelled", retryable=True)
                response = self._single_get(client, current.url)
                if response.status_code in _REDIRECT_STATUSES and response.headers.get("location"):
                    hops += 1
                    if hops > self.max_redirects:
                        raise HttpFetchError(
                            "too_many_redirects",
                            f"Exceeded {self.max_redirects} redirects from {requested}",
                            retryable=False,
                        )
                    target = urljoin(current.url, response.headers["location"])
                    try:
                        current = self.guard.validate(target)  # re-guard every hop
                    except UrlValidationError as exc:
                        raise HttpFetchError("blocked_redirect", str(exc), retryable=False) from exc
                    continue
                if response.status_code >= 400:
                    raise HttpFetchError(
                        "http_status",
                        f"Source returned HTTP {response.status_code}",
                        retryable=response.status_code >= 500 or response.status_code == 429,
                    )
                return FetchResponse(
                    requested_url=requested,
                    final_url=current.url,
                    status_code=response.status_code,
                    media_type=(response.headers.get("content-type", "").split(";")[0].strip().lower()),
                    headers={k: v for k, v in response.headers.items()
                             if k.lower() in _SAFE_RESPONSE_HEADERS},
                    content=response.content,
                )

    def _single_get(self, client, url: str):
        headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
        try:
            with client.stream("GET", url, headers=headers) as response:
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
                    raise HttpFetchError(
                        "too_large",
                        f"Declared Content-Length {declared} exceeds limit {self.max_bytes}",
                        retryable=False,
                    )
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > self.max_bytes:
                        raise HttpFetchError(
                            "too_large",
                            f"Body exceeded size limit {self.max_bytes} bytes during download",
                            retryable=False,
                        )
                # Materialize a response carrying the buffered body for the caller.
                return _BufferedResponse(response.status_code, response.headers, bytes(chunks))
        except httpx.TimeoutException as exc:
            raise HttpFetchError("timeout", f"Request to source timed out after {self.timeout}s",
                                 retryable=True) from exc
        except httpx.HTTPError as exc:
            raise HttpFetchError("network_error", f"Network error contacting source: {exc}",
                                 retryable=True) from exc


class _BufferedResponse:
    """A minimal response view decoupled from the streamed httpx response, so the
    content is available after the stream context closes."""

    def __init__(self, status_code: int, headers, content: bytes):
        self.status_code = status_code
        self.headers = headers
        self.content = content
