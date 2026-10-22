"""Phase 1A feed discovery: validation, provider normalization, dedup,
ranking, partial success, SSRF protection, worker integration, idempotency,
ownership, and the discovery-to-F3 contract handoff."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.api.app import create_app
from defence_studio.contracts.acquisition import request_from_dict as acquisition_request_from_dict
from defence_studio.contracts.discovery import (
    DiscoveryFilters,
    DiscoveryLimits,
    RequestValidationError,
    request_from_dict,
)
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import initialize_database
from defence_studio.discovery.handler import build_discovery_handler
from defence_studio.discovery.providers import (
    ConfiguredSearchApiProvider,
    DiscoveryContext,
    DiscoveryProviderRegistry,
    RssFeedProvider,
)
from defence_studio.discovery.ranking import score_candidate
from defence_studio.discovery.service import DiscoveryService
from defence_studio.services.discovery import DiscoveryApiService, DiscoveryValidationError
from defence_studio.services.jobs import JobService
from defence_studio.services.worker import Worker, WorkerService
from defence_studio.storage.artifacts import ArtifactStore

SECRET = "phase-one-test-signing-secret-32-bytes-minimum"
OWNER = "discovery-owner"
PUBLIC = "93.184.216.34"

RSS_XML = b"""<?xml version="1.0"?>
<rss><channel><title>Defence Wire</title>
<item><title>Rafale jets deployed near border</title>
<link>https://news.example/rafale-deploy</link>
<description>Air force update</description>
<pubDate>Mon, 05 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Unrelated cricket match report</title>
<link>https://news.example/cricket</link>
<description>Sports news</description>
<pubDate>Mon, 05 Oct 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""


def _access_token(subject: str) -> str:
    import base64
    import hashlib
    import hmac
    import time

    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = encode(json.dumps({"sub": subject, "exp": int(time.time()) + 600}).encode())
    signing_input = f"{header}.{payload}"
    signature = encode(hmac.new(SECRET.encode(), signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


def _resolver(host, port):
    return [PUBLIC]


def _rss_client_factory(xml_by_url: dict[str, bytes], calls: list | None = None):
    def handler(request):
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        for prefix, content in xml_by_url.items():
            if url.startswith(prefix):
                return httpx.Response(200, headers={"content-type": "application/rss+xml"}, content=content)
        return httpx.Response(404)

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    return factory


# ---------------------------------------------------------------------------
# 1. Request validation
# ---------------------------------------------------------------------------


def test_request_requires_nonempty_topic():
    with pytest.raises(RequestValidationError):
        request_from_dict({"topic": "   "}, job_id="job-1")


def test_request_rejects_invalid_limit():
    with pytest.raises(RequestValidationError):
        request_from_dict({"topic": "S-400", "limits": {"max_results": 0}}, job_id="job-1")


def test_request_rejects_malformed_date():
    with pytest.raises(RequestValidationError):
        request_from_dict({"topic": "S-400", "filters": {"date_from": "not-a-date"}}, job_id="job-1")


def test_request_rejects_date_range_reversed():
    with pytest.raises(RequestValidationError):
        request_from_dict(
            {"topic": "S-400", "filters": {"date_from": "2026-05-01", "date_to": "2026-01-01"}},
            job_id="job-1",
        )


def test_request_rejects_unknown_provider():
    with pytest.raises(RequestValidationError):
        request_from_dict({"topic": "S-400", "filters": {"providers": ["made_up_provider"]}}, job_id="job-1")


def test_request_never_rewrites_topic_text():
    request = request_from_dict({"topic": "  Rafale deal  "}, job_id="job-1")
    assert request.topic == "Rafale deal"  # trimmed, never reworded


# ---------------------------------------------------------------------------
# 2. Provider normalization, partial success, credentials
# ---------------------------------------------------------------------------


def _context(topic="Rafale jets", search_terms=(), language="en", cancel=None, filters=None, limits=None):
    return DiscoveryContext(
        job_id="job-1", topic=topic, search_terms=search_terms, language=language,
        filters=filters or DiscoveryFilters(), limits=limits or DiscoveryLimits(),
        guard=UrlGuard(resolver=_resolver), cancel=cancel or threading.Event(),
    )


def test_rss_provider_reports_not_configured_without_feeds():
    provider = RssFeedProvider(feeds_by_language={})
    outcome, candidates = provider.discover(_context())
    assert outcome.status.value == "UNAVAILABLE_NOT_CONFIGURED"
    assert candidates == []


def test_rss_provider_matches_topic_and_skips_unrelated_entries():
    provider = RssFeedProvider(
        feeds_by_language={"en": ("https://feeds.example/defence.xml",)},
        client_factory=_rss_client_factory({"https://feeds.example/defence.xml": RSS_XML}),
    )
    outcome, candidates = provider.discover(_context())
    assert outcome.status.value == "OK"
    assert len(candidates) == 1
    assert candidates[0].requested_url == "https://news.example/rafale-deploy"


def test_rss_provider_partial_success_when_one_feed_fails():
    def handler(request):
        if "good" in str(request.url):
            return httpx.Response(200, headers={"content-type": "application/rss+xml"}, content=RSS_XML)
        return httpx.Response(500)

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    provider = RssFeedProvider(
        feeds_by_language={"en": ("https://feeds.example/good.xml", "https://feeds.example/bad.xml")},
        client_factory=factory,
    )
    outcome, candidates = provider.discover(_context())
    assert outcome.status.value == "PARTIAL"
    assert len(candidates) == 1


def test_search_api_provider_reports_missing_credentials_without_network_call():
    calls = []
    provider = ConfiguredSearchApiProvider(api_url="", api_key="", client_factory=_rss_client_factory({}, calls))
    outcome, candidates = provider.discover(_context())
    assert outcome.status.value == "UNAVAILABLE_MISSING_CREDENTIALS"
    assert outcome.requires_credentials is True
    assert candidates == []
    assert calls == []


def test_search_api_provider_returns_candidates_when_configured():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"title": "Rafale deal update", "url": "https://wire.example/rafale", "published_at": "2026-10-01"},
        ]})

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    provider = ConfiguredSearchApiProvider(api_url="https://search.example/api", api_key="k", client_factory=factory)
    outcome, candidates = provider.discover(_context())
    assert outcome.status.value == "OK"
    assert len(candidates) == 1
    assert candidates[0].requested_url == "https://wire.example/rafale"


def _search_provider(handler):
    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    return ConfiguredSearchApiProvider(
        api_url="https://search.example/api", api_key="k", client_factory=factory, sleep=lambda _s: None,
    )


def test_search_api_empty_results_are_not_success_with_candidates():
    provider = _search_provider(lambda request: httpx.Response(200, json={"results": []}))
    outcome, candidates = provider.discover(_context())
    assert outcome.status.value == "COMPLETED_NO_RESULTS"
    assert candidates == []
    assert outcome.http_attempts == 1


def test_search_api_malformed_json_and_missing_results_are_errors():
    bad = _search_provider(lambda request: httpx.Response(200, content=b"not-json"))
    outcome, candidates = bad.discover(_context())
    assert outcome.status.value == "ERROR" and candidates == []
    assert "not valid JSON" in outcome.message
    missing = _search_provider(lambda request: httpx.Response(200, json={"items": []}))
    outcome, candidates = missing.discover(_context())
    assert outcome.status.value == "ERROR" and "results" in outcome.message
    assert candidates == []


def test_search_api_timeout_is_an_error_after_bounded_retries():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    provider = _search_provider(handler)
    outcome, candidates = provider.discover(_context())
    assert outcome.status.value == "ERROR" and candidates == []
    assert outcome.http_attempts == 3
    assert "timed out" in outcome.message or "timeout" in outcome.message.lower()


# ---------------------------------------------------------------------------
# 3. Deterministic ranking and missing metadata
# ---------------------------------------------------------------------------


def test_ranking_is_deterministic():
    args = dict(title="Rafale jets deployed", published_at="2026-10-01", candidate_language="en",
               candidate_type="news_feed", topic="Rafale jets", search_terms=(), requested_language="en",
               preferred_source_types=())
    first = score_candidate(**args)
    second = score_candidate(**args)
    assert first == second


def test_ranking_missing_date_is_neutral_not_fabricated():
    factors = score_candidate(
        title="Rafale jets deployed", published_at=None, candidate_language="en", candidate_type="news_feed",
        topic="Rafale jets", search_terms=(), requested_language="en", preferred_source_types=(),
    )
    assert factors.recency_score == 0.5
    assert "No publication date" in factors.explanation[1]


# ---------------------------------------------------------------------------
# 4. Canonical-URL dedup (distinct publishers with similar titles stay distinct)
# ---------------------------------------------------------------------------


def test_service_dedups_same_canonical_url_across_providers():
    registry = DiscoveryProviderRegistry()
    calls = []
    feed_xml = RSS_XML
    rss = RssFeedProvider(
        feeds_by_language={"en": ("https://feeds.example/defence.xml",)},
        client_factory=_rss_client_factory({"https://feeds.example/defence.xml": feed_xml}, calls),
    )

    def search_handler(request):
        return httpx.Response(200, json={"results": [
            {"title": "Rafale jets deployed near border (duplicate)",
             "url": "https://news.example/rafale-deploy", "published_at": "2026-10-05"},
        ]})

    def search_factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(search_handler), **kw)

    search = ConfiguredSearchApiProvider(api_url="https://search.example/api", api_key="k", client_factory=search_factory)
    registry.register(rss)
    registry.register(search)

    service = DiscoveryService(guard=UrlGuard(resolver=_resolver), registry=registry)
    request = request_from_dict({"topic": "Rafale jets"}, job_id="job-1")
    result = service.run(request)

    assert result.returned == 1
    assert result.candidates[0].merged_from_providers in (("configured_search_api",), ("rss_feed",))
    assert any("seen from both" in note for note in result.dedup_notes)


def test_service_keeps_distinct_publishers_with_similar_titles():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"title": "Rafale jets deployed", "url": "https://publisher-a.example/story", "published_at": "2026-10-05"},
            {"title": "Rafale jets deployed", "url": "https://publisher-b.example/story", "published_at": "2026-10-05"},
        ]})

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    registry = DiscoveryProviderRegistry()
    registry.register(ConfiguredSearchApiProvider(api_url="https://search.example/api", api_key="k", client_factory=factory))
    service = DiscoveryService(guard=UrlGuard(resolver=_resolver), registry=registry)
    request = request_from_dict(
        {"topic": "Rafale jets", "filters": {"providers": ["configured_search_api"]}}, job_id="job-1",
    )
    result = service.run(request)
    assert result.returned == 2  # different canonical URLs never merged


# ---------------------------------------------------------------------------
# 5. SSRF protection reused from F3
# ---------------------------------------------------------------------------


def test_rss_provider_blocks_private_address_targets():
    def blocked_resolver(host, port):
        return ["127.0.0.1"]

    provider = RssFeedProvider(feeds_by_language={"en": ("https://internal.example/feed.xml",)})
    context = DiscoveryContext(
        job_id="job-1", topic="x", search_terms=(), language="en", filters=DiscoveryFilters(),
        limits=DiscoveryLimits(), guard=UrlGuard(resolver=blocked_resolver), cancel=threading.Event(),
    )
    outcome, candidates = provider.discover(context)
    assert outcome.status.value == "ERROR"
    assert candidates == []


# ---------------------------------------------------------------------------
# 6. Worker integration: missing request fails closed; full run through F2
# ---------------------------------------------------------------------------


def _new_queued_job(db, key="disc"):
    initialize_database(db)
    job = JobService(db).create(OWNER, key, "Rafale jets", "en")
    WorkerService(db).queue(job["job_id"], OWNER)
    return job["job_id"]


def test_missing_discovery_request_fails_the_stage_closed(tmp_path):
    db = tmp_path / "db.sqlite3"
    store = ArtifactStore(tmp_path / "storage")

    def trivial(_context, _cancel):
        return None

    discover = build_discovery_handler(store, guard=UrlGuard(resolver=_resolver))
    handlers = {JOB_STAGES[0]: trivial, JOB_STAGES[1]: discover}
    job_id = _new_queued_job(db)
    worker = Worker(db, "w1", handlers, retry_safe_stages=set(handlers))
    while worker.run_once():
        pass

    state = JobService(db).get(job_id, OWNER)
    stage1 = next(s for s in state["stages"] if s["stage_key"] == JOB_STAGES[1])
    assert stage1["status"] == "FAILED"
    assert "discovery_request" in stage1["error"]


def test_full_discovery_runs_through_durable_worker(tmp_path):
    db = tmp_path / "db.sqlite3"
    store = ArtifactStore(tmp_path / "storage")
    job_id = _new_queued_job(db)

    def produce_request(context, _cancel):
        payload = {"job_id": context.job_id, "topic": "Rafale jets"}
        data = json.dumps(payload).encode("utf-8")
        uri = f"jobs/{context.job_id}/discovery/discovery_request.json"
        stored = store.write_bytes(context.job_id, uri, data, "application/json")
        return [{"artifact_type": "discovery_request", "uri": stored.uri,
                 "sha256": stored.sha256, "schema_version": "defence-studio.discovery-request.v1"}]

    registry = DiscoveryProviderRegistry()
    registry.register(RssFeedProvider(
        feeds_by_language={"en": ("https://feeds.example/defence.xml",)},
        client_factory=_rss_client_factory({"https://feeds.example/defence.xml": RSS_XML}),
    ))
    discover = build_discovery_handler(store, registry=registry, guard=UrlGuard(resolver=_resolver))
    handlers = {JOB_STAGES[0]: produce_request, JOB_STAGES[1]: discover}
    worker = Worker(db, "w1", handlers, retry_safe_stages=set(handlers))
    while worker.run_once():
        pass

    state = JobService(db).get(job_id, OWNER)
    stages = {s["stage_key"]: s["status"] for s in state["stages"]}
    assert stages[JOB_STAGES[0]] == "COMPLETED"
    assert stages[JOB_STAGES[1]] == "COMPLETED"

    result_ref = next(a for a in state["artifacts"] if a["artifact_type"] == "discovery_result")
    assert store.sha256_of(job_id, result_ref["uri"]) == result_ref["sha256"]
    result = json.loads(store.read_text(job_id, result_ref["uri"]))
    assert result["returned"] == 1


def test_worker_retry_after_lease_expiry_still_completes_discovery(tmp_path):
    db = tmp_path / "db.sqlite3"
    store = ArtifactStore(tmp_path / "storage")
    job_id = _new_queued_job(db)

    def produce_request(context, _cancel):
        payload = {"job_id": context.job_id, "topic": "Rafale jets"}
        data = json.dumps(payload).encode("utf-8")
        uri = f"jobs/{context.job_id}/discovery/discovery_request.json"
        stored = store.write_bytes(context.job_id, uri, data, "application/json")
        return [{"artifact_type": "discovery_request", "uri": stored.uri,
                 "sha256": stored.sha256, "schema_version": "defence-studio.discovery-request.v1"}]

    foundation = Worker(db, "w0", {JOB_STAGES[0]: produce_request}, retry_safe_stages={JOB_STAGES[0]})
    while foundation.run_once():
        pass

    registry = DiscoveryProviderRegistry()
    registry.register(RssFeedProvider(
        feeds_by_language={"en": ("https://feeds.example/defence.xml",)},
        client_factory=_rss_client_factory({"https://feeds.example/defence.xml": RSS_XML}),
    ))
    discover = build_discovery_handler(store, registry=registry, guard=UrlGuard(resolver=_resolver))
    ws = WorkerService(db)

    t0 = datetime(2026, 10, 10, tzinfo=timezone.utc)
    ctx1 = ws.claim_next("w-a", {JOB_STAGES[1]}, lease_seconds=5, now=t0)
    assert ctx1 is not None
    discover(ctx1, threading.Event())
    recovered = ws.recover_expired(t0 + timedelta(seconds=6))
    assert recovered == 1

    t1 = t0 + timedelta(seconds=8)
    ctx2 = ws.claim_next("w-b", {JOB_STAGES[1]}, lease_seconds=30, now=t1)
    assert ctx2 is not None and ctx2.attempt == 2
    outputs = discover(ctx2, threading.Event())
    ws.complete(ctx2, outputs, now=t1 + timedelta(seconds=1))

    state = JobService(db).get(job_id, OWNER)
    stage1 = next(s for s in state["stages"] if s["stage_key"] == JOB_STAGES[1])
    assert stage1["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# 7. API-facing service: request idempotency/conflict, ownership, selection,
#    and the discovery -> F3 acquisition_request contract handoff
# ---------------------------------------------------------------------------


def _drive_to_completed_discovery(tmp_path, job_id, db, store_root):
    """Produce a discovery_result artifact the way the real worker would."""
    store = ArtifactStore(store_root)
    registry = DiscoveryProviderRegistry()
    registry.register(RssFeedProvider(
        feeds_by_language={"en": ("https://feeds.example/defence.xml",)},
        client_factory=_rss_client_factory({"https://feeds.example/defence.xml": RSS_XML}),
    ))
    discover = build_discovery_handler(store, registry=registry, guard=UrlGuard(resolver=_resolver))

    def produce_request(context, _cancel):
        payload = {"job_id": context.job_id, "topic": "Rafale jets"}
        data = json.dumps(payload).encode("utf-8")
        uri = f"jobs/{context.job_id}/discovery/discovery_request.json"
        stored = store.write_bytes(context.job_id, uri, data, "application/json")
        return [{"artifact_type": "discovery_request", "uri": stored.uri,
                 "sha256": stored.sha256, "schema_version": "defence-studio.discovery-request.v1"}]

    handlers = {JOB_STAGES[0]: produce_request, JOB_STAGES[1]: discover}
    worker = Worker(db, "w1", handlers, retry_safe_stages=set(handlers))
    while worker.run_once():
        pass


def _api(tmp_path):
    db = tmp_path / "db.sqlite3"
    store_root = tmp_path / "storage"
    initialize_database(db)
    job = JobService(db).create(OWNER, "api-disc", "Rafale jets", "en")
    return db, store_root, job["job_id"], DiscoveryApiService(db, store_root)


def test_create_request_is_idempotent_for_identical_content(tmp_path):
    db, store_root, job_id, api = _api(tmp_path)
    first = api.create_request(job_id, OWNER, {"topic": "Rafale jets"})
    second = api.create_request(job_id, OWNER, {"topic": "Rafale jets"})
    assert first["artifact"]["version"] == second["artifact"]["version"]


def test_create_request_rejects_wrong_owner(tmp_path):
    from defence_studio.services.discovery import DiscoveryNotFound
    db, store_root, job_id, api = _api(tmp_path)
    with pytest.raises(DiscoveryNotFound):
        api.create_request(job_id, "someone-else", {"topic": "Rafale jets"})


def test_create_request_conflicts_once_discovery_has_started(tmp_path):
    from defence_studio.services.discovery import DiscoveryConflict
    db, store_root, job_id, api = _api(tmp_path)
    api.create_request(job_id, OWNER, {"topic": "Rafale jets"})
    WorkerService(db).queue(job_id, OWNER)
    _drive_to_completed_discovery(tmp_path, job_id, db, store_root)
    with pytest.raises(DiscoveryConflict):
        api.create_request(job_id, OWNER, {"topic": "Different topic"})


def test_select_candidates_rejects_unknown_id(tmp_path):
    db, store_root, job_id, api = _api(tmp_path)
    api.create_request(job_id, OWNER, {"topic": "Rafale jets"})
    WorkerService(db).queue(job_id, OWNER)
    _drive_to_completed_discovery(tmp_path, job_id, db, store_root)
    with pytest.raises(DiscoveryValidationError):
        api.select_candidates(job_id, OWNER, ["does-not-exist"])


def test_select_candidates_rejects_before_discovery_completes(tmp_path):
    db, store_root, job_id, api = _api(tmp_path)
    api.create_request(job_id, OWNER, {"topic": "Rafale jets"})
    with pytest.raises(DiscoveryValidationError):
        api.select_candidates(job_id, OWNER, ["anything"])


def test_select_candidates_produces_f3_compatible_acquisition_request(tmp_path):
    db, store_root, job_id, api = _api(tmp_path)
    api.create_request(job_id, OWNER, {"topic": "Rafale jets"})
    WorkerService(db).queue(job_id, OWNER)
    _drive_to_completed_discovery(tmp_path, job_id, db, store_root)

    candidates = api.list_candidates(job_id, OWNER)["candidates"]
    assert len(candidates) == 1
    candidate_id = candidates[0]["candidate_id"]

    selection = api.select_candidates(job_id, OWNER, [candidate_id])
    # The real F3 parser must accept this payload unmodified.
    parsed = acquisition_request_from_dict(selection["acquisition_request"])
    assert parsed.job_id == job_id
    assert parsed.targets[0].target_id == candidate_id
    assert parsed.targets[0].url == candidates[0]["canonical_url"]

    # Re-selecting the same candidates reuses the artifact (no duplicate version).
    again = api.select_candidates(job_id, OWNER, [candidate_id])
    assert again["artifact"]["version"] == selection["artifact"]["version"]

    persisted = api.get_selection(job_id, OWNER)
    assert persisted["selected_candidate_ids"] == [candidate_id]


def test_api_cross_owner_candidate_listing_denied(tmp_path):
    db, store_root, job_id, api = _api(tmp_path)
    api.create_request(job_id, OWNER, {"topic": "Rafale jets"})
    WorkerService(db).queue(job_id, OWNER)
    _drive_to_completed_discovery(tmp_path, job_id, db, store_root)

    app = create_app(db, SECRET, store_root)
    with TestClient(app) as client:
        mine = client.get(f"/api/jobs/{job_id}/discovery/candidates",
                          headers={"Authorization": f"Bearer {_access_token(OWNER)}"})
        other = client.get(f"/api/jobs/{job_id}/discovery/candidates",
                           headers={"Authorization": f"Bearer {_access_token('intruder')}"})
    assert mine.status_code == 200
    assert len(mine.json()["candidates"]) == 1
    assert other.status_code == 404


# ---------------------------------------------------------------------------
# 8. RM-1: taxonomy, filters, pagination, retry, history, providers
# ---------------------------------------------------------------------------

from defence_studio.discovery import taxonomy as tx  # noqa: E402
from defence_studio.contracts.discovery import result_to_dict  # noqa: E402
from defence_studio.discovery.providers import fetch_with_retry  # noqa: E402
from defence_studio.acquisition.http_client import HttpFetchError  # noqa: E402


def test_classify_prefers_feed_config_then_tag_then_keywords():
    assert tx.classify("Anything", None, feed_category="NEIGHBOURHOOD") == ("NEIGHBOURHOOD", "FEED_CONFIG")
    assert tx.classify("Anything", None, provider_tag="RUSSIA_UKRAINE") == ("RUSSIA_UKRAINE", "PROVIDER_TAG")
    assert tx.classify("Ukraine drone strike overnight", None) == ("RUSSIA_UKRAINE", "KEYWORD_RULE")
    assert tx.classify("Cooking tips", None) == ("UNCLASSIFIED", "UNCLASSIFIED")
    regions, basis = tx.classify_regions("Pakistan tests missile", None)
    assert regions == ["PAKISTAN"] and basis == "KEYWORD_RULE"


def test_invalid_feed_config_fails_closed(tmp_path):
    path = tmp_path / "feeds.json"
    path.write_text(json.dumps({"feeds": [{"url": "https://x.example/f", "category": "NOPE"}]}))
    with pytest.raises(tx.DiscoveryConfigError):
        tx.load_feed_configs(path)
    path.write_text("{not json")
    with pytest.raises(tx.DiscoveryConfigError):
        tx.load_feed_configs(path)


def test_shipped_feed_config_is_valid():
    feeds = tx.load_feed_configs()
    assert feeds and all(f.url.startswith("https://") for f in feeds)


def test_request_rejects_unknown_filter_values():
    with pytest.raises(RequestValidationError):
        request_from_dict({"topic": "x", "filters": {"categories": ["BOGUS"]}}, job_id="j")
    with pytest.raises(RequestValidationError):
        request_from_dict({"topic": "x", "filters": {"surprise": ["a"]}}, job_id="j")
    ok = request_from_dict({"topic": "x", "filters": {"categories": ["INDIAN_DEFENCE"], "regions": ["INDIA"]}}, job_id="j")
    assert ok.filters.categories == ("INDIAN_DEFENCE",)


def _feed_service_run(filters, configs=None):
    registry = DiscoveryProviderRegistry()
    if configs is None:
        configs = [tx.FeedConfig(url="https://a.example/feed.xml", language="en", category="INDIAN_DEFENCE",
                                 source_class="OFFICIAL", publisher="Pub A", region="INDIA")]
    registry.register(RssFeedProvider(
        feed_configs=configs, client_factory=_rss_client_factory({"https://a.example/feed.xml": RSS_XML})))
    service = DiscoveryService(guard=UrlGuard(resolver=_resolver), registry=registry)
    return service.run(request_from_dict({"topic": "Rafale", "filters": filters}, job_id="j"))


def test_taxonomy_fields_filled_from_feed_config_and_filters_apply():
    result = _feed_service_run({})
    assert result.returned >= 1
    c = result.candidates[0]
    assert (c.category, c.category_basis, c.source_class) == ("INDIAN_DEFENCE", "FEED_CONFIG", "OFFICIAL")
    assert c.regions == ("INDIA",) and c.retrieved_at == result.discovered_at
    assert _feed_service_run({"categories": ["INDIAN_DEFENCE"]}).returned == result.returned
    assert _feed_service_run({"categories": ["RUSSIA_UKRAINE"]}).returned == 0
    assert _feed_service_run({"source_classes": ["COMPANY_RELEASE"]}).returned == 0


def test_unverifiable_filter_flags_not_drops():
    result = _feed_service_run({"source_classes": ["OFFICIAL"]},
                               configs=[tx.FeedConfig(url="https://a.example/feed.xml", language="en")])
    assert result.returned >= 1
    assert all("source_class_filter_unverifiable" in c.filter_flags for c in result.candidates)


class _Flaky:
    def __init__(self, failures, retryable=True, message="Source returned HTTP 503"):
        self.calls, self.failures, self.retryable, self.message = 0, failures, retryable, message

    def get(self, url, cancel=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise HttpFetchError("http_status", self.message, retryable=self.retryable)
        return "ok"


def test_fetch_with_retry_retries_retryable_only_and_counts_attempts():
    sleeps = []
    assert fetch_with_retry(_Flaky(2), "u", None, sleeps.append) == ("ok", 3, False)
    assert len(sleeps) == 2
    g = _Flaky(5, retryable=False)
    with pytest.raises(HttpFetchError) as err:
        fetch_with_retry(g, "u", None, lambda s: None)
    assert g.calls == 1 and err.value.attempts == 1
    h = _Flaky(9)
    with pytest.raises(HttpFetchError):
        fetch_with_retry(h, "u", None, lambda s: None)
    assert h.calls == 3  # 1 try + 2 retries


def test_429_marks_rate_limited():
    with pytest.raises(HttpFetchError) as err:
        fetch_with_retry(_Flaky(9, message="Source returned HTTP 429"), "u", None, lambda s: None)
    assert err.value.rate_limited is True


def test_provider_descriptor_has_no_secret():
    d = ConfiguredSearchApiProvider(api_url="https://search.example/api", api_key="SUPER-SECRET-KEY").describe()
    assert d["configured"] is True and "SUPER-SECRET-KEY" not in json.dumps(d)
    assert ConfiguredSearchApiProvider(api_url="", api_key="").describe()["configured"] is False


def test_search_api_sends_key_as_bearer_and_redacts_errors():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(401)

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    registry = DiscoveryProviderRegistry()
    registry.register(ConfiguredSearchApiProvider(api_url="https://search.example/api", api_key="SUPER-SECRET-KEY",
                                                  client_factory=factory, sleep=lambda s: None))
    result = DiscoveryService(guard=UrlGuard(resolver=_resolver), registry=registry).run(
        request_from_dict({"topic": "Rafale"}, job_id="j"))
    assert seen["auth"] == "Bearer SUPER-SECRET-KEY"
    assert result.providers[0].status.value == "ERROR"
    assert "SUPER-SECRET-KEY" not in json.dumps(result_to_dict(result))


def test_near_duplicate_same_publisher_title_day_merged():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"title": "Rafale jets deployed", "publisher": "Pub", "url": "https://p.example/a?x=1", "published_at": "2026-10-05"},
            {"title": "Rafale Jets Deployed!", "publisher": "Pub", "url": "https://p.example/amp/a", "published_at": "2026-10-05"},
        ]})

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    registry = DiscoveryProviderRegistry()
    registry.register(ConfiguredSearchApiProvider(api_url="https://search.example/api", api_key="k", client_factory=factory))
    result = DiscoveryService(guard=UrlGuard(resolver=_resolver), registry=registry).run(
        request_from_dict({"topic": "Rafale jets"}, job_id="j"))
    assert result.returned == 1 and result.candidates[0].warnings


def test_api_listing_pagination_history_rerun(tmp_path):
    from defence_studio.services.discovery import DiscoveryNotFound, DiscoveryValidationError
    db, store_root, job_id, api = _api(tmp_path)
    api.create_request(job_id, OWNER, {"topic": "Rafale jets"})
    WorkerService(db).queue(job_id, OWNER)
    _drive_to_completed_discovery(tmp_path, job_id, db, store_root)
    page = api.list_candidates(job_id, OWNER, page_size=1)
    assert page["total_count"] >= 1 and len(page["candidates"]) == 1
    if page["next_cursor"]:
        second = api.list_candidates(job_id, OWNER, page_size=1, cursor=page["next_cursor"])
        assert second["candidates"][0]["candidate_id"] != page["candidates"][0]["candidate_id"]
    with pytest.raises(DiscoveryValidationError):
        api.list_candidates(job_id, OWNER, page_size=0)
    with pytest.raises(DiscoveryValidationError):
        api.list_candidates(job_id, OWNER, cursor="abc")
    flagged = api.list_candidates(job_id, OWNER, category="RUSSIA_UKRAINE")["candidates"]
    assert all("category_filter_unverifiable" in c["filter_flags"] for c in flagged)  # kept + flagged, not dropped
    history = api.list_searches(OWNER)
    assert history["count"] == 1 and history["searches"][0]["status"] in ("COMPLETED", "COMPLETED_WITH_FAILURES")
    assert api.list_searches("someone-else")["count"] == 0
    rerun = api.rerun(job_id, OWNER)
    assert rerun["job_id"] != job_id and rerun["rerun_of"] == job_id
    assert api.list_searches(OWNER)["count"] == 2
    with pytest.raises(DiscoveryNotFound):
        api.rerun(job_id, "someone-else")


def test_http_routes_providers_taxonomy_searches_auth_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL", "https://search.example/api")
    monkeypatch.setenv("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY", "ENV-SECRET-KEY")
    db, store_root, job_id, api = _api(tmp_path)
    app = create_app(db, SECRET, store_root)
    headers = {"Authorization": f"Bearer {_access_token(OWNER)}"}
    with TestClient(app) as client:
        for path in ("/api/discovery/providers", "/api/discovery/taxonomy", "/api/discovery/searches"):
            assert client.get(path).status_code == 401
        providers = client.get("/api/discovery/providers", headers=headers)
        assert providers.status_code == 200
        assert "ENV-SECRET-KEY" not in providers.text
        names = {p["name"]: p for p in providers.json()["providers"]}
        assert names["configured_search_api"]["configured"] is True
        assert "INDIAN_DEFENCE" in client.get("/api/discovery/taxonomy", headers=headers).json()["categories"]
        assert client.get("/api/discovery/searches", headers=headers).json()["count"] == 0
        assert client.get(f"/api/jobs/{job_id}/discovery/candidates?page_size=0", headers=headers).status_code == 422
