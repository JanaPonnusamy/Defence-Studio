"""PDF report (17 sections) from the shared report model, using ReportLab."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .money import fmt_inr
from .report_model import QUESTIONS

_SS = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_SS["Heading1"], fontSize=15, spaceAfter=6, textColor=colors.HexColor("#1F3A5F"))
H2 = ParagraphStyle("h2", parent=_SS["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1F3A5F"))
BODY = ParagraphStyle("body", parent=_SS["BodyText"], fontSize=8.5, leading=11)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7, leading=8.5)
HEAD = ParagraphStyle("head", parent=SMALL, textColor=colors.white, fontName="Helvetica-Bold")
COVER = ParagraphStyle("cover", parent=_SS["Title"], fontSize=24, alignment=TA_CENTER, spaceAfter=12)
WARN = ParagraphStyle("warn", parent=BODY, textColor=colors.HexColor("#9A3412"), fontSize=10)


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        return fmt_inr(value)
    if isinstance(value, date):
        return value.strftime("%d-%b-%Y")
    if isinstance(value, (list, tuple)):
        return ", ".join(map(str, value))
    return str(value)


def _p(text, style=BODY) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def _table(headers, rows, widths=None, max_rows=None) -> Table:
    shown = rows if max_rows is None else rows[:max_rows]
    data = [[_p(h, HEAD) for h in headers]] + [[_p(_fmt(v), SMALL) for v in row] for row in shown]
    if not shown:
        data.append([_p("No records.", SMALL)] + [""] * (len(headers) - 1))
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C4D6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FB")]),
    ]))
    return t


def _sheet(model, name):
    return next(s for s in model["sheets"] if s["name"] == name)


def write_pdf(model: dict, path: Path) -> Path:
    run, result = model["run"], model["result"]
    path.parent.mkdir(parents=True, exist_ok=True)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(12 * mm, 8 * mm, f"ESIC Compliance Audit {model['audit_id']}  |  generated {model['generated_at']:%d-%b-%Y %H:%M:%S}"
                          + ("  |  SYNTHETIC TEST DATA" if model["synthetic"] else ""))
        canvas.drawRightString(doc.pagesize[0] - 12 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm, title=model["file_stem"],
                            author="ESIC Compliance Audit module", subject=f"Audit {model['audit_id']}")
    s: list = []
    # 1. cover
    s += [Spacer(1, 40 * mm), _p("ESIC Compliance Audit Report", COVER),
          _p(f"Audit ID: {model['audit_id']}", H2), _p(f"Generated: {model['generated_at']:%d-%b-%Y %H:%M:%S}"),
          _p(f"Audit status: {run['status_for_report']}")]
    if model["synthetic"]:
        s.append(_p("SYNTHETIC TEST DATA - produced against the local fixture portal. These are NOT real ESIC records.", WARN))
    s.append(PageBreak())
    # 2-4. identification, employer, scope
    s += [_p("2. Audit identification", H1),
          _table(["Field", "Value"], [["Audit ID", model["audit_id"]], ["Status", run["status_for_report"]],
                                      ["Extraction version", run["extraction_version"]], ["Rules version", result["rules_version"]],
                                      ["Calculation version", result["calculation_version"]]], [70 * mm, 180 * mm]),
          _p("3. Employer account summary", H1),
          _table(["Field", "Value"], [["Employer code (masked)", run.get("employer_identifier_masked") or "NOT AVAILABLE"],
                                      ["Portal", run["portal_url"]]], [70 * mm, 180 * mm]),
          _p("4. Audit scope and inspection timestamp", H1),
          _table(["Field", "Value"], [["Inspection started (UTC)", run["started_at"]], ["As-of date for open items", result["as_of"]]]
                 + [[f"Coverage: {k}", v] for k, v in result["coverage"].items()], [70 * mm, 180 * mm]),
          _p("Inspection stages", H2),
          _table(["Stage", "Outcome", "Detail", "Recorded (UTC)"],
                 [[st["stage"], st["outcome"], st["detail"], st["recorded_at"]] for st in model["stages"]],
                 [45 * mm, 30 * mm, 140 * mm, 45 * mm])]
    # 5. executive summary
    s += [PageBreak(), _p("5. Executive summary", H1),
          _table(["Question", "Answer"], [[QUESTIONS.get(k, k), v] for k, v in result["answers"].items()], [150 * mm, 50 * mm]),
          Spacer(1, 4 * mm), _p("Financial summary", H2),
          _table(["Item", "Amount", "Status"], [[ln["label"], ln["amount"], ln["status"]] for ln in model["summary"]],
                 [130 * mm, 50 * mm, 80 * mm])]
    # 6-10 detail sections
    for no, name, title in ((6, "Payment Reconciliation", "Monthly contribution reconciliation"),
                            (7, "Delayed Payments", "Delayed payment analysis"),
                            (8, "Interest Details", "Interest reconciliation"),
                            (9, "Damages Details", "Damages reconciliation"),
                            (10, "Recovery Records", "Recovery and defaulter records")):
        sh = _sheet(model, name)
        s += [PageBreak(), _p(f"{no}. {title}", H1), _table(sh["headers"], sh["rows"])]
    contrib = _sheet(model, "Contribution History")
    s += [_p("Contribution history (all challans read)", H2), _table(contrib["headers"], contrib["rows"])]
    # 11. confirmed outstanding
    s += [PageBreak(), _p("11. Confirmed outstanding liabilities", H1),
          _table(["Item", "Amount", "Status"], [[ln["label"], ln["amount"], ln["status"]] for ln in model["summary"][:5]],
                 [130 * mm, 50 * mm, 80 * mm])]
    # 12. potential
    sh = _sheet(model, "Potential Liabilities")
    s += [_p("12. Unassessed potential liabilities (ESTIMATED - not ESIC demands)", H1), _table(sh["headers"], sh["rows"])]
    # 13. discrepancies
    sh = _sheet(model, "Discrepancies")
    s += [PageBreak(), _p("13. Missing or inconsistent records", H1), _table(sh["headers"], sh["rows"])]
    # 14. methodology
    sh = _sheet(model, "Legal Rules Reference")
    s += [PageBreak(), _p("14. Legal calculation methodology", H1),
          _p("Due date: last day of the wage month plus the days set by the applicable rule. Delay days are counted from the day "
             "after the due date to the payment date (or the as-of date when unpaid). Indicative interest = amount x annual rate x "
             "delay days / 365, simple, split by the rule in force on each delay day. Maximum damages use the Regulation 31C slab for "
             "the total delay in months, by day segment, capped at the amount of arrears. Confirmed outstanding = assessed minus "
             "payments shown, per demand; amounts inside recovery certificates are not added again."),
          _table(["Rule ID", "Provision", "Applies to", "Method", "Status", "Source"],
                 [[r[1], r[3], r[4], r[5], r[7], f"{r[8]} ({r[9]})"] for r in sh["rows"]],
                 [30 * mm, 55 * mm, 40 * mm, 50 * mm, 22 * mm, 75 * mm])]
    # 15. evidence
    sh = _sheet(model, "Evidence Index")
    s += [PageBreak(), _p("15. Source evidence references", H1), _table(sh["headers"], sh["rows"])]
    # 16. limitations
    s += [PageBreak(), _p("16. Audit limitations and disclaimers", H1)] + [_p("- " + d) for d in model["disclaimers"]]
    if result["totals"]["categories_not_available"]:
        s.append(_p("Not available in this audit: " + ", ".join(result["totals"]["categories_not_available"]), WARN))
    # 17. actions
    s += [_p("17. Final recommended actions", H1)] + [_p(f"{i}. {a}") for i, a in enumerate(model["actions"], start=1)]
    doc.build(s, onFirstPage=footer, onLaterPages=footer)
    return path
