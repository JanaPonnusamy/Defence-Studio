"""Read-only boundary, credential hygiene and API access control."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.masking import mask_employer_code, redact
from app.safety import DENY, FORBIDDEN_LABELS, classify_request, guard_script, is_click_allowed

APP_DIR = Path(__file__).resolve().parent.parent / "app"
ESIC = ("esic.gov.in", "esic.in")
URL = "https://portal.esic.gov.in/EmployerPortal/x.aspx"
FORM = "application/x-www-form-urlencoded"


@pytest.mark.parametrize("label", FORBIDDEN_LABELS + ("File Monthly Contributions", "Update Employer Details", "Make Payment",
                                                      "btnGenerateChallan", "Change Password", "Proceed to Pay"))
def test_forbidden_labels_are_never_clicked(label):
    assert not is_click_allowed(label) and DENY.search(label)


@pytest.mark.parametrize("label", ["View Contribution History", "Consolidated View Contribution History", "Interest For Delay Payment",
                                   "Recovery/Defaulter Challan", "View RC", "Monthly Contribution", "View", "Search", "Next", "2"])
def test_view_labels_allowed(label):
    assert is_click_allowed(label)


@pytest.mark.parametrize("body,allowed", [
    ("__EVENTTARGET=ctl00%24gvHistory&__EVENTARGUMENT=Page%242&__VIEWSTATE=x", True),
    ("__EVENTTARGET=ctl00%24lnkConsolidatedView&__EVENTARGUMENT=", True),
    ("__EVENTTARGET=&ddlYear=2024&btnView=View", True),
    ("__EVENTTARGET=&btnGenerateChallan=Generate+Challan", False),
    ("__EVENTTARGET=&ctl00%24Main%24btnSubmit=Go", False),
    ("__EVENTTARGET=ctl00%24lnkPayNow&__EVENTARGUMENT=", False),
    ("__EVENTTARGET=&btnShow=Confirm+Payment", False),
    ("__EVENTTARGET=ctl00%24gv&__EVENTARGUMENT=Delete%240", False),
])
def test_network_guard_postbacks(body, allowed):
    assert classify_request("POST", URL, "document", body, FORM, ESIC, locked=True)[0] is allowed


def test_network_guard_blocks_payment_hosts_even_during_login():
    assert not classify_request("GET", "https://onlinesbi.sbi/sbicollect/", "document", None, None, ESIC, locked=False)[0]
    assert not classify_request("GET", "https://www.billdesk.com/pay", "document", None, None, ESIC, locked=True)[0]


def test_network_guard_blocks_writes_and_navigation_off_portal():
    assert not classify_request("POST", "https://evil.example/x", "xhr", "a=1", FORM, ESIC, locked=True)[0]
    assert not classify_request("PUT", URL, "xhr", "a=1", FORM, ESIC, locked=True)[0]
    assert not classify_request("GET", "https://example.com/", "document", None, None, ESIC, locked=True)[0]
    assert classify_request("GET", "https://cdn.example.com/x.js", "script", None, None, ESIC, locked=True)[0]
    assert not classify_request("POST", URL, "fetch", '{"action": "submitChallan"}', "application/json", ESIC, locked=True)[0]


def test_login_phase_does_not_inspect_login_form():
    # the login POST carries credentials; before lock the guard must not parse it at all
    allow, reason = classify_request("POST", URL, "document", "txtPassword=secret&btnLogin=Login", FORM, ESIC, locked=False)
    assert allow and reason == "login phase"


def test_guard_script_embeds_deny_pattern():
    js = guard_script()
    assert "generat" in js and "confirm" in js and "%DENY%" not in js


def test_module_never_types_into_or_submits_forms():
    src = "\n".join(p.read_text(encoding="utf-8") for p in APP_DIR.rglob("*.py"))
    assert not re.search(r"\.(fill|type|press|press_sequentially|set_input_files|check|select_option)\(", src)
    assert "storage_state" not in src  # no persisted authenticated profile
    assert "launch_persistent_context" not in src


def test_no_api_route_accepts_credentials_or_payments(tmp_path):
    app = create_app(load_settings(storage_dir=tmp_path, access_token="t"))
    paths = " ".join(getattr(r, "path", "") for r in app.routes).lower()
    for word in ("password", "otp", "captcha", "pay", "challan", "submit", "credential"):
        assert word not in paths


def test_api_requires_token(tmp_path):
    client = TestClient(create_app(load_settings(storage_dir=tmp_path, access_token="secret-token")))
    assert client.get("/api/audits").status_code == 401
    assert client.get("/api/audits", headers={"X-ESIC-Audit-Token": "wrong"}).status_code == 401
    assert client.get("/api/audits", headers={"X-ESIC-Audit-Token": "secret-token"}).status_code == 200
    assert client.get("/api/audits", headers={"Authorization": "Bearer secret-token"}).status_code == 200
    assert client.get("/api/audits/x/reports/pdf").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_masking_and_redaction():
    assert mask_employer_code("99000000000000123") == "99************123"
    out = redact("login txtPassword=abc otp: 123456 code 99000000000000123")
    assert "abc" not in out and "123456" not in out and "99000000000000123" not in out


def test_logs_redact_via_logger(caplog):
    from app.masking import configure_logging
    log = configure_logging()
    with caplog.at_level("INFO", logger="esic_audit"):
        log.info("password=abc123 employer 99000000000000123")
    assert "abc123" not in caplog.text and "99000000000000123" not in caplog.text
