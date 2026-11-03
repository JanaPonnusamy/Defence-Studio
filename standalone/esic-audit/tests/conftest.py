import sys
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def contribution(month, challan, amount, paid=None, status="Paid", created=None):
    return {"wage_month": month, "challan_reference": challan, "contribution_amount": D(amount) if amount is not None else None,
            "payment_date": paid, "payment_status": status, "challan_created_date": created,
            "employer_contribution": None, "employee_contribution": None, "source_reference": f"test {challan}"}


def demand(period, ref, assessed, paid, outstanding, status=None):
    conv = lambda v: D(v) if v is not None else None
    return {"contribution_period": period, "demand_reference": ref, "assessed_amount": conv(assessed), "paid_amount": conv(paid),
            "outstanding_amount": conv(outstanding), "assessment_date": None, "assessment_status": status,
            "source_reference": f"test {ref}"}


@pytest.fixture
def synthetic_records():
    """Same synthetic figures as tests/fixtures/portal_server.py (invented test data)."""
    return {
        "contribution": [
            contribution("2024-04", "C1001", "10000.00", date(2024, 5, 14)),
            contribution("2024-05", "C1002", "12000.00", date(2024, 6, 20)),
            contribution("2024-06", "C1003", "11500.00", date(2024, 9, 30)),
            contribution("2024-07", "C1004", "9000.00", None, "Pending", date(2024, 8, 10)),
            contribution("2024-08", "C1005", "10000.00", date(2024, 9, 10)),
            contribution("2024-10", "C1006", "8000.00", date(2025, 5, 20)),
        ],
        "interest": [
            demand("2024-05", "INT-C1002", "19.73", "19.73", "0.00"),
            demand("2024-06", "INT-C1003", "291.12", "0.00", "291.12"),
            demand("2024-10", "INT-C1006", "489.21", "0.00", "489.21"),
        ],
        "damages": [
            demand("2024-06", "D-2024-17", "242.60", "0.00", "242.60", "Assessed"),
            demand("2024-10", "D-2025-03", "1019.18", "500.00", "519.18", "Assessed"),
        ],
        "recovery": [{
            "recovery_reference": "RC/2025/001", "demand_date": date(2025, 8, 1), "period_from": "2024-10", "period_to": "2024-10",
            "linked_references": None, "principal_amount": D("0.00"), "interest_component": D("489.21"),
            "damages_component": D("519.18"), "demand_amount": D("1008.39"), "paid_amount": D("0.00"),
            "outstanding_amount": D("1008.39"), "recovery_status": "Pending", "source_reference": "test RC"}],
    }


FULL = {"contribution": "CAPTURED", "interest": "CAPTURED", "damages": "CAPTURED", "recovery": "CAPTURED"}
