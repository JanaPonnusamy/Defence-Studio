"""Decision C (2026-11-01): production approval is human-only, enforced in the backend.

Replaces test_script_auto_approval.py. Real persistence and validation; provider calls are fake.
Negative tests prove automated actors cannot approve evidence packages or scripts by any route
(service, internal locked path, constructor flag, policy run, worker, legacy ledger rows, REST)."""
import json
from contextlib import closing

import pytest

from defence_studio.contracts.evidence import canonical
from defence_studio.database import connect
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.human_approval import HumanApprovalRequired, is_automated_actor
from defence_studio.services.script import ScriptService, ScriptStaleRevision
from test_script_phase3 import GOOD_CONFIG, FakeProvider, _basic_job, _default_body, approved, OWNER  # noqa: F401

AUTOMATED = ["AUTOMATED_VALIDATION", "AUTOMATED_POLICY", "automated-validation", "system:scheduler",
             "worker-research-1", "automation", "SYSTEM", "", "   "]


def rsha(s, language="ta", version=1):
    rows = ScriptService(s["db"], s["root"]).list_revisions(s["job"], OWNER, language)["revisions"]
    return next(r["revision_sha256"] for r in rows if r["version"] == version)


def _generate(s, key="h-1", language="ta"):
    service = ScriptService(s["db"], s["root"], provider=s["provider"])
    return service, service.generate(s["job"], OWNER, {**GOOD_CONFIG, "language": language}, key)


# -- generation never approves --------------------------------------------------------------------

def test_valid_draft_waits_for_human_and_replay_survives_restart(approved):
    s = approved
    service, revision = _generate(s)
    assert revision["validation"]["passed"]
    assert revision["status"] == "AWAITING_REVIEW" and revision["approval"] is None
    assert service.revision_history(s["job"], OWNER, "ta", 1) == []
    with pytest.raises(ScriptStaleRevision):
        service.export_approved(s["job"], OWNER, "ta")                  # not production-eligible
    restarted = ScriptService(s["db"], s["root"], provider=s["provider"])
    assert restarted.generate(s["job"], OWNER, GOOD_CONFIG, "h-1") == revision
    assert s["provider"].calls == 1
    assert restarted.list_revisions(s["job"], OWNER)["approval_mode"] == "HUMAN_ONLY"


def test_constructor_cannot_enable_automatic_approval(approved):
    with pytest.raises(ValueError, match="human-only"):
        ScriptService(approved["db"], approved["root"], auto_approve=True)


# -- the human approval record --------------------------------------------------------------------

def test_human_approval_records_identity_versions_and_audit(approved):
    s = approved
    service, revision = _generate(s)
    row = service.approve(s["job"], OWNER, "ta", 1, rsha(s), "Read every section", "human-ok")
    assert row["status"] == "APPROVED"
    exported = service.export_approved(s["job"], OWNER, "ta")
    a = exported["approval"]
    assert a["approval_kind"] == "HUMAN" and a["reviewer_id"] == OWNER and a["decision"] == "APPROVED"
    assert a["version"] == 1 and a["revision_sha256"] == rsha(s) and a["approved_at"]
    assert a["evidence_package_version"] == 1 and a["evidence_package_sha256"] == revision["package_sha256"]
    assert a["audit"]["actor_kind"] == "HUMAN" and a["audit"]["idempotency_key"] == "human-ok"
    assert a["audit"]["evidence_package_reviewer"] == OWNER
    history = service.revision_history(s["job"], OWNER, "ta", 1)
    assert [h["reviewer_id"] for h in history] == [OWNER]
    # idempotent replay after restart
    again = ScriptService(s["db"], s["root"]).approve(s["job"], OWNER, "ta", 1, rsha(s),
                                                     "Read every section", "human-ok")
    assert again["status"] == "APPROVED"


def test_human_approval_is_language_scoped_and_supersedes_old_revision(approved):
    s = approved
    service = ScriptService(s["db"], s["root"], provider=s["provider"])
    ta = service.generate(s["job"], OWNER, GOOD_CONFIG, "ta-1")
    en = service.generate(s["job"], OWNER, {**GOOD_CONFIG, "language": "en"}, "en-1")
    service.approve(s["job"], OWNER, "ta", 1, rsha(s, "ta", 1), "ok", "a-ta-1")
    with pytest.raises(ScriptStaleRevision):
        service.export_approved(s["job"], OWNER, "en")                  # approving ta never approves en
    service.approve(s["job"], OWNER, "en", 1, rsha(s, "en", 1), "ok", "a-en-1")
    ta2 = service.generate(s["job"], OWNER, GOOD_CONFIG, "ta-2")
    assert service.export_approved(s["job"], OWNER, "ta")["version"] == 1  # draft v2 does not replace approval
    service.approve(s["job"], OWNER, "ta", 2, rsha(s, "ta", 2), "ok", "a-ta-2")
    assert service.get_revision(s["job"], OWNER, "ta", 1)["status"] == "SUPERSEDED"
    assert service.export_approved(s["job"], OWNER, "ta")["version"] == 2


# -- automated actors are refused everywhere -------------------------------------------------------

@pytest.mark.parametrize("actor", AUTOMATED)
def test_automated_actor_cannot_approve_script(approved, actor):
    s = approved
    service, revision = _generate(s)
    assert is_automated_actor(actor)
    with pytest.raises(HumanApprovalRequired):
        service.approve(s["job"], actor, "ta", 1, rsha(s), "auto", f"k-{len(actor)}")
    with closing(connect(s["db"])) as c:
        c.execute("BEGIN IMMEDIATE")
        with pytest.raises(HumanApprovalRequired):                    # the internal locked path too
            service._approve_locked(c, s["job"], actor, "ta", 1, rsha(s), "auto", "k-x")
        c.rollback()
    assert service.get_revision(s["job"], OWNER, "ta", 1)["status"] == "AWAITING_REVIEW"


@pytest.mark.parametrize("actor", AUTOMATED)
def test_automated_actor_cannot_approve_evidence_package(tmp_path, actor):
    db, root, jobs, store, job, review, result, ref, _, acq_sha = _basic_job(tmp_path)
    review.decide_source(job, OWNER, "src-001", "APPROVED_FOR_USE", acq_sha, 0, "Source context inspected", "s1")
    review.decide_claim(job, OWNER, result.claims[0].claim_id, "APPROVED_FOR_USE", ref["sha256"], 0, "Passage", "c1")
    evidence = EvidenceService(db, root)
    revision = evidence.draft(job, OWNER)["revision"]
    with pytest.raises(HumanApprovalRequired):
        evidence.approve(job, actor, revision, "auto", "auto-key")
    with pytest.raises(TypeError):                                    # no automated variant exists any more
        evidence.approve(job, OWNER, revision, "auto", "auto-key", automated={"policy_version": "x", "run_id": "y"})
    with closing(connect(db)) as c:
        assert c.execute("SELECT COUNT(*) FROM evidence_packages").fetchone()[0] == 0


# -- legacy automated rows never make anything eligible ---------------------------------------------

def _insert_legacy_package_approval(db, root, job):
    evidence = EvidenceService(db, root)
    state = evidence.draft(job, OWNER)
    body = {**state["package"], "status": "APPROVED", "approval": {
        "revision": state["revision"], "version": 1, "approval_kind": "AUTOMATED_POLICY", "reviewer_id": None,
        "approved_by": "AUTOMATED_POLICY", "policy_version": "v1", "run_id": "legacy", "note": "auto",
        "approved_at": "2026-10-30T00:00:00+00:00"}}
    uri = f"jobs/{job}/evidence/legacy.json"
    stored = evidence.store.write_bytes(job, uri, canonical(body), "application/json")
    with closing(connect(db)) as c:
        c.execute("INSERT INTO evidence_packages VALUES(?,?,?,?,?,?,?,?,?)",
                  (job, 1, state["revision"], uri, stored.sha256, "AUTOMATED_POLICY", "auto", "2026-10-30", "legacy"))
        c.commit()
    return state["revision"]


def test_legacy_automated_package_approval_blocks_generation_until_a_human_approves(tmp_path):
    db, root, jobs, store, job, review, result, ref, _, acq_sha = _basic_job(tmp_path)
    review.decide_source(job, OWNER, "src-001", "APPROVED_FOR_USE", acq_sha, 0, "Source context inspected", "s1")
    review.decide_claim(job, OWNER, result.claims[0].claim_id, "APPROVED_FOR_USE", ref["sha256"], 0, "Passage", "c1")
    revision = _insert_legacy_package_approval(db, root, job)
    evidence = EvidenceService(db, root)
    draft = evidence.draft(job, OWNER)
    assert draft["current_approval"] is None and draft["legacy_automated_approval"]["reviewer_id"] == "AUTOMATED_POLICY"
    claim = draft["package"]["eligible_claim_ids"][0]
    provider = FakeProvider(body_factory=lambda _p: _default_body(claim))
    service = ScriptService(db, root, provider=provider)
    with pytest.raises(ScriptStaleRevision, match="human"):
        service.generate(job, OWNER, GOOD_CONFIG, "legacy-gen")
    assert provider.calls == 0
    with pytest.raises(Exception):
        evidence.export(job, OWNER)
    # The same revision can now receive a human approval (v16 ledger: unique per reviewer).
    evidence.approve(job, OWNER, revision, "Human reviewed the evidence", "human-pkg")
    assert service.generate(job, OWNER, GOOD_CONFIG, "legacy-gen")["status"] == "AWAITING_REVIEW"
    with closing(connect(db)) as c:
        assert [r[0] for r in c.execute("SELECT reviewer_id FROM evidence_packages ORDER BY version")] == [
            "AUTOMATED_POLICY", OWNER]


def test_legacy_automated_script_approval_is_not_production_eligible(approved):
    s = approved
    service, revision = _generate(s)
    body = {**revision, "status": "APPROVED", "approval": {
        "revision_sha256": rsha(s), "version": 1, "reviewer_id": "AUTOMATED_VALIDATION",
        "note": "Passed script validation", "approved_at": "2026-10-30T00:00:00+00:00",
        "approval_kind": "AUTOMATED_VALIDATION", "policy_version": revision["validation"]["validator_version"]}}
    for key in ("uri", "sha256", "revision_sha256_db"):
        body.pop(key, None)
    with closing(connect(s["db"])) as c:
        row = dict(c.execute("SELECT * FROM script_revisions WHERE job_id=?", (s["job"],)).fetchone())
    from defence_studio.contracts.script import validate_revision
    stored = service._read_json_artifact(s["job"], row)
    legacy = {**stored, "status": "APPROVED", "approval": body["approval"]}
    uri = f"jobs/{s['job']}/scripts/ta/legacy-approved.json"
    written = service.store.write_bytes(s["job"], uri, canonical(validate_revision(legacy)), "application/json")
    with closing(connect(s["db"])) as c:
        c.execute("UPDATE script_revisions SET status='APPROVED', uri=?, sha256=? WHERE job_id=?",
                  (uri, written.sha256, s["job"]))
        c.commit()
    with pytest.raises(ScriptStaleRevision, match="legacy automated"):
        service.export_approved(s["job"], OWNER, "ta")
    service.approve(s["job"], OWNER, "ta", 1, rsha(s), "Human confirmed", "human-confirm")
    assert service.export_approved(s["job"], OWNER, "ta")["approval"]["approval_kind"] == "HUMAN"


# -- evidence freshness and fact-check still gate the human approval ----------------------------------

def test_evidence_change_after_generation_blocks_human_approval(approved):
    s = approved
    service, revision = _generate(s)
    claim = s["evidence"].draft(s["job"], OWNER)["package"]["claims"][0]
    s["evidence"].decide_claim(s["job"], OWNER, claim["claim_id"], "NEEDS_MORE_EVIDENCE",
                               claim["review"]["reviewed_artifact_sha256"], 1, "Changed after generation", "later")
    with pytest.raises(ScriptStaleRevision):
        service.approve(s["job"], OWNER, "ta", 1, rsha(s), "ok", "stale-approve")


def test_new_factcheck_blocks_human_approval(approved):
    s = approved
    from defence_studio.services.factcheck import FactcheckService
    service, revision = _generate(s)
    check = FactcheckService(s["db"], s["root"])
    check.enqueue(OWNER, "fc-claims", s["job"], "claims", {
        "claims": [{"claim_id": s["claim_id"], "text": "Officials reported a routine patrol.",
                    "source_id": "origin", "category": "news", "extraction_confidence": 0.9}],
        "sources": [{"source_id": "origin", "excerpt": "Officials reported a routine patrol.",
                     "publisher": "origin", "category": "news"}],
        "collection": {"attempted": True, "errors": []}})
    check.run_pending()
    check.enqueue(OWNER, "fc-pkg", s["job"], "package", {"topic": "New verification"})
    check.run_pending()
    with pytest.raises(ScriptStaleRevision):
        service.approve(s["job"], OWNER, "ta", 1, rsha(s), "ok", "fc-approve")


# -- REST: the only approval route is an authenticated human request -------------------------------

def test_api_requires_human_approval_for_package_and_script(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from defence_studio.api.app import create_app
    from conftest import SECRET, access_token
    from test_content_flow import build_content_fixture, MockContentProvider
    db, root, job, owner = build_content_fixture(tmp_path)              # already human-approved package
    provider = MockContentProvider()
    monkeypatch.setattr(ScriptService, "_resolve_provider", lambda self, owner_id=None: provider)
    human = {"Authorization": "Bearer " + access_token(owner)}
    robot = {"Authorization": "Bearer " + access_token("AUTOMATED_VALIDATION")}
    with TestClient(create_app(db, SECRET, root)) as client:
        base = f"/api/jobs/{job}/script"
        response = client.post(base + "/generate", headers={**human, "Idempotency-Key": "api-gen"},
                               json={**GOOD_CONFIG, "language": "en", "terminology": {}})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "AWAITING_REVIEW" and body["approval"] is None
        assert client.get(base + "/revisions", headers=human).json()["approval_mode"] == "HUMAN_ONLY"
        assert client.get(base + "/export/en", headers=human).status_code == 409
        approve = f"{base}/revisions/en/{body['version']}/approve"
        sha = next(r["revision_sha256"] for r in client.get(base + "/revisions", headers=human).json()["revisions"]
                   if r["language"] == "en" and r["version"] == body["version"])
        payload = {"revision_sha256": sha, "note": "auto"}
        denied = client.post(approve, headers={**robot, "Idempotency-Key": "robot"}, json=payload)
        assert denied.status_code == 403, denied.text
        ok = client.post(approve, headers={**human, "Idempotency-Key": "human"},
                         json={**payload, "note": "Read and approved"})
        assert ok.status_code == 201, ok.text
        exported = client.get(base + "/export/en", headers=human).json()
        assert exported["approval"]["approval_kind"] == "HUMAN" and exported["approval"]["reviewer_id"] == owner
        pkg = client.post(f"/api/jobs/{job}/package/approve", headers={**robot, "Idempotency-Key": "robot-pkg"},
                          json={"revision": "0" * 64, "note": "auto"})
        assert pkg.status_code == 403
    with closing(connect(db)) as c:
        assert [r[0] for r in c.execute("SELECT reviewer_id FROM script_review_history")] == [owner]
        assert [r[0] for r in c.execute("SELECT reviewer_id FROM evidence_packages")] == [owner]
    assert json.dumps(exported).count("AUTOMATED") == 0
