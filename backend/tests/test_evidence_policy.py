"""Controlled source registry, automatic evidence policy, readiness, and job-workspace cleanup.

All offline: fake resolver / fake transport / fake provider. No network, no paid call.
"""
import copy
import json
import sqlite3
import threading
from contextlib import closing

import pytest

from defence_studio.acquisition.source_policy import SourcePolicy, SourcePolicyError
from defence_studio.acquisition.url_guard import UrlGuard, UrlValidationError
from defence_studio.contracts.analysis import ANALYSIS_RESULT_ARTIFACT
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import connect
from defence_studio.services.analysis import AnalysisService
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.evidence_policy import (
    AUTO_ACCEPTED, NEEDS_REVIEW, REJECTED, SOURCE_BLOCKED, EvidencePolicyService, load_evidence_policy)
from defence_studio.services.job_workspace import JobWorkspaceService, WorkspaceError
from defence_studio.services.review import ReviewService, StaleRevision
from defence_studio.services.script import ScriptService
from test_review_f5 import (ACQUISITION_STAGE, ANALYSIS_STAGE, OWNER, _advance, _complete, _setup, _source,
                            _store_acquisition)
from test_script_phase3 import GOOD_CONFIG, FakeProvider

TOPIC = "S-400 Triumf air defence system"
REGISTRY = {
    "version": "defence-studio.source-policy.v1",
    "sources": [
        {"id": "inst-a", "domain": "research.example", "url_patterns": ["^/s400"], "category": "RESEARCH_INSTITUTION",
         "publisher": "Institute A", "independence_group": "a", "enabled": True,
         "trust": {"descriptive_claims": True, "quantitative_claims": "CORROBORATION_REQUIRED"}},
        {"id": "news-b", "domain": "news.example", "include_subdomains": True, "url_patterns": ["^/"],
         "category": "INDEPENDENT_REPORTING", "publisher": "News B", "independence_group": "b", "enabled": True,
         "trust": {"descriptive_claims": True, "quantitative_claims": "CORROBORATION_REQUIRED"}},
        {"id": "off", "domain": "disabled.example", "url_patterns": ["^/"], "category": "RESEARCH_INSTITUTION",
         "enabled": False, "trust": {"descriptive_claims": True}},
    ],
}
SRC_A = ("The S-400 Triumf is a long-range surface-to-air missile system developed in Russia.\n"
         "The S-400 can engage aerial targets at ranges up to 400 km according to the manufacturer.\n"
         "The S-400 uses the 48N6 missile family for medium-range engagements.\n"
         "The deployment status of the S-400 40N6 missile is unclear.\n"
         "Skip to the content facebook twitter youtube.\n"
         "Michael Foss, “S-400 Triumf,” in Jane's Weapons (London: IHS Global, 2016).\n"
         "The S-400 can engage aerial targets at ranges up to 400 km according to the manufacturer.\n")
SRC_B = ("The S-400 Triumf entered service with the Russian armed forces in the mid-2000s.\n"
         "Analysts note that the S-400 reaches targets at ranges up to 400 km in its longest-range configuration.\n"
         "The S-400 is operated alongside the older S-300 systems.\n")


def source_policy():
    return SourcePolicy.from_dict(REGISTRY)


def make_job(tmp_path, sources=None, topic=TOPIC):
    sources = sources or [("src-a", "https://research.example/s400", SRC_A, "ACQUIRED"),
                          ("src-b", "https://news.example/story", SRC_B, "ACQUIRED"),
                          ("src-c", "https://random.invalid/page", "", "FAILED")]
    db, root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, "policy-job", topic, "en")["job_id"]
    jobs.transition(job, OWNER, JOB_STAGES[0], "SKIPPED")
    jobs.transition(job, OWNER, JOB_STAGES[1], "SKIPPED")
    _advance(jobs, job, ACQUISITION_STAGE)
    built = []
    for sid, url, text, status in sources:
        s = _source(store, job, sid, url, text, status=status)
        if status != "ACQUIRED":
            s["artifacts"], s["error"] = [], "SOURCE_NOT_ALLOWLISTED: URL host/path is not permitted"
        built.append(s)
    uri, sha = _store_acquisition(store, job, built)
    jobs.add_artifact(job, OWNER, ACQUISITION_STAGE, "acquisition_manifest", uri, sha,
                      "defence-studio.acquisition-result.v1")
    _complete(jobs, job, ACQUISITION_STAGE)
    _advance(jobs, job, ANALYSIS_STAGE)
    ref, _ = AnalysisService(db, root).run_and_store(job, OWNER, uri, sha, topic=topic)
    jobs.add_artifact(job, OWNER, ANALYSIS_STAGE, ref["artifact_type"], ref["uri"], ref["sha256"], ref["schema_version"])
    _complete(jobs, job, ANALYSIS_STAGE)
    return db, root, jobs, job


def policy_service(db, root, tmp_path, **readiness):
    ep = load_evidence_policy()
    ep = copy.deepcopy(ep)
    ep["readiness"].update({"min_accepted_claims": 2, "min_accepted_sources": 2, **readiness})
    ep["relevance"]["min_source_anchor_mentions"] = 1
    return EvidencePolicyService(db, root, policy=ep, source_policy=source_policy(),
                                 workspace=JobWorkspaceService(db, tmp_path / "ws"))


# -- source registry ---------------------------------------------------------------------------------
def test_allowlist_matches_host_and_path_and_ignores_disabled_entries():
    p = source_policy()
    assert p.entry_for("https://research.example/s400/page").id == "inst-a"
    assert p.entry_for("https://research.example/other") is None             # path pattern
    assert p.entry_for("https://sub.news.example/x").id == "news-b"          # subdomain allowed there
    assert p.entry_for("https://research.example.evil.com/s400") is None     # suffix trick
    assert p.entry_for("https://disabled.example/x") is None                 # disabled
    assert p.entry_for("ftp://research.example/s400") is None
    assert p.entry_for("https://user:pw@research.example/s400") is None
    with pytest.raises(SourcePolicyError):
        p.require("https://unknown.example/")


@pytest.mark.parametrize("bad", [
    {"version": "x", "sources": []},
    {"version": "defence-studio.source-policy.v1", "sources": [{"id": "a", "domain": "http://x.y", "category": "RESEARCH_INSTITUTION"}]},
    {"version": "defence-studio.source-policy.v1", "sources": [{"id": "a", "domain": "x.y", "category": "BLOG"}]},
])
def test_malformed_registry_is_rejected(bad):
    with pytest.raises(SourcePolicyError):
        SourcePolicy.from_dict(bad)


def test_default_registry_file_loads_and_enables_only_known_sources():
    p = SourcePolicy.load()
    enabled = {e.domain for e in p.entries if e.enabled}
    assert {"missilethreat.csis.org", "www.rusi.org", "www.pib.gov.in"} <= enabled
    assert all(not e.enabled for e in p.entries if e.id in ("mod-india", "sipri"))


def public_resolver(host, port):
    return ["93.184.216.34"]


def test_guard_enforces_policy_on_initial_url_and_every_redirect_target():
    guard = UrlGuard(resolver=public_resolver, policy=source_policy())
    assert guard.validate("https://research.example/s400/a").host == "research.example"
    with pytest.raises(UrlValidationError, match="SOURCE_NOT_ALLOWLISTED"):
        guard.validate("https://elsewhere.example/s400")
    # a redirect hop is validated by the same call: an allowlisted origin cannot launder a bad target
    with pytest.raises(UrlValidationError, match="SOURCE_NOT_ALLOWLISTED"):
        guard.validate("https://research.example.evil.example/s400")
    # private addresses stay blocked even for an allowlisted-looking name
    private = UrlGuard(resolver=lambda h, p: ["10.0.0.5"], policy=source_policy())
    with pytest.raises(UrlValidationError):
        private.validate("https://research.example/s400")
    with pytest.raises(UrlValidationError):
        guard.validate("file:///etc/passwd")


# -- automatic evidence policy ------------------------------------------------------------------------
def test_policy_dispositions_provenance_and_readiness(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    r = svc.run(job, OWNER)
    states = {s["source_id"]: s["state"] for s in r["sources"]}
    assert states == {"src-a": AUTO_ACCEPTED, "src-b": AUTO_ACCEPTED, "src-c": SOURCE_BLOCKED}
    by_text = {}
    for c in r["claims"]:
        by_text.setdefault(c["text"], c)          # first occurrence wins; later copies are duplicates
    corroborated = by_text["The S-400 can engage aerial targets at ranges up to 400 km according to the manufacturer."]
    # A shared number alone is not contextual corroboration.
    assert corroborated["state"] == NEEDS_REVIEW
    assert corroborated["assessment_outcome"] == "NEEDS_MORE_SOURCES"
    assert by_text["The deployment status of the S-400 40N6 missile is unclear."]["state"] == NEEDS_REVIEW
    texts = list(by_text)
    assert not any("Skip to" in t or "facebook" in t for t in texts)                 # boilerplate never a claim
    bib = [c for c in r["claims"] if "IHS Global" in c["text"]]
    assert bib and bib[0]["state"] == REJECTED and "bibliography" in bib[0]["reasons"][0]
    dup = [c for c in r["claims"] if c["state"] == REJECTED and c["reasons"][0].startswith("duplicate of")]
    assert len(dup) == 1
    assert r["ready_for_generation"] and r["package_approval"]["approval_kind"] == "AUTOMATED_POLICY"
    assert r["package_approval"]["reviewer_id"] == "AUTOMATED_POLICY"      # never a person's identity
    # provenance: every accepted claim keeps its exact passage and source
    for c in r["claims"]:
        assert c["source_id"] and c["passage"]["start"] is not None
    assert r["policy_version"] == "defence-studio.evidence-policy.v1" and r["analysis_versions"]["extractor_version"]
    # only accepted claims are usable evidence
    pkg = EvidenceService(db, root).export(job, OWNER)
    accepted = {c["claim_id"] for c in r["claims"] if c["state"] == AUTO_ACCEPTED}
    assert set(pkg["eligible_claim_ids"]) == accepted and accepted
    # automated outcomes live in their own table; the human ledger stays empty
    rv = ReviewService(db, root).list_claims(job, OWNER)["claims"]
    assert all(c["review"] is None for c in rv)
    with closing(connect(db)) as c:
        assert c.execute("SELECT COUNT(*) FROM claim_reviews").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM claim_review_history").fetchone()[0] == 0
        unresolved = c.execute("SELECT state, reasons_json FROM evidence_policy_decisions WHERE subject_id=?",
                               (by_text["The deployment status of the S-400 40N6 missile is unclear."]["claim_id"],)).fetchone()
    assert unresolved["state"] == NEEDS_REVIEW and "uncertainty" in unresolved["reasons_json"]
    appr = EvidenceService(db, root).export(job, OWNER)["approval"]
    assert appr["approval_kind"] == "AUTOMATED_POLICY" and appr["reviewer_id"] is None and appr["policy_version"]


def test_single_source_figure_is_not_accepted(tmp_path):
    srcs = [("src-a", "https://research.example/s400", SRC_A, "ACQUIRED"),
            ("src-b", "https://news.example/story", "The S-400 is operated alongside the older S-300 systems in Russia.\n"
                                                    "The S-400 Triumf entered service with the Russian armed forces.\n", "ACQUIRED")]
    db, root, _, job = make_job(tmp_path, srcs)
    r = policy_service(db, root, tmp_path).run(job, OWNER)
    fig = [c for c in r["claims"] if "400 km" in c["text"] and not c["reasons"][0].startswith("duplicate")]
    assert fig and all(c["state"] == NEEDS_REVIEW and "single-source figure" in c["reasons"][0] for c in fig)


def test_readiness_failure_stops_before_approval_and_before_any_provider_call(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path, min_accepted_claims=999)
    r = svc.run(job, OWNER)
    assert not r["ready_for_generation"] and r["package_approval"] is None
    assert r["policy_criteria_failed"][0]["criterion"] == "min_accepted_claims" and r["policy_criteria_failed"][0]["action"]
    assert "Generation is stopped" in r["explanation"]
    provider = FakeProvider()
    with pytest.raises(StaleRevision):
        ScriptService(db, root, provider=provider).generate(job, OWNER, GOOD_CONFIG, "gen-1")
    assert provider.calls == 0                       # no OpenAI call when readiness fails


def test_irrelevant_source_is_rejected_and_its_claims_excluded(tmp_path):
    srcs = [("src-a", "https://research.example/s400", SRC_A, "ACQUIRED"),
            ("src-b", "https://news.example/story", SRC_B, "ACQUIRED"),
            ("src-d", "https://news.example/weather", "The regional weather office forecast heavy rain across the coast tomorrow.\n", "ACQUIRED")]
    db, root, _, job = make_job(tmp_path, srcs)
    r = policy_service(db, root, tmp_path).run(job, OWNER)
    assert {s["source_id"]: s["state"] for s in r["sources"]}["src-d"] == REJECTED
    assert not [c for c in r["claims"] if c["source_id"] == "src-d" and c["state"] == AUTO_ACCEPTED]


def test_person_decision_is_never_overwritten_by_policy(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    first = svc.run(job, OWNER)
    target = next(c for c in first["claims"] if c["state"] == AUTO_ACCEPTED)
    review = ReviewService(db, root)
    sha = review.list_claims(job, OWNER)["analysis_artifact_sha256"]
    review.decide_claim(job, OWNER, target["claim_id"], "REJECTED", sha, 0,      # ledger is empty: first human decision
                        "Reviewer disagrees with this statement", "human-1")
    again = svc.run(job, OWNER)
    assert any(h["subject"] == target["claim_id"] for h in again["human_decisions_preserved"])
    after = next(c for c in review.list_claims(job, OWNER)["claims"] if c["claim_id"] == target["claim_id"])
    assert after["review"]["decision"] == "REJECTED" and after["review"]["reviewer_id"] == OWNER
    assert target["claim_id"] not in EvidenceService(db, root).draft(job, OWNER)["package"]["eligible_claim_ids"]


def test_policy_run_is_idempotent_and_owner_scoped(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    a, b = svc.run(job, OWNER), svc.run(job, OWNER)
    assert a["run_id"] == b["run_id"] and a["result_sha256"] == b["result_sha256"]
    assert len(EvidenceService(db, root).draft(job, OWNER)["approvals"]) == 1
    with pytest.raises(Exception):
        svc.run(job, "someone-else")
    assert svc.latest(job, OWNER)["result_sha256"] == a["result_sha256"]


# -- temporary workspace ---------------------------------------------------------------------------
def test_workspace_path_traversal_is_prevented(tmp_path):
    db, root, jobs, job = make_job(tmp_path)
    ws = JobWorkspaceService(db, tmp_path / "ws")
    with pytest.raises(WorkspaceError):
        ws.job_dir("../../etc")
    with pytest.raises(WorkspaceError):
        ws.job_dir("not-a-uuid")
    ws.create(job)
    for bad in ("../x", "a/../../x", "/abs", "..", ""):
        with pytest.raises(WorkspaceError):
            ws.resolve(job, bad)
    assert ws.write_text(job, "policy/ok.json", "{}").parent.name == "policy"


def _force_status(db, job, status):
    with closing(connect(db)) as c:
        c.execute("UPDATE workflow_jobs SET status=? WHERE job_id=?", (status, job))
        c.commit()


@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED", "CANCELLED"])
def test_cleanup_after_terminal_states_keeps_durable_outputs(tmp_path, terminal):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    result = svc.run(job, OWNER)
    ws = svc.workspace
    path = ws.job_dir(job)
    assert path.exists() and any(path.rglob("*.json"))
    assert ws.sweep() == []                                    # job not terminal: untouched
    with pytest.raises(WorkspaceError):
        ws.cleanup(job)
    _force_status(db, job, terminal)
    out = ws.sweep()
    assert out == [{"job_id": job, "cleaned": True, "error": None}]
    assert not path.exists()
    assert ws.cleanup(job)["cleaned"]                          # idempotent
    # durable outputs, provenance and job history survive
    assert svc.latest(job, OWNER)["result_sha256"] == result["result_sha256"]
    assert (root / result["result_uri"]).exists()
    assert EvidenceService(db, root).export(job, OWNER)["status"] == "APPROVED"
    with closing(connect(db)) as c:
        assert c.execute("SELECT status FROM workflow_jobs WHERE job_id=?", (job,)).fetchone()[0] == terminal
        assert c.execute("SELECT COUNT(*) FROM evidence_policy_decisions WHERE job_id=?", (job,)).fetchone()[0] > 0


def test_cleanup_failure_is_visible_and_retried(tmp_path, monkeypatch):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    svc.run(job, OWNER)
    _force_status(db, job, "FAILED")
    import shutil
    real = shutil.rmtree
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    res = svc.workspace.sweep()
    assert res[0]["cleaned"] is False and "locked" in res[0]["error"]
    st = svc.workspace.status(job)
    assert st["exists"] and st["cleanup_error"] and st["cleaned_at"] is None
    monkeypatch.setattr(shutil, "rmtree", real)
    assert svc.workspace.sweep()[0]["cleaned"] is True        # retried safely
    st = svc.workspace.status(job)
    assert not st["exists"] and st["cleanup_error"] is None and st["cleanup_attempts"] == 2


def test_cleanup_is_isolated_between_jobs_and_after_restart(tmp_path):
    db, root, jobs, job1 = make_job(tmp_path)
    job2 = jobs.create(OWNER, "other", "Another", "en")["job_id"]
    ws = JobWorkspaceService(db, tmp_path / "ws")
    ws.write_text(job1, "a.txt", "1")
    ws.write_text(job2, "b.txt", "2")
    (tmp_path / "ws" / "keep-me").mkdir()                       # foreign directory: never ours
    _force_status(db, job1, "COMPLETED")
    # a fresh service instance (worker restart) finishes the job's cleanup
    restarted = JobWorkspaceService(db, tmp_path / "ws")
    done = restarted.sweep()
    assert [d["job_id"] for d in done] == [job1]
    assert not restarted.job_dir(job1).exists()
    assert restarted.job_dir(job2).exists() and (tmp_path / "ws" / "keep-me").exists()


def test_concurrent_cleanup_is_safe(tmp_path):
    db, root, _, job = make_job(tmp_path)
    ws = JobWorkspaceService(db, tmp_path / "ws")
    ws.write_text(job, "x/y.txt", "z")
    _force_status(db, job, "COMPLETED")
    results = []
    ts = [threading.Thread(target=lambda: results.append(ws.cleanup(job))) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(r["cleaned"] for r in results) and not ws.job_dir(job).exists()
