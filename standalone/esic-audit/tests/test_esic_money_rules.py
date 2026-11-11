from datetime import date
from decimal import Decimal as D

import pytest

from app.money import InvalidValue, add_months, fmt_inr, parse_date, parse_money, parse_wage_month
from app.rules import engine


@pytest.mark.parametrize("raw,expected", [("1,23,456.50", D("123456.50")), ("Rs. 500/-", D("500.00")), ("₹ 12,000", D("12000.00")),
                                          ("(250.00)", D("-250.00")), ("0.005", D("0.01"))])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw", ["", "-", "NA", None, "Nil"])
def test_blank_money_is_none_not_zero(raw):
    assert parse_money(raw) is None


@pytest.mark.parametrize("raw", ["12a.00", "1.2.3", "abc"])
def test_invalid_money_raises(raw):
    with pytest.raises(InvalidValue):
        parse_money(raw)


def test_money_is_decimal_never_float():
    assert isinstance(parse_money("0.10"), D) and parse_money("0.10") + parse_money("0.20") == D("0.30")


@pytest.mark.parametrize("raw,expected", [("Apr-2024", "2024-04"), ("April 2024", "2024-04"), ("04/2024", "2024-04"),
                                          ("2024-04", "2024-04"), ("042024", "2024-04")])
def test_parse_wage_month(raw, expected):
    assert parse_wage_month(raw) == expected


def test_parse_date_formats():
    assert parse_date("14/05/2024") == date(2024, 5, 14) == parse_date("14-May-2024") == parse_date("14/05/2024 10:22:01")


def test_fmt_inr_indian_grouping():
    assert fmt_inr(D("1234567.8")) == "Rs. 12,34,567.80" and fmt_inr(None) == "NOT AVAILABLE"


def test_add_months_clamps_month_end():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_due_date_current_rule_is_15_days_official():
    d = engine.due_date_for("2024-04")
    assert d.due_date == date(2024, 5, 15) and d.rule_id == "REG31-15D" and d.level == "OFFICIAL_TEXT"


def test_due_date_before_june_2017_uses_21_days_secondary():
    d = engine.due_date_for("2017-05")
    assert d.due_date == date(2017, 6, 21) and d.level == "SECONDARY"


def test_due_date_after_code_commencement_is_unverified():
    assert engine.due_date_for("2025-11").level == "UNKNOWN"


def test_interest_simple_daily():
    e = engine.interest_estimate(D("12000.00"), date(2024, 6, 15), date(2024, 6, 20))
    assert e.delay_days == 5 and e.amount == D("19.73") and e.basis == "VERIFIED"


def test_interest_zero_when_on_time():
    e = engine.interest_estimate(D("1000"), date(2024, 6, 15), date(2024, 6, 15))
    assert e.amount == D("0.00") and e.delay_days == 0


def test_interest_split_across_code_commencement_is_unverified():
    e = engine.interest_estimate(D("10000"), date(2025, 11, 15), date(2025, 11, 30))
    assert [s.rule_id for s in e.segments] == ["REG31A-12", "COSS-S127-12"]
    assert [s.days for s in e.segments] == [5, 10] and e.basis == "UNVERIFIED"


def test_interest_historical_rate_effective_date():
    e = engine.interest_estimate(D("10000"), date(2005, 9, 25), date(2005, 10, 5))
    assert [(s.rule_id, s.days, s.rate_percent) for s in e.segments] == [("REG31A-15", 5, D("15")), ("REG31A-12", 5, D("12"))]


@pytest.mark.parametrize("due,end,months,rate", [
    (date(2024, 6, 15), date(2024, 6, 20), 0, D("5")),
    (date(2024, 7, 15), date(2024, 9, 30), 2, D("10")),
    (date(2024, 7, 15), date(2024, 11, 15), 4, D("15")),
    (date(2024, 11, 15), date(2025, 5, 20), 6, D("25")),
])
def test_damages_slab(due, end, months, rate):
    e = engine.damages_estimate(D("10000"), due, end)
    assert e.delay_months == months and e.segments[0].rate_percent == rate


def test_damages_values_match_fixture():
    assert engine.damages_estimate(D("11500"), date(2024, 7, 15), date(2024, 9, 30)).amount == D("242.60")
    assert engine.damages_estimate(D("8000"), date(2024, 11, 15), date(2025, 5, 20)).amount == D("1019.18")


def test_damages_capped_at_arrears():
    e = engine.damages_estimate(D("1000"), date(2018, 1, 15), date(2024, 1, 15))
    assert e.capped and e.amount == D("1000.00")


def test_every_rule_has_source_and_level():
    rows = engine.rules_reference()
    assert rows and all(r["source_url"].startswith("https://") and r["status"] in ("VERIFIED", "UNVERIFIED") for r in rows)
