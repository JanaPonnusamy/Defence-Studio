"""Package lifecycle against real F3/F4 artifact shapes and persistent F5 reviews."""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from defence_studio.api.app import create_app
from defence_studio.contracts.evidence import canonical, digest, timestamp, validate_package
from defence_studio.contracts.script import ScriptRequestV1, validate_response
from defence_studio.database import connect, initialize_database, _upgrade_v2_to_v3, _upgrade_v3_to_v4
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.review import ReviewNotFound, ReviewValidationError, StaleRevision
from defence_studio.services.script_contract import prepare_request
from test_review_f5 import _basic_job, OWNER, _LEGACY_V2_SCHEMA, ANALYSIS_STAGE
from conftest import SECRET, access_token


@pytest.fixture
def research(tmp_path):
    values = _basic_job(tmp_path)
    return values, EvidenceService(values[0], values[1])


def reviewed(research):
    values, service = research
    db, root, jobs, store, job, review, result, ref, _, acq_sha = values
    review.decide_source(job, OWNER, "src-001", "APPROVED_FOR_USE", acq_sha, 0, "Source context inspected", "s1")
    review.decide_claim(job, OWNER, result.claims[0].claim_id, "APPROVED_FOR_USE", ref["sha256"], 0, "Exact passage inspected", "c1")
    return job, service, service.draft(job, OWNER)


def test_draft_schema_and_missing_review_gate(research):
    values, service = research
    state = service.draft(values[4], OWNER)
    assert not state["package"]["ready"]
    assert state["package"]["status"] == "DRAFT"
    assert validate_package(state["package"])
    assert "normalized_text" not in state["package"]["sources"][0]["normalized"]
    assert state == service.draft(values[4], OWNER)
    with pytest.raises(StaleRevision):
        service.approve(values[4], OWNER, state["revision"], "Tried early", "early")
    with pytest.raises(StaleRevision):
        service.export(values[4], OWNER)
    assert service.export(values[4], OWNER, draft=True)["approval"] is None


def test_approval_export_idempotency_history_and_restart(research):
    job, service, draft = reviewed(research)
    assert draft["package"]["ready"]
    approved = service.approve(job, OWNER, draft["revision"], "Checked limitations", "approve")
    assert service.approve(job, OWNER, draft["revision"], "Checked limitations", "approve") == approved
    assert service.approve(job, OWNER, draft["revision"], "Checked limitations", "other-key") == approved
    fresh = EvidenceService(research[0][0], research[0][1])
    exported = fresh.export(job, OWNER)
    assert exported["status"] == "APPROVED"
    assert digest(exported) == approved["sha256"]
    assert len(fresh.draft(job, OWNER)["approvals"]) == 1
    assert len(list((research[0][1] / "jobs" / job / "evidence").glob("*.json"))) == 1


def test_review_change_invalidates_package_and_preserves_history(research):
    job, service, draft = reviewed(research)
    service.approve(job, OWNER, draft["revision"], "Approved", "approve")
    v = research[0]
    v[5].decide_claim(job, OWNER, v[6].claims[0].claim_id, "NEEDS_MORE_EVIDENCE", v[7]["sha256"], 1, "New concern", "c2")
    with pytest.raises(StaleRevision):
        service.export(job, OWNER)
    state = service.draft(job, OWNER)
    assert state["current_approval"] is None and len(state["approvals"]) == 1
    assert len(state["package"]["audit"]["claim"]) == 2
    with pytest.raises(StaleRevision):
        service.approve(job, OWNER, draft["revision"], "Outdated", "a2")


@pytest.mark.parametrize("operation", ["draft", "export", "timeline"])
def test_cross_owner_reads_are_denied(research, operation):
    with pytest.raises(ReviewNotFound):
        getattr(research[1], operation)(research[0][4], "someone-else")


def test_cross_owner_approval_and_idempotency_payload_conflict(research):
    job, service, draft = reviewed(research)
    with pytest.raises(ReviewNotFound):
        service.approve(job, "someone-else", draft["revision"], "Unauthorized", "same")
    service.approve(job, OWNER, draft["revision"], "Approved", "same")
    with pytest.raises(StaleRevision):
        service.approve(job, OWNER, draft["revision"], "Changed note", "same")


@pytest.mark.parametrize("value,precision,utc", [
    (None,"MISSING",None), ("2026-10-11","DATE_ONLY",None),
    ("2026-10-11T10:00:00","TIMEZONE_UNKNOWN",None), ("yesterday","UNRELIABLE",None),
    ("2026-10-11T10:00:00+05:30","TIMESTAMP","2026-10-11T04:30:00+00:00"),
    ("Sun, 11 Oct 2026 10:00:00 +0530","TIMESTAMP","2026-10-11T04:30:00+00:00"),
])
def test_timestamp_never_invents_zone_or_time(value, precision, utc):
    result = timestamp(value)
    assert result["original"] == value and result["precision"] == precision and result["utc"] == utc


def test_timeline_filters_and_passage(research):
    job, service, _ = reviewed(research)
    items = service.timeline(job, OWNER, source_id="src-001")["items"]
    assert {i["kind"] for i in items} >= {"PUBLICATION", "ACQUISITION", "ANALYSIS", "REVIEW"}
    assert any(i["passage"] and i["original_url"] for i in items)
    ranged = service.timeline(job, OWNER, start="2020-01-01T00:00:00Z")["items"]
    assert all(i["date"]["utc"] for i in ranged)
    assert service.timeline(job, OWNER, source_id="absent")["items"] == []
    with pytest.raises(ReviewValidationError):
        service.timeline(job, OWNER, start="2026-01-01")
    with pytest.raises(ReviewValidationError):
        service.timeline(job, OWNER, start="2027-01-01T00:00:00Z", end="2026-01-01T00:00:00Z")


def test_corrupt_original_bytes_prevent_export(research):
    job, service, draft = reviewed(research)
    service.approve(job, OWNER, draft["revision"], "Approved", "a")
    research[0][3].write_bytes(job, f"jobs/{job}/sources/src-001.bin", b"tampered", "text/plain")
    assert any("corrupt" in b for b in service.draft(job, OWNER)["package"]["blockers"])
    with pytest.raises(StaleRevision):
        service.export(job, OWNER)


def test_corrupt_package_bytes_fail_closed(research):
    job, service, draft = reviewed(research)
    ref = service.approve(job, OWNER, draft["revision"], "Approved", "a")
    research[0][3].write_bytes(job, ref["uri"], b"{}", "application/json")
    with pytest.raises(ReviewValidationError):
        service.export(job, OWNER)


def replace_analysis(values, mutate):
    db, root, jobs, store, job, _, _, ref, _, _ = values
    body = json.loads(store.read_text(job, ref["uri"]))
    mutate(body)
    data = canonical(body)
    sha = hashlib.sha256(data).hexdigest()
    saved = store.write_bytes(job, f"jobs/{job}/analysis/{sha}.json", data, "application/json")
    jobs.add_artifact(job, OWNER, ANALYSIS_STAGE, "analysis_result", saved.uri, sha, body["schema_version"])
    return sha


def test_stale_analysis_and_invalid_offsets_block_approval(research):
    job, service, _ = reviewed(research)
    replace_analysis(research[0], lambda b: b["claims"][0]["passage"].update(start_offset=99999))
    draft = service.draft(job, OWNER)["package"]
    assert draft["claims"][0]["is_stale"]
    assert any("exact supporting passage" in b for b in draft["blockers"])


def test_conflicts_require_explicit_exclusion(research):
    values, service = research
    def add_conflict(body):
        other = json.loads(json.dumps(body["claims"][0]))
        other["claim_id"] = "claim-other"
        body["claims"].append(other)
        body["conflicts"] = [{"group_id":"conflict-1", "claim_id_a":body["claims"][0]["claim_id"],
                              "claim_id_b":"claim-other", "explanation":"Needs review"}]
    sha = replace_analysis(values, add_conflict)
    job = values[4]
    values[5].decide_source(job, OWNER, "src-001", "APPROVED_FOR_USE", values[9], 0, "Inspected", "s")
    for cid in (values[6].claims[0].claim_id, "claim-other"):
        values[5].decide_claim(job, OWNER, cid, "APPROVED_FOR_USE", sha, 0, "Reviewed", cid)
    assert not service.draft(job, OWNER)["package"]["ready"]
    values[5].decide_claim(job, OWNER, "claim-other", "REJECTED", sha, 1, "Excluded conflicting passage", "reject")
    body = service.draft(job, OWNER)["package"]
    assert body["ready"] and len(body["conflicts"]) == 1
    assert "claim-other" not in body["eligible_claim_ids"]


def test_migration_from_reconstructed_v3_preserves_legacy_data(tmp_path):
    db = tmp_path / "legacy.db"
    with closing(sqlite3.connect(db, isolation_level=None)) as con:
        con.executescript(_LEGACY_V2_SCHEMA)
        _upgrade_v2_to_v3(con)
        con.execute("INSERT INTO source_reviews VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("legacy-job-1","s","REJECTED","acquisition_manifest","a"*64,"legacy-owner","old note",1,"then","then"))
    initialize_database(db)
    initialize_database(db)
    with closing(connect(db)) as con:
        assert con.execute("SELECT version FROM schema_info").fetchone()[0] == 9
        assert con.execute("SELECT note FROM source_reviews").fetchone()[0] == "old note"
        assert con.execute("SELECT topic FROM workflow_jobs").fetchone()[0] == "Legacy topic"
        assert con.execute("SELECT COUNT(*) FROM evidence_packages").fetchone()[0] == 0


def test_failed_migration_rolls_back_ddl_and_version(tmp_path):
    with closing(sqlite3.connect(tmp_path / "rollback.db", isolation_level=None)) as con:
        con.execute("CREATE TABLE schema_info(version INTEGER)")
        con.execute("INSERT INTO schema_info VALUES(3)")
        # Missing singleton makes the final version update fail after DDL.
        with pytest.raises(sqlite3.OperationalError):
            _upgrade_v3_to_v4(con)
        assert con.execute("SELECT version FROM schema_info").fetchone()[0] == 3
        assert con.execute("SELECT name FROM sqlite_master WHERE name='evidence_packages'").fetchone() is None


def test_api_auth_strict_validation_and_export_boundary(research):
    job, service, draft = reviewed(research)
    with TestClient(create_app(research[0][0], SECRET, research[0][1])) as client:
        # Canonical Phase 2 surface is /package/* (core-design route names); the
        # earlier /evidence/* alias was retired when the two implementations
        # were reconciled onto a single EvidenceService engine.
        path = f"/api/jobs/{job}/package"
        assert client.get(path).status_code == 401
        headers = {"Authorization":"Bearer " + access_token(OWNER), "Idempotency-Key":"api-approval"}
        assert client.get(path, headers={"Authorization":"Bearer " + access_token("other")}).status_code == 404
        assert client.get(path + "/export", headers=headers).status_code == 409
        body = {"revision":draft["revision"],"note":"All evidence reviewed"}
        assert client.post(path + "/approve", headers=headers, json={**body,"status":"APPROVED"}).status_code == 422
        assert client.post(path + "/approve", headers=headers, json=body).status_code == 201
        response = client.get(path + "/export", headers=headers)
        assert response.status_code == 200 and response.json()["status"] == "APPROVED"
        assert "attachment" in response.headers["content-disposition"]


def test_script_boundary_requires_current_ledger_approval(research):
    job, service, draft = reviewed(research)
    payload = {"schema_version":"defence-studio.script-request.v1", "package_sha256":"a"*64,
               "language":"ta", "model":"configured-test-model", "prompt_version":"defence-studio.source-grounded-script.v1"}
    with pytest.raises(StaleRevision):
        prepare_request(service, job, OWNER, payload)
    service.approve(job, OWNER, draft["revision"], "Reviewed", "a")
    payload["package_sha256"] = digest(service.export(job, OWNER))
    prepared = prepare_request(service, job, OWNER, payload)
    request = ScriptRequestV1.model_validate(payload)
    response = dict(schema_version="defence-studio.script-response.v1", request_sha256=prepared["request_sha256"],
                    package_sha256=payload["package_sha256"], language="ta", model="configured-test-model",
                    statements=[{"text":"ஆதாரத்தில் கூறப்பட்டுள்ளது.", "claim_ids":prepared["eligible_claim_ids"], "qualification":"Attributed source statement"}],
                    input_tokens=100, output_tokens=50, retries=0, measured_text_cost_inr=0.1, status="REQUIRES_HUMAN_REVIEW")
    assert validate_response(response, request, prepared["evidence"])
    with pytest.raises(ValueError):
        validate_response({**response,"measured_text_cost_inr":11.0}, request, prepared["evidence"])
    response["statements"][0]["claim_ids"] = ["unapproved-claim"]
    with pytest.raises(ValueError):
        validate_response(response, request, prepared["evidence"])
    with pytest.raises(ValidationError):
        ScriptRequestV1.model_validate({**payload,"language":"xx"})


def test_schema_rejects_forged_ready_and_approval_flags(research):
    body = research[1].draft(research[0][4], OWNER)["package"]
    with pytest.raises(ValueError):
        validate_package({**body,"ready":True})
    with pytest.raises(ValueError):
        validate_package({**body,"status":"APPROVED"})


def test_blank_review_notes_are_rejected(research):
    values, service = research
    with pytest.raises(ReviewValidationError, match="explanation"):
        values[5].decide_source(values[4], OWNER, "src-001", "REJECTED", values[9], 0, "  ", "blank")
    assert not service.draft(values[4], OWNER)["package"]["audit"]["source"]


def test_review_retry_returns_original_decision_after_later_review(research):
    values, _ = research
    review, job, sha = values[5], values[4], values[9]
    first = review.decide_source(job, OWNER, "src-001", "APPROVED_FOR_USE", sha, 0, "First note", "first")
    review.decide_source(job, OWNER, "src-001", "REJECTED", sha, 1, "Later note", "later")
    assert review.decide_source(job, OWNER, "src-001", "APPROVED_FOR_USE", sha, 0, "First note", "first") == first
    from defence_studio.services.review import RevisionConflict
    with pytest.raises(RevisionConflict):
        review.decide_source(job, OWNER, "src-001", "REJECTED", sha, 0, "First note", "first")


def test_malformed_and_wrong_schema_artifacts_fail_with_validation_error(research):
    values, service = research
    replace_analysis(values, lambda b: b.update(schema_version="defence-studio.analysis-result.v999"))
    with pytest.raises(ReviewValidationError, match="schema"):
        service.draft(values[4], OWNER)


def test_approval_insert_failure_is_recoverable(research, monkeypatch):
    job, service, draft = reviewed(research)
    original_write = service.store.write_bytes
    def crash_after_write(*args, **kwargs):
        original_write(*args, **kwargs)
        raise OSError("Simulated crash after artifact write")
    monkeypatch.setattr(service.store, "write_bytes", crash_after_write)
    with pytest.raises(OSError):
        service.approve(job, OWNER, draft["revision"], "Reviewed", "crash")
    assert service.draft(job, OWNER)["approvals"] == []
    monkeypatch.setattr(service.store, "write_bytes", original_write)
    service.approve(job, OWNER, draft["revision"], "Reviewed", "crash")
    assert service.export(job, OWNER)["status"] == "APPROVED"


def test_concurrent_approvals_create_one_ledger_version(research):
    from concurrent.futures import ThreadPoolExecutor
    job, service, draft = reviewed(research)
    def approve(key):
        return service.approve(job, OWNER, draft["revision"], "Reviewed", key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, ["one", "two"]))
    assert results[0] == results[1]
    assert len(service.draft(job, OWNER)["approvals"]) == 1
