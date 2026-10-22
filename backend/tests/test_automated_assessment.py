"""Automated assessment regressions; isolated SQLite and stored fixture content only."""
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import pytest
from defence_studio.database import connect
from defence_studio.services.jobs import JobService
from defence_studio.services.review import ReviewService, ReviewNotFound, ReviewValidationError
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.evidence_policy import EvidencePolicyService
from defence_studio.storage.artifacts import ArtifactStore
from test_evidence_policy import make_job, policy_service, OWNER


def revise(db, root, job, change):
    service = ReviewService(db, root)
    with closing(connect(db)) as c:
        ref = service._latest_artifact(c, job, "analysis_result")
    data = service._read_json_artifact(job, ref)
    change(data)
    blob = json.dumps(data, sort_keys=True).encode()
    store = ArtifactStore(root)
    saved = store.write_bytes(job, f"jobs/{job}/analysis/revision-{hashlib.sha256(blob).hexdigest()}.json", blob, "application/json")
    JobService(db).add_artifact(job, OWNER, "extraction_validation_normalization_deduplication", "analysis_result", saved.uri, saved.sha256, ref["schema_version"])
    return saved


def test_structured_outcomes_persist_separately_and_replay(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    a = svc.run_assessment(job, OWNER)
    b = svc.run_assessment(job, OWNER)
    assert a["run_id"] == b["run_id"]
    outcomes = {r["assessment_outcome"] for r in a["claims"]}
    assert {"SUPPORTED", "INSUFFICIENT_EVIDENCE", "NEEDS_MORE_SOURCES"} <= outcomes
    claims = ReviewService(db, root).list_claims(job, OWNER)["claims"]
    assert all(c["review"] is None and c["assessment"] for c in claims)
    assert all(c["assessment"]["analysis_sha256"] == a["analysis_sha256"] for c in claims)
    with closing(connect(db)) as c:
        assert c.execute("select count(*) from claim_review_history").fetchone()[0] == 0
        assert c.execute("select count(*) from source_review_history").fetchone()[0] == 0
        assert c.execute("select count(*) from evidence_policy_runs").fetchone()[0] == 1
    assert svc.latest(job, OWNER)["is_stale"] is False
    with pytest.raises(ReviewNotFound): svc.run_assessment(job, "different-owner")


def test_conflict_is_not_resolved_or_approved_automatically(tmp_path):
    db, root, _, job = make_job(tmp_path)
    def change(d):
        a, b = d["claims"][0], d["claims"][-1]
        d["conflicts"] = [{"group_id":"material", "claim_id_a":a["claim_id"], "claim_id_b":b["claim_id"], "basis":"fixture", "explanation":"Conflicting stored accounts"}]
    revise(db, root, job, change)
    r = policy_service(db, root, tmp_path).run_assessment(job, OWNER)
    assert any(c["assessment_outcome"] == "CONTRADICTED" for c in r["claims"])
    assert not r["ready_for_generation"]
    assert any(f["criterion"] == "no_unresolved_material_conflicts" for f in r["policy_criteria_failed"])
    assert r["package_approval"] is None


def test_bad_passage_and_corrupt_analysis_fail_closed(tmp_path):
    db, root, _, job = make_job(tmp_path)
    saved = revise(db, root, job, lambda d: d["claims"][0]["passage"].update(start_offset=999999))
    svc = policy_service(db, root, tmp_path)
    r = svc.run_assessment(job, OWNER)
    assert r["claims"][0]["assessment_outcome"] == "ASSESSMENT_FAILED"
    assert not r["ready_for_generation"]
    ArtifactStore(root).write_bytes(job, saved.uri, b"corrupt", "application/json")
    failed = svc.run_assessment(job, OWNER)
    assert failed["assessment_outcome"] == "ASSESSMENT_FAILED"
    assert not failed["ready_for_generation"]
    assert svc.latest(job, OWNER)["assessment_outcome"] == "ASSESSMENT_FAILED"


def test_changed_analysis_and_policy_invalidate_assessment(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    first = svc.run_assessment(job, OWNER)
    revise(db, root, job, lambda d: d.update(analysis_timestamp="2026-10-16T00:00:00+00:00"))
    assert svc.latest(job, OWNER)["is_stale"]
    assert not any(c["assessment"] for c in ReviewService(db, root).list_claims(job, OWNER)["claims"])
    second = svc.run_assessment(job, OWNER)
    assert first["run_id"] != second["run_id"]
    cfg = copy.deepcopy(svc.policy)
    cfg["version"] = "changed-policy-version"
    changed = EvidencePolicyService(db, root, policy=cfg, source_policy=svc.sources)
    assert changed.latest(job, OWNER)["is_stale"]
    assert changed.run_assessment(job, OWNER)["run_id"] != second["run_id"]


def test_concurrent_assessment_has_one_audit_run(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: svc.run_assessment(job, OWNER), range(2)))
    assert len({v["run_id"] for v in values}) == 1
    assert all(v.get("assessment_outcome") != "ASSESSMENT_FAILED" for v in values)
    with closing(connect(db)) as c:
        assert c.execute("select count(*) from evidence_policy_runs").fetchone()[0] == 1


def test_inputs_changed_during_assessment_are_not_published(tmp_path, monkeypatch):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    evaluate = svc.evaluate
    def changed(*args):
        result = evaluate(*args)
        revise(db, root, job, lambda d: d.update(analysis_timestamp="2026-10-16T00:00:00+00:00"))
        return result
    monkeypatch.setattr(svc, "evaluate", changed)
    with pytest.raises(ReviewValidationError, match="changed during assessment"):
        svc.run(job, OWNER)
    with closing(connect(db)) as c:
        assert c.execute("select count(*) from evidence_policy_decisions").fetchone()[0] == 0


def test_corrupt_source_produces_failed_assessment_not_support(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    source = ReviewService(db, root).list_sources(job, OWNER)["sources"][0]
    ArtifactStore(root).write_bytes(job, source["artifacts"][0]["uri"], b"changed bytes", "text/plain")
    result = svc.run_assessment(job, OWNER)
    assert result["sources"][0]["assessment_outcome"] == "ASSESSMENT_FAILED"
    assert all(c["assessment_outcome"] == "ASSESSMENT_FAILED" for c in result["claims"] if c["source_id"] == source["source_id"])
    assert not result["ready_for_generation"]


def test_two_pages_from_one_owner_do_not_satisfy_independence(tmp_path):
    from test_evidence_policy import REGISTRY
    from defence_studio.acquisition.source_policy import SourcePolicy
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    registry = copy.deepcopy(REGISTRY)
    registry["sources"][1]["independence_group"] = "a"
    svc.sources = SourcePolicy.from_dict(registry)
    r = svc.run_assessment(job, OWNER)
    failures = {f["criterion"]: f for f in r["policy_criteria_failed"]}
    assert failures["min_accepted_sources"]["actual"] == 1
    assert not r["ready_for_generation"]


def test_old_assessor_decisions_cannot_authorize_current_generation(tmp_path, monkeypatch):
    import defence_studio.services.evidence_policy as module
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    current = module.ASSESSOR_VERSION
    monkeypatch.setattr(module, "ASSESSOR_VERSION", "legacy-assessor")
    old = svc.run_assessment(job, OWNER)
    assert not old["ready_for_generation"]
    assert not any(c["assessment"] for c in ReviewService(db, root).list_claims(job, OWNER)["claims"])
    monkeypatch.setattr(module, "ASSESSOR_VERSION", current)
    assert svc.latest(job, OWNER)["is_stale"]
    latest = svc.run_assessment(job, OWNER)
    assert latest["run_id"] != old["run_id"]
    assert any(c["assessment"] for c in ReviewService(db, root).list_claims(job, OWNER)["claims"])


def test_diagnostic_then_approval_and_recovery_refresh_current_result(tmp_path):
    db, root, _, job = make_job(tmp_path)
    svc = policy_service(db, root, tmp_path)
    first = svc.run(job, OWNER, approve_package=False)
    assert not first["ready_for_generation"]
    approved = svc.run_assessment(job, OWNER)
    assert approved["ready_for_generation"]
    assert svc.latest(job, OWNER)["ready_for_generation"]
    with closing(connect(db)) as c:
        ref = ReviewService._latest_artifact(c, job, "analysis_result")
        decisions_before = c.execute("select count(*) from evidence_policy_decisions").fetchone()[0]
    store = ArtifactStore(root)
    original = store.read_bytes(job, ref["uri"])
    store.write_bytes(job, ref["uri"], b"corrupt", "application/json")
    assert svc.run_assessment(job, OWNER)["assessment_outcome"] == "ASSESSMENT_FAILED"
    store.write_bytes(job, ref["uri"], original, "application/json")
    assert svc.run_assessment(job, OWNER)["ready_for_generation"]
    assert svc.latest(job, OWNER)["ready_for_generation"]
    with closing(connect(db)) as c:
        assert c.execute("select count(*) from evidence_policy_decisions").fetchone()[0] == decisions_before
