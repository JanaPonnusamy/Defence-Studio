"""One report model feeding both the PDF and the Excel output, so their figures cannot diverge."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .money import ZERO, fmt_inr
from .rules.engine import rules_reference

DISCLAIMERS = [
    "This is a read-only inspection of the pages the employer could open on the ESIC portal at the time shown. "
    "Records that were not displayed, could not be read, or were outside the inspected periods are not covered.",
    "Only amounts shown on ESIC pages are labelled CONFIRMED BY ESIC. Everything labelled ESTIMATED or UNVERIFIED is computed "
    "by this module from statutory rules and is not an ESIC demand.",
    "Damages under Regulation 31C are discretionary maximum rates; ESIC levies them only after assessment and a hearing, "
    "and may levy less or waive them. Estimated damages are therefore upper bounds.",
    "The Code on Social Security, 2020 replaced the ESI Act, 1948 from 21.11.2025. Rules for delay falling on or after that "
    "date could not be verified from an official text and are labelled UNVERIFIED.",
    "NOT AVAILABLE means the information could not be read; it is never treated as zero.",
    "This report is not legal advice. Confirm all liabilities with the ESIC Branch/Regional Office before acting.",
]

QUESTIONS = {
    "outstanding_damages": "Are there any outstanding ESIC damages?",
    "esic_assessed_damages": "Has ESIC officially assessed damages?",
    "damages_demands_pending": "Are any damages demands pending?",
    "delayed_contributions": "Are there delayed monthly contributions?",
    "recovery_or_defaulter_records": "Are there recovery or defaulter records?",
    "possible_unassessed_damages": "Are there possible unassessed damages requiring clarification?",
}

STAT = {"confirmed": "CONFIRMED BY ESIC", "na": "NOT AVAILABLE"}


def _status_for(value, label):
    return STAT["na"] if value is None else label


def summary_lines(result: dict) -> list[dict]:
    t = result["totals"]
    total_label = "OUTSTANDING" if t["total_is_complete"] else "OUTSTANDING (INCOMPLETE - see limitations)"
    lines = [
        ("confirmed_damages_outstanding", "Confirmed damages outstanding", t["confirmed_damages_outstanding"], STAT["confirmed"]),
        ("confirmed_interest_outstanding", "Confirmed interest outstanding", t["confirmed_interest_outstanding"], STAT["confirmed"]),
        ("confirmed_recovery_outstanding", "Confirmed recovery demand outstanding", t["confirmed_recovery_outstanding"], STAT["confirmed"]),
        ("included_in_recovery_not_added_again", "Less: interest/damages already inside recovery demands",
         -t["included_in_recovery_not_added_again"] if t["included_in_recovery_not_added_again"] else ZERO, "NOT ADDED AGAIN"),
        ("total_confirmed_outstanding", "TOTAL CONFIRMED OUTSTANDING LIABILITY", t["total_confirmed_outstanding"], total_label),
        ("estimated_potential_damages_max", "Estimated potential damages, unassessed (maximum)", t["estimated_potential_damages_max"], "ESTIMATED"),
        ("estimated_damages_status_unknown_max", "Estimated damages, assessment status unknown (maximum)", t["estimated_damages_status_unknown_max"], "ESTIMATED"),
        ("estimated_potential_damages_unverified_part", "  of which on an UNVERIFIED rule basis", t["estimated_potential_damages_unverified_part"], "UNVERIFIED"),
        ("indicative_interest_verified_basis", "Indicative interest on delays (verified rule basis)", t["indicative_interest_verified_basis"], "ESTIMATED"),
        ("indicative_interest_unverified_basis", "Indicative interest on delays (unverified rule basis)", t["indicative_interest_unverified_basis"], "UNVERIFIED"),
        ("contributions_not_shown_as_paid", "Contributions not shown as paid on the portal", t["contributions_not_shown_as_paid"], "OUTSTANDING"),
    ]
    return [{"key": k, "label": label, "amount": amount, "status": _status_for(amount, status)} for k, label, amount, status in lines]


def recommended_actions(result: dict) -> list[str]:
    t, c, a = result["totals"], result["counts"], result["answers"]
    out = []
    if t["confirmed_damages_outstanding"]:
        out.append(f"Settle or formally contest the assessed damages outstanding ({fmt_inr(t['confirmed_damages_outstanding'])}); keep the ESIC order references.")
    if t["confirmed_interest_outstanding"]:
        out.append(f"Pay the interest shown by ESIC ({fmt_inr(t['confirmed_interest_outstanding'])}) through the portal's normal process (this module never pays).")
    if t["confirmed_recovery_outstanding"]:
        out.append("Contact the Recovery Officer about the recovery certificate(s) listed and obtain a written statement of the balance.")
    if c["periods_unpaid"]:
        out.append(f"{c['periods_unpaid']} contribution period(s) are not shown as paid: verify bank realisation and challan status with ESIC.")
    if t["estimated_potential_damages_max"] or t["estimated_damages_status_unknown_max"]:
        out.append("Late payments exist without a visible damages assessment: ask the Branch Office whether damages proceedings are proposed; "
                   "the estimated figures are statutory maximums, not demands.")
    if t["possible_double_count"]:
        out.append("Ask ESIC which demands each recovery certificate covers so that no amount is paid twice.")
    if t["categories_not_available"]:
        out.append("Re-run the audit or capture the missing pages manually: " + ", ".join(t["categories_not_available"]) + " could not be read.")
    if a["delayed_contributions"] == "YES":
        out.append("Pay future contributions before the 15th of the following month to avoid further interest and damages.")
    if not out:
        out.append("No outstanding liability was found in the inspected records. Keep this report with the evidence index.")
    return out


def build(run: dict, result: dict, stages: list[dict], evidence: list[dict], generated_at: datetime) -> dict:
    audit_id = run["audit_uuid"]
    periods = result["periods"]
    contribution_rows, recon_rows, delayed_rows = [], [], []
    for p in periods:
        for pay in p["payments"]:
            contribution_rows.append([p["wage_month"], p["due_date"], pay["challan_reference"], pay["challan_created_date"],
                                      pay["payment_date"], pay["payment_status"], pay["amount"], pay["source_reference"]])
            ie, de = pay["interest_estimate"], pay["damages_estimate"]
            if pay["delay_days"]:
                delayed_rows.append([p["wage_month"], p["due_date"], pay["payment_date"], pay["payment_class"], pay["amount"],
                                     pay["delay_days"], ie["delay_months"] if ie else None,
                                     ie["amount"] if ie else None, de["amount"] if de else None,
                                     ie["label"] if ie else "NOT AVAILABLE", (ie["explanation"] if ie else "") + (" || " + de["explanation"] if de else "")])
        recon_rows.append([p["wage_month"], p["due_date"], p["status"], p["total_amount"], p["paid_amount"], p["unpaid_amount"],
                           p["max_delay_days"], p["due_rule"], p["due_rule_level"]])

    def demand_rows(items):
        return [[i["period"], i["demand_reference"], i["assessment_date"], i["state"], i["assessed"], i["paid"],
                 i["stated_outstanding"], i["outstanding"], i["outstanding_basis"], i["covered_by_rc"] or "",
                 "CONFIRMED BY ESIC" if i["state"] == "ASSESSED" else i["state"], i["source_reference"]] for i in items]

    demand_headers = ["Period", "Demand reference", "Assessment date", "State", "Assessed", "Paid", "Outstanding shown by ESIC",
                      "Outstanding (reconciled)", "Basis", "Inside recovery certificate", "Label", "Source"]
    lines = summary_lines(result)
    sheets = [
        {"name": "Contribution History", "headers": ["Wage month", "Due date", "Challan", "Challan created", "Payment date", "Status",
                                                    "Amount", "Source"],
         "rows": contribution_rows, "money": [6], "dates": [1, 3, 4], "totals": [6]},
        {"name": "Payment Reconciliation", "headers": ["Wage month", "Due date", "Period status", "Total", "Paid", "Not shown as paid",
                                                      "Max delay (days)", "Due-date rule", "Rule verification"],
         "rows": recon_rows, "money": [3, 4, 5], "dates": [1], "totals": [3, 4, 5]},
        {"name": "Delayed Payments", "headers": ["Wage month", "Due date", "Payment date / as-of", "Payment class", "Amount", "Delay days",
                                                "Delay months", "Indicative interest", "Max damages", "Label", "Explanation"],
         "rows": delayed_rows, "money": [4, 7, 8], "dates": [1, 2], "totals": [7, 8]},
        {"name": "Interest Details", "headers": demand_headers, "rows": demand_rows(result["interest"]),
         "money": [4, 5, 6, 7], "dates": [2], "totals": [4, 5, 7]},
        {"name": "Damages Details", "headers": demand_headers, "rows": demand_rows(result["damages"]),
         "money": [4, 5, 6, 7], "dates": [2], "totals": [4, 5, 7]},
        {"name": "Recovery Records", "headers": ["RC reference", "Demand date", "Period from", "Period to", "Linked demands", "Principal",
                                                "Interest", "Damages", "Demand", "Paid", "Outstanding", "Basis", "Status", "Source"],
         "rows": [[r["recovery_reference"], r["demand_date"], r["period_from"], r["period_to"], ", ".join(r["linked_references"]),
                   r["principal"], r["interest_component"], r["damages_component"], r["demand"], r["paid"], r["outstanding"],
                   r["outstanding_basis"], r["status"], r["source_reference"]] for r in result["recovery"]],
         "money": [5, 6, 7, 8, 9, 10], "dates": [1], "totals": [8, 9, 10]},
        {"name": "Outstanding Summary", "headers": ["Item", "Amount", "Status"],
         "rows": [[ln["label"], ln["amount"], ln["status"]] for ln in lines], "money": [1], "dates": [], "totals": []},
        {"name": "Potential Liabilities", "headers": ["Period", "Category", "Max damages", "Rule basis", "Max delay days", "Explanation"],
         "rows": [[x["period"], x["category"], x["max_damages"], "ESTIMATED" if x["basis"] == "VERIFIED" else "UNVERIFIED",
                   x["delay_days"], x["explanation"]] for x in result["potential_damages"]],
         "money": [2], "dates": [], "totals": [2]},
        {"name": "Discrepancies", "headers": ["Severity", "Type", "Period", "Message", "Source"],
         "rows": [[d["severity"], d["type"], d["period"], d["message"], d["source"]] for d in result["discrepancies"]],
         "money": [], "dates": [], "totals": []},
        {"name": "Evidence Index", "headers": ["Type", "Page", "Captured at (UTC)", "File", "SHA-256", "Extraction status"],
         "rows": [[e["evidence_type"], e["page_name"], e["captured_at"], e["artifact_path"].replace("\\", "/").split("/")[-1],
                   e["content_hash"], e["extraction_status"]] for e in evidence], "money": [], "dates": [], "totals": []},
        {"name": "Legal Rules Reference", "headers": ["Category", "Rule ID", "Rule", "Legal provision", "Applies to", "Method",
                                                     "Verification", "Status", "Source", "Source URL", "Published", "Rules version", "Note"],
         "rows": [[r["category"], r["rule_id"], r["rule_name"], r["provision"], r["applies"], r["method"], r["verification"],
                   r["status"], r["source_title"], r["source_url"], r["source_publication_date"], r["rule_version"], r["note"]]
                  for r in rules_reference()], "money": [], "dates": [], "totals": []},
    ]
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    return {
        "audit_id": audit_id, "generated_at": generated_at, "file_stem": f"ESIC_Compliance_Audit_{stamp}",
        "run": run, "result": result, "stages": stages, "summary": lines, "sheets": sheets,
        "actions": recommended_actions(result), "disclaimers": DISCLAIMERS, "synthetic": bool(run.get("synthetic")),
    }


def money_or_blank(value) -> Decimal | None:
    return value if isinstance(value, Decimal) else None
