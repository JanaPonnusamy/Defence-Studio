"""Fixed-precision INR and date helpers. Currency never touches float."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISA = Decimal("0.01")
ZERO = Decimal("0.00")

_MONEY_CLEAN = re.compile(r"(?i)(rs\.?|inr|₹|/-|\s|,)")


class InvalidValue(ValueError):
    """A portal value that could not be parsed. Never silently converted to zero."""


def q(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def parse_money(raw: str | None) -> Decimal | None:
    """Parse an Indian-formatted amount ("1,23,456.50", "Rs. 500/-"). Blank -> None.

    Anything that is not a clean number raises InvalidValue so the caller records a
    data-quality warning instead of inventing a value.
    """
    if raw is None:
        return None
    text = _MONEY_CLEAN.sub("", str(raw))
    if text in ("", "-", "--", "NA", "N/A", "na", "n/a", "Nil", "nil", "NIL"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise InvalidValue(f"not an amount: {raw!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - guarded by regex
        raise InvalidValue(f"not an amount: {raw!r}") from exc
    return q(-value if negative else value)


_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%b-%Y", "%d %b %Y",
                 "%d-%B-%Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %I:%M:%S %p",
                 "%d-%m-%Y %H:%M:%S", "%d-%b-%Y %H:%M:%S")


def parse_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if text in ("", "-", "--", "NA", "N/A"):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise InvalidValue(f"not a date: {raw!r}")


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_wage_month(raw: str | None) -> str | None:
    """Return "YYYY-MM" for "Apr-2024", "April 2024", "04/2024", "2024-04", "042024"."""
    if raw is None:
        return None
    text = " ".join(str(raw).split()).strip()
    if not text:
        return None
    m = re.fullmatch(r"(?i)([a-z]{3,9})[\s\-/,]*(\d{4})", text)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return f"{int(m.group(2)):04d}-{_MONTHS[m.group(1)[:3].lower()]:02d}"
    m = re.fullmatch(r"(\d{1,2})[\-/\s](\d{4})", text) or re.fullmatch(r"(\d{2})(\d{4})", text)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{4})[\-/](\d{1,2})", text)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    raise InvalidValue(f"not a wage month: {raw!r}")


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, last))


def fmt_inr(value: Decimal | None) -> str:
    """Indian digit grouping: 12,34,567.89. None -> 'NOT AVAILABLE'."""
    if value is None:
        return "NOT AVAILABLE"
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(q(value)):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}Rs. {whole}.{frac}"
