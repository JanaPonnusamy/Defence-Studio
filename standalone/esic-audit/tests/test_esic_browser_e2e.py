"""End-to-end audits in a real (headless) Chrome against the SYNTHETIC fixture portal.

The test class `HumanSession` plays the employer: it types the (fake) login itself, and
deliberately tries forbidden actions so the read-only guards can be observed. The module's
own code never types into or submits a form (see test_esic_security).
"""
import hashlib
import time
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.browser import BrowserSession
from app.config import load_settings
from app.main import create_app
from app.orchestrator import AuditOrchestrator
from app.repository import Repository
from tests.fixtures.portal_server import FixturePortal

pytestmark = pytest.mark.browser
H = {"X-ESIC-Audit-Token": "t"}
FAKE_PASSWORD = "synthetic-pass-Zx81"


def _chrome_available() -> str | None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        for channel in ("chrome", "msedge", None):
            try:
                p.chromium.launch(channel=channel, headless=True).close()
                return channel or "bundled"
            except Exception:
                continue
    return None


CHANNEL = _chrome_available()
if CHANNEL is None:
    pytest.skip("no Chrome/Edge/Chromium available for Playwright", allow_module_level=True)


class HumanSession(BrowserSession):
    base = ""
    login = True
    try_forbidden = True

    def start(self):
        page = super().start()
        if self.login:  # the employer logs in by hand (test stand-in)
            page.fill("#txtUserName", "synthetic-user")
            page.fill("#txtPassword", FAKE_PASSWORD)
            page.fill("#txtChallanCaptcha", "AB12")
            page.click("#btnLogin")
            page.wait_for_load_state()
        return page

    def before_user_capture(self, category):
        page = self.page
        if category == "damages" and self.try_forbidden:
            page.goto(self.base + "/InterestForDelay.aspx")
            page.click("input[name=btnGenerateChallan]")  # user tries to generate a challan -> click guard
            page.wait_for_timeout(300)
            page.evaluate("""() => fetch('/InterestForDelay.aspx', {method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: 'btnPayNow=Pay'}).catch(() => 0)""")
            page.goto(self.base + "/ViewRC.aspx")
            page.click("text=Pay online")  # payment link -> click guard
            try:
                page.goto("https://onlinesbi.sbi/sbicollect/")  # payment gateway -> network guard
            except Exception:
                pass
            page.wait_for_timeout(500)  # let Chrome settle on its blocked-navigation error page
            page.goto(self.base + "/DamagesOrders.aspx")
        elif category == "damages":
            page.goto(self.base + "/DamagesOrders.aspx")


@pytest.fixture
def portal():
    with FixturePortal() as p:
        yield p


def make(tmp_path, portal, **session_attrs):
    settings = load_settings(storage_dir=tmp_path, login_url=portal.login_url, headless=True, access_token="t",
                             browser_channel=None if CHANNEL == "bundled" else CHANNEL)
    repo = Repository(settings.db_path)

    def factory(on_event):
        s = HumanSession(settings.login_url, settings.allowed_host_suffixes, settings.browser_channel, settings.headless, on_event)
        s.base = portal.base
        for k, v in session_attrs.items():
            setattr(s, k, v)
        return s
    orch = AuditOrchestrator(settings, repo, session_factory=factory)
    return settings, repo, orch, TestClient(create_app(settings, orch))


def wait_for(client, uuid, pred, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        d = client.get(f"/api/audits/{uuid}", headers=H).json()
        if pred(d):
            return d
        time.sleep(0.3)
    raise AssertionError(f"timeout; last status {d['status']} / {d['current_activity']} / {d['stages'][-3:]}")


def stage(d, name, outcome=None):
    return any(s["stage"] == name and (outcome is None or s["outcome"] == outcome) for s in d["stages"])


def run_audit(client, orch, capture_damages=True):
    uuid = client.post("/api/audits", headers=H).json()["audit_uuid"]
    assert client.post("/api/audits", headers=H).status_code == 409  # one audit at a time
    wait_for(client, uuid, lambda d: d["status"] == "WAITING_FOR_LOGIN" and "Log in" in (d["current_activity"] or ""))
    client.post(f"/api/audits/{uuid}/confirm-login", headers=H)
    wait_for(client, uuid, lambda d: stage(d, "ASSISTED_CAPTURE", "WAITING"))
    if capture_damages:
        client.post(f"/api/audits/{uuid}/capture", headers=H, json={"category": "damages", "page_name": "Damages Orders"})
        wait_for(client, uuid, lambda d: stage(d, "CAPTURE:damages"))
    client.post(f"/api/audits/{uuid}/finish", headers=H)
    orch.wait(120)
    return client.get(f"/api/audits/{uuid}", headers=H).json()


def test_full_read_only_audit_and_history(tmp_path, portal):
    settings, repo, orch, client = make(tmp_path, portal)
    d = run_audit(client, orch)
    assert d["status"] == "COMPLETED", d["error_summary"]
    t = d["result"]["totals"]
    assert D(t["confirmed_damages_outstanding"]) == D("761.78")
    assert D(t["confirmed_interest_outstanding"]) == D("780.33")
    assert D(t["confirmed_recovery_outstanding"]) == D("1008.39")
    assert D(t["total_confirmed_outstanding"]) == D("1542.11")
    assert d["employer_identifier_masked"] == "99************123"
    assert d["result"]["counts"]["periods_inspected"] == 6  # page 2 reached through the pager postback
    assert stage(d, "INSPECT:consolidated_history", "CAPTURED")  # postback menu link
    assert stage(d, "BROWSER_SESSION", "CLOSED") and stage(d, "READ_ONLY_GUARD", "ACTIVE")

    # read-only boundary held: nothing state-changing reached the portal
    assert portal.state.forbidden_hits == []
    assert portal.state.logins == 1
    events = " | ".join(e["message"] for e in client.get(f"/api/audits/{d['audit_uuid']}/logs", headers=H).json()["events"])
    assert "blocked click" in events and "request blocked" in events and "payment/bank host blocked" in events
    assert not any(p.startswith(("/FileMonthly", "/UpdateEmployer")) for _, p, _ in portal.state.requests)

    # the password typed by the user is nowhere in the database, evidence or reports
    for f in Path(settings.storage_dir).rglob("*"):
        if f.is_file() and f.name != "access_token.txt":
            assert FAKE_PASSWORD.encode() not in f.read_bytes(), f

    # evidence and reports exist with matching checksums
    ev = client.get(f"/api/audits/{d['audit_uuid']}/evidence", headers=H).json()
    assert len(ev) >= 10 and {e["evidence_type"] for e in ev} == {"screenshot", "html_snapshot"}
    body = client.get(f"/api/audits/{d['audit_uuid']}/evidence/{ev[0]['id']}", headers=H).content
    assert hashlib.sha256(body).hexdigest() == ev[0]["content_hash"]
    pdf = client.get(f"/api/audits/{d['audit_uuid']}/reports/pdf", headers=H)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert client.get(f"/api/audits/{d['audit_uuid']}/reports/xlsx", headers=H).status_code == 200
    first_reports = {r["report_type"]: r["checksum"] for r in d["reports"]}

    # second audit (damages not captured -> PARTIAL) never overwrites the first
    d2 = run_audit(client, orch, capture_damages=False)
    assert d2["status"] == "PARTIAL" and d2["result"]["totals"]["confirmed_damages_outstanding"] is None
    again = client.get(f"/api/audits/{d['audit_uuid']}", headers=H).json()
    assert {r["report_type"]: r["checksum"] for r in again["reports"]} == first_reports and again["status"] == "COMPLETED"
    assert len(client.get("/api/audits", headers=H).json()) == 2
    cmp = client.get(f"/api/compare?older={d['audit_uuid']}&newer={d2['audit_uuid']}", headers=H).json()
    assert {x["reference"] for x in cmp["corrected_or_missing"]} == {"D-2024-17", "D-2025-03"}


def test_cancel_while_waiting_for_login_closes_browser(tmp_path, portal):
    _, repo, orch, client = make(tmp_path, portal, login=False)
    uuid = client.post("/api/audits", headers=H).json()["audit_uuid"]
    wait_for(client, uuid, lambda d: d["status"] == "WAITING_FOR_LOGIN" and "Log in" in (d["current_activity"] or ""))
    client.post(f"/api/audits/{uuid}/confirm-login", headers=H)  # not logged in yet
    wait_for(client, uuid, lambda d: stage(d, "LOGIN_CHECK", "NOT_YET"))
    client.post(f"/api/audits/{uuid}/cancel", headers=H)
    orch.wait(30)
    d = client.get(f"/api/audits/{uuid}", headers=H).json()
    assert d["status"] == "CANCELLED" and stage(d, "BROWSER_SESSION", "CLOSED") and d["result"] is None
    assert portal.state.logins == 0


def test_expired_session_never_reports_zero(tmp_path, portal):
    _, repo, orch, client = make(tmp_path, portal)
    uuid = client.post("/api/audits", headers=H).json()["audit_uuid"]
    wait_for(client, uuid, lambda d: d["status"] == "WAITING_FOR_LOGIN" and "Log in" in (d["current_activity"] or ""))
    client.post(f"/api/audits/{uuid}/confirm-login", headers=H)
    wait_for(client, uuid, lambda d: d["status"] in ("AUTHENTICATED", "INSPECTING"))
    portal.state.expired = True  # portal logs the user out
    wait_for(client, uuid, lambda d: stage(d, "ASSISTED_CAPTURE", "WAITING") or d["status"] in ("FAILED", "PARTIAL"))
    try:
        client.post(f"/api/audits/{uuid}/finish", headers=H)
    except Exception:
        pass
    orch.wait(60)
    d = client.get(f"/api/audits/{uuid}", headers=H).json()
    assert d["status"] in ("FAILED", "PARTIAL")
    if d["result"]:
        assert d["result"]["totals"]["total_is_complete"] is False
    assert any(s["outcome"] == "SESSION_EXPIRED" for s in d["stages"]) or d["status"] == "FAILED"


def test_user_login_is_not_stalled_while_waiting(tmp_path, portal):
    """Regression: the employer's own login POST must reach the portal while the module waits."""
    settings = load_settings(storage_dir=tmp_path, login_url=portal.login_url + "?auto=1", headless=True, access_token="t",
                             browser_channel=None if CHANNEL == "bundled" else CHANNEL)
    repo = Repository(settings.db_path)

    def factory(on_event):
        s = HumanSession(settings.login_url, settings.allowed_host_suffixes, settings.browser_channel, settings.headless, on_event)
        s.base, s.login = portal.base, False
        return s
    orch = AuditOrchestrator(settings, repo, session_factory=factory)
    client = TestClient(create_app(settings, orch))
    uuid = client.post("/api/audits", headers=H).json()["audit_uuid"]
    end = time.monotonic() + 20
    while portal.state.logins == 0 and time.monotonic() < end:
        time.sleep(0.2)
    assert portal.state.logins == 1  # page-submitted login went through while the orchestrator was idle
    client.post(f"/api/audits/{uuid}/confirm-login", headers=H)
    wait_for(client, uuid, lambda d: d["status"] in ("AUTHENTICATED", "INSPECTING"))
    client.post(f"/api/audits/{uuid}/cancel", headers=H)
    orch.wait(60)
