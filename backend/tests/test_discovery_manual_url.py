"""Manual seed-URL discovery: guarded fetch, dedup, redirects, extraction failures.

HTTP is mocked (httpx.MockTransport); these tests do not touch the network.
"""
import threading

import httpx
import pytest

from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.contracts.discovery import RequestValidationError, request_from_dict
from defence_studio.discovery.providers import (
    DiscoveryContext, ManualUrlProvider, default_registry, extract_page,
)
from defence_studio.contracts.discovery import DiscoveryFilters, DiscoveryLimits
from defence_studio.discovery.service import DiscoveryService

PUBLIC = "93.184.216.34"
PRIVATE = "10.0.0.5"
BODY = "S-400 air defence system overview. " * 20


def page(title="S-400 Triumf", site="CSIS Missile Threat", date="2024-05-01", canonical=None, body=BODY):
    meta = f'<meta property="og:title" content="{title}">'
    if site:
        meta += f'<meta property="og:site_name" content="{site}">'
    if date:
        meta += f'<meta property="article:published_time" content="{date}">'
    if canonical:
        meta += f'<link rel="canonical" href="{canonical}">'
    return f"<html><head><title>{title}</title>{meta}<script>x=1</script></head><body><p>{body}</p></body></html>".encode()


def ctx(seeds, resolver=lambda h, p: [PUBLIC], **limits):
    return DiscoveryContext(
        job_id="j", topic="S-400", search_terms=(), language="en", filters=DiscoveryFilters(),
        limits=DiscoveryLimits(**limits), guard=UrlGuard(resolver=resolver),
        cancel=threading.Event(), seed_urls=tuple(seeds),
    )


def provider(routes):
    def handler(request):
        r = routes.get(str(request.url))
        if r is None:
            return httpx.Response(404)
        return r(request) if callable(r) else r

    return ManualUrlProvider(client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw),
                             sleep=lambda s: None)


def html(content, status=200, ctype="text/html; charset=utf-8"):
    return httpx.Response(status, headers={"content-type": ctype}, content=content)


def test_valid_url_preserves_metadata_and_marks_reachability_only():
    p = provider({"https://a.example/s400": html(page())})
    outcome, cands = p.discover(ctx(["https://a.example/s400"]))
    assert outcome.status.value == "OK" and len(cands) == 1
    c = cands[0]
    assert (c.title, c.publisher, c.published_at) == ("S-400 Triumf", "CSIS Missile Threat", "2024-05-01")
    assert c.original_metadata["reachability_only"] is True and c.original_metadata["http_status"] == 200
    assert c.original_metadata["fetch_status"] == "REACHABLE"


def test_missing_date_and_publisher_are_not_invented():
    p = provider({"https://a.example/x": html(page(site=None, date=None))})
    _, cands = p.discover(ctx(["https://a.example/x"]))
    assert cands[0].published_at is None
    assert cands[0].publisher == "a.example" and cands[0].original_metadata["publisher_declared"] is False


def test_request_rejects_invalid_seed_urls():
    for bad in ("ftp://a.example/x", "not a url", "https://user:pw@a.example/x", ""):
        with pytest.raises(RequestValidationError):
            request_from_dict({"topic": "t", "seed_urls": [bad]}, job_id="j")
    with pytest.raises(RequestValidationError):
        request_from_dict({"topic": "t", "seed_urls": "https://a.example"}, job_id="j")


def test_request_dedups_and_is_backward_compatible():
    r = request_from_dict({"topic": "t", "seed_urls": ["https://A.example/x#f", "https://a.example/x"]}, job_id="j")
    assert r.seed_urls == ("https://a.example/x",)
    assert request_from_dict({"topic": "t"}, job_id="j").seed_urls == ()


def test_private_and_loopback_destinations_blocked():
    p = provider({})
    out, cands = p.discover(ctx(["https://internal.example/x"], resolver=lambda h, port: [PRIVATE]))
    assert cands == [] and out.status.value == "ERROR"
    out, cands = p.discover(ctx(["http://127.0.0.1/x", "http://169.254.169.254/latest"]))
    assert cands == [] and out.status.value == "ERROR"


def test_redirect_to_prohibited_destination_is_blocked():
    def resolver(host, port):
        return [PRIVATE] if host == "internal.example" else [PUBLIC]

    p = provider({"https://a.example/r": httpx.Response(302, headers={"location": "https://internal.example/s"})})
    out, cands = p.discover(ctx(["https://a.example/r"], resolver=resolver))
    assert cands == [] and "internal.example" in out.message or "blocked" in out.message.lower()


def test_redirect_to_public_destination_followed():
    p = provider({"https://a.example/r": httpx.Response(301, headers={"location": "https://b.example/final"}),
                  "https://b.example/final": html(page())})
    _, cands = p.discover(ctx(["https://a.example/r"]))
    assert cands[0].original_metadata["final_url"] == "https://b.example/final"


def test_duplicate_urls_and_identical_content_deduplicated():
    routes = {"https://a.example/x": html(page()), "https://a.example/y": html(page()),
              "https://a.example/z": httpx.Response(301, headers={"location": "https://a.example/x"})}
    p = provider(routes)
    out, cands = p.discover(ctx(["https://a.example/x", "https://a.example/z", "https://a.example/y"]))
    assert [c.requested_url for c in cands] == ["https://a.example/x"]
    assert "duplicate" in out.message


def test_extraction_failures_reported_not_fabricated():
    routes = {"https://a.example/empty": html(b"<html><body>hi</body></html>"),
              "https://a.example/pdf": html(b"%PDF", ctype="application/pdf"),
              "https://a.example/gone": httpx.Response(404),
              "https://a.example/ok": html(page())}
    out, cands = provider(routes).discover(ctx(list(routes)))
    assert out.status.value == "PARTIAL" and len(cands) == 1
    assert "extraction failed" in out.message and "unsupported media type" in out.message and "404" in out.message


def test_extract_page_ignores_off_host_canonical():
    meta = extract_page(page(canonical="https://evil.example/x"), "https://a.example/p")
    assert meta["canonical_url"] is None
    assert "x=1" not in meta["text"]


def test_service_routes_by_seed_urls_and_keeps_rss_default():
    reg = default_registry(client_factory=lambda **kw: httpx.Client(
        transport=httpx.MockTransport(lambda r: html(page())), **kw))
    svc = DiscoveryService(guard=UrlGuard(resolver=lambda h, p: [PUBLIC]), registry=reg)
    seeded = svc.run(request_from_dict({"topic": "S-400", "seed_urls": ["https://a.example/x"]}, job_id="j"))
    assert [o.provider for o in seeded.providers] == ["manual_url"] and seeded.returned == 1
    plain = svc.run(request_from_dict({"topic": "S-400"}, job_id="j"))
    assert "manual_url" not in [o.provider for o in plain.providers]


def test_undeclared_charset_decodes_cp1252_without_replacement_chars():
    body = ("Russia\u2019 s air defence. " * 20).encode("cp1252")
    out = extract_page(b"<html><head><title>Russia\x92s Air Defence</title></head><body><p>" + body + b"</p></body></html>",
                       "https://a.example/x")
    assert "\ufffd" not in out["title"] and out["title"] == "Russia\u2019s Air Defence"
