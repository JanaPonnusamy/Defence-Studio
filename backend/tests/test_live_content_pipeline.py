"""Opt-in live path: one permitted source, real acquisition, real OpenAI.

Ordinary pytest runs skip this. Set DEFENCE_STUDIO_LIVE_CONTENT_PIPELINE=1 to
spend one bounded Chat Completions call. Search-provider credentials are not
required: the configured search API is absent, so this uses an explicit
allowlisted source URL. It does not write human source or claim reviews.
"""
from __future__ import annotations

import json
import os
import shutil
from contextlib import closing
from pathlib import Path

import pytest

from defence_studio.acquisition.handler import build_acquisition_handler
from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.database import connect, initialize_database
from defence_studio.discovery.handler import build_discovery_handler
from defence_studio.discovery.providers import DiscoveryProviderRegistry, ManualUrlProvider
from defence_studio.providers.openai_text import OpenAiTextProvider
from defence_studio.research_worker import build_research_worker
from defence_studio.services.discovery import DiscoveryApiService
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.evidence_policy import EvidencePolicyService, load_evidence_policy
from defence_studio.services.jobs import JobService
from defence_studio.services.review import ReviewService
from defence_studio.services.script import ScriptService
from defence_studio.services.worker import WorkerService
from defence_studio.storage.artifacts import ArtifactStore

URL = "https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2323814&lang=2&reg=48"
TOPIC = "GSAT-7C and Varunastra defence contracts"
LIVE = os.environ.get("DEFENCE_STUDIO_LIVE_CONTENT_PIPELINE") == "1"


def _load_ignored_env():
    path = Path(__file__).resolve().parents[1] / ".env.local"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


@pytest.mark.skipif(not LIVE, reason="opt-in live OpenAI; set DEFENCE_STUDIO_LIVE_CONTENT_PIPELINE=1")
def test_live_permitted_source_to_structured_openai_draft():
    _load_ignored_env()
    if not os.environ.get("DEFENCE_STUDIO_OPENAI_API_KEY"):
        pytest.fail("DEFENCE_STUDIO_OPENAI_API_KEY is not configured; no live call was made")
    root = Path(os.environ.get("TEMP", ".")) / "defence-studio-live-content-20261022"
    if root.exists():
        shutil.rmtree(root)
    db, store_root = root / "ds.sqlite3", root / "store"
    root.mkdir(parents=True, exist_ok=True)
    initialize_database(db)
    owner = "live-content-operator"
    job = JobService(db).create(owner, "live-content", TOPIC, "en")["job_id"]
    store = ArtifactStore(store_root)
    guard = UrlGuard(policy=SourcePolicy.load())
    registry = DiscoveryProviderRegistry()
    registry.register(ManualUrlProvider(sleep=lambda _s: None))
    DiscoveryApiService(db, store_root).create_request(
        job, owner, {"topic": TOPIC, "language": "en", "seed_urls": [URL]})
    WorkerService(db).queue(job, owner)
    policy = load_evidence_policy()
    # Isolated readiness so one permitted article can reach generation.
    # Production policy files are not modified.
    policy["readiness"].update(min_accepted_sources=1, min_accepted_claims=1)
    policy["relevance"]["min_source_anchor_mentions"] = 1
    worker = build_research_worker(
        db, store_root, assessment_service=EvidencePolicyService(db, store_root, policy=policy),
        discovery_handler=build_discovery_handler(store, registry=registry, guard=guard),
        acquisition_handler=build_acquisition_handler(store, guard=guard))
    while worker.run_once():
        pass
    worker.shutdown()
    review = ReviewService(db, store_root)
    sources = review.list_sources(job, owner)["sources"]
    claims = review.list_claims(job, owner)["claims"]
    assert sources and claims, "real acquisition produced no source or claims"
    assert sources[0].get("canonical_url") == URL
    draft = EvidenceService(db, store_root).draft(job, owner)
    assert draft["package"]["ready"], draft["package"].get("blockers")
    assert all(c["review"] is None for c in claims)
    provider = OpenAiTextProvider(max_retries=0, timeout=90)
    service = ScriptService(db, store_root, provider=provider)
    config = {
        "language": "en", "audience": "general public", "tone": "measured",
        "script_format": "narration", "target_duration_seconds": 60, "target_words": 120,
        "model": "gpt-4o-mini", "prompt_version": "defence-studio.source-grounded-script.v1",
        "max_text_cost_inr": 1.0, "terminology": {},
    }
    revision = service.generate(job, owner, config, "live-openai-structured-1")
    constraint = provider.last_exchange["output_constraint"]
    assert constraint["mode"] == "json_schema" and constraint["strict"] is True
    assert constraint.get("parse") == "direct"
    assert provider.last_exchange["response"]["http_status"] == 200
    assert revision["validation"]["passed"], revision["validation"]["issues"]
    assert revision["usage"]["provider"] == "openai"
    assert revision["usage"]["model"] == "gpt-4o-mini"
    assert revision["usage"]["tokens_available"] is True
    assert revision["sections"]
    reopened = ScriptService(db, store_root).get_revision(job, owner, "en", revision["version"])
    assert reopened["sections"] == revision["sections"]
    assert reopened["title"] == revision["title"]
    with closing(connect(db)) as connection:
        human_reviews = connection.execute("SELECT COUNT(*) FROM source_reviews").fetchone()[0]
        human_reviews += connection.execute("SELECT COUNT(*) FROM claim_reviews").fetchone()[0]
        human_reviews += connection.execute("SELECT COUNT(*) FROM source_review_history").fetchone()[0]
        human_reviews += connection.execute("SELECT COUNT(*) FROM claim_review_history").fetchone()[0]
        script_actors = [row[0] for row in connection.execute(
            "SELECT reviewer_id FROM script_review_history")]
    assert human_reviews == 0
    assert script_actors == ["AUTOMATED_VALIDATION"] or revision["status"] != "APPROVED"
    summary = {
        "job_id": job,
        "owner_id": owner,
        "database": str(db),
        "storage": str(store_root),
        "source_url": URL,
        "source_count": len(sources),
        "claim_count": len(claims),
        "published_at": (sources[0].get("declared_metadata") or {}).get("published_at"),
        "model": revision["usage"]["model"],
        "provider": revision["usage"]["provider"],
        "http_status": provider.last_exchange["response"]["http_status"],
        "structured_output": constraint,
        "validation_passed": revision["validation"]["passed"],
        "status": revision["status"],
        "section_count": len(revision["sections"]),
        "input_tokens": revision["usage"]["input_tokens"],
        "output_tokens": revision["usage"]["output_tokens"],
        "retries": revision["usage"]["retries"],
        "measured_text_cost_inr": revision["usage"]["measured_text_cost_inr"],
        "request_sha256": revision["request_sha256"],
        "package_sha256": revision["package_sha256"],
    }
    (root / "live-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    blob = json.dumps(summary) + json.dumps(revision["usage"])
    assert "sk-" not in blob
