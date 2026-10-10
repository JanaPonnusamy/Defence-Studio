import httpx
import pytest

from defence_studio.acquisition.providers.youtube import SubtitleTrack, YoutubeSubtitles
from defence_studio.acquisition.service import AcquisitionService, default_registry
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.contracts.acquisition import (
    AcquisitionLimits,
    AcquisitionRequestV1,
    AcquisitionTarget,
    MetadataStatus,
    SourceStatus,
)
from defence_studio.storage.artifacts import ArtifactStore

JOB = "22222222-2222-2222-2222-222222222222"
PUBLIC = "93.184.216.34"


def factory_for(handler):
    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)
    return factory


def make_service(tmp_path, http_handler=None, youtube_backend=None, resolver=None):
    store = ArtifactStore(tmp_path)
    guard = UrlGuard(resolver=resolver or (lambda host, port: [PUBLIC]))
    registry = default_registry(youtube_backend=youtube_backend)
    client_factory = factory_for(http_handler) if http_handler else None
    return AcquisitionService(store, guard=guard, registry=registry, client_factory=client_factory), store


def target(tid, url, method="auto", langs=()):
    return AcquisitionTarget(target_id=tid, url=url, method=method, language_preferences=tuple(langs))


def request(*targets, **limits):
    return AcquisitionRequestV1(
        job_id=JOB, targets=tuple(targets),
        limits=AcquisitionLimits(**limits) if limits else AcquisitionLimits(),
    )


HTML_FULL = (
    b"<html lang='ta'><head><title>Rafale delivery</title>"
    b"<meta property='og:site_name' content='Defence News'>"
    b"<meta property='article:published_time' content='2026-01-05T10:00:00Z'>"
    b"</head><body>content</body></html>"
)


def test_http_page_acquires_html_with_declared_metadata(tmp_path):
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=HTML_FULL)

    service, store = make_service(tmp_path, http_handler=handler)
    result = service.run(request(target("t1", "https://news.example/rafale")))
    assert result.succeeded == 1 and result.failed == 0
    source = result.sources[0]
    assert source.status == SourceStatus.ACQUIRED
    assert source.source_type == "web_page"
    assert source.declared_metadata.title == "Rafale delivery"
    assert source.declared_metadata.title_status == MetadataStatus.COLLECTED
    assert source.declared_metadata.publisher == "Defence News"
    assert source.declared_metadata.published_at == "2026-01-05T10:00:00Z"
    assert source.declared_metadata.language == "ta"
    # Real bytes on disk, hash matches the recorded reference.
    assert len(source.artifacts) == 1
    ref = source.artifacts[0]
    assert store.exists(JOB, ref.uri)
    assert store.sha256_of(JOB, ref.uri) == ref.sha256
    assert source.provenance.requested_url == "https://news.example/rafale"
    assert source.provenance.source_component.startswith("defence_studio.acquisition.providers.http_page")


def test_pdf_detected_and_metadata_not_collected(tmp_path):
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7\n...")

    service, store = make_service(tmp_path, http_handler=handler)
    source = service.run(request(target("t1", "https://gov.example/report.pdf"))).sources[0]
    assert source.status == SourceStatus.ACQUIRED
    assert source.source_type == "document_pdf"
    assert source.declared_metadata.title_status == MetadataStatus.NOT_COLLECTED
    assert source.artifacts[0].uri.endswith(".pdf")


def test_missing_html_metadata_marked_unavailable(tmp_path):
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html><body>x</body></html>")

    service, _ = make_service(tmp_path, http_handler=handler)
    source = service.run(request(target("t1", "https://news.example/bare"))).sources[0]
    assert source.declared_metadata.title is None
    assert source.declared_metadata.title_status == MetadataStatus.UNAVAILABLE


def test_empty_body_is_not_a_success(tmp_path):
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"")

    service, _ = make_service(tmp_path, http_handler=handler)
    source = service.run(request(target("t1", "https://news.example/empty"))).sources[0]
    assert source.status == SourceStatus.FAILED
    assert source.error.code == "empty_body" and source.error.retryable is True


def test_partial_success_and_no_substitution(tmp_path):
    def handler(req):
        if req.url.path.endswith("/good"):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=HTML_FULL)
        return httpx.Response(404, content=b"nope")

    service, _ = make_service(tmp_path, http_handler=handler)
    result = service.run(request(
        target("good", "https://news.example/good"),
        target("bad", "https://news.example/bad"),
    ))
    assert (result.requested, result.succeeded, result.failed) == (2, 1, 1)
    bad = next(s for s in result.sources if s.target_id == "bad")
    assert bad.status == SourceStatus.FAILED
    assert bad.error.code == "http_status" and bad.error.retryable is False
    assert bad.artifacts == ()


def test_in_request_duplicate_is_skipped(tmp_path):
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=HTML_FULL)

    service, _ = make_service(tmp_path, http_handler=handler)
    result = service.run(request(
        target("a", "https://news.example/same"),
        target("b", "https://news.example/same"),
    ))
    statuses = {s.target_id: s.status for s in result.sources}
    assert statuses["a"] == SourceStatus.ACQUIRED
    assert statuses["b"] == SourceStatus.SKIPPED_DUPLICATE
    assert result.skipped_duplicate == 1


def test_idempotent_reuse_avoids_redownload(tmp_path):
    calls = {"n": 0}

    def handler(_req):
        calls["n"] += 1
        return httpx.Response(200, headers={"content-type": "text/html"}, content=HTML_FULL)

    service, store = make_service(tmp_path, http_handler=handler)
    first = service.run(request(target("t1", "https://news.example/reuse")))
    assert first.sources[0].status == SourceStatus.ACQUIRED and calls["n"] == 1
    # Fresh service instance, same store -> reuse the hash-verified artifact.
    service2, _ = make_service(tmp_path, http_handler=handler)
    service2.store = store
    second = service2.run(request(target("t1", "https://news.example/reuse")))
    assert second.sources[0].status == SourceStatus.ACQUIRED
    assert calls["n"] == 1  # no second network fetch
    assert any("Reused" in w for w in second.sources[0].warnings)


def test_blocked_address_fails_closed(tmp_path):
    service, store = make_service(
        tmp_path, http_handler=lambda r: httpx.Response(200, content=b"x"),
        resolver=lambda host, port: ["10.0.0.5"],
    )
    source = service.run(request(target("t1", "https://intranet.example/secret"))).sources[0]
    assert source.status == SourceStatus.FAILED
    assert source.error.code == "blocked_address" and source.error.retryable is False
    assert source.artifacts == ()


def test_redirect_to_internal_is_blocked(tmp_path):
    def handler(req):
        return httpx.Response(302, headers={"location": "http://metadata.internal/latest"})

    def resolver(host, port):
        return [PUBLIC] if host == "news.example" else ["169.254.169.254"]

    service, _ = make_service(tmp_path, http_handler=handler, resolver=resolver)
    source = service.run(request(target("t1", "https://news.example/redir"))).sources[0]
    assert source.status == SourceStatus.FAILED
    assert source.error.code == "blocked_redirect"


def test_oversized_declared_length_rejected(tmp_path):
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 100)

    service, _ = make_service(tmp_path, http_handler=handler)
    source = service.run(request(target("t1", "https://news.example/big"),
                                 max_bytes_per_source=10)).sources[0]
    assert source.status == SourceStatus.FAILED and source.error.code == "too_large"


def test_oversized_streamed_without_length_rejected(tmp_path):
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=iter([b"x" * 100]))

    service, _ = make_service(tmp_path, http_handler=handler)
    source = service.run(request(target("t1", "https://news.example/stream"),
                                 max_bytes_per_source=10)).sources[0]
    assert source.status == SourceStatus.FAILED and source.error.code == "too_large"


def test_timeout_is_retryable(tmp_path):
    def handler(_req):
        raise httpx.TimeoutException("slow")

    service, _ = make_service(tmp_path, http_handler=handler)
    source = service.run(request(target("t1", "https://news.example/slow"))).sources[0]
    assert source.status == SourceStatus.FAILED
    assert source.error.code == "timeout" and source.error.retryable is True


def test_invalid_url_target_fails(tmp_path):
    service, _ = make_service(tmp_path, http_handler=lambda r: httpx.Response(200, content=b"x"))
    source = service.run(request(target("t1", "not-a-url"))).sources[0]
    assert source.status == SourceStatus.FAILED and source.error.code == "invalid_url"


def test_method_mismatch_fails_closed(tmp_path):
    backend = lambda *a, **k: YoutubeSubtitles("x", None, None, None, None, ())
    service, _ = make_service(tmp_path, youtube_backend=backend)
    source = service.run(request(
        target("t1", "https://news.example/not-youtube", method="youtube_subtitles"),
    )).sources[0]
    assert source.status == SourceStatus.FAILED and source.error.code == "method_mismatch"


def test_max_sources_cap_is_enforced(tmp_path):
    def handler(_req):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=HTML_FULL)

    service, _ = make_service(tmp_path, http_handler=handler)
    result = service.run(request(
        target("a", "https://news.example/1"),
        target("b", "https://news.example/2"),
        max_sources=1,
    ))
    assert result.requested == 2
    assert len(result.sources) == 1
    assert any("capped" in w for w in result.warnings)


# --- YouTube subtitle provider (fake backend = adapter boundary) ---

def _youtube_backend(url, languages, max_bytes, cancel):
    return YoutubeSubtitles(
        video_id="dQw4w9WgXcQ", title="Rafale briefing", channel="Air Force",
        upload_date="20260105", webpage_url=url,
        tracks=(SubtitleTrack(language="en", is_auto=False, ext="vtt", content=b"WEBVTT\n\n00:00 hi"),),
    )


def test_youtube_subtitles_acquired_without_video(tmp_path):
    service, store = make_service(tmp_path, youtube_backend=_youtube_backend)
    source = service.run(request(
        target("yt", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", langs=("en",)),
    )).sources[0]
    assert source.status == SourceStatus.ACQUIRED
    assert source.source_type == "youtube_video"
    assert source.original_metadata["has_video_download"] is False
    assert source.declared_metadata.title == "Rafale briefing"
    assert source.declared_metadata.publisher == "Air Force"
    assert source.declared_metadata.published_at == "2026-01-05"
    assert source.artifacts[0].uri.endswith(".vtt")
    assert store.sha256_of(JOB, source.artifacts[0].uri) == source.artifacts[0].sha256


def test_youtube_no_subtitles_fails_honestly(tmp_path):
    backend = lambda *a, **k: YoutubeSubtitles("id", "t", "c", "20260101", "u", ())
    service, _ = make_service(tmp_path, youtube_backend=backend)
    source = service.run(request(
        target("yt", "https://youtu.be/abc12345678", method="youtube_subtitles"),
    )).sources[0]
    assert source.status == SourceStatus.FAILED
    assert source.error.code == "no_subtitles" and source.error.retryable is False
