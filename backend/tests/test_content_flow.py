"""Content-only chain: fixture HTTP, simulated human review and mocked AI; no paid calls."""
import json

import httpx

from defence_studio.acquisition.handler import build_acquisition_handler
from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.database import initialize_database
from defence_studio.discovery.handler import build_discovery_handler
from defence_studio.discovery.providers import DiscoveryProviderRegistry, ManualUrlProvider
from defence_studio.providers.offline_text import OfflineExtractiveProvider
from defence_studio.research_worker import build_research_worker
from defence_studio.services.discovery import DiscoveryApiService
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.jobs import JobService
from defence_studio.services.review import ReviewService
from defence_studio.services.script import ScriptService
from defence_studio.services.worker import WorkerService
from defence_studio.storage.artifacts import ArtifactStore
from test_script_phase3 import GOOD_CONFIG

URL = "https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2323814&lang=2&reg=48"
TOPIC = "GSAT-7C and Varunastra defence contracts"
# Paraphrased official-source facts, explicitly a transport fixture rather than live acquisition.
TEXT = ("According to the Ministry of Defence, GSAT-7C is intended for the Indian Air Force. "
        "The Ministry of Defence reported that GSAT-7C supports secure communications for Air Force assets. "
        "The Ministry of Defence described GSAT-7C as indigenously designed and manufactured. "
        "According to the Ministry of Defence, Varunastra is an indigenous ship-launched anti-submarine torpedo. "
        "The Ministry of Defence reported that Varunastra was developed by Naval Science and Technological Laboratory. "
        "The Ministry of Defence stated that Varunastra enhances the Indian Navy anti-submarine capability.")


class MockContentProvider:
    name = "mock-ai-content-contract"

    def __init__(self):
        self.prompts = []

    def generate(self, **kwargs):
        self.prompts.append(kwargs["user_prompt"])
        return OfflineExtractiveProvider().generate(**kwargs)


def build_content_fixture(work):
    db, root = work / "ds.sqlite3", work / "store"
    initialize_database(db)
    owner = "content-fixture-reviewer"
    jobs = JobService(db)
    job = jobs.create(owner, "content-topic", TOPIC, "en")["job_id"]
    store = ArtifactStore(root)
    requests = []

    def transport(request):
        assert str(request.url) == URL
        requests.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=
            '<html lang="en"><head><title>GSAT-7C and Varunastra contracts</title>'
            '<meta property="og:site_name" content="Ministry of Defence / PIB">'
            f'</head><body><div>Posted On: 15 OCT 2026 3:22PM by PIB Delhi</div><article><p>{TEXT}</p></article></body></html>')

    factory = lambda **kw: httpx.Client(transport=httpx.MockTransport(transport), **kw)
    guard = UrlGuard(policy=SourcePolicy.load(), resolver=lambda *_: ["93.184.216.34"])
    registry = DiscoveryProviderRegistry()
    registry.register(ManualUrlProvider(client_factory=factory, sleep=lambda _: None))
    discovery = DiscoveryApiService(db, root)
    discovery.create_request(job, owner, {"topic": TOPIC, "language": "en", "seed_urls": [URL]})
    WorkerService(db).queue(job, owner)
    # Explicit small fixture policy; production still requires 2 sources / 8 claims.
    from defence_studio.services.evidence_policy import EvidencePolicyService, load_evidence_policy
    fixture_policy = load_evidence_policy()
    fixture_policy["readiness"].update(min_accepted_sources=1, min_accepted_claims=1)
    fixture_policy["relevance"]["min_source_anchor_mentions"] = 1
    assessor = EvidencePolicyService(db, root, policy=fixture_policy)
    worker = build_research_worker(db, root, assessment_service=assessor,
        discovery_handler=build_discovery_handler(store, registry=registry, guard=guard, client_factory=factory),
        acquisition_handler=build_acquisition_handler(store, guard=guard, client_factory=factory))
    while worker.run_once():
        pass
    worker.shutdown()
    assert jobs.get(job, owner)["status"] == "PAUSED"
    review = ReviewService(db, root)
    sources = review.list_sources(job, owner)
    claims = review.list_claims(job, owner)
    assert requests and claims["claims"]
    assert sources["sources"][0]["declared_metadata"]["published_at"] == "2026-10-15"
    # Automatic assessment uses the isolated fixture policy, without human decisions.
    evidence = EvidenceService(db, root)
    draft = evidence.draft(job, owner)
    assert draft["package"]["ready"], draft["package"]["blockers"]
    assert draft["current_approval"]["reviewer_id"] == "AUTOMATED_POLICY"
    assert all(c["review"] is None for c in claims["claims"])
    return db, root, job, owner


def test_research_reaches_mock_ai_and_persisted_draft(tmp_path):
    db, root, job, owner = build_content_fixture(tmp_path)
    provider = MockContentProvider()
    service = ScriptService(db, root, provider=provider)
    config = {**GOOD_CONFIG, "language": "en", "terminology": {}}
    revision = service.generate(job, owner, config, "content-generate")
    assert revision["status"] == "APPROVED", revision["validation"]
    assert revision["approval"]["approval_kind"] == "AUTOMATED_VALIDATION"
    assert provider.prompts and "Varunastra" in provider.prompts[0]
    sources = ReviewService(db, root).list_sources(job, owner)["sources"]
    assert any(s.get("canonical_url") == URL for s in sources)
    assert revision["sections"] and any(s["narration"].strip() for s in revision["sections"])
    assert all(s["evidence"]["source_ids"] for s in revision["sections"])
    fresh = ScriptService(db, root, provider=provider)
    loaded = fresh.get_revision(job, owner, "en", revision["version"])
    assert loaded["sections"] == revision["sections"]
    replay = fresh.generate(job, owner, config, "content-generate")
    assert replay["version"] == revision["version"] and len(provider.prompts) == 1
    assert JobService(db).get(job, owner)["status"] == "PAUSED"
