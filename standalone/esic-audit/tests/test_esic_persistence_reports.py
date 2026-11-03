"""Database integrity (append-only, immutable snapshots) and PDF/Excel reports that reconcile."""
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal as D

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from app.money import fmt_inr
from app.parsing import parse_table
from app.reconcile import reconcile
from app.report_model import build
from app.report_pdf import write_pdf
from app.report_xlsx import write_xlsx
from app.repository import Repository
from tests.conftest import FULL
from tests.fixtures.portal_server import HIST_HEAD, HIST_P1

EXPECTED_SHEETS = ["Executive Summary", "Contribution History", "Payment Reconciliation", "Delayed Payments", "Interest Details",
                   "Damages Details", "Recovery Records", "Outstanding Summary", "Potential Liabilities", "Discrepancies",
                   "Evidence Index", "Legal Rules Reference"]


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "t.sqlite3")


def _run_with_records(repo):
    run = repo.create_run("http://127.0.0.1/x", True, "test")
    recs = parse_table("contribution", {"headers": HIST_HEAD, "rows": HIST_P1}, "p").records
    return run, recs


def test_records_roundtrip_decimal_and_dates(repo):
    run, recs = _run_with_records(repo)
    assert repo.store_records(run["id"], recs) == (4, 0)
    loaded = repo.load_records(run["id"])["contribution"]
    assert loaded[1]["contribution_amount"] == D("12000.00") and loaded[1]["payment_date"] == date(2024, 6, 20)
    raw_type = repo.conn.execute("SELECT typeof(contribution_amount) FROM esic_contribution_records LIMIT 1").fetchone()[0]
    assert raw_type == "text"  # never REAL


def test_duplicates_within_audit_ignored(repo):
    run, recs = _run_with_records(repo)
    repo.store_records(run["id"], recs)
    assert repo.store_records(run["id"], recs) == (0, 4)


def test_records_are_append_only(repo):
    run, recs = _run_with_records(repo)
    repo.store_records(run["id"], recs)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repo.conn.execute("UPDATE esic_contribution_records SET contribution_amount='0'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        repo.conn.execute("DELETE FROM esic_contribution_records")


def test_terminal_audit_is_frozen_and_history_undeletable(repo):
    run, _ = _run_with_records(repo)
    repo.set_status(run["id"], "COMPLETED", "done")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        repo.set_status(run["id"], "INSPECTING", "again")
    with pytest.raises(sqlite3.DatabaseError):
        repo.conn.execute("DELETE FROM esic_audit_runs")


def test_each_audit_keeps_its_own_records(repo):
    a, recs = _run_with_records(repo)
    b = repo.create_run("http://127.0.0.1/x", True, "test")
    repo.store_records(a["id"], recs)
    repo.store_records(b["id"], recs[:1])
    assert len(repo.load_records(a["id"])["contribution"]) == 4 and len(repo.load_records(b["id"])["contribution"]) == 1


def test_activity_and_events_are_redacted(repo):
    run, _ = _run_with_records(repo)
    repo.add_event(run["id"], "INFO", "txtPassword=hunter2 for 99000000000000123")
    msg = repo.events(run["id"])[0]["message"]
    assert "hunter2" not in msg and "99000000000000123" not in msg


@pytest.fixture
def model(synthetic_records, tmp_path):
    result = reconcile(synthetic_records, FULL, date(2026, 11, 3))
    run = {"audit_uuid": "11111111-2222-3333-4444-555555555555", "status_for_report": "COMPLETED", "portal_url": "http://127.0.0.1/x",
           "started_at": "2026-11-03T05:00:00+00:00", "extraction_version": "x", "employer_identifier_masked": "99************123",
           "synthetic": 1}
    ev = [{"evidence_type": "screenshot", "page_name": "View RC", "captured_at": "2026-11-03T05:01:00+00:00",
           "artifact_path": str(tmp_path / "a.png"), "content_hash": "ab" * 32, "extraction_status": "CAPTURED"}]
    return build(run, result, [{"stage": "INSPECTING", "outcome": "ENTERED", "detail": "", "recorded_at": "t"}], ev,
                 datetime(2026, 11, 3, 10, 30, 0))


def test_excel_has_12_sheets_with_audit_id_filters_and_frozen_headers(model, tmp_path):
    path = write_xlsx(model, tmp_path / f"{model['file_stem']}.xlsx")
    assert path.name == "ESIC_Compliance_Audit_20261103_103000.xlsx"
    wb = load_workbook(path)
    assert wb.sheetnames == EXPECTED_SHEETS
    for ws in wb.worksheets:
        assert model["audit_id"] in ws["A2"].value
        assert ws.freeze_panes is not None
    for name in EXPECTED_SHEETS[1:]:
        assert wb[name].auto_filter.ref
    assert "SUBTOTAL" in str(wb["Damages Details"].cell(row=4 + len(model["sheets"][4]["rows"]) + 1, column=8).value)


def _excel_summary(path):
    ws = load_workbook(path)["Outstanding Summary"]
    return {ws.cell(row=r, column=1).value: (ws.cell(row=r, column=2).value, ws.cell(row=r, column=3).value) for r in range(5, ws.max_row + 1)}


def test_pdf_and_excel_reconcile_exactly(model, tmp_path):
    x = write_xlsx(model, tmp_path / "r.xlsx")
    p = write_pdf(model, tmp_path / "r.pdf")
    summary = _excel_summary(x)
    text = re.sub(r"\s+", " ", " ".join(page.extract_text() for page in PdfReader(str(p)).pages))
    for line in model["summary"]:
        xv, xs = summary[line["label"]]
        assert (D(str(xv)) if xv is not None else None) == line["amount"]
        assert xs == line["status"]
        assert fmt_inr(line["amount"]) in text
    assert "1,542.11" in text and "SYNTHETIC TEST DATA" in text
    for heading in ("Executive summary", "Damages reconciliation", "Legal calculation methodology", "Final recommended actions",
                    "Audit limitations and disclaimers", "Source evidence references"):
        assert heading in text


def test_excel_detail_rows_add_up_to_summary(model, tmp_path):
    wb = load_workbook(write_xlsx(model, tmp_path / "r.xlsx"))
    ws = wb["Damages Details"]
    outstanding = sum(D(str(ws.cell(row=r, column=8).value)) for r in range(5, 5 + len(model["sheets"][4]["rows"])))
    assert outstanding == model["result"]["totals"]["confirmed_damages_outstanding"]


def test_not_available_shown_not_zero(synthetic_records, tmp_path):
    result = reconcile(dict(synthetic_records, damages=[]), dict(FULL, damages="NOT_AVAILABLE"), date(2026, 11, 3))
    run = {"audit_uuid": "u", "status_for_report": "PARTIAL", "portal_url": "x", "started_at": "t", "extraction_version": "x", "synthetic": 0}
    m = build(run, result, [], [], datetime(2026, 11, 3, 10, 0, 0))
    line = next(ln for ln in m["summary"] if ln["key"] == "confirmed_damages_outstanding")
    assert line["amount"] is None and line["status"] == "NOT AVAILABLE"
    text = " ".join(pg.extract_text() for pg in PdfReader(str(write_pdf(m, tmp_path / "p.pdf"))).pages)
    assert "NOT AVAILABLE" in text and "INCOMPLETE" in text
