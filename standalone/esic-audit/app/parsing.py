"""Turn captured portal tables into typed records.

A captured table is {"caption": str, "headers": [str], "rows": [[str]]}. Column meaning is
inferred from header text only (never from column position). A table that does not map
to the minimum required fields for its category is reported as UNMAPPED, not guessed.
Every record keeps its original cell values (`raw`), and every value that fails to parse
becomes None plus a data-quality warning - never zero.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .money import InvalidValue, parse_date, parse_money, parse_wage_month

CATEGORIES = ("contribution", "interest", "damages", "recovery", "employer")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


# (field, kind, synonyms). Order matters: specific fields are matched before generic ones.
FIELD_MAP: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {
    "contribution": [
        ("employer_contribution", "money", ("employer contribution", "employers contribution", "employer share", "er contribution")),
        ("employee_contribution", "money", ("employee contribution", "employees contribution", "ip contribution", "employee share", "ee contribution")),
        ("challan_created_date", "date", ("challan created date", "challan creation date", "challan generated date", "challan generation date", "created date", "generated on")),
        ("payment_date", "date", ("challan submitted date", "payment date", "date of payment", "paid date", "paid on", "realisation date", "realization date", "transaction date", "deposit date", "submitted date")),
        ("challan_reference", "text", ("challan number", "challan no", "challan num", "challan id", "transaction number", "transaction no", "transaction id")),
        ("wage_month", "month", ("contribution period", "contribution month", "wage month", "wage period", "challan period", "month", "period")),
        ("contribution_amount", "money", ("total contribution", "contribution amount", "challan amount", "total amount", "amount paid", "amount")),
        ("payment_status", "text", ("payment status", "challan status", "transaction status", "status")),
    ],
    "interest": [
        ("assessment_date", "date", ("assessment date", "order date", "demand date", "date of order")),
        ("demand_reference", "text", ("demand number", "demand no", "order number", "order no", "reference number", "reference no", "challan number", "challan no")),
        ("contribution_period", "month", ("contribution period", "wage month", "period", "month")),
        ("paid_amount", "money", ("interest paid", "paid amount", "amount paid", "paid")),
        ("outstanding_amount", "money", ("outstanding amount", "outstanding", "balance amount", "balance", "pending amount", "amount due", "due amount")),
        ("assessed_amount", "money", ("assessed interest", "interest assessed", "interest amount", "interest due", "demand amount", "interest")),
    ],
    "damages": [
        ("assessment_date", "date", ("assessment date", "order date", "demand date", "date of order")),
        ("assessment_status", "text", ("assessment status", "order status", "status")),
        ("demand_reference", "text", ("demand number", "demand no", "order number", "order no", "reference number", "reference no", "challan number", "challan no")),
        ("contribution_period", "month", ("contribution period", "wage month", "period", "month")),
        ("paid_amount", "money", ("damages paid", "paid amount", "amount paid", "paid")),
        ("outstanding_amount", "money", ("outstanding amount", "outstanding", "balance amount", "balance", "pending amount", "amount due", "due amount")),
        ("assessed_amount", "money", ("damages assessed", "assessed damages", "damages levied", "damages amount", "demand amount", "damages")),
    ],
    "recovery": [
        ("demand_date", "date", ("rc date", "date of issue", "issue date", "demand date", "certificate date")),
        ("period_from", "month", ("period from", "from period", "from month", "from")),
        ("period_to", "month", ("period to", "to period", "to month", "to")),
        ("linked_references", "text", ("demand reference", "related demand", "order reference", "linked demand")),
        ("recovery_reference", "text", ("rc number", "rc no", "recovery certificate number", "recovery certificate no", "certificate number", "reference number", "reference no")),
        ("principal_amount", "money", ("contribution amount", "principal", "contribution")),
        ("interest_component", "money", ("interest amount", "interest")),
        ("damages_component", "money", ("damages amount", "damages")),
        ("paid_amount", "money", ("recovered amount", "amount recovered", "paid amount", "amount paid", "recovered", "paid")),
        ("outstanding_amount", "money", ("outstanding amount", "outstanding", "balance amount", "balance", "pending amount", "amount due")),
        ("demand_amount", "money", ("total demand", "demand amount", "rc amount", "total amount", "amount")),
        ("recovery_status", "text", ("rc status", "recovery status", "status")),
    ],
}

REQUIRED = {
    "contribution": [("wage_month",), ("contribution_amount", "payment_date", "challan_reference")],
    "interest": [("assessed_amount", "outstanding_amount")],
    "damages": [("assessed_amount", "outstanding_amount", "assessment_status")],
    "recovery": [("recovery_reference", "demand_amount", "outstanding_amount")],
}


@dataclass
class ParsedRecord:
    category: str
    values: dict[str, Any]
    raw: dict[str, str]
    source_reference: str
    record_key: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class TableParse:
    category: str
    mapping: dict[int, str]
    mapped: bool
    records: list[ParsedRecord]
    warnings: list[str]
    skipped_rows: int = 0


def map_headers(category: str, headers: list[str]) -> dict[int, str]:
    remaining = {i: norm(h) for i, h in enumerate(headers) if norm(h)}
    mapping: dict[int, str] = {}
    for exact in (True, False):
        for fld, _kind, synonyms in FIELD_MAP[category]:
            if fld in mapping.values():
                continue
            for syn in synonyms:
                hit = next((i for i, h in remaining.items()
                            if (h == syn if exact else re.search(rf"\b{re.escape(syn)}\b", h))), None)
                if hit is not None:
                    mapping[hit] = fld
                    del remaining[hit]
                    break
    return mapping


def _is_noise_row(cells: list[str]) -> bool:
    texts = [c.strip() for c in cells if c and c.strip()]
    if not texts:
        return True
    first = norm(texts[0])
    if first in ("total", "grand total", "sub total", "subtotal", "no records found", "no record found", "no data found"):
        return True
    tokens = " ".join(texts).split()
    return all(re.fullmatch(r"(\d+|\.\.\.|<|>|<<|>>|next|prev|previous|first|last)", t, re.I) for t in tokens)


def _key(category: str, parts: list[Any]) -> str:
    return hashlib.sha256(json.dumps([category] + [str(p) for p in parts], ensure_ascii=False).encode()).hexdigest()[:32]


def _convert(kind: str, raw: str):
    if kind == "money":
        return parse_money(raw)
    if kind == "date":
        return parse_date(raw)
    if kind == "month":
        return parse_wage_month(raw)
    text = " ".join(str(raw).split())
    return text or None


def parse_table(category: str, table: dict, source_reference: str) -> TableParse:
    headers = [str(h) for h in table.get("headers") or []]
    mapping = map_headers(category, headers)
    fields = set(mapping.values())
    mapped = all(any(f in fields for f in group) for group in REQUIRED[category])
    result = TableParse(category, mapping, mapped, [], [])
    if not mapped:
        result.warnings.append(
            f"UNMAPPED table '{table.get('caption') or source_reference}' for {category}: headers {headers!r} "
            "do not contain the required columns; table kept as evidence only.")
        return result
    kinds = {fld: kind for fld, kind, _ in FIELD_MAP[category]}
    for row_no, cells in enumerate(table.get("rows") or [], start=1):
        cells = [str(c) for c in cells]
        if _is_noise_row(cells) or [norm(c) for c in cells] == [norm(h) for h in headers]:
            result.skipped_rows += 1
            continue
        raw = {headers[i] if i < len(headers) else f"col{i}": cells[i] for i in range(len(cells))}
        values: dict[str, Any] = {}
        warnings: list[str] = []
        for idx, fld in mapping.items():
            cell = cells[idx] if idx < len(cells) else ""
            try:
                values[fld] = _convert(kinds[fld], cell)
            except InvalidValue as exc:
                values[fld] = None
                warnings.append(f"{source_reference} row {row_no}: {fld} {exc}")
        ref = f"{source_reference} row {row_no}"
        identity = {
            "contribution": [values.get("wage_month"), values.get("challan_reference")],
            "interest": [values.get("contribution_period"), values.get("demand_reference"), values.get("assessed_amount")],
            "damages": [values.get("contribution_period"), values.get("demand_reference"), values.get("assessed_amount")],
            "recovery": [values.get("recovery_reference")],
        }[category]
        if not any(identity[1:] if category != "recovery" else identity):
            identity = identity + [json.dumps(cells, ensure_ascii=False)]  # no natural key: use the whole row
        result.records.append(ParsedRecord(category, values, raw, ref, _key(category, identity), warnings))
        result.warnings.extend(warnings)
    return result


_EMPLOYER_CODE = re.compile(r"(?<!\d)(\d{17})(?!\d)")


def find_employer_code(page_text: str) -> str | None:
    """Locate a 17-digit ESIC employer code in page text (used only to produce a masked label)."""
    m = _EMPLOYER_CODE.search(page_text or "")
    return m.group(1) if m else None
