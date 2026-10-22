"""Empty-run recovery, immutable history, policy boundaries; all transport is mocked."""
import json
import pytest
from defence_studio.database import initialize_database, connect
from defence_studio.services.jobs import JobService
from defence_studio.services.worker import WorkerService
from defence_studio.services.discovery import DiscoveryApiService, DiscoveryConflict, DiscoveryNotFound
from defence_studio.research_worker import build_research_worker
from defence_studio.storage.artifacts import ArtifactStore
from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError

OWNER = "recovery-test"

def empty_run(tmp_path):
    db, root = tmp_path / "test.sqlite3", tmp_path / "store"
    initialize_database(db)
    jobs = JobService(db)
    jid = jobs.create(OWNER, "one", "India defence", "en")["job_id"]
    service = DiscoveryApiService(db, root)
    payload = {"topic": "India defence", "language": "en"}
    service.create_request(jid, OWNER, payload)
    def empty(context, cancel):
        store = ArtifactStore(root)
        uri = f"jobs/{jid}/empty/{context.claim_id}.json"
        blob = store.write_bytes(jid, uri, json.dumps({"candidates": [], "returned": 0}).encode(), "application/json")
        return [{"artifact_type": "discovery_result", "uri": uri, "sha256": blob.sha256, "schema_version": "defence-studio.discovery-result.v2"}]
    worker = build_research_worker(db, root, discovery_handler=empty)
    WorkerService(db).queue(jid, OWNER)
    for _ in range(4):
        assert worker.run_once()
    assert jobs.get(jid, OWNER)["status"] == "AWAITING_REVIEW"
    return db, root, jid, jobs, service, worker, payload

def test_empty_retry_keeps_identity_bytes_history_and_is_idempotent(tmp_path):
    db, root, jid, jobs, service, worker, payload = empty_run(tmp_path)
    old = jobs.get(jid, OWNER)
    store = ArtifactStore(root)
    assert service.retry_empty(jid, OWNER, "retry-one", payload)["status"] == "QUEUED"
    assert service.retry_empty(jid, OWNER, "retry-one", payload)["replayed"]
    for _ in range(3):
        assert worker.run_once()
    assert not worker.run_once()
    state = jobs.get(jid, OWNER)
    from defence_studio.services.research_monitor import ResearchMonitorService
    monitor = ResearchMonitorService(db, root).monitor(jid, OWNER)
    with connect(db) as timing:
        latest = timing.execute("SELECT occurred_at FROM workflow_transitions WHERE job_id=? AND stage_key='news_feed_and_topic_discovery' AND to_status='RUNNING' ORDER BY transition_id DESC LIMIT 1", (jid,)).fetchone()[0]
    assert monitor["stages"][1]["started_at"] == latest
    assert state["status"] == "AWAITING_REVIEW"
    assert state["job_id"] == jid
    assert len(jobs.list_for_owner(OWNER)) == 1
    assert service.retry_empty(jid, OWNER, "retry-one", payload)["replayed"]
    assert len(state["artifacts"]) > len(old["artifacts"])
    for a in old["artifacts"]:
        assert store.sha256_of(jid, a["uri"]) == a["sha256"]
    with connect(db) as c:
        assert c.execute("SELECT count(*) FROM workflow_transitions WHERE job_id=? AND error LIKE 'Owner requested retry%'", (jid,)).fetchone()[0] == 4
    assert state["stages"][4]["status"] == "AWAITING_REVIEW"
    assert state["stages"][5]["status"] == "PENDING"

def test_retry_rejects_wrong_owner_key_change_and_nonempty_evidence(tmp_path):
    db, root, jid, jobs, service, worker, payload = empty_run(tmp_path)
    with pytest.raises(DiscoveryNotFound):
        service.retry_empty(jid, "intruder", "key", payload)
    service.retry_empty(jid, OWNER, "key", payload)
    with pytest.raises(DiscoveryNotFound):
        service.retry_empty(jid, "intruder", "key", payload)
    with pytest.raises(DiscoveryConflict):
        service.retry_empty(jid, OWNER, "key", {**payload, "search_terms": ["different"]})
    for _ in range(3): worker.run_once()
    # The original fixture's populated candidate result must prevent in-place retry.
    a = next(a for a in jobs.get(jid, OWNER)["artifacts"] if a["artifact_type"] == "discovery_result")
    ArtifactStore(root).write_bytes(jid, a["uri"], b'{"candidates":[{"url":"https://example.com"}]}', "application/json")
    # Integrity failure is also a fail-closed rejection, before state mutation.
    from defence_studio.services.discovery import DiscoveryValidationError
    with pytest.raises(DiscoveryValidationError):
        service.retry_empty(jid, OWNER, "new-key", payload)
    assert jobs.get(jid, OWNER)["status"] == "AWAITING_REVIEW"

@pytest.mark.parametrize("url", [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://breakingdefense.com/feed/",
    "https://www.thehindu.com/news/national/feeder/default.rss",
])
def test_verified_feed_exact_registry_paths(url):
    policy = SourcePolicy.load()
    guard = UrlGuard(policy=policy, resolver=lambda *_: ["93.184.216.34"])
    assert guard.validate(url).url == url
    from urllib.parse import urlsplit
    host = urlsplit(url).hostname
    for bad in [url + "&other=1", f"https://{host}/unapproved", url.replace(host, host + ".evil.test"), url.replace(host, "sub." + host), url.replace("https://", "ftp://"), url.replace("https://", "https://secret@")]:
        with pytest.raises(UrlValidationError): guard.validate(bad)
    with pytest.raises(UrlValidationError):
        UrlGuard(policy=policy, resolver=lambda *_: ["127.0.0.1"]).validate(url)




def test_retry_rejects_existing_sources_and_reviewed_work(tmp_path):
    from defence_studio.services.discovery import DiscoveryValidationError
    db, root, jid, jobs, service, worker, payload = empty_run(tmp_path)
    store = ArtifactStore(root)
    artifact = next(a for a in jobs.get(jid, OWNER)["artifacts"] if a["artifact_type"] == "acquisition_manifest")
    value = json.loads(store.read_text(jid, artifact["uri"]))
    value["sources"] = [{"source_id": "persisted"}]
    stored = store.write_bytes(jid, artifact["uri"], json.dumps(value).encode(), "application/json")
    with connect(db) as c:
        c.execute("UPDATE workflow_artifacts SET sha256=? WHERE artifact_id=?", (stored.sha256, artifact["artifact_id"]))
        c.commit()
    with pytest.raises(DiscoveryConflict, match="already contains evidence"):
        service.retry_empty(jid, OWNER, "blocked", payload)
    assert jobs.get(jid, OWNER)["status"] == "AWAITING_REVIEW"


def test_retry_api_auth_validation_and_replay(tmp_path):
    from fastapi.testclient import TestClient
    from defence_studio.api.app import create_app
    from defence_studio.auth import mint_dev_session_token
    db, root, jid, jobs, service, worker, payload = empty_run(tmp_path)
    secret = "disposable-recovery-test-secret-long-enough"
    with TestClient(create_app(database_file=db, storage_root_path=root, signing_secret=secret)) as client:
        token = mint_dev_session_token(secret, OWNER)
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "http-retry"}
        route = f"/api/jobs/{jid}/discovery/retry-empty"
        assert client.post(route, json=payload).status_code == 401
        result = client.post(route, json=payload, headers=headers)
        assert result.status_code == 202, result.text
        assert result.json()["job_id"] == jid
        assert client.post(route, json=payload, headers=headers).json()["replayed"]


def test_feed_redirect_cannot_escape_policy():
    import httpx
    from defence_studio.acquisition.http_client import GuardedFetcher, HttpFetchError
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://attacker.example/collect"})
    def factory(**kw): return httpx.Client(transport=httpx.MockTransport(response), **kw)
    guard = UrlGuard(policy=SourcePolicy.load(), resolver=lambda *_: ["93.184.216.34"])
    with pytest.raises(HttpFetchError, match="not permitted"):
        GuardedFetcher(guard, 10000, 2, 3, client_factory=factory).get("https://breakingdefense.com/feed/")
    assert calls == ["https://breakingdefense.com/feed/"]

