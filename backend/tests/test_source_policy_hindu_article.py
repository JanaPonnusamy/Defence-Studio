"""Narrow owner-approved The Hindu article path; nearby URLs stay refused."""
import pytest

from defence_studio.acquisition.source_policy import SourcePolicy, SourcePolicyError
from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError

ARTICLE = (
    "https://www.thehindu.com/news/national/"
    "mod-signs-4106-crore-contracts-for-iaf-communication-satellite-navy-torpedoes/"
    "article71588378.ece"
)
RSS = "https://www.thehindu.com/news/national/feeder/default.rss"
ENTRY_ID = "thehindu-gsat7c-varunastra-20261015"


def test_hindu_article_is_the_only_html_path_on_that_host():
    policy = SourcePolicy.load()
    entry = policy.require(ARTICLE)
    assert entry.id == ENTRY_ID
    assert entry.domain == "www.thehindu.com"
    assert entry.include_subdomains is False
    assert entry.independence_group == "thehindu"
    assert entry.category == "INDEPENDENT_REPORTING"
    assert entry.publisher == "The Hindu"
    assert entry.quantitative_trust == "ATTRIBUTED_ONLY"
    assert entry.descriptive_trusted is True
    assert entry.enabled is True
    assert entry.allowed_content_types == ("text/html",)
    assert "published_at" in entry.provenance_requirements
    rss = policy.require(RSS)
    assert rss.id == "thehindu-national-feed"
    assert rss.independence_group == "thehindu"


def test_nearby_hindu_and_unrelated_urls_remain_blocked():
    policy = SourcePolicy.load()
    blocked = [
        "https://www.thehindu.com/",
        "https://www.thehindu.com/news/national/",
        "https://www.thehindu.com/news/national/mod-signs-4106-crore-contracts-for-iaf-communication-satellite-navy-torpedoes/",
        "https://www.thehindu.com/news/national/mod-signs-4106-crore-contracts-for-iaf-communication-satellite-navy-torpedoes/article71588378.ece?utm=1",
        "https://thehindu.com/news/national/mod-signs-4106-crore-contracts-for-iaf-communication-satellite-navy-torpedoes/article71588378.ece",
        "https://www.thehindubusinessline.com/news/national/mod-signs-4106-crore-deals-to-acquire-secured-dedicated-satellite-communication-capability-heavy-weight-torpedoes/article71588969.ece",
        "https://theprint.in/defence/iaf-to-get-first-dedicated-communications-satellite-gsat-7c-to-be-operational-by-2030/3072991/",
        "https://timesofindia.indiatimes.com/defence/news/def-min-inks-rs-4106cr-contracts-for-dedicated-sat-gsat-7c-for-iaf-varunastra-torpedoes-for-indian-navy/articleshow/134966141.cms",
    ]
    for url in blocked:
        assert policy.entry_for(url) is None, url
        with pytest.raises(SourcePolicyError, match="not permitted"):
            policy.require(url)


def test_guard_allows_exact_https_article_and_refuses_other_hindu_paths():
    policy = SourcePolicy.load()
    guard = UrlGuard(resolver=lambda host, port: ["93.184.216.34"], policy=policy)
    allowed = guard.validate(ARTICLE)
    assert allowed.host == "www.thehindu.com"
    assert allowed.scheme == "https"
    with pytest.raises(UrlValidationError, match="SOURCE_NOT_ALLOWLISTED"):
        guard.validate("https://www.thehindu.com/news/national/")
    with pytest.raises(UrlValidationError, match="SOURCE_NOT_ALLOWLISTED"):
        guard.validate(ARTICLE + "?utm=1")
