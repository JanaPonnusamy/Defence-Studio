"""Offline integration: production handlers, mocked network only at transport."""
import json

import httpx
import pytest

from defence_studio.acquisition.handler import build_acquisition_handler
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.database import initialize_database
from defence_studio.discovery.handler import build_discovery_handler
from defence_studio.discovery.providers import DiscoveryProviderRegistry, RssFeedProvider
from defence_studio.research_worker import build_research_worker
from defence_studio.services.discovery import DiscoveryApiService, DiscoveryConflict
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.jobs import JobService
from defence_studio.services.review import ReviewService
from defence_studio.services.worker import WorkerService
from defence_studio.storage.artifacts import ArtifactStore

OWNER = "offline-browser-test"
FEED = b'''<rss><channel><title>Offline QA fixture</title>
<item><title>Rafale update one</title><link>https://qa.example/one</link></item>
<item><title>Rafale update two</title><link>https://qa.example/two</link></item>
</channel></rss>'''


def pipeline(tmp_path):
    db, root = tmp_path / "workflow.sqlite3", tmp_path / "storage"
    initialize_database(db)
    jobs, store = JobService(db), ArtifactStore(root)
    job = jobs.create(OWNER, "offline-pipeline", "Rafale", "en")["job_id"]
    discovery = DiscoveryApiService(db, root)
    discovery.create_request(job, OWNER, {"topic":"Rafale", "language":"en", "filters":{"providers":["rss_feed"]}})
    calls = []
    def response(request):
        calls.append(str(request.url))
        if request.url.path == "/feed":
            return httpx.Response(200, headers={"content-type":"application/rss+xml"}, content=FEED)
        return httpx.Response(200, headers={"content-type":"text/html"}, text='''<html lang="en"><head>
<title>Offline test source</title><meta name="author" content="QA fixture"></head><body>
<p>Officials reportedly confirmed the Rafale unit has 12 aircraft in 2026.</p></body></html>''')
    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(response), **kw)
    guard = UrlGuard(resolver=lambda *_: ["93.184.216.34"])
    registry = DiscoveryProviderRegistry()
    registry.register(RssFeedProvider(feeds_by_language={"en":("https://qa.example/feed",)}, client_factory=factory))
    def worker():
        return build_research_worker(db, root,
            discovery_handler=build_discovery_handler(store, registry=registry, guard=guard),
            acquisition_handler=build_acquisition_handler(store, guard=guard, client_factory=factory))
    WorkerService(db).queue(job, OWNER)
    running = worker()
    assert running.run_once()  # actual foundation handler
    assert running.run_once()  # actual discovery handler
    return db, root, job, jobs, store, discovery, worker, calls


def test_topic_acquires_discovered_urls_and_stops_for_human_review(tmp_path):
    db, root, job, jobs, store, discovery, worker, calls = pipeline(tmp_path)
    assert len(calls) == 1
    candidates = discovery.list_candidates(job, OWNER)["candidates"]
    assert len(candidates) == 2
    restarted = worker()
    assert restarted.run_once()  # acquisition of every discovered URL
    assert restarted.run_once()  # analysis
    assert not restarted.run_once()  # human review; script is not registered
    state = jobs.get(job, OWNER)
    assert state["status"] == "AWAITING_REVIEW"
    assert state["stages"][4]["status"] == "AWAITING_REVIEW"
    assert state["stages"][5]["status"] == "PENDING"
    assert {c.split("/")[-1] for c in calls} >= {"feed", "one", "two"}
    review = ReviewService(db, root)
    sources = review.list_sources(job, OWNER)
    claims = review.list_claims(job, OWNER)
    assert len(sources["sources"]) == 2
    assert all(source["status"] == "ACQUIRED" for source in sources["sources"])
    assert claims["claims"]
    assert all(claim["review_status"] == "PENDING" for claim in claims["claims"])
    assert any(claim["evidence_class"] == "attributed" for claim in claims["claims"])
    for claim in claims["claims"]:
        assert claim["passage"]["text"] == claim["claim_text"]
        assert claim["source_id"] in {source["source_id"] for source in sources["sources"]}
    draft = EvidenceService(db, root).draft(job, OWNER)
    assert draft["package"]["ready"] is False
    assert any("approved" in blocker.lower() for blocker in draft["package"]["blockers"])
    assert draft["approvals"] == []


def test_selection_revisions_are_immutable_and_locked_after_claim(tmp_path):
    db, root, job, jobs, store, discovery, worker, calls = pipeline(tmp_path)
    ids = [c["candidate_id"] for c in discovery.list_candidates(job, OWNER)["candidates"]]
    first = discovery.select_candidates(job, OWNER, [ids[0]])
    second = discovery.select_candidates(job, OWNER, ids)
    assert first["artifact"]["uri"] != second["artifact"]["uri"]
    assert store.sha256_of(job, first["artifact"]["uri"]) == first["artifact"]["sha256"]
    assert discovery.select_candidates(job, OWNER, list(reversed(ids)))["artifact"] == second["artifact"]
    worker().run_once()
    with pytest.raises(DiscoveryConflict):
        discovery.select_candidates(job, OWNER, [ids[0]])
    assert discovery.select_candidates(job, OWNER, ids)["artifact"] == second["artifact"]


def test_discovery_request_updates_preserve_old_bytes(tmp_path):
    db, root = tmp_path / "request.db", tmp_path / "storage"
    initialize_database(db)
    job = JobService(db).create(OWNER, "request", "Rafale", "en")["job_id"]
    service = DiscoveryApiService(db, root)
    first = service.create_request(job, OWNER, {"topic":"Rafale"})
    service.create_request(job, OWNER, {"topic":"Rafale updates"})
    ref = first["artifact"]
    assert ArtifactStore(root).sha256_of(job, ref["uri"]) == ref["sha256"]
