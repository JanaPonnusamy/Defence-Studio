"""Reconciliation: contributions, interest, damages, recovery and the no-double-count rule."""
from datetime import date
from decimal import Decimal as D

from app.reconcile import classify_payment, reconcile
from tests.conftest import FULL, contribution, demand

AS_OF = date(2026, 11, 3)


def test_expected_confirmed_totals(synthetic_records):
    r = reconcile(synthetic_records, FULL, AS_OF)
    t = r["totals"]
    assert t["confirmed_damages_outstanding"] == D("761.78")
    assert t["confirmed_interest_outstanding"] == D("780.33")
    assert t["confirmed_recovery_outstanding"] == D("1008.39")
    assert t["included_in_recovery_not_added_again"] == D("1008.39")
    assert t["total_confirmed_outstanding"] == D("1542.11")  # RC not added on top of its own interest + damages
    assert t["total_is_complete"] is True


def test_period_classification(synthetic_records):
    r = reconcile(synthetic_records, FULL, AS_OF)
    status = {p["wage_month"]: p["status"] for p in r["periods"]}
    assert status == {"2024-04": "PAID_ON_TIME", "2024-05": "PAID_LATE", "2024-06": "PAID_LATE", "2024-07": "UNPAID",
                      "2024-08": "PAID_ON_TIME", "2024-10": "PAID_LATE"}
    p = {p["wage_month"]: p for p in r["periods"]}
    assert p["2024-05"]["max_delay_days"] == 5 and p["2024-06"]["max_delay_days"] == 77
    assert r["counts"]["delayed_payments"] == 4  # 3 late + the unpaid one (open delay)


def test_challan_without_payment_is_not_paid(synthetic_records):
    r = reconcile(synthetic_records, FULL, AS_OF)
    jul = next(p for p in r["periods"] if p["wage_month"] == "2024-07")
    assert jul["payments"][0]["payment_class"] == "CHALLAN_NOT_CONFIRMED" and jul["unpaid_amount"] == D("9000.00")
    assert r["totals"]["contributions_not_shown_as_paid"] == D("9000.00")
    assert classify_payment({"challan_reference": "X", "payment_status": "Payment Initiated"}) == "CHALLAN_NOT_CONFIRMED"
    assert classify_payment({"challan_reference": "X", "payment_status": "Not Paid", "payment_date": date(2024, 1, 1)}) == "CHALLAN_NOT_CONFIRMED"


def test_potential_damages_only_where_not_assessed(synthetic_records):
    r = reconcile(synthetic_records, FULL, AS_OF)
    periods = {x["period"]: x for x in r["potential_damages"]}
    assert set(periods) == {"2024-05", "2024-07"}  # 2024-06 assessed; 2024-10 assessed and inside the RC
    assert periods["2024-05"]["max_damages"] == D("8.22") and periods["2024-05"]["category"] == "POTENTIAL_UNASSESSED"
    assert r["totals"]["estimated_potential_damages_max"] > 0
    assert r["answers"]["possible_unassessed_damages"] == "YES"


def test_missing_period_flagged(synthetic_records):
    r = reconcile(synthetic_records, FULL, AS_OF)
    assert any(d["type"] == "MISSING_CONTRIBUTION_PERIOD" and d["period"] == "2024-09" for d in r["discrepancies"])


def test_not_available_is_never_zero(synthetic_records):
    cov = dict(FULL, damages="NOT_AVAILABLE")
    recs = dict(synthetic_records, damages=[])
    r = reconcile(recs, cov, AS_OF)
    assert r["totals"]["confirmed_damages_outstanding"] is None
    assert r["totals"]["total_is_complete"] is False and "damages" in r["totals"]["categories_not_available"]
    assert r["answers"]["outstanding_damages"] == "NOT AVAILABLE"
    assert all(x["category"] == "ASSESSMENT_STATUS_UNKNOWN" for x in r["potential_damages"])


def test_empty_page_means_zero_only_when_read(synthetic_records):
    r = reconcile(dict(synthetic_records, recovery=[]), dict(FULL, recovery="EMPTY"), AS_OF)
    assert r["totals"]["confirmed_recovery_outstanding"] == D("0.00") and r["answers"]["recovery_or_defaulter_records"] == "NO"


def test_duplicate_challan_counted_once():
    recs = {"contribution": [contribution("2024-04", "C1", "100", date(2024, 5, 1)),
                             contribution("2024-04", "C1", "100", date(2024, 5, 1))]}
    r = reconcile(recs, {"contribution": "CAPTURED"}, AS_OF)
    assert r["periods"][0]["total_amount"] == D("100.00")


def test_inconsistent_duplicate_flagged():
    recs = {"contribution": [contribution("2024-04", "C1", "100", date(2024, 5, 1)),
                             contribution("2024-05", "C1", "250", date(2024, 6, 1))]}
    r = reconcile(recs, {"contribution": "CAPTURED"}, AS_OF)
    assert any(d["type"] == "INCONSISTENT_DUPLICATE" for d in r["discrepancies"]) and len(r["periods"]) == 1


def test_outstanding_uses_assessed_minus_paid_and_flags_mismatch():
    recs = {"interest": [demand("2024-05", "I1", "100.00", "40.00", "80.00")]}
    r = reconcile(recs, {"interest": "CAPTURED"}, AS_OF)
    assert r["interest"][0]["outstanding"] == D("60.00")
    assert any(d["type"] == "OUTSTANDING_MISMATCH" for d in r["discrepancies"])


def test_payment_unknown_is_not_assumed():
    r = reconcile({"damages": [demand("2024-05", "D1", "100.00", None, None, "Assessed")]}, {"damages": "CAPTURED"}, AS_OF)
    assert r["damages"][0]["outstanding"] is None and r["totals"]["total_is_complete"] is False
    assert any(d["type"] == "PAYMENT_UNKNOWN" for d in r["discrepancies"])


def test_proposed_damages_are_pending_not_confirmed():
    r = reconcile({"damages": [demand("2024-05", "D1", "100.00", "0", "100.00", "Show cause notice issued")]},
                  {"damages": "CAPTURED"}, AS_OF)
    assert r["damages"][0]["state"] == "PENDING_ASSESSMENT"
    assert r["totals"]["confirmed_damages_outstanding"] == D("0.00")
    assert r["answers"]["damages_demands_pending"] == "YES"


def test_negative_outstanding_not_used_as_credit():
    r = reconcile({"interest": [demand("2024-05", "I1", "100.00", "150.00", None)]}, {"interest": "CAPTURED"}, AS_OF)
    assert r["interest"][0]["outstanding"] == D("0.00") and any(d["type"] == "NEGATIVE_OUTSTANDING" for d in r["discrepancies"])


def test_rc_linked_by_reference(synthetic_records):
    recs = dict(synthetic_records)
    rc = dict(recs["recovery"][0], period_from=None, period_to=None, linked_references="INT-C1006, D-2025-03")
    r = reconcile(dict(recs, recovery=[rc]), FULL, AS_OF)
    assert r["totals"]["total_confirmed_outstanding"] == D("1542.11") and r["totals"]["possible_double_count"] == D("0.00")


def test_rc_without_linkage_flags_possible_double_count(synthetic_records):
    rc = dict(synthetic_records["recovery"][0], period_from=None, period_to=None, linked_references=None)
    r = reconcile(dict(synthetic_records, recovery=[rc]), FULL, AS_OF)
    assert r["totals"]["possible_double_count"] == D("1542.11")
    assert any(d["type"] == "POSSIBLE_DOUBLE_COUNT" for d in r["discrepancies"])


def test_esic_interest_differs_from_indicative_is_reported():
    recs = {"contribution": [contribution("2024-05", "C1", "12000", date(2024, 6, 20))],
            "interest": [demand("2024-05", "I1", "50.00", "0", "50.00")]}
    r = reconcile(recs, {"contribution": "CAPTURED", "interest": "CAPTURED"}, AS_OF)
    assert any(d["type"] == "INTEREST_DIFFERENCE" for d in r["discrepancies"])


def test_deterministic(synthetic_records):
    assert reconcile(synthetic_records, FULL, AS_OF) == reconcile(synthetic_records, FULL, AS_OF)
