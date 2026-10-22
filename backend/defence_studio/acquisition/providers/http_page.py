"""HTTP web-page / document acquisition provider.

Downloads an approved http(s) URL through the guarded fetcher, validates that
usable bytes actually arrived (a 200 with an empty body is not a success),
stores the raw content under the job folder, and records only *declared*,
source-supplied metadata (the HTML ``<title>`` and a few head meta tags). It
does not parse article bodies, run OCR, or interpret PDFs — extraction and
normalization are a later pipeline stage, and nothing here is treated as a
verified fact.
"""

from __future__ import annotations

from html.parser import HTMLParser

from defence_studio.acquisition.http_client import HttpFetchError
from defence_studio.acquisition.provider import (
    AcquisitionProvider,
    ProviderContext,
    ProviderError,
    ProviderResult,
    StoredOutput,
)
from defence_studio.acquisition.url_guard import ValidatedUrl
from defence_studio.contracts.acquisition import DeclaredMetadata, MetadataStatus
from defence_studio.storage.artifacts import safe_filename


_PDF_MAGIC = b"%PDF-"


class _HeadMetadata(HTMLParser):
    """Extract only <title> and selected <meta> values from the document head.

    Deliberately shallow: stops collecting once the head closes, so this is
    metadata acquisition, not content extraction.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.publisher: str | None = None
        self.published_at: str | None = None
        self.language: str | None = None
        self._in_title = False
        self._done_head = False

    def handle_starttag(self, tag, attrs):
        if self._done_head:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "html":
            for key, value in attrs:
                if key == "lang" and value:
                    self.language = value.strip()
        elif tag == "meta":
            data = {key.lower(): (value or "") for key, value in attrs}
            name = (data.get("property") or data.get("name") or "").lower()
            content = data.get("content", "").strip()
            if not content:
                return
            if name in ("og:site_name", "article:publisher") and not self.publisher:
                self.publisher = content
            elif name in ("article:published_time", "og:published_time") and not self.published_at:
                self.published_at = content

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self._done_head = True

    def handle_data(self, data):
        if self._in_title and not self._done_head:
            text = data.strip()
            if text:
                self.title = (self.title or "") + text


class HttpPageProvider(AcquisitionProvider):
    name = "http_page"
    version = "0.1.0"
    source_component = "defence_studio.acquisition.providers.http_page@0.1.0"
    fallback = True  # generic; yields to any specific provider under `auto`

    def supports(self, validated_url: ValidatedUrl) -> bool:
        return validated_url.scheme in ("http", "https")

    def acquire(self, context: ProviderContext) -> ProviderResult:
        try:
            response = context.fetcher.get(context.validated_url.url, context.cancel)
        except HttpFetchError as exc:
            raise ProviderError(exc.code, exc.message, exc.retryable) from exc

        body = response.content
        if not body:
            # A reachable source that returns nothing is not a usable acquisition.
            raise ProviderError("empty_body", "Source returned an empty body", retryable=True)

        is_pdf = body[:5] == _PDF_MAGIC or response.media_type == "application/pdf"
        if is_pdf:
            source_type, extension, declared = "document_pdf", ".pdf", self._pdf_metadata(context)
        elif "html" in response.media_type or response.media_type in ("", "text/plain"):
            source_type, extension, declared = "web_page", ".html", self._html_metadata(body, context)
        else:
            source_type, extension, declared = "binary", ".bin", self._unknown_metadata()

        filename = safe_filename("content", context.validated_url.url, extension)
        uri = f"jobs/{context.job_id}/research/sources/{context.source_id}/{filename}"
        stored = context.store.write_bytes(context.job_id, uri, body, response.media_type or "application/octet-stream")

        return ProviderResult(
            source_type=source_type,
            artifacts=(StoredOutput(
                artifact_type="source_content",
                uri=stored.uri,
                sha256=stored.sha256,
                bytes=stored.bytes,
                media_type=stored.media_type,
            ),),
            declared_metadata=declared,
            original_metadata={
                "http_status": response.status_code,
                "response_headers": response.headers,
                "declared_media_type": response.media_type,
            },
            final_url=response.final_url,
        )

    def _html_metadata(self, body: bytes, context: ProviderContext) -> DeclaredMetadata:
        parser = _HeadMetadata()
        try:
            parser.feed(body.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 - a malformed head must not fail acquisition
            parser = _HeadMetadata()
        if not parser.published_at:
            from defence_studio.acquisition.source_dates import pib_publication_date
            parser.published_at = pib_publication_date(body.decode("utf-8", errors="replace"), context.validated_url.url)
        return DeclaredMetadata(
            title=parser.title or None,
            title_status=MetadataStatus.COLLECTED if parser.title else MetadataStatus.UNAVAILABLE,
            publisher=parser.publisher or None,
            publisher_status=MetadataStatus.COLLECTED if parser.publisher else MetadataStatus.UNAVAILABLE,
            published_at=parser.published_at or None,
            published_at_status=MetadataStatus.COLLECTED if parser.published_at else MetadataStatus.UNAVAILABLE,
            language=parser.language or (context.target.language_preferences[0]
                                         if context.target.language_preferences else None),
            language_status=MetadataStatus.COLLECTED if parser.language else MetadataStatus.UNAVAILABLE,
        )

    @staticmethod
    def _pdf_metadata(context: ProviderContext) -> DeclaredMetadata:
        # PDF document metadata lives in the binary; parsing it is extraction-phase
        # work, so it is honestly NOT_COLLECTED rather than claimed unavailable.
        return DeclaredMetadata(
            title_status=MetadataStatus.NOT_COLLECTED,
            publisher_status=MetadataStatus.NOT_COLLECTED,
            published_at_status=MetadataStatus.NOT_COLLECTED,
            language_status=MetadataStatus.NOT_COLLECTED,
        )

    @staticmethod
    def _unknown_metadata() -> DeclaredMetadata:
        return DeclaredMetadata(
            title_status=MetadataStatus.NOT_COLLECTED,
            publisher_status=MetadataStatus.NOT_COLLECTED,
            published_at_status=MetadataStatus.NOT_COLLECTED,
            language_status=MetadataStatus.NOT_COLLECTED,
        )
