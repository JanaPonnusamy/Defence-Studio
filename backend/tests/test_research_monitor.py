"""Research monitor / evidence explorer: read-only views over durable state."""
import csv
import io
import json
from contextlib import closing

import pytest

from defence_studio.api.app import create_app
from defence_studio.database import connect
from defence_studio.services.research_monitor import ResearchMonitorService, display_state
from defence_studio.services.review import ReviewNotFound
from test_evidence_policy import OWNER, make_job, policy_service
from conftest import SECRET, access_token
from fastapi.testclient import TestClient


@pytest.fixture
def ran(tmp_path):
    db, root, _, job = make_job(tmp_path)
    policy_service(db, root, tmp_path).run(job, OWNER)
    return ResearchMonitorService(db, root), job, db, root


def test_monitor_counts_come_from_real_acquisition_and_policy(ran):
    mon, job, *_ = ran
    m = mon.monitor(job, OWNER)
    assert m["counts"]["fetched"] == 2 and m["counts"]["failed"] == 1
    assert m["counts"]["approved"] == 2 and m["counts"]["rejected"] >= 1
    hosts = {s["hostname"] for s in m["sources"]}
    assert {"research.example", "news.example", "random.invalid"} <= hosts
    failed = next(s for s in m["sources"] if s["hostname"] == "random.invalid")
    assert failed["acquisition_status"] == "FAILED" and "NOT_ALLOWLISTED" in failed["error"]
    # fetch duration is not recorded by the contract: reported as unknown, never invented
    assert all(s["fetch_duration_seconds"] is None for s in m["sources"])


def test_stage_timing_and_unmeasured_progress_are_not_fabricated(ran):
    mon, job, *_ = ran
    m = mon.monitor(job, OWNER)
    assert m["stages_total"] == 13
    done = [s for s in m["stages"] if s["status"] == "COMPLETED" and s["started_at"]]
    assert done and all(s["duration_seconds"] is not None and s["duration_seconds"] >= 0 for s in done)
    pending = [s for s in m["stages"] if s["status"] == "PENDING"]
    assert pending and all(s["progress"] is None and s["duration_seconds"] is None for s in pending)
    assert m["pipeline_mapping"]["evidence_analysis_and_review"]


def test_estimate_without_history_is_labelled_default_low_confidence(ran):
    mon, job, db, _ = ran
    # The assessed fixture is paused; estimates apply only while work runs.
    with closing(connect(db)) as c:
        c.execute("UPDATE workflow_jobs SET status='RUNNING' WHERE job_id=?", (job,))
        c.commit()
    est = mon.monitor(job, OWNER)["estimate"]
    assert est["basis"] in ("default", "complete") and est["confidence"] in ("low", "n/a")
    assert est["samples"] == 0


@pytest.mark.parametrize("status", ["AWAITING_REVIEW", "PAUSED"])
def test_waiting_for_review_or_action_has_no_completion_estimate(ran, status):
    mon, job, db, _ = ran
    with closing(connect(db)) as c:
        c.execute("UPDATE workflow_jobs SET status=? WHERE job_id=?", (status, job))
        c.commit()
    est = mon.monitor(job, OWNER)["estimate"]
    assert est["remaining_seconds"] is None
    assert est["basis"] == status.lower()


def test_controls_do_not_claim_unimplemented_operations(ran):
    mon, job, *_ = ran
    c = mon.monitor(job, OWNER)["controls"]
    assert not any(c[k] for k in ("pause", "resume", "cancel", "retry"))


def test_display_states_are_distinct():
    assert display_state("PAUSED", []) == "PAUSED"
    assert display_state("CANCELLED", []) == "CANCELLED"
    assert display_state("AWAITING_REVIEW", []) == "AWAITING_READINESS"
    assert display_state("QUEUED", [{"status": "PENDING", "attempt_count": 0, "retry_after": None}]) == "QUEUED"
    assert display_state("RUNNING", [{"status": "RUNNING", "attempt_count": 0, "retry_after": None}]) == "RUNNING"
    future = "2999-01-01T00:00:00+00:00"
    assert display_state("RUNNING", [{"status": "PENDING", "attempt_count": 1, "retry_after": future}]) == "WAITING_RETRY"


def test_explorer_claims_have_score_passage_context_and_provenance(ran):
    mon, job, *_ = ran
    ex = mon.explorer(job, OWNER)
    assert ex["claims"] and ex["freshness"]["status"] == "CURRENT"
    for c in ex["claims"]:
        assert c["score"]["scale"] == "0-100" and c["policy_version"] and c["analysis_sha256"]
        if c["context"]:
            assert c["context"]["passage"] == c["text"]       # passage integrity
    acc = [c for c in ex["claims"] if c["state"] == "AUTO_ACCEPTED"]
    assert acc and all(c["contradicted"] is False for c in acc)
    assert ex["policy_run"]["scoring_policy_version"]


def test_unaccepted_claims_stay_unaccepted_and_reasons_are_visible(ran):
    mon, job, *_ = ran
    ex = mon.explorer(job, OWNER)
    unresolved = [c for c in ex["claims"] if c["state"] == "NEEDS_REVIEW"]
    assert unresolved and all(c["reasons"] for c in unresolved)
    assert "script_generation_permitted" in ex["readiness"]


def test_explorer_before_policy_run_reports_not_evaluated(tmp_path):
    db, root, _, job = make_job(tmp_path)
    ex = ResearchMonitorService(db, root).explorer(job, OWNER)
    assert ex["freshness"]["status"] == "NOT_EVALUATED" and ex["claims"] == []
    assert ex["readiness"]["ready"] is False and ex["readiness"]["reasons"]


def test_readiness_failure_reasons_are_exposed(tmp_path):
    db, root, _, job = make_job(tmp_path)
    policy_service(db, root, tmp_path, min_accepted_claims=99).run(job, OWNER)
    ex = ResearchMonitorService(db, root).explorer(job, OWNER)
    assert ex["readiness"]["ready"] is False
    assert any("min_accepted_claims" in r for r in ex["readiness"]["reasons"])


def test_export_csv_and_json_preserve_reasons_and_provenance(ran):
    mon, job, *_ = ran
    body, media, _ = mon.export(job, OWNER, "csv")
    rows = list(csv.DictReader(io.StringIO(body)))
    assert media == "text/csv" and rows and rows[0]["policy_version"] and rows[0]["analysis_sha256"]
    assert all(r["reasons"] for r in rows)
    j = json.loads(mon.export(job, OWNER, "json")[0])
    assert j["policy_run"]["result_sha256"]


def test_other_owner_cannot_read_any_view(ran):
    mon, job, *_ = ran
    for call in (mon.monitor, mon.explorer):
        with pytest.raises(ReviewNotFound):
            call(job, "someone-else")
    with pytest.raises(ReviewNotFound):
        mon.source_detail(job, "someone-else", "src-a")
    with pytest.raises(ReviewNotFound):
        mon.source_detail(job, OWNER, "../etc/passwd")


def test_http_routes_require_auth_and_owner(ran, tmp_path):
    _, job, db, root = ran
    app = create_app(db, SECRET, root)
    with TestClient(app) as c:
        assert c.get(f"/api/jobs/{job}/monitor").status_code in (401, 403)
        h = {"Authorization": f"Bearer {access_token(OWNER)}"}
        assert c.get(f"/api/jobs/{job}/monitor", headers=h).status_code == 200
        other = {"Authorization": f"Bearer {access_token('intruder')}"}
        assert c.get(f"/api/jobs/{job}/evidence/explorer", headers=other).status_code == 404
        assert c.get(f"/api/jobs/{job}/evidence/report.exe", headers=h).status_code == 404
