"""Pre-live-test regression: readiness / hash / policy / budget gates run BEFORE any
provider request, blocked generation makes zero outbound requests, and policy or
generation never write the human review ledger. Isolated DBs, mock transport only."""
import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from defence_studio.providers.openai_text import OpenAiTextProvider
from defence_studio.services.evidence_policy import EvidencePolicyService, load_evidence_policy
from defence_studio.services.review import ReviewError
from defence_studio.services.script import ScriptGenerationFailed, ScriptService
from test_evidence_policy import make_job, policy_service, source_policy
from test_review_f5 import OWNER
from test_script_phase3 import GOOD_CONFIG

KEY = "sk-readiness-TESTKEY-000000"


class Transport:
    def __init__(self, body_for_claims):
        self.requests = []
        self.body_for_claims = body_for_claims

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(self.body_for_claims)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 100}})


def _provider(transport):
    return OpenAiTextProvider(api_key=KEY, max_retries=0, client_factory=lambda **k: httpx.Client(
        transport=httpx.MockTransport(transport), **k))


def _counts(db):
    with closing(sqlite3.connect(db)) as c:
        return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("claim_reviews", "claim_review_history"))


def _ready_job(tmp_path):
    db, root, jobs, job = make_job(tmp_path)
    result = policy_service(db, root, tmp_path).run(job, OWNER)
    assert result["ready_for_generation"] and result["package_approval"], "fixture must be ready"
    return db, root, job, result


def _body(db, root, job):
    pkg = ScriptService(db, root).draft(job, OWNER)["package"]
    cid = pkg["eligible_claim_ids"][0]
    return {"title": "t", "opening_hook": "", "closing": "c", "sections": [{
        "section_id": "s1", "heading": "h", "narration": "According to the source, a system exists.",
        "requires_evidence": True, "statement_kind": "ATTRIBUTED_CLAIM",
        "evidence": {"claim_ids": [cid], "source_ids": [], "qualification": "attributed"}, "presentation_note": ""}]}


def _tree_hashes(root, job):
    base = Path(root) / "jobs" / job
    return {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*")) if p.is_file() and "scripts" not in p.parts}


def test_ready_package_builds_request_once_and_leaves_review_ledger_and_artifacts_untouched(tmp_path):
    db, root, job, run = _ready_job(tmp_path)
    assert run["package_approval"] is not None and "AUTOMATED_POLICY" in json.dumps(run["package_approval"])
    before_counts, before_tree = _counts(db), _tree_hashes(root, job)
    t = Transport(_body(db, root, job))
    ScriptService(db, root, provider=_provider(t)).generate(job, OWNER, GOOD_CONFIG, "live-ready-1")
    assert len(t.requests) == 1
    msgs = t.requests[0]["messages"]
    assert msgs[0]["role"] == "system" and "Approved evidence" in msgs[1]["content"]
    assert KEY not in json.dumps(t.requests)
    assert _counts(db) == before_counts == (0, 0)            # automated policy never writes the human ledger
    assert _tree_hashes(root, job) == before_tree             # durable evidence untouched


@pytest.mark.parametrize("artifact", ["research/acquisition_result.json", "research/analysis"])
def test_stale_or_tampered_hash_blocks_generation_with_zero_requests(tmp_path, artifact):
    db, root, job, _ = _ready_job(tmp_path)
    t = Transport({})
    base = Path(root) / "jobs" / job / "research"
    target = base / "acquisition_result.json" if artifact.endswith(".json") else next((base / "analysis").glob("*.json"))
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ReviewError):
        ScriptService(db, root, provider=_provider(t)).generate(job, OWNER, GOOD_CONFIG, "live-stale-1")
    assert t.requests == []


def test_outdated_policy_approval_is_not_current_after_policy_change(tmp_path):
    db, root, job, run = _ready_job(tmp_path)
    ep = copy.deepcopy(load_evidence_policy())
    ep["version"] = "defence-studio.evidence-policy.v2"
    ep["readiness"].update({"min_accepted_claims": 999, "min_accepted_sources": 2})
    ep["relevance"]["min_source_anchor_mentions"] = 1
    v2 = EvidencePolicyService(db, root, policy=ep, source_policy=source_policy(),
                               workspace=policy_service(db, root, tmp_path).workspace)
    out = v2.run(job, OWNER)
    assert out["policy_version"].endswith("v2") and not out["ready_for_generation"]
    t = Transport({})
    with pytest.raises(ReviewError):
        ScriptService(db, root, provider=_provider(t)).generate(job, OWNER, GOOD_CONFIG, "live-policy-1")
    assert t.requests == []
    assert _counts(db) == (0, 0)


def test_insufficient_evidence_blocks_generation_with_zero_requests(tmp_path):
    db, root, jobs, job = make_job(tmp_path)
    out = policy_service(db, root, tmp_path, min_accepted_claims=999).run(job, OWNER)
    assert not out["ready_for_generation"]
    t = Transport({})
    with pytest.raises(ReviewError):
        ScriptService(db, root, provider=_provider(t)).generate(job, OWNER, GOOD_CONFIG, "live-insuff-1")
    assert t.requests == []


def test_budget_failure_blocks_after_gate_and_before_any_request(tmp_path):
    db, root, job, _ = _ready_job(tmp_path)
    t = Transport(_body(db, root, job))
    with pytest.raises(ScriptGenerationFailed):
        ScriptService(db, root, provider=_provider(t)).generate(
            job, OWNER, {**GOOD_CONFIG, "max_text_cost_inr": 0.0001}, "live-budget-1")
    assert t.requests == []
    # unknown model cannot be priced -> refused for a paid provider
    with pytest.raises(ScriptGenerationFailed):
        ScriptService(db, root, provider=_provider(t)).generate(
            job, OWNER, {**GOOD_CONFIG, "model": "unpriced-model"}, "live-budget-2")
    assert t.requests == []
