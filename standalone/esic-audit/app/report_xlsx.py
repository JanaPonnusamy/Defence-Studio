"""Excel report (12 worksheets) from the shared report model."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .report_model import QUESTIONS

INR = '"₹"#,##0.00;[Red]-"₹"#,##0.00'
DATE = "DD-MMM-YYYY"
HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
TOTAL_FILL = PatternFill("solid", fgColor="E8EEF5")
WARN_FILL = PatternFill("solid", fgColor="FFF4CE")
HEADER_ROW = 4


def _cell_value(value):
    if isinstance(value, Decimal):
        return value  # openpyxl writes Decimal as a number without float conversion in the XML text
    if isinstance(value, (list, tuple, set)):
        return ", ".join(map(str, value))
    return value


def _title(ws, model: dict, title: str) -> None:
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Audit ID: {model['audit_id']}   |   Generated: {model['generated_at']:%d-%b-%Y %H:%M:%S}" + (
        "   |   SYNTHETIC TEST DATA - NOT REAL ESIC RECORDS" if model["synthetic"] else "")
    ws["A2"].font = Font(italic=True, size=9, color="555555")


def _table(ws, model: dict, sheet: dict) -> None:
    _title(ws, model, sheet["name"])
    headers, rows = sheet["headers"], sheet["rows"]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=HEADER_ROW, column=c, value=h)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    widths = [max(10, min(60, len(h) + 2)) for h in headers]
    for r, row in enumerate(rows, start=HEADER_ROW + 1):
        for c, value in enumerate(row):
            cell = ws.cell(row=r, column=c + 1, value=_cell_value(value))
            if c in sheet["money"]:
                cell.number_format = INR
            elif c in sheet["dates"] and isinstance(value, date):
                cell.number_format = DATE
            if isinstance(value, str) and value in ("UNVERIFIED", "NOT AVAILABLE", "ESTIMATED"):
                cell.fill = WARN_FILL
            text = "" if value is None else str(value)
            widths[c] = max(widths[c], min(60, len(text) + 2))
    last = HEADER_ROW + len(rows)
    if sheet["totals"] and rows:
        tr = last + 1
        ws.cell(row=tr, column=1, value="Total").font = Font(bold=True)
        for c in sheet["totals"]:
            col = get_column_letter(c + 1)
            cell = ws.cell(row=tr, column=c + 1, value=f"=SUBTOTAL(9,{col}{HEADER_ROW + 1}:{col}{last})")
            cell.number_format, cell.font, cell.fill = INR, Font(bold=True), TOTAL_FILL
    if not rows:
        ws.cell(row=HEADER_ROW + 1, column=1, value="No records in this audit for this sheet.").font = Font(italic=True)
    for c, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(row=HEADER_ROW + 1, column=1)
    ws.auto_filter.ref = f"A{HEADER_ROW}:{get_column_letter(len(headers))}{max(last, HEADER_ROW + 1)}"


def _executive(ws, model: dict) -> None:
    run, result = model["run"], model["result"]
    _title(ws, model, "ESIC Compliance Audit - Executive Summary")
    rows = [
        ("Audit ID", model["audit_id"]), ("Audit status", run["status_for_report"]),
        ("Employer (masked)", run.get("employer_identifier_masked") or "NOT AVAILABLE"),
        ("Portal", run["portal_url"]), ("Inspection started (UTC)", run["started_at"]),
        ("As-of date for open items", result["as_of"]), ("Rules version", result["rules_version"]),
        ("Calculation version", result["calculation_version"]), ("Contribution periods inspected", result["counts"]["periods_inspected"]),
        ("Delayed payments identified", result["counts"]["delayed_payments"]), ("Discrepancies", result["counts"]["discrepancies"]),
    ]
    r = HEADER_ROW
    for k, v in rows:
        ws.cell(row=r, column=1, value=k).font = Font(bold=True)
        cell = ws.cell(row=r, column=2, value=_cell_value(v))
        if isinstance(v, date):
            cell.number_format = DATE
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Key questions").font = Font(bold=True, size=12)
    r += 1
    for k, v in result["answers"].items():
        ws.cell(row=r, column=1, value=QUESTIONS.get(k, k))
        ws.cell(row=r, column=2, value=v)
        r += 1
    r += 1
    ws.cell(row=r, column=1, value="Financial summary").font = Font(bold=True, size=12)
    r += 1
    for ln in model["summary"]:
        ws.cell(row=r, column=1, value=ln["label"])
        cell = ws.cell(row=r, column=2, value=ln["amount"])
        cell.number_format = INR
        ws.cell(row=r, column=3, value=ln["status"])
        if ln["key"] == "total_confirmed_outstanding":
            for c in (1, 2, 3):
                ws.cell(row=r, column=c).font = Font(bold=True)
                ws.cell(row=r, column=c).fill = TOTAL_FILL
        r += 1
    ws.column_dimensions["A"].width = 58
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["C"].width = 44
    ws.freeze_panes = "A4"


def write_xlsx(model: dict, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Executive Summary"
    _executive(ws, model)
    for sheet in model["sheets"]:
        _table(wb.create_sheet(sheet["name"][:31]), model, sheet)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
