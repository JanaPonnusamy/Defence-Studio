"""Topic discovery feeds acquisition without a manual URL list."""
from __future__ import annotations

import json

import httpx
import pytest

from defence_studio.acquisition.handler import build_acquisition_handler
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import initialize_database
from defence_studio.services.analysis import make_analysis_handler
from defence_studio.services.jobs import JobService
from defence_studio.services.review import ReviewNotFound, ReviewService
from defence_studio.services.worker import Worker, WorkerService
from defence_studio.storage.artifacts import ArtifactStore

OWNER = "topic-owner"
PUBLIC = "93.184.216.34"
PAGE = (
    "<html lang='en'><head><title>Fleet note</title>"
    "<meta property='og:site_name' content='Example Desk'></head><body>"
    "<p>The Rafale unit has 12 aircraft in 2026.</p></body></html>"
)


def _resolver(host, port):
    return [PUBLIC]


def _factory(counter, pages):
    def handler(request):
        counter["n"] += 1
        counter["urls"].append(str(request.url))
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="missing")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)

    def factory(**kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    return factory


def _discovery_payload(candidates):
    return {
        "schema_version": "defence-studio.discovery-result.v2",
        "candidates": candidates,
        "providers": [{
            "provider": "configured_search_api",
            "status": "UNAVAILABLE_MISSING_CREDENTIALS",
            "message": "Search API credentials are not configured.",
        }],
        "status": "COMPLETED" if candidates else "COMPLETED_NO_RESULTS",
    }


def _candidate(cid, url):
    return {
        "candidate_id": cid,
        "canonical_url": url,
        "declared_metadata": {"title": cid, "publisher": "Example Desk", "language": "en"},
    }


def _run(tmp_path, discovery_doc, pages, extra_artifacts=None):
    db = tmp_path / "db.sqlite3"
    store = ArtifactStore(tmp_path / "storage")
    initialize_database(db)
    job_id = JobService(db).create(OWNER, "topic", "Rafale", "en")["job_id"]
    WorkerService(db).queue(job_id, OWNER)
    counter = {"n": 0, "urls": []}

    def foundation(_context, _cancel):
        return None

    def discover(context, _cancel):
        data = json.dumps(discovery_doc).encode()
        stored = store.write_bytes(
            context.job_id, f"jobs/{context.job_id}/research/discovery_result.json",
            data, "application/json",
        )
        outputs = [{
            "artifact_type": "discovery_result", "uri": stored.uri,
            "sha256": stored.sha256, "schema_version": "defence-studio.discovery-result.v2",
        }]
        for artifact in extra_artifacts or []:
            body_doc = dict(artifact["body"])
            if body_doc.get("job_id") == "filled-by-handler":
                body_doc["job_id"] = context.job_id
            body = json.dumps(body_doc).encode()
            written = store.write_bytes(context.job_id, artifact["uri"].format(job_id=context.job_id), body, "application/json")
            outputs.append({
                "artifact_type": artifact["artifact_type"], "uri": written.uri,
                "sha256": written.sha256, "schema_version": artifact["schema_version"],
            })
        return outputs

    handlers = {
        JOB_STAGES[0]: foundation,
        JOB_STAGES[1]: discover,
        JOB_STAGES[2]: build_acquisition_handler(
            store, guard=UrlGuard(resolver=_resolver), client_factory=_factory(counter, pages),
        ),
        JOB_STAGES[3]: make_analysis_handler(db, tmp_path / "storage"),
    }
    worker = Worker(db, "topic-worker", handlers, retry_safe_stages=set(handlers))
    while worker.run_once():
        pass
    return db, job_id, counter


def test_discovered_urls_are_fetched_and_claims_cite_them(tmp_path):
    db, job_id, counter = _run(tmp_path, _discovery_payload([
        _candidate("keep", "https://qa.example/keep"),
        _candidate("missing", "https://qa.example/missing"),
    ]), {"/keep": PAGE})
    state = JobService(db).get(job_id, OWNER)
    assert state["status"] == "AWAITING_REVIEW"
    assert state["stages"][5]["status"] == "PENDING"
    review = ReviewService(db, tmp_path / "storage")
    sources = {row["target_id"]: row for row in review.list_sources(job_id, OWNER)["sources"]}
    assert sources["keep"]["status"] == "ACQUIRED"
    assert sources["keep"]["normalized"]["status"] == "NORMALIZED"
    assert sources["missing"]["status"] == "FAILED"
    assert sources["missing"]["normalized"]["status"] == "FAILED"
    claims = review.list_claims(job_id, OWNER)["claims"]
    assert claims and all(claim["source_id"] == sources["keep"]["source_id"] for claim in claims)
    assert all(claim["evidence_class"] == "unverified" for claim in claims)
    assert claims[0]["passage"]["text"] in sources["keep"]["normalized"]["normalized_text"]
    with pytest.raises(ReviewNotFound):
        review.list_claims(job_id, "someone-else")


def test_private_url_is_not_fetched(tmp_path):
    db, job_id, counter = _run(tmp_path, _discovery_payload([
        _candidate("local", "http://127.0.0.1/secret"),
    ]), {})
    review = ReviewService(db, tmp_path / "storage")
    source = review.list_sources(job_id, OWNER)["sources"][0]
    assert counter["n"] == 0
    assert source["status"] == "FAILED"
    assert source["error"]["code"] == "blocked_address"
    assert review.list_claims(job_id, OWNER)["claims"] == []


def test_empty_discovery_does_not_pretend_sources_exist(tmp_path):
    db, job_id, counter = _run(tmp_path, _discovery_payload([]), {})
    assert counter["n"] == 0
    review = ReviewService(db, tmp_path / "storage")
    listed = review.list_sources(job_id, OWNER)
    assert listed["sources"] == []
    body = json.loads(ArtifactStore(tmp_path / "storage").read_text(job_id, listed["acquisition_artifact_uri"]))
    assert body["requested"] == 0 and body["succeeded"] == 0
    assert body["warnings"]
    assert review.list_claims(job_id, OWNER)["claims"] == []


def test_explicit_selection_limits_what_is_fetched(tmp_path):
    request = {
        "schema_version": "defence-studio.acquisition-request.v1",
        "job_id": "filled-by-handler",
        "targets": [{"target_id": "chosen", "url": "https://qa.example/chosen", "method": "http_page"}],
    }
    db, job_id, counter = _run(
        tmp_path,
        _discovery_payload([
            _candidate("other", "https://qa.example/other"),
            _candidate("chosen", "https://qa.example/chosen"),
        ]),
        {"/chosen": PAGE, "/other": PAGE},
        extra_artifacts=[{
            "artifact_type": "acquisition_request",
            "schema_version": "defence-studio.acquisition-request.v1",
            "uri": "jobs/{job_id}/discovery/selection.json",
            "body": request,
        }],
    )
    assert counter["urls"] and all(url.endswith("/chosen") for url in counter["urls"])
    sources = ReviewService(db, tmp_path / "storage").list_sources(job_id, OWNER)["sources"]
    assert [row["target_id"] for row in sources] == ["chosen"]
