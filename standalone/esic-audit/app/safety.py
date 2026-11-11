"""Read-only safety boundary for the browser session.

Three independent layers, all active from the moment the employer confirms login:

1. Inspector click allow-list - the module itself only clicks menu links named in
   portal_map.json and pager links; every candidate passes `is_click_allowed` first.
2. In-page guard (GUARD_JS) - a capture-phase listener in every frame that cancels any
   click/submit (including the user's own clicks) on a control whose text, value, id,
   name or title matches the state-changing vocabulary, and auto-dismisses confirm()
   dialogs. Blocked attempts are reported back to the audit log.
3. Network guard (`classify_request`) - aborts POSTs whose form fields name a
   state-changing control (ASP.NET puts the clicked button's name and __EVENTTARGET in
   the body), any write request to a non-ESIC host, and any navigation to a payment
   gateway.

Field VALUES are never logged or stored: only field NAMES are inspected, and the login
form (username/password/CAPTCHA) is never read - during the login phase the guard only
blocks payment gateways.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

FORBIDDEN_LABELS = ("Generate Challan", "Submit", "Pay", "Confirm Payment", "Modify", "Update", "Delete", "Save Changes")

# Matches control names/captions that could change portal state.
DENY = re.compile(
    r"(?i)(generat|submit|\bpay\b|pay\s*now|paynow|btnpay|make\s*payment|online\s*payment|proceed\s*to\s*pay|"
    r"confirm|modif|update|delet|remove|save|\bedit|\badd\b|btnadd|insert|declar|\bfile\b|filing|btnfile|apply|"
    r"register|approv|upload|reset|change\s*password|changepassword|cancel\s*challan|initiat|proceed|sign\s*up|"
    r"e-?sign|\botp\b)")
# Captions of controls that only display data.
VIEW_ONLY = re.compile(r"(?i)^\s*(view|show|search|go|get|find|display|fetch|next|prev(ious)?|first|last|page|\d+|>|>>|<|<<|\.\.\.)"
                       r"(\s+(details?|history|records?|report|data|list|contribution(s)?))?\s*$")

PAYMENT_HOSTS = re.compile(r"(?i)(sbi|onlinesbi|billdesk|razorpay|paytm|payu|ccavenue|atom|techprocess|"
                           r"icicibank|hdfcbank|axisbank|pnb|bankofbaroda|canarabank|unionbank|indianbank|"
                           r"npci|upi|bhim|paygov|pfms|gateway)")
_CONTROL_FIELD = re.compile(r"(?i)(^|\$|_)(btn|cmd|button|imgbtn|lnk|link|ibtn)")
_FORM_CONTROL_NAMES = ("__EVENTTARGET", "__EVENTARGUMENT")


def is_click_allowed(label: str) -> bool:
    """The module's own clicks: never a denied caption."""
    return bool(label and label.strip()) and not DENY.search(label)


def host_allowed(host: str, allowed_suffixes: tuple[str, ...]) -> bool:
    host = (host or "").lower().split(":")[0]
    return any(host == s or host.endswith("." + s) for s in allowed_suffixes)


def classify_request(method: str, url: str, resource_type: str, post_data: str | None, content_type: str | None,
                     allowed_suffixes: tuple[str, ...], locked: bool) -> tuple[bool, str]:
    """Return (allow, reason). `locked` is True once the read-only inspection starts."""
    host = urlparse(url).hostname or ""
    if PAYMENT_HOSTS.search(host) and not host_allowed(host, allowed_suffixes):
        return False, f"payment/bank host blocked: {host}"
    if not locked:
        return True, "login phase"
    method = method.upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        if resource_type == "document" and not host_allowed(host, allowed_suffixes):
            return False, f"navigation off the official portal blocked: {host}"
        return True, "read"
    if not host_allowed(host, allowed_suffixes):
        return False, f"{method} to non-portal host blocked: {host}"
    if method != "POST":
        return False, f"{method} blocked during read-only inspection"
    fields: list[tuple[str, str]] = []
    if post_data and "application/x-www-form-urlencoded" in (content_type or "application/x-www-form-urlencoded"):
        try:
            fields = parse_qsl(post_data, keep_blank_values=True)
        except ValueError:
            fields = []
    elif post_data:
        # JSON / multipart / unknown body: only allow when it carries no state-changing vocabulary.
        if DENY.search(post_data[:8000]):
            return False, "non-form POST with state-changing vocabulary blocked"
        return True, "non-form POST (no state-changing vocabulary)"
    names = {k for k, _ in fields}
    target = next((v for k, v in fields if k == "__EVENTTARGET"), "")
    argument = next((v for k, v in fields if k == "__EVENTARGUMENT"), "")
    if target and DENY.search(target):
        return False, f"postback target blocked: {target}"
    if argument and DENY.search(argument) and not argument.startswith("Page$"):
        return False, f"postback argument blocked: {argument[:40]}"
    for name, value in fields:
        if name in _FORM_CONTROL_NAMES or not _CONTROL_FIELD.search(name.split("$")[-1]):
            continue
        # a button field is present only when that button was clicked
        if DENY.search(name) or (value and DENY.search(value)):
            return False, f"state-changing control blocked: {name}"
    if any(DENY.search(n) for n in names if n not in _FORM_CONTROL_NAMES and _CONTROL_FIELD.search(n.split("$")[-1])):
        return False, "state-changing control blocked"
    return True, "view postback"


GUARD_JS = r"""
(() => {
  if (window.__esicReadOnlyGuard) return;
  window.__esicReadOnlyGuard = true;
  const DENY = new RegExp(%DENY%, 'i');
  const describe = (el) => [el.innerText, el.value, el.id, el.name, el.title, el.getAttribute && el.getAttribute('aria-label'),
                            el.getAttribute && el.getAttribute('href'), el.getAttribute && el.getAttribute('onclick')]
                           .filter(Boolean).map(String).join(' ').slice(0, 300);
  const report = (what) => { try { window.__esicGuardReport && window.__esicGuardReport(what); } catch (e) {} };
  const controlOf = (node) => node && node.closest && node.closest('button, input[type=submit], input[type=button], input[type=image], a, [role=button], [onclick]');
  document.addEventListener('click', (ev) => {
    const el = controlOf(ev.target);
    if (!el) return;
    const text = describe(el);
    if (DENY.test(text)) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      report('blocked click: ' + text.replace(/\s+/g, ' ').slice(0, 120));
    }
  }, true);
  document.addEventListener('submit', (ev) => {
    const btn = ev.submitter;
    const text = btn ? describe(btn) : '';
    if (!btn || DENY.test(text)) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      report('blocked form submit: ' + (text || 'no submitter').replace(/\s+/g, ' ').slice(0, 120));
    }
  }, true);
  window.confirm = () => { report('confirm() dialog auto-cancelled'); return false; };
  window.open = ((orig) => function (url, ...rest) {
    if (url && DENY.test(String(url))) { report('window.open blocked'); return null; }
    return orig.call(window, url, ...rest);
  })(window.open);
})();
"""


def guard_script() -> str:
    import json
    return GUARD_JS.replace("%DENY%", json.dumps(DENY.pattern.replace("(?i)", "")))
