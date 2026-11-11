"""Deterministic reconciliation of extracted ESIC records. No LLM, no floats.

Vocabulary used in every output (and in the reports):
  CONFIRMED BY ESIC - amount shown on an ESIC page (assessed / demanded / outstanding as displayed)
  PAID              - payment shown on an ESIC page with a confirming status or payment date
  OUTSTANDING       - assessed minus verified payments
  ESTIMATED         - computed here from statutory rules; never an ESIC demand
  UNVERIFIED        - estimate whose legal basis could not be verified from an official text
  NOT AVAILABLE     - the page/value could not be read; never treated as zero
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from . import CALCULATION_VERSION
from .money import ZERO, q
from .rules import engine

PAID_WORDS = re.compile(r"\b(paid|success|successful|realised|realized|settled|credited|completed|approved)\b", re.I)
NOT_PAID_WORDS = re.compile(r"\b(not paid|unpaid|pending|initiated|in process|processing|generated|failed|failure|rejected|cancelled|canceled|expired|awaiting)\b", re.I)
PROPOSED_WORDS = re.compile(r"\b(proposed|show cause|notice|hearing|draft|provisional|under process)\b", re.I)
NOT_ASSESSED_WORDS = re.compile(r"\b(not assessed|unassessed|waived|withdrawn|set aside|quashed)\b", re.I)
TOLERANCE = Decimal("1.00")


def _sum(values) -> Decimal:
    return q(sum((v for v in values if v is not None), ZERO))


def classify_payment(row: dict) -> str:
    status = row.get("payment_status") or ""
    if status and NOT_PAID_WORDS.search(status):
        return "FAILED" if re.search(r"fail|reject|cancel|expired", status, re.I) else "CHALLAN_NOT_CONFIRMED"
    if row.get("payment_date"):
        return "PAID" if status and PAID_WORDS.search(status) else "PAID_DATE_ONLY"
    if status and PAID_WORDS.search(status):
        return "PAID_NO_DATE"
    if row.get("challan_reference") or row.get("challan_created_date"):
        return "CHALLAN_NOT_CONFIRMED"
    return "NO_PAYMENT_RECORD"


def _month_range(lo: str, hi: str) -> list[str]:
    y, m = (int(p) for p in lo.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= hi:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _est_dict(est: engine.Estimate) -> dict:
    return {"amount": est.amount, "basis": est.basis, "delay_days": est.delay_days,
            "delay_months": est.delay_months, "capped": est.capped, "explanation": est.explanation,
            "label": "ESTIMATED" if est.basis == "VERIFIED" else "UNVERIFIED"}


def _refs(text: str | None) -> set[str]:
    return {t.strip().upper() for t in re.split(r"[,;\s]+", text or "") if t.strip()}


def reconcile(records: dict[str, list[dict]], coverage: dict[str, str], as_of: date) -> dict[str, Any]:
    """records[category] = list of {**values, "source_reference": str}. coverage[category] in
    CAPTURED | EMPTY | NOT_AVAILABLE (EMPTY = page read successfully and showed no records)."""
    disc: list[dict] = []

    def flag(kind: str, severity: str, message: str, ref: str | None = None, period: str | None = None):
        disc.append({"type": kind, "severity": severity, "message": message, "source": ref, "period": period})

    def available(cat: str) -> bool:
        return coverage.get(cat) in ("CAPTURED", "EMPTY")

    # ---------------- A. contributions ----------------
    by_month: dict[str, list[dict]] = defaultdict(list)
    seen_challans: dict[str, dict] = {}
    for row in records.get("contribution", []):
        month = row.get("wage_month")
        if not month:
            flag("MISSING_PERIOD", "HIGH", "Contribution row without a readable wage month; excluded from period analysis.",
                 row.get("source_reference"))
            continue
        ch = (row.get("challan_reference") or "").upper()
        if ch:
            prior = seen_challans.get(ch)
            if prior and (prior.get("wage_month") != month or prior.get("contribution_amount") != row.get("contribution_amount")):
                flag("INCONSISTENT_DUPLICATE", "HIGH",
                     f"Challan {ch} appears with different month/amount ({prior.get('wage_month')} {prior.get('contribution_amount')} vs "
                     f"{month} {row.get('contribution_amount')}); second occurrence excluded.", row.get("source_reference"), month)
                continue
            if prior:
                continue  # exact duplicate across pages: count once
            seen_challans[ch] = row
        by_month[month].append(row)

    periods = []
    totals_interest_ind = {"VERIFIED": ZERO, "UNVERIFIED": ZERO}
    for month in sorted(by_month):
        try:
            due = engine.due_date_for(month)
        except engine.RuleNotFound:
            flag("NO_DUE_DATE_RULE", "HIGH", f"No due-date rule for {month}.", None, month)
            continue
        payments = []
        for row in by_month[month]:
            cls = classify_payment(row)
            amount = row.get("contribution_amount")
            if amount is None and row.get("employer_contribution") is not None and row.get("employee_contribution") is not None:
                amount = q(row["employer_contribution"] + row["employee_contribution"])
            pay = {"challan_reference": row.get("challan_reference"), "amount": amount,
                   "challan_created_date": row.get("challan_created_date"), "payment_date": row.get("payment_date"),
                   "payment_status": row.get("payment_status"), "payment_class": cls,
                   "source_reference": row.get("source_reference"), "delay_days": None,
                   "interest_estimate": None, "damages_estimate": None}
            if amount is None:
                flag("MISSING_AMOUNT", "MEDIUM", f"Contribution amount not readable for {month} challan {row.get('challan_reference') or '-'}.",
                     row.get("source_reference"), month)
            if cls in ("PAID", "PAID_DATE_ONLY"):
                end = row["payment_date"]
                pay["delay_days"] = max(0, (end - due.due_date).days)
                if cls == "PAID_DATE_ONLY":
                    flag("PAYMENT_STATUS_NOT_SHOWN", "LOW",
                         f"{month}: payment date shown without a confirmation status; treated as paid on that date.",
                         row.get("source_reference"), month)
            elif cls == "PAID_NO_DATE":
                flag("MISSING_PAYMENT_DATE", "HIGH", f"{month}: status says paid but no payment date is shown; delay cannot be determined.",
                     row.get("source_reference"), month)
                end = None
            else:
                end = as_of  # still open: exposure runs to the audit date
                pay["delay_days"] = max(0, (as_of - due.due_date).days)
                flag("UNPAID_CONTRIBUTION", "HIGH",
                     f"{month}: {cls.replace('_', ' ').lower()} (challan {row.get('challan_reference') or '-'}); a challan alone is not proof of payment.",
                     row.get("source_reference"), month)
            if end is not None and amount is not None and pay["delay_days"]:
                ie = engine.interest_estimate(amount, due.due_date, end, due.level)
                de = engine.damages_estimate(amount, due.due_date, end, due.level)
                pay["interest_estimate"], pay["damages_estimate"] = _est_dict(ie), _est_dict(de)
                totals_interest_ind[ie.basis] = q(totals_interest_ind[ie.basis] + ie.amount)
            payments.append(pay)
        classes = {p["payment_class"] for p in payments}
        paid = {"PAID", "PAID_DATE_ONLY"}
        if classes <= paid:
            status = "PAID_LATE" if any(p["delay_days"] for p in payments) else "PAID_ON_TIME"
        elif "PAID_NO_DATE" in classes:
            status = "DELAY_UNKNOWN"
        elif classes & paid:
            status = "PARTLY_UNPAID"
        else:
            status = "UNPAID"
        periods.append({
            "wage_month": month, "due_date": due.due_date, "due_rule": due.rule_id, "due_rule_level": due.level,
            "due_explanation": due.explanation, "status": status,
            "total_amount": _sum(p["amount"] for p in payments),
            "paid_amount": _sum(p["amount"] for p in payments if p["payment_class"] in paid),
            "unpaid_amount": _sum(p["amount"] for p in payments if p["payment_class"] not in paid | {"PAID_NO_DATE"}),
            "max_delay_days": max([p["delay_days"] or 0 for p in payments] or [0]),
            "payments": payments,
        })
    if len(periods) > 1:
        present = {p["wage_month"] for p in periods}
        for m in _month_range(periods[0]["wage_month"], periods[-1]["wage_month"]):
            if m not in present:
                flag("MISSING_CONTRIBUTION_PERIOD", "MEDIUM",
                     f"No contribution record found for {m} inside the inspected range; confirm whether a return was due.", None, m)

    # ---------------- B/C. interest & damages demands ----------------
    def demand_items(cat: str) -> list[dict]:
        items = []
        for row in records.get(cat, []):
            assessed, paid_amt, stated = row.get("assessed_amount"), row.get("paid_amount"), row.get("outstanding_amount")
            status_text = row.get("assessment_status") or ""
            if cat == "damages" and NOT_ASSESSED_WORDS.search(status_text):
                state = "NOT_LEVIED"
            elif cat == "damages" and PROPOSED_WORDS.search(status_text):
                state = "PENDING_ASSESSMENT"
            elif assessed is None and stated is None:
                state = "STATUS_UNKNOWN"
            else:
                state = "ASSESSED"
            computed = q(assessed - paid_amt) if assessed is not None and paid_amt is not None else None
            if state != "ASSESSED":
                outstanding, basis = None, "NOT APPLICABLE" if state == "NOT_LEVIED" else "NOT AVAILABLE"
            elif computed is not None:
                outstanding, basis = computed, "assessed - paid"
                if stated is not None and abs(stated - computed) > TOLERANCE:
                    flag("OUTSTANDING_MISMATCH", "HIGH",
                         f"{cat} {row.get('demand_reference') or row.get('contribution_period')}: ESIC shows outstanding {stated} "
                         f"but assessed {assessed} - paid {paid_amt} = {computed}. Using assessed - paid.",
                         row.get("source_reference"), row.get("contribution_period"))
            elif stated is not None:
                outstanding, basis = stated, "as displayed by ESIC"
            else:
                outstanding, basis = None, "NOT AVAILABLE"
                flag("PAYMENT_UNKNOWN", "MEDIUM",
                     f"{cat} {row.get('demand_reference') or row.get('contribution_period')}: assessed {assessed} but payments "
                     "and balance are not shown; outstanding cannot be confirmed.", row.get("source_reference"),
                     row.get("contribution_period"))
            if outstanding is not None and outstanding < 0:
                flag("NEGATIVE_OUTSTANDING", "MEDIUM", f"{cat} {row.get('demand_reference')}: payments exceed the demand by {-outstanding}; "
                     "treated as nil liability, possible credit to verify.", row.get("source_reference"), row.get("contribution_period"))
                outstanding = ZERO
            items.append({"period": row.get("contribution_period"), "demand_reference": row.get("demand_reference"),
                          "assessment_date": row.get("assessment_date"), "assessment_status": status_text or None,
                          "state": state, "assessed": assessed, "paid": paid_amt, "stated_outstanding": stated,
                          "outstanding": outstanding, "outstanding_basis": basis, "covered_by_rc": None,
                          "source_reference": row.get("source_reference")})
        return items

    interest_items, damages_items = demand_items("interest"), demand_items("damages")

    # ---------------- D. recovery ----------------
    rc_items = []
    for row in records.get("recovery", []):
        demand, paid_amt, stated = row.get("demand_amount"), row.get("paid_amount"), row.get("outstanding_amount")
        comps = [row.get("principal_amount"), row.get("interest_component"), row.get("damages_component")]
        if demand is None and all(c is not None for c in comps):
            demand = _sum(comps)
        elif demand is not None and all(c is not None for c in comps) and abs(_sum(comps) - demand) > TOLERANCE:
            flag("RC_COMPONENT_MISMATCH", "MEDIUM", f"RC {row.get('recovery_reference')}: components sum to {_sum(comps)} but demand is {demand}.",
                 row.get("source_reference"))
        computed = q(demand - paid_amt) if demand is not None and paid_amt is not None else None
        if computed is not None:
            outstanding, basis = computed, "demand - paid"
            if stated is not None and abs(stated - computed) > TOLERANCE:
                flag("OUTSTANDING_MISMATCH", "HIGH", f"RC {row.get('recovery_reference')}: ESIC shows {stated}, demand - paid = {computed}. Using demand - paid.",
                     row.get("source_reference"))
        elif stated is not None:
            outstanding, basis = stated, "as displayed by ESIC"
        else:
            outstanding, basis = None, "NOT AVAILABLE"
            flag("INCOMPLETE_RC", "HIGH", f"RC {row.get('recovery_reference')}: outstanding cannot be determined from the page.",
                 row.get("source_reference"))
        if outstanding is not None and outstanding < 0:
            flag("NEGATIVE_OUTSTANDING", "MEDIUM", f"RC {row.get('recovery_reference')}: recovered more than demanded.", row.get("source_reference"))
            outstanding = ZERO
        rc_items.append({"recovery_reference": row.get("recovery_reference"), "demand_date": row.get("demand_date"),
                         "period_from": row.get("period_from"), "period_to": row.get("period_to"),
                         "linked_references": sorted(_refs(row.get("linked_references"))),
                         "principal": row.get("principal_amount"), "interest_component": row.get("interest_component"),
                         "damages_component": row.get("damages_component"), "demand": demand, "paid": paid_amt,
                         "outstanding": outstanding, "outstanding_basis": basis,
                         "status": row.get("recovery_status"), "source_reference": row.get("source_reference")})

    # Overlap: a demand already inside an RC is not added to the total twice.
    overlap_undetermined = ZERO
    for kind, items in (("interest", interest_items), ("damages", damages_items)):
        for item in items:
            if item["state"] != "ASSESSED" or not item["outstanding"]:
                continue
            for rc in rc_items:
                comp = rc["interest_component"] if kind == "interest" else rc["damages_component"]
                linked = item["demand_reference"] and item["demand_reference"].upper() in rc["linked_references"]
                in_range = (item["period"] and rc["period_from"] and rc["period_to"]
                            and rc["period_from"] <= item["period"] <= rc["period_to"] and (comp is None or comp > 0))
                if linked or in_range:
                    item["covered_by_rc"] = rc["recovery_reference"]
                    break
            if item["covered_by_rc"] is None and rc_items and any(
                    not rc["linked_references"] and not (rc["period_from"] and rc["period_to"]) for rc in rc_items):
                overlap_undetermined = q(overlap_undetermined + item["outstanding"])
    if overlap_undetermined:
        flag("POSSIBLE_DOUBLE_COUNT", "HIGH",
             f"Recovery certificate(s) do not show which demands they cover; {overlap_undetermined} of interest/damages "
             "may already be inside the RC amount. Total includes both; clarify with ESIC.")

    # Potential (unassessed) damages for late/unpaid periods without a damages record.
    damage_periods = {i["period"] for i in damages_items if i["period"]}
    rc_ranges = [(rc["period_from"], rc["period_to"]) for rc in rc_items if rc["period_from"] and rc["period_to"]]
    potential = []
    for p in periods:
        exposure = [pay for pay in p["payments"] if pay["damages_estimate"]]
        if not exposure:
            continue
        if p["wage_month"] in damage_periods or any(lo <= p["wage_month"] <= hi for lo, hi in rc_ranges):
            continue
        amount = _sum(pay["damages_estimate"]["amount"] for pay in exposure)
        basis = "UNVERIFIED" if any(pay["damages_estimate"]["basis"] != "VERIFIED" for pay in exposure) else "VERIFIED"
        potential.append({"period": p["wage_month"], "max_damages": amount, "basis": basis,
                          "category": "POTENTIAL_UNASSESSED" if available("damages") else "ASSESSMENT_STATUS_UNKNOWN",
                          "delay_days": p["max_delay_days"],
                          "explanation": " | ".join(pay["damages_estimate"]["explanation"] for pay in exposure)})

    # Compare ESIC interest against our indicative figure per period.
    esic_interest_by_period = defaultdict(lambda: ZERO)
    for i in interest_items:
        if i["period"] and i["assessed"] is not None:
            esic_interest_by_period[i["period"]] += i["assessed"]
    for p in periods:
        ind = _sum(pay["interest_estimate"]["amount"] for pay in p["payments"] if pay["interest_estimate"])
        if p["wage_month"] in esic_interest_by_period and abs(esic_interest_by_period[p["wage_month"]] - ind) > TOLERANCE:
            flag("INTEREST_DIFFERENCE", "LOW",
                 f"{p['wage_month']}: ESIC interest {q(esic_interest_by_period[p['wage_month']])} vs indicative {ind}. "
                 "ESIC's figure prevails; difference may come from posting/realisation dates.", None, p["wage_month"])

    # ---------------- E. totals ----------------
    def confirmed(items, cat):
        if not available(cat):
            return None
        return _sum(i["outstanding"] for i in items if i["state"] == "ASSESSED")

    c_dmg, c_int = confirmed(damages_items, "damages"), confirmed(interest_items, "interest")
    c_rc = _sum(r["outstanding"] for r in rc_items) if available("recovery") else None
    covered = _sum(i["outstanding"] for i in interest_items + damages_items if i["covered_by_rc"] and i["state"] == "ASSESSED")
    missing = [c for c in ("contribution", "interest", "damages", "recovery") if not available(c)]
    parts = [c_dmg, c_int, c_rc]
    total = q(sum((v for v in parts if v is not None), ZERO) - covered) if any(v is not None for v in parts) else None
    unknown_items = sum(1 for i in interest_items + damages_items if i["outstanding"] is None and i["state"] in ("ASSESSED", "STATUS_UNKNOWN")) \
        + sum(1 for r in rc_items if r["outstanding"] is None)
    totals = {
        "confirmed_damages_assessed": _sum(i["assessed"] for i in damages_items if i["state"] == "ASSESSED") if available("damages") else None,
        "confirmed_damages_paid": _sum(i["paid"] for i in damages_items if i["state"] == "ASSESSED") if available("damages") else None,
        "confirmed_damages_outstanding": c_dmg,
        "confirmed_interest_assessed": _sum(i["assessed"] for i in interest_items if i["state"] == "ASSESSED") if available("interest") else None,
        "confirmed_interest_paid": _sum(i["paid"] for i in interest_items if i["state"] == "ASSESSED") if available("interest") else None,
        "confirmed_interest_outstanding": c_int,
        "confirmed_recovery_outstanding": c_rc,
        "included_in_recovery_not_added_again": covered,
        "total_confirmed_outstanding": total,
        "total_is_complete": not missing and unknown_items == 0,
        "categories_not_available": missing,
        "items_with_unknown_outstanding": unknown_items,
        "possible_double_count": overlap_undetermined,
        "estimated_potential_damages_max": _sum(x["max_damages"] for x in potential if x["category"] == "POTENTIAL_UNASSESSED"),
        "estimated_damages_status_unknown_max": _sum(x["max_damages"] for x in potential if x["category"] == "ASSESSMENT_STATUS_UNKNOWN"),
        "estimated_potential_damages_unverified_part": _sum(x["max_damages"] for x in potential if x["basis"] != "VERIFIED"),
        "indicative_interest_verified_basis": totals_interest_ind["VERIFIED"],
        "indicative_interest_unverified_basis": totals_interest_ind["UNVERIFIED"],
        "contributions_not_shown_as_paid": _sum(p["unpaid_amount"] for p in periods) if available("contribution") else None,
        "damages_pending_assessment": sum(1 for i in damages_items if i["state"] == "PENDING_ASSESSMENT"),
    }
    counts = {
        "periods_inspected": len(periods),
        "periods_paid_late": sum(1 for p in periods if p["status"] == "PAID_LATE"),
        "periods_unpaid": sum(1 for p in periods if p["status"] in ("UNPAID", "PARTLY_UNPAID")),
        "periods_delay_unknown": sum(1 for p in periods if p["status"] == "DELAY_UNKNOWN"),
        "delayed_payments": sum(1 for p in periods for pay in p["payments"] if pay["delay_days"]),
        "interest_demands": len(interest_items), "damages_demands": len(damages_items),
        "damages_demands_outstanding": sum(1 for i in damages_items if i["outstanding"]),
        "recovery_records": len(rc_items), "potential_damages_periods": len(potential),
        "discrepancies": len(disc),
    }
    answers = {
        "outstanding_damages": "NOT AVAILABLE" if c_dmg is None else ("YES" if c_dmg > 0 else "NO"),
        "esic_assessed_damages": "NOT AVAILABLE" if not available("damages") else ("YES" if any(i["state"] == "ASSESSED" for i in damages_items) else "NO"),
        "damages_demands_pending": "NOT AVAILABLE" if not available("damages") else (
            "YES" if counts["damages_demands_outstanding"] or totals["damages_pending_assessment"] else "NO"),
        "delayed_contributions": "NOT AVAILABLE" if not available("contribution") else ("YES" if counts["delayed_payments"] else "NO"),
        "recovery_or_defaulter_records": "NOT AVAILABLE" if not available("recovery") else ("YES" if rc_items else "NO"),
        "possible_unassessed_damages": "YES" if potential else ("NO" if available("contribution") else "NOT AVAILABLE"),
    }
    return {"calculation_version": CALCULATION_VERSION, "rules_version": engine.rules_version(), "as_of": as_of,
            "coverage": dict(coverage), "periods": periods, "interest": interest_items, "damages": damages_items,
            "recovery": rc_items, "potential_damages": potential, "totals": totals, "counts": counts,
            "answers": answers, "discrepancies": disc}
