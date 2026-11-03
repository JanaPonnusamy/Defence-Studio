"""Deterministic statutory rules engine (due dates, indicative interest, maximum damages).

Every figure carries an explanation and a basis flag:
* basis "VERIFIED"   - every rule used was read in an official text (still only an ESTIMATE);
* basis "UNVERIFIED" - at least one rule rests on a secondary source or none at all.
No figure produced here is ever a confirmed ESIC liability.
"""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from ..money import ZERO, add_months, q

RULES_PATH = Path(__file__).with_name("statutory_rules.json")


class RuleNotFound(LookupError):
    pass


@lru_cache(maxsize=1)
def load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def rules_version() -> str:
    return load_rules()["rules_version"]


def _d(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


@dataclass
class DueDate:
    wage_month: str
    due_date: date
    rule_id: str
    level: str
    explanation: str


@dataclass
class Segment:
    rule_id: str
    start: date
    end: date
    days: int
    rate_percent: Decimal
    amount: Decimal
    level: str


@dataclass
class Estimate:
    kind: str  # "INTEREST" | "DAMAGES"
    principal: Decimal
    due_date: date
    end_date: date
    delay_days: int
    delay_months: int
    amount: Decimal
    basis: str  # VERIFIED | UNVERIFIED
    segments: list[Segment] = field(default_factory=list)
    explanation: str = ""
    capped: bool = False


def due_date_for(wage_month: str) -> DueDate:
    year, month = (int(p) for p in wage_month.split("-"))
    for rule in load_rules()["due_date_rules"]:
        lo, hi = rule["wage_month_from"], rule["wage_month_to"]
        if (lo is None or wage_month >= lo) and (hi is None or wage_month <= hi):
            last = date(year, month, calendar.monthrange(year, month)[1])
            due = last + timedelta(days=rule["days_after_month_end"])
            return DueDate(wage_month, due, rule["id"], rule["level"],
                           f"{rule['provision']}: due within {rule['days_after_month_end']} days of "
                           f"{last.isoformat()} -> {due.isoformat()} [{rule['id']}, {rule['level']}]")
    raise RuleNotFound(f"no due-date rule for {wage_month}")


def _rule_for_day(rules: list[dict], day: date) -> dict | None:
    for rule in rules:
        lo, hi = _d(rule.get("from")), _d(rule.get("to"))
        if (lo is None or day >= lo) and (hi is None or day <= hi):
            return rule
    return None


def _segments(rules: list[dict], due: date, end: date, rate_of) -> list[tuple[dict | None, date, date, int]]:
    """Split the delay days (due+1 .. end, inclusive) into runs governed by one rule each."""
    out: list[tuple[dict | None, date, date, int]] = []
    day = due + timedelta(days=1)
    while day <= end:
        rule = _rule_for_day(rules, day)
        hi = _d(rule.get("to")) if rule else None
        run_end = min(end, hi) if hi else end
        if rule is None:  # find where the next rule starts to bound the gap
            starts = [_d(r["from"]) for r in rules if r.get("from") and _d(r["from"]) > day]
            run_end = min([end] + [s - timedelta(days=1) for s in starts])
        out.append((rule, day, run_end, (run_end - day).days + 1))
        day = run_end + timedelta(days=1)
    return out


def delay_months(due: date, end: date) -> int:
    months = 0
    while add_months(due, months + 1) <= end:
        months += 1
    return months


def interest_estimate(principal: Decimal, due: date, end: date, due_level: str = "OFFICIAL_TEXT") -> Estimate:
    """Simple interest per day of delay: principal x rate x days / 365 (indicative only)."""
    rules = load_rules()["interest_rules"]
    days = max(0, (end - due).days)
    est = Estimate("INTEREST", principal, due, end, days, delay_months(due, end) if days else 0, ZERO, "VERIFIED")
    if days == 0:
        est.explanation = "Paid on or before the due date: no interest."
        return est
    total = Decimal(0)
    for rule, start, stop, n in _segments(rules, due, end, None):
        if rule is None:
            est.basis = "UNVERIFIED"
            est.segments.append(Segment("NO-RULE", start, stop, n, Decimal(0), ZERO, "UNKNOWN"))
            continue
        rate = Decimal(rule["annual_rate_percent"])
        part = principal * rate / Decimal(100) * Decimal(n) / Decimal(365)
        total += part
        est.segments.append(Segment(rule["id"], start, stop, n, rate, q(part), rule["level"]))
        if rule["level"] != "OFFICIAL_TEXT":
            est.basis = "UNVERIFIED"
    if due_level != "OFFICIAL_TEXT":
        est.basis = "UNVERIFIED"
    est.amount = q(total)
    est.explanation = (f"Indicative interest on {q(principal)} for {days} day(s) of delay "
                       f"({due.isoformat()} -> {end.isoformat()}): "
                       + "; ".join(f"{s.days}d x {s.rate_percent}% p.a. [{s.rule_id}, {s.level}] = {s.amount}"
                                   for s in est.segments)
                       + f". Total {est.amount}. Formula: amount x rate x days / 365, simple interest.")
    return est


def damages_estimate(principal: Decimal, due: date, end: date, due_level: str = "OFFICIAL_TEXT") -> Estimate:
    """Maximum damages under the Regulation 31C slab (upper bound; ESIC must assess)."""
    rules = load_rules()["damages_rules"]
    days = max(0, (end - due).days)
    months = delay_months(due, end) if days else 0
    est = Estimate("DAMAGES", principal, due, end, days, months, ZERO, "VERIFIED")
    if days == 0:
        est.explanation = "Paid on or before the due date: no damages exposure."
        return est
    total = Decimal(0)
    for rule, start, stop, n in _segments(rules, due, end, None):
        if rule is None:
            est.basis = "UNVERIFIED"
            est.segments.append(Segment("NO-RULE", start, stop, n, Decimal(0), ZERO, "UNKNOWN"))
            continue
        rate = next(Decimal(s["max_rate_percent"]) for s in rule["slabs"]
                    if s["delay_months_below"] is None or months < s["delay_months_below"])
        part = principal * rate / Decimal(100) * Decimal(n) / Decimal(365)
        total += part
        est.segments.append(Segment(rule["id"], start, stop, n, rate, q(part), rule["level"]))
        if rule["level"] != "OFFICIAL_TEXT":
            est.basis = "UNVERIFIED"
    if due_level != "OFFICIAL_TEXT":
        est.basis = "UNVERIFIED"
    total = q(total)
    if total > q(principal):  # Section 85B: damages may not exceed the arrears
        total, est.capped = q(principal), True
    est.amount = total
    est.explanation = (f"MAXIMUM possible damages (Reg. 31C slab, delay {months} full month(s), {days} day(s)): "
                       + "; ".join(f"{s.days}d x {s.rate_percent}% p.a. [{s.rule_id}, {s.level}] = {s.amount}"
                                   for s in est.segments)
                       + (" ; capped at the amount of arrears (s.85B)" if est.capped else "")
                       + f". Upper bound {est.amount}. ESIC levies damages only after assessment and hearing;"
                         " the actual levy may be lower or waived.")
    return est


def rules_reference() -> list[dict]:
    """Flat list of every rule with its source, for the report's legal reference section."""
    data = load_rules()
    rows = []
    for group, label in (("due_date_rules", "Due date"), ("interest_rules", "Interest"), ("damages_rules", "Damages")):
        for rule in data[group]:
            src = data["sources"][rule["source"]]
            if group == "due_date_rules":
                applies = f"wage months {rule['wage_month_from'] or 'earliest'} to {rule['wage_month_to'] or 'present'}"
                method = f"due = last day of wage month + {rule['days_after_month_end']} days"
            elif group == "interest_rules":
                applies = f"delay days {rule['from'] or 'earliest'} to {rule['to'] or 'present'}"
                method = f"{rule['annual_rate_percent']}% p.a. simple, per day of delay"
            else:
                applies = f"delay days {rule['from'] or 'earliest'} to {rule['to'] or 'present'}"
                method = ("max % p.a. by delay: " + ", ".join(
                    f"<{s['delay_months_below']}m {s['max_rate_percent']}%" if s["delay_months_below"]
                    else f">=6m {s['max_rate_percent']}%" for s in rule["slabs"]) + "; capped at arrears")
            rows.append({
                "category": label, "rule_id": rule["id"], "rule_name": rule["name"],
                "provision": rule["provision"], "applies": applies, "method": method,
                "verification": rule["level"], "status": "VERIFIED" if rule["level"] == "OFFICIAL_TEXT" else "UNVERIFIED",
                "source_title": src["title"], "source_url": src["url"],
                "source_publication_date": src["publication_date"], "rule_version": data["rules_version"],
                "note": rule.get("note", ""),
            })
    return rows
