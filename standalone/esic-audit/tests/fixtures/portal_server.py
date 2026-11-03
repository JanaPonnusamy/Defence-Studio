"""SYNTHETIC ESIC-like portal for tests. Every figure here is invented test data.

It imitates the ASP.NET WebForms patterns of the real portal (postback links, GridView
pager, a Generate Challan button) so the read-only guards can be exercised end to end.
`ServerState.forbidden_hits` records every request that would have changed state; the
security tests assert it stays empty.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

EMPLOYER_CODE = "99000000000000123"  # synthetic 17-digit code

LAYOUT = """<!doctype html><html><head><title>{title}</title>
<script>function __doPostBack(t,a){{var f=document.forms[0];f.__EVENTTARGET.value=t;f.__EVENTARGUMENT.value=a;f.submit();}}</script>
</head><body><form method="post" action="{action}">
<input type="hidden" name="__EVENTTARGET" value=""><input type="hidden" name="__EVENTARGUMENT" value="">
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="SYNTHETICVIEWSTATE">
<div id="hdr">SYNTHETIC TEST PORTAL - Employer Code: {code} <a href="/Logout.aspx">Logout</a></div>
<table id="menuwrap"><tr><td>
<ul id="menu"><li><span>Employer</span><ul><li><a href="/UpdateEmployer.aspx">Update Employer Details</a></li></ul></li>
<li><span>Monthly Contribution</span><ul>
<li><a href="/FileMonthly.aspx">File Monthly Contributions</a></li>
<li><a href="/ViewContributionHistory.aspx">View Contribution History</a></li>
<li><a href="javascript:__doPostBack('ctl00$lnkConsolidatedView','')">Consolidated View Contribution History</a></li>
<li><a href="/InterestForDelay.aspx">Interest For Delay Payment</a></li>
<li><a href="/RecoveryDefaulter.aspx">Recovery/Defaulter Challan</a></li>
<li><a href="/ViewRC.aspx">View RC</a></li>
</ul></li></ul></td></tr></table>
<h2>{title}</h2>{body}
</form></body></html>"""

LOGIN = """<!doctype html><html><head><title>ESIC Login (synthetic)</title></head><body>
<form method="post" action="/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx">
<input name="txtUserName" type="text" id="txtUserName" placeholder="Type your username">
<input name="txtPassword" type="password" id="txtPassword" placeholder="Type your password">
<input name="txtChallanCaptcha" type="text" id="txtChallanCaptcha" placeholder="Type your Captcha">
<input type="submit" name="btnLogin" value="Login" id="btnLogin"></form></body></html>"""


def grid(gid: str, headers: list[str], rows: list[list[str]], pager: str = "") -> str:
    h = "".join(f"<th>{x}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    p = f'<tr><td colspan="{len(headers)}">{pager}</td></tr>' if pager else ""
    return f'<table id="{gid}"><tr>{h}</tr>{b}{p}</table>'


HIST_HEAD = ["Sl No", "Contribution Period", "Challan Number", "Challan Created Date", "Challan Submitted Date", "Amount Paid", "Status"]
HIST_P1 = [
    ["1", "Apr-2024", "C1001", "10/05/2024", "14/05/2024", "10,000.00", "Paid"],
    ["2", "May-2024", "C1002", "18/06/2024", "20/06/2024", "12,000.00", "Paid"],
    ["3", "Jun-2024", "C1003", "28/09/2024", "30/09/2024", "11,500.00", "Paid"],
    ["4", "Jul-2024", "C1004", "10/08/2024", "", "9,000.00", "Pending"],
]
HIST_P2 = [
    ["5", "Aug-2024", "C1005", "08/09/2024", "10/09/2024", "10,000.00", "Paid"],
    ["6", "Oct-2024", "C1006", "18/05/2025", "20/05/2025", "8,000.00", "Paid"],
]
INTEREST = grid("gvInterest", ["Contribution Period", "Challan Number", "Interest Amount", "Interest Paid", "Balance"], [
    ["May-2024", "INT-C1002", "19.73", "19.73", "0.00"],
    ["Jun-2024", "INT-C1003", "291.12", "0.00", "291.12"],
    ["Oct-2024", "INT-C1006", "489.21", "0.00", "489.21"],
]) + '<input type="submit" name="btnGenerateChallan" value="Generate Challan">'
DAMAGES = grid("gvDamages", ["Contribution Period", "Order Number", "Order Date", "Damages Amount", "Damages Paid", "Balance", "Status"], [
    ["Jun-2024", "D-2024-17", "15/01/2025", "242.60", "0.00", "242.60", "Assessed"],
    ["Oct-2024", "D-2025-03", "10/07/2025", "1,019.18", "500.00", "519.18", "Assessed"],
])
RC = grid("gvRC", ["RC Number", "RC Date", "Period From", "Period To", "Contribution", "Interest", "Damages", "Total Demand",
                   "Recovered Amount", "Balance", "Status"], [
    ["RC/2025/001", "01/08/2025", "Oct-2024", "Oct-2024", "0.00", "489.21", "519.18", "1,008.39", "0.00", "1,008.39", "Pending"],
]) + '<a href="https://onlinesbi.sbi/sbicollect/">Pay online</a>'


@dataclass
class ServerState:
    requests: list = field(default_factory=list)
    forbidden_hits: list = field(default_factory=list)
    logins: int = 0
    expired: bool = False


def make_handler(state: ServerState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, html: str, status=200, headers=None):
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, to: str, cookie: str | None = None):
            self.send_response(302)
            self.send_header("Location", to)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _authed(self) -> bool:
            return "sess=1" in (self.headers.get("Cookie") or "") and not state.expired

        def _page(self, path: str, form: dict) -> None:
            t, a = form.get("__EVENTTARGET", ""), form.get("__EVENTARGUMENT", "")
            if path == "/Home.aspx" and t == "ctl00$lnkConsolidatedView":
                return self._send(LAYOUT.format(title="Consolidated View Contribution History", action="/Home.aspx", code=EMPLOYER_CODE,
                                                body=grid("gvCons", HIST_HEAD, [HIST_P1[1], HIST_P2[1]])))
            if path == "/Home.aspx":
                return self._send(LAYOUT.format(title="Employer Home", action="/Home.aspx", code=EMPLOYER_CODE, body="<p>Welcome</p>"))
            if path == "/ViewContributionHistory.aspx":
                if a == "Page$2":
                    return self._send(LAYOUT.format(title="View Contribution History", action=path, code=EMPLOYER_CODE,
                                                    body=grid("gvHistory", HIST_HEAD, HIST_P2, '<a href="javascript:__doPostBack(\'ctl00$gvHistory\',\'Page$1\')">1</a> <span>2</span>')))
                return self._send(LAYOUT.format(title="View Contribution History", action=path, code=EMPLOYER_CODE,
                                                body=grid("gvHistory", HIST_HEAD, HIST_P1, '<span>1</span> <a href="javascript:__doPostBack(\'ctl00$gvHistory\',\'Page$2\')">2</a>')
                                                + '<input type="submit" name="btnGenerate" value="Generate Challan">'))
            if path == "/InterestForDelay.aspx":
                return self._send(LAYOUT.format(title="Interest For Delay Payment", action=path, code=EMPLOYER_CODE, body=INTEREST))
            if path == "/RecoveryDefaulter.aspx":
                return self._send(LAYOUT.format(title="Recovery/Defaulter Challan", action=path, code=EMPLOYER_CODE,
                                                body="<p>No Record Found</p>"))
            if path == "/ViewRC.aspx":
                return self._send(LAYOUT.format(title="View RC", action=path, code=EMPLOYER_CODE, body=RC))
            if path == "/DamagesOrders.aspx":
                return self._send(LAYOUT.format(title="Damages Orders", action=path, code=EMPLOYER_CODE, body=DAMAGES))
            if path in ("/FileMonthly.aspx", "/UpdateEmployer.aspx"):
                state.forbidden_hits.append(("GET", path))
                return self._send(LAYOUT.format(title="FORBIDDEN PAGE", action=path, code=EMPLOYER_CODE, body=""))
            self._send("<h1>404</h1>", 404)

        def do_GET(self):
            path = urlparse(self.path).path
            state.requests.append(("GET", path, ()))
            if path == "/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx":
                return self._send(LOGIN)
            if path == "/expire":
                state.expired = True
                return self._send("expired")
            if not self._authed():
                return self._redirect("/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx")
            self._page(path, {})

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            form = dict(parse_qsl(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True))
            names = tuple(sorted(form))
            state.requests.append(("POST", path, names))
            if path == "/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx":
                state.logins += 1
                state.expired = False
                return self._redirect("/Home.aspx", cookie="sess=1; Path=/")
            if any(n.lower().startswith(("btngenerate", "btnpay", "btnsubmit", "btnsave", "btnupdate", "btndelete")) for n in form):
                state.forbidden_hits.append(("POST", path, names))
                return self._send("<h1>STATE CHANGED (this must never happen)</h1>")
            if not self._authed():
                return self._redirect("/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx")
            self._page(path, form)

    return Handler


class FixturePortal:
    def __init__(self):
        self.state = ServerState()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    @property
    def login_url(self) -> str:
        return self.base + "/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
