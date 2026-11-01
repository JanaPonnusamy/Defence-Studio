"""Decision A (2026-11-01): eligible article pages of BBC, Defense News, Breaking Defense and The Hindu.

Uses the real `config/source_policy.json` and the real acquisition service; only the network is a
MockTransport. Covers: each publisher, feed discovery vs article acquisition, redirects (approved,
unapproved, HTTPS->HTTP downgrade), malformed URLs, access failures, tracking-parameter duplicates,
robots.txt, and paywall-like stubs that must not count as acquired articles."""

import httpx
import pytest

from defence_studio.acquisition.service import AcquisitionService, default_registry
from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.acquisition.url_guard import UrlGuard, canonical_url
from defence_studio.contracts.acquisition import AcquisitionRequestV1, AcquisitionTarget, SourceStatus
from defence_studio.storage.artifacts import ArtifactStore

JOB = "33333333-3333-3333-3333-333333333333"
PUBLIC = "93.184.216.34"

ARTICLES = {
    "bbc-news-articles": ("https://www.bbc.com/news/articles/c4g7xy1z2abo", "bbc"),
    "bbc-news-articles-uk": ("https://www.bbc.co.uk/news/world-asia-india-68512345", "bbc"),
    "defensenews-articles": ("https://www.defensenews.com/global/asia-pacific/2025/04/21/india-tests-brahmos/", "defensenews"),
    "breakingdefense-articles": ("https://breakingdefense.com/2024/04/philippines-receives-brahmos/", "breakingdefense"),
    "thehindu-news-articles": ("https://www.thehindu.com/news/national/brahmos-test-fired/article68012345.ece", "thehindu"),
}
FEEDS = ["https://feeds.bbci.co.uk/news/world/rss.xml", "https://www.defensenews.com/feed/",
         "https://breakingdefense.com/feed/", "https://www.thehindu.com/news/national/feeder/default.rss"]


def article_html(words=220, published="2025-04-21T08:00:00Z"):
    para = " ".join(["BrahMos"] + ["word"] * (words - 1))
    return (f"<html lang='en'><head><title>BrahMos</title>"
            f"<script type='application/ld+json'>{{\"datePublished\": \"{published}\"}}</script>"
            f"</head><body><script>var x = '{'tracking ' * 500}';</script><p>{para}</p></body></html>").encode()


def make(tmp_path, handler, policy=None):
    policy = policy or SourcePolicy.load()
    guard = UrlGuard(resolver=lambda host, port: [PUBLIC], policy=policy, purpose="article")
    factory = lambda **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw)  # noqa: E731
    return AcquisitionService(ArtifactStore(tmp_path), guard=guard, registry=default_registry(), client_factory=factory)


def run(service, *urls):
    targets = tuple(AcquisitionTarget(target_id=f"t{i}", url=u, method="auto") for i, u in enumerate(urls))
    return service.run(AcquisitionRequestV1(job_id=JOB, targets=targets))


def site(pages, robots=None, log=None):
    """Mock web: `pages` maps URL -> Response; robots.txt defaults to allow-all."""
    def handler(request):
        url = str(request.url)
        if log is not None:
            log.append(url)
        if request.url.path == "/robots.txt":
            body = robots.get(request.url.host, "User-agent: *\nAllow: /\n") if robots else "User-agent: *\nAllow: /\n"
            return body if isinstance(body, httpx.Response) else httpx.Response(200, text=body)
        if url in pages:
            return pages[url]
        return httpx.Response(404)
    return handler


def html(body):
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body)


@pytest.mark.parametrize("entry_id", sorted(ARTICLES))
def test_each_approved_publisher_article_is_acquired_with_provenance(tmp_path, entry_id):
    url, group = ARTICLES[entry_id]
    result = run(make(tmp_path, site({url: html(article_html())})), url)
    src = result.sources[0]
    assert src.status == SourceStatus.ACQUIRED, src
    access = src.original_metadata["publisher_access"]
    assert access["policy_entry"] == entry_id and access["access_kind"] == "article"
    assert access["independence_group"] == group
    assert access["article_paragraph_words"] >= 150
    assert access["robots"] and all(r["allowed"] for r in access["robots"])
    assert src.provenance.requested_url == url and src.provenance.final_url == url
    assert src.provenance.acquired_at and src.artifacts[0].sha256
    assert src.declared_metadata.published_at == "2025-04-21T08:00:00Z"


def test_feeds_are_discovery_only_and_never_acquired_as_articles(tmp_path):
    policy = SourcePolicy.load()
    for feed in FEEDS:
        assert policy.entry_for(feed) is not None, feed                       # discovery (purpose any)
        assert policy.entry_for(feed, "feed_discovery") is not None, feed
        assert policy.entry_for(feed, "article") is None, feed                # acquisition refused
    result = run(make(tmp_path, site({})), FEEDS[0])
    assert result.sources[0].status == SourceStatus.FAILED
    assert "SOURCE_NOT_ALLOWLISTED" in result.sources[0].error.message


def test_article_entries_are_exact_https_hosts_and_narrow_paths():
    policy = SourcePolicy.load()
    refused = [
        "http://www.bbc.com/news/articles/c4g7xy1z2abo",                     # plain HTTP
        "https://bbc.com/news/articles/c4g7xy1z2abo",                        # host not exact
        "https://evil.www.bbc.com/news/articles/c4g7xy1z2abo",               # subdomain
        "https://www.bbc.com.evil.example/news/articles/c4g7xy1z2abo",       # look-alike
        "https://www.bbc.com/news/live/c4g7xy1z2abo",
        "https://www.bbc.com/sport/football/123",
        "https://www.defensenews.com/",
        "https://www.defensenews.com/video/2025/04/21/x/",
        "https://breakingdefense.com/tag/brahmos/",
        "https://www.thehindu.com/opinion/editorial/brahmos/article68012345.ece",
        "https://www.thehindu.com/news/national/",
        "https://www.thehindubusinessline.com/news/national/x/article1.ece",
    ]
    for url in refused:
        assert policy.entry_for(url, "article") is None, url
    for entry_id in ARTICLES:
        entry = next(e for e in policy.entries if e.id == entry_id)
        assert entry.https_only and entry.respect_robots and entry.min_article_words >= 150
        assert entry.access == ("article",) and not entry.include_subdomains


def test_redirect_to_approved_article_is_followed_and_final_url_recorded(tmp_path):
    start = "https://www.bbc.co.uk/news/world-asia-india-68512345"
    final = "https://www.bbc.com/news/articles/c4g7xy1z2abo"
    pages = {start: httpx.Response(301, headers={"location": final}), final: html(article_html())}
    src = run(make(tmp_path, site(pages)), start).sources[0]
    assert src.status == SourceStatus.ACQUIRED
    assert src.provenance.requested_url == start and src.final_url == final


@pytest.mark.parametrize("location", [
    "https://evil.example/news/articles/c4g7xy1z2abo",              # unapproved domain
    "http://www.bbc.com/news/articles/c4g7xy1z2abo",                # HTTPS -> HTTP downgrade
    "https://www.bbc.com/account/signin?ptrt=x",                    # approved host, unapproved path
    "https://feeds.bbci.co.uk/news/world/rss.xml",                  # feed-only entry
    "https://127.0.0.1/news/articles/c4g7xy1z2abo",                 # internal address
])
def test_suspicious_redirects_are_rejected(tmp_path, location):
    start = ARTICLES["bbc-news-articles"][0]
    log = []
    pages = {start: httpx.Response(302, headers={"location": location}), location: html(article_html())}
    src = run(make(tmp_path, site(pages, log=log)), start).sources[0]
    assert src.status == SourceStatus.FAILED
    assert src.error.code == "blocked_redirect"
    assert location not in log            # the rejected destination is never requested
    assert src.artifacts == ()


@pytest.mark.parametrize("bad", ["not a url", "https://", "ftp://www.bbc.com/news/articles/c4g7xy1z2abo",
                                 "https://user:pw@www.bbc.com/news/articles/c4g7xy1z2abo", ""])
def test_malformed_urls_fail_without_network(tmp_path, bad):
    log = []
    src = run(make(tmp_path, site({}, log=log)), bad or " ").sources[0]
    assert src.status == SourceStatus.FAILED and log == []


@pytest.mark.parametrize("status,code", [(403, "access_restricted"), (401, "access_restricted"),
                                         (451, "access_restricted"), (404, "http_status")])
def test_publisher_access_failure_is_reported_not_bypassed(tmp_path, status, code):
    url = ARTICLES["thehindu-news-articles"][0]
    log = []
    src = run(make(tmp_path, site({url: httpx.Response(status)}, log=log)), url).sources[0]
    assert src.status == SourceStatus.FAILED and src.error.code == code
    assert src.error.retryable is False
    assert log.count(url) == 1            # no retry, no alternate route


def test_paywall_or_consent_stub_is_not_counted_as_acquired_article(tmp_path):
    url = ARTICLES["thehindu-news-articles"][0]
    stub = b"<html><body><p>Subscribe to continue reading this premium story.</p></body></html>"
    src = run(make(tmp_path, site({url: html(stub)})), url).sources[0]
    assert src.status == SourceStatus.FAILED and src.error.code == "article_content_unavailable"
    assert src.artifacts == ()
    pdf = httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7 x")
    src = run(make(tmp_path / "p", site({url: pdf})), url).sources[0]
    assert src.status == SourceStatus.FAILED and src.error.code == "article_content_unavailable"


def test_tracking_parameter_duplicates_collapse_to_one_source(tmp_path):
    clean = ARTICLES["bbc-news-articles"][0]
    rss = clean + "?at_medium=RSS&at_campaign=rss"
    utm = clean + "?utm_source=x&utm_medium=y"
    assert canonical_url(rss) == canonical_url(utm) == clean
    # at_* is only analytics on BBC hosts; elsewhere it is kept (could be a real parameter).
    assert canonical_url("https://other.example/a?at_medium=1") == "https://other.example/a?at_medium=1"
    result = run(make(tmp_path, site({clean: html(article_html())})), rss, utm, clean)
    assert [s.status for s in result.sources] == [SourceStatus.ACQUIRED, SourceStatus.SKIPPED_DUPLICATE,
                                                  SourceStatus.SKIPPED_DUPLICATE]


def test_robots_disallow_blocks_fetch(tmp_path):
    url = ARTICLES["defensenews-articles"][0]
    log = []
    robots = {"www.defensenews.com": "User-agent: *\nDisallow: /global/\n"}
    src = run(make(tmp_path, site({url: html(article_html())}, robots=robots, log=log)), url).sources[0]
    assert src.status == SourceStatus.FAILED and src.error.code == "robots_disallowed"
    assert url not in log


def test_robots_server_error_fails_closed_and_404_means_no_rules(tmp_path):
    url = ARTICLES["breakingdefense-articles"][0]
    err = {"breakingdefense.com": httpx.Response(503)}
    src = run(make(tmp_path, site({url: html(article_html())}, robots=err)), url).sources[0]
    assert src.status == SourceStatus.FAILED and src.error.code == "robots_disallowed"
    none = {"breakingdefense.com": httpx.Response(404)}
    src = run(make(tmp_path / "b", site({url: html(article_html())}, robots=none)), url).sources[0]
    assert src.status == SourceStatus.ACQUIRED


def test_robots_agent_specific_rule_applies_to_defence_studio(tmp_path):
    url = ARTICLES["bbc-news-articles"][0]
    robots = {"www.bbc.com": "User-agent: DefenceStudio-Acquisition\nDisallow: /news/\n\nUser-agent: *\nAllow: /\n"}
    src = run(make(tmp_path, site({url: html(article_html())}, robots=robots)), url).sources[0]
    assert src.error.code == "robots_disallowed"


def test_other_registry_entries_keep_previous_behaviour():
    policy = SourcePolicy.load()
    pib = "https://www.pib.gov.in/PressReleasePage.aspx?PRID=1234567"
    entry = policy.entry_for(pib, "article")
    assert entry is not None and entry.access == ("feed_discovery", "article") and not entry.respect_robots
