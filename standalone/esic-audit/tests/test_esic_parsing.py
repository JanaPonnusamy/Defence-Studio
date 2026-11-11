from decimal import Decimal as D

from app.parsing import find_employer_code, map_headers, parse_table
from tests.fixtures.portal_server import HIST_HEAD, HIST_P1


def test_contribution_headers_map_by_name_not_position():
    m = map_headers("contribution", list(reversed(HIST_HEAD)))
    assert set(m.values()) >= {"wage_month", "challan_reference", "challan_created_date", "payment_date", "contribution_amount", "payment_status"}


def test_parse_contribution_rows_and_pager():
    t = {"headers": HIST_HEAD, "rows": HIST_P1 + [["1 2"]], "caption": "gv"}
    p = parse_table("contribution", t, "View Contribution History")
    assert p.mapped and len(p.records) == 4 and p.skipped_rows == 1
    r = p.records[1].values
    assert r["wage_month"] == "2024-05" and r["contribution_amount"] == D("12000.00") and r["challan_reference"] == "C1002"
    assert p.records[1].raw["Amount Paid"] == "12,000.00"  # original value preserved


def test_unmapped_table_is_reported_not_guessed():
    p = parse_table("interest", {"headers": ["Foo", "Bar"], "rows": [["1", "2"]]}, "x")
    assert not p.mapped and not p.records and "UNMAPPED" in p.warnings[0]


def test_invalid_amount_becomes_none_with_warning():
    p = parse_table("contribution", {"headers": HIST_HEAD, "rows": [["1", "Apr-2024", "C9", "", "", "12,0x0", "Paid"]]}, "x")
    assert p.records[0].values["contribution_amount"] is None and any("contribution_amount" in w for w in p.warnings)


def test_record_key_stable_for_duplicates():
    a = parse_table("contribution", {"headers": HIST_HEAD, "rows": [HIST_P1[1]]}, "page A").records[0]
    b = parse_table("contribution", {"headers": HIST_HEAD, "rows": [HIST_P1[1]]}, "page B").records[0]
    assert a.record_key == b.record_key


def test_total_and_no_record_rows_skipped():
    p = parse_table("contribution", {"headers": HIST_HEAD, "rows": [["Total", "", "", "", "", "100", ""], ["No Records Found"]]}, "x")
    assert not p.records and p.skipped_rows == 2


def test_employer_code_detection():
    assert find_employer_code("Employer Code: 99000000000000123 Logout") == "99000000000000123"
    assert find_employer_code("no code 1234") is None
