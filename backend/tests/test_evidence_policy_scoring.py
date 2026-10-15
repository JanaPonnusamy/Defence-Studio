"""Automatic evidence policy: deterministic 0-100 scoring, strict classes, independence, gates.

All fixtures are local; no network, no provider. The policy engine records decisions under the
append-only ``evidence_policy_decisions`` table (never the human review ledger) and never alter a human decision.
"""
import copy
import sqlite3
from contextlib import closing

import pytest

from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.contracts.analysis import ANALYSIS_RESULT_ARTIFACT
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.services.analysis import AnalysisService
from defence_studio.services.evidence_policy import EvidencePolicyService, load_evidence_policy
from defence_studio.services.job_workspace import JobWorkspaceService
from defence_studio.services.review import ReviewService
from test_review_f5 import (ACQUISITION_STAGE, ANALYSIS_STAGE, OWNER, _advance, _complete, _setup, _source,
                            _store_acquisition, ACQUISITION_RESULT_ARTIFACT, ACQUISITION_RESULT_SCHEMA)

TOPIC = "S-400"
FILLER = ("The S-400 Triumf is a Russian long range air defence missile system. "
          "The S-400 is also known by the NATO reporting name SA-21 Growler. "
          "Analysts describe the S-400 as a layered air defence system. ")
RANGE = "The S-400 has a maximum engagement range of 400 km with the 40N6 missile. "
SERVICE = "The S-400 Triumf entered Russian military service during the year 2007 after long development. "

REGISTRY = {
    "version": "defence-studio.source-policy.v1",
    "sources": [
        {"id": "csis", "domain": "csis.example", "category": "RESEARCH_INSTITUTION", "publisher": "CSIS",
         "independence_group": "csis", "enabled": True,
         "trust": {"descriptive_claims": True, "quantitative_claims": "CORROBORATION_REQUIRED"}},
        {"id": "rusi", "domain": "rusi.example", "category": "RESEARCH_INSTITUTION", "publisher": "RUSI",
         "independence_group": "rusi", "enabled": True,
         "trust": {"descriptive_claims": True, "quantitative_claims": "CORROBORATION_REQUIRED"}},
        {"id": "maker", "domain": "maker.example", "category": "MANUFACTURER", "publisher": "Maker",
         "independence_group": "maker", "enabled": True,
         "trust": {"descriptive_claims": True, "quantitative_claims": "ATTRIBUTED_ONLY"}},
    ],
}


def _policy(**readiness):
    pol = copy.deepcopy(load_evidence_policy())
    pol["readiness"].update({"min_accepted_claims": 1, "min_accepted_sources": 1}, **readiness)
    return pol


def build(tmp_path, sources, *, policy=None):
    """sources: list of (source_id, url, text). Returns (service, job_id, review, db, root)."""
    db, root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "policy-job", TOPIC, "en")
    job_id = job["job_id"]
    jobs.transition(job_id, OWNER, JOB_STAGES[0], "SKIPPED")
    jobs.transition(job_id, OWNER, JOB_STAGES[1], "SKIPPED")
    _advance(jobs, job_id, ACQUISITION_STAGE)
    built = [_source(store, job_id, sid, url, text) for sid, url, text in sources]
    uri, sha = _store_acquisition(store, job_id, built)
    jobs.add_artifact(job_id, OWNER, ACQUISITION_STAGE, ACQUISITION_RESULT_ARTIFACT, uri, sha, ACQUISITION_RESULT_SCHEMA)
    _complete(jobs, job_id, ACQUISITION_STAGE)
    _advance(jobs, job_id, ANALYSIS_STAGE)
    ref, _ = AnalysisService(db, root).run_and_store(job_id, OWNER, uri, sha, topic=TOPIC)
    jobs.add_artifact(job_id, OWNER, ANALYSIS_STAGE, ref["artifact_type"], ref["uri"], ref["sha256"], ref["schema_version"])
    _complete(jobs, job_id, ANALYSIS_STAGE)
    svc = EvidencePolicyService(db, root, policy=policy or _policy(), source_policy=SourcePolicy.from_dict(REGISTRY),
                                workspace=JobWorkspaceService(db, tmp_path / "ws"))
    return svc, job_id, ReviewService(db, root), db, root


def by_text(result, needle):
    return [c for c in result["claims"] if needle in c["text"]]


def two_groups(tmp_path):
    return build(tmp_path, [("a", "https://csis.example/s400", FILLER + RANGE + SERVICE),
                            ("b", "https://rusi.example/s400", FILLER + RANGE)])


def test_strong_corroborated_claim_is_auto_accepted_with_score_breakdown(tmp_path):
    svc, job, *_ = two_groups(tmp_path)
    res = svc.evaluate(job, OWNER)
    claim = by_text(res, "400 km")[0]
    assert claim["state"] == "AUTO_ACCEPTED", claim
    sc = claim["score"]
    assert sc["scale"] == "0-100" and sc["total"] >= 70 and sc["calibrated"] is False
    assert set(sc["components"]) == set(sc["weights"]) and sum(sc["weights"].values()) == 100
    assert sc["scoring_policy_version"] == "defence-studio.claim-score.v1"
    assert claim["supporting_evidence"] and claim["contradicting_evidence"] == []
    assert res["scoring_policy_version"] == "defence-studio.claim-score.v1"


def test_single_source_performance_claim_is_not_accepted(tmp_path):
    svc, job, *_ = build(tmp_path, [("a", "https://csis.example/s400", FILLER + RANGE)])
    claim = by_text(svc.evaluate(job, OWNER), "400 km")[0]
    assert claim["state"] == "NEEDS_REVIEW"
    assert "policy requires 2" in claim["reasons"][0]


def test_same_group_urls_are_not_independent(tmp_path):
    svc, job, *_ = build(tmp_path, [("a", "https://csis.example/one", FILLER + RANGE),
                                    ("b", "https://csis.example/two", FILLER + RANGE)])
    claims = by_text(svc.evaluate(job, OWNER), "400 km")
    assert claims and all(c["state"] != "AUTO_ACCEPTED" for c in claims)


def test_manufacturer_performance_claim_is_not_independent_verification(tmp_path):
    svc, job, *_ = build(tmp_path, [("a", "https://maker.example/s400", FILLER + RANGE),
                                    ("b", "https://csis.example/s400", FILLER + RANGE)])
    res = svc.evaluate(job, OWNER)
    maker = [c for c in res["claims"] if c["source_id"] == "a" and "400 km" in c["text"]][0]
    assert maker["state"] == "NEEDS_REVIEW"
    csis = [c for c in res["claims"] if c["source_id"] == "b" and "400 km" in c["text"]][0]
    assert csis["state"] != "AUTO_ACCEPTED"     # duplicate copy / one non-manufacturer group


def test_disallowed_source_is_blocked_and_its_claims_excluded(tmp_path):
    svc, job, *_ = build(tmp_path, [("a", "https://csis.example/s400", FILLER + SERVICE),
                                    ("x", "https://random.example/s400", FILLER + RANGE)])
    res = svc.evaluate(job, OWNER)
    assert [s for s in res["sources"] if s["source_id"] == "x"][0]["state"] == "SOURCE_BLOCKED"
    assert all(c["state"] == "SOURCE_BLOCKED" for c in res["claims"] if c["source_id"] == "x")


def test_missing_publication_date_stays_unknown(tmp_path):
    svc, job, *_ = two_groups(tmp_path)
    src = svc.evaluate(job, OWNER)["sources"][0]
    assert src["published_at"] is None and src["published_at_basis"] == "UNKNOWN"


def test_scoring_is_deterministic(tmp_path):
    svc, job, *_ = two_groups(tmp_path)
    a, b = svc.evaluate(job, OWNER), svc.evaluate(job, OWNER)
    assert a == b


def test_high_score_cannot_override_failed_mandatory_condition(tmp_path):
    pol = _policy()
    pol["scoring"]["auto_accept_min_score"] = 0
    svc, job, *_ = build(tmp_path, [("a", "https://csis.example/s400", FILLER + RANGE)], policy=pol)
    claim = by_text(svc.evaluate(job, OWNER), "400 km")[0]
    assert claim["state"] == "NEEDS_REVIEW"


def test_score_below_threshold_blocks_acceptance(tmp_path):
    pol = _policy()
    pol["scoring"]["auto_accept_min_score"] = 101
    svc, job, *_ = two_groups(tmp_path)
    svc.policy = pol
    assert all(c["state"] != "AUTO_ACCEPTED" for c in svc.evaluate(job, OWNER)["claims"])


def test_readiness_fails_with_exact_reasons_under_default_thresholds(tmp_path):
    svc, job, *_ = two_groups(tmp_path)
    svc.policy = load_evidence_policy()
    out = svc.run(job, OWNER)
    assert not out["ready_for_generation"]
    assert out["policy_criteria_failed"][0]["criterion"] == "min_accepted_claims"
    assert out["package_approval"] is None and "Generation is stopped" in out["explanation"]


def test_run_records_policy_actor_idempotently_and_preserves_human_history(tmp_path):
    svc, job, review, db, _ = two_groups(tmp_path)
    claim = by_text(svc.evaluate(job, OWNER), "400 km")[0]
    ana_sha = svc.evaluate(job, OWNER)["analysis_sha256"]
    review.decide_claim(job, OWNER, claim["claim_id"], "NEEDS_MORE_EVIDENCE", ana_sha, 0, "human hold", "h1")
    first = svc.run(job, OWNER)
    assert any(h["subject"] == claim["claim_id"] for h in first["human_decisions_preserved"])
    rec = [c for c in review.list_claims(job, OWNER)["claims"] if c["claim_id"] == claim["claim_id"]][0]["review"]
    assert rec["decision"] == "NEEDS_MORE_EVIDENCE" and rec["reviewer_id"] == OWNER
    second = svc.run(job, OWNER)
    assert first["run_id"] == second["run_id"] and first["result_sha256"] == second["result_sha256"]


def test_policy_version_change_rerecords_decisions(tmp_path):
    svc, job, review, db, root = two_groups(tmp_path)
    svc.run(job, OWNER)
    pol = _policy()
    pol["version"] = "defence-studio.evidence-policy.v2"
    v2 = EvidencePolicyService(db, root, policy=pol, source_policy=svc.sources, workspace=svc.workspace)
    out = v2.run(job, OWNER)
    assert out["policy_version"].endswith("v2")
    with closing(sqlite3.connect(db)) as c:
        versions = [r[0] for r in c.execute(
            "SELECT DISTINCT policy_version FROM evidence_policy_decisions WHERE job_id=? ORDER BY decision_id", (job,))]
        assert versions == ["defence-studio.evidence-policy.v1", "defence-studio.evidence-policy.v2"]
        assert c.execute("SELECT COUNT(*) FROM claim_reviews").fetchone()[0] == 0     # human ledger untouched
    assert not [c for c in review.list_claims(job, OWNER)["claims"] if c.get("review")]


def test_decision_records_survive_workspace_cleanup(tmp_path):
    svc, job, _, db, root = two_groups(tmp_path)
    svc.run(job, OWNER)
    svc.workspace.sweep() if hasattr(svc.workspace, "sweep") else None   # non-terminal job: nothing removed
    assert svc.latest(job, OWNER)["counts"]["claims"]
    with closing(sqlite3.connect(db)) as c:
        assert c.execute("SELECT COUNT(*) FROM evidence_policy_runs WHERE job_id=?", (job,)).fetchone()[0] == 1
