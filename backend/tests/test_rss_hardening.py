"""RSS hardening: feed errors vs empty success, hostile XML, malformed entries, partial failure."""
import threading

import httpx
import pytest

from defence_studio.discovery.providers import RssFeedProvider, _parse_feed
from test_discovery_f1a import RSS_XML, _context, _rss_client_factory
import xml.etree.ElementTree as ET


def provider(content_by_url):
    return RssFeedProvider(feeds_by_language={"en": tuple(content_by_url)},
                           client_factory=_rss_client_factory(content_by_url))


def test_doctype_and_entity_bombs_rejected():
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><rss><channel/></rss>'
    with pytest.raises(ET.ParseError):
        _parse_feed(bomb)
    out, cands = provider({"https://feeds.example/bomb.xml": bomb}).discover(_context())
    assert out.status.value == "ERROR" and cands == []


def test_non_feed_200_is_error_not_empty_success():
    html = b"<html><body><h1>Service unavailable</h1></body></html>"
    out, cands = provider({"https://feeds.example/x.xml": html}).discover(_context())
    assert out.status.value == "ERROR" and "unrecognized feed root" in out.message and cands == []


def test_valid_empty_feed_is_no_results_not_error():
    empty = b"<rss><channel><title>T</title></channel></rss>"
    out, cands = provider({"https://feeds.example/e.xml": empty}).discover(_context())
    assert out.status.value == "COMPLETED_NO_RESULTS" and cands == []


def test_malformed_entries_skipped_without_losing_good_ones():
    xml = (b"<rss><channel><title>Wire</title>"
           b"<item><title>Rafale jets arrive</title></item>"                       # no link
           b"<item><title>Rafale jets delivered</title><link>https://n.example/a</link>"
           b"<pubDate>Mon, 05 May 2025 10:00:00 GMT</pubDate></item></channel></rss>")
    out, cands = provider({"https://feeds.example/m.xml": xml}).discover(_context())
    assert out.status.value == "OK" and [c.requested_url for c in cands] == ["https://n.example/a"]
    assert cands[0].published_at == "Mon, 05 May 2025 10:00:00 GMT" and cands[0].source_type == "news_feed"


def test_truncated_xml_is_a_distinct_feed_error_while_other_feed_succeeds():
    out, cands = provider({"https://feeds.example/good.xml": RSS_XML,
                           "https://feeds.example/trunc.xml": b"<rss><channel><item><title>x"}).discover(_context())
    assert out.status.value == "PARTIAL" and "could not parse" in out.message and len(cands) == 1


def test_timeout_is_reported_as_feed_failure():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)
    p = RssFeedProvider(feeds_by_language={"en": ("https://feeds.example/slow.xml",)}, client_factory=factory,
                        sleep=lambda s: None)
    out, cands = p.discover(_context())
    assert out.status.value == "ERROR" and cands == [] and "slow.xml" in out.message
