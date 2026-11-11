"""Ephemeral, user-authenticated Playwright session and read-only page inspector.

* The browser profile is a fresh in-memory context: no storage state is ever saved, so the
  authenticated session dies with `close()`.
* The module never types into the login form, never reads it, and never submits it.
* After the employer confirms login, `lock()` activates the read-only guards (safety.py).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .safety import VIEW_ONLY, classify_request, guard_script, is_click_allowed

PORTAL_MAP = json.loads((Path(__file__).with_name("portal_map.json")).read_text(encoding="utf-8"))

_TABLES_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('table').forEach((t, idx) => {
    if (t.querySelector('table')) return;               // layout wrapper
    const rows = Array.from(t.rows).filter(r => r.offsetParent !== null || r.getClientRects().length);
    if (rows.length < 1) return;
    let headerRow = t.tHead && t.tHead.rows.length ? t.tHead.rows[t.tHead.rows.length - 1] : null;
    if (!headerRow) headerRow = rows.find(r => r.querySelector('th')) || null;
    let body = rows.filter(r => r !== headerRow && !(t.tHead && t.tHead.contains(r)));
    if (!headerRow && body.length > 1) { headerRow = body[0]; body = body.slice(1); }
    if (!headerRow) return;
    const headers = Array.from(headerRow.cells).map(c => clean(c.innerText));
    if (headers.filter(Boolean).length < 2) return;
    const data = body.map(r => Array.from(r.cells).map(c => clean(c.innerText)));
    let caption = clean(t.caption && t.caption.innerText) || t.id || '';
    out.push({index: idx, caption, headers, rows: data});
  });
  return out;
}
"""


class SessionError(RuntimeError):
    pass


@dataclass
class Capture:
    category: str
    page_name: str
    url: str
    tables: list[dict]
    text: str
    screenshot: bytes
    html: bytes
    no_records_marker: bool


@dataclass
class BrowserSession:
    login_url: str
    allowed_suffixes: tuple[str, ...]
    channel: str | None
    headless: bool
    on_event: Callable[[str, str], None]
    locked: bool = False
    blocked: list[str] = field(default_factory=list)

    def start(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(channel=self.channel, headless=self.headless)
        except Exception as exc:
            if self.channel is None:
                raise SessionError(f"browser could not start: {exc}") from exc
            self.on_event("WARN", f"{self.channel} not available ({exc.__class__.__name__}); trying bundled Chromium")
            try:
                self._browser = self._pw.chromium.launch(headless=self.headless)
            except Exception as exc2:
                raise SessionError(f"browser could not start: {exc2}") from exc2
        self.context = self._browser.new_context(accept_downloads=False, service_workers="block")
        self.context.expose_binding("__esicGuardReport", lambda source, what: self._blocked(str(what)))
        self.context.route("**/*", self._route)
        self.context.on("page", lambda p: p.on("dialog", self._dialog))
        # Diagnostics: page-level navigations only (method, status, path) - never bodies or form values.
        self.context.on("response", lambda r: r.request.resource_type == "document" and self.on_event(
            "NAV", f"{r.request.method} {r.status} {urlparse(r.url).netloc}{urlparse(r.url).path}"))
        self.context.on("requestfailed", lambda r: r.resource_type == "document" and self.on_event(
            "WARN", f"navigation failed {urlparse(r.url).path}: {r.failure}"))
        page = self.context.new_page()
        page.on("dialog", self._dialog)
        page.goto(self.login_url, wait_until="domcontentloaded", timeout=60_000)
        return page

    # -- guards -------------------------------------------------------------
    def _blocked(self, what: str) -> None:
        self.blocked.append(what)
        self.on_event("BLOCKED", what)

    def _dialog(self, dialog) -> None:
        # Never accept a portal dialog automatically (it may confirm a transaction).
        self.on_event("INFO", f"portal dialog dismissed ({dialog.type})")
        try:
            dialog.dismiss()
        except Exception:
            pass

    def _route(self, route, request) -> None:
        post = ctype = None
        if self.locked and request.method.upper() not in ("GET", "HEAD", "OPTIONS"):
            try:
                post = request.post_data
            except Exception:
                post = "<binary>"
            ctype = request.headers.get("content-type")
        allow, reason = classify_request(request.method, request.url, request.resource_type, post, ctype,
                                         self.allowed_suffixes, self.locked)
        if allow:
            route.continue_()
        else:
            self._blocked(f"request blocked - {reason}")
            route.abort("blockedbyclient")

    def lock(self) -> None:
        self.locked = True
        script = guard_script()
        self.context.add_init_script(script)
        for page in self.context.pages:
            for frame in page.frames:
                try:
                    frame.evaluate(script)
                except Exception:
                    pass

    # -- state ----------------------------------------------------------------
    @property
    def page(self):
        pages = [p for p in self.context.pages if not p.is_closed()] if getattr(self, "context", None) else []
        if not pages:
            raise SessionError("the portal window was closed")
        return pages[-1]

    def alive(self) -> bool:
        try:
            return bool(self.page) and self._browser.is_connected()
        except SessionError:
            return False

    def verify_authenticated(self) -> tuple[bool, str]:
        page = self.page
        url = page.url
        if any(m.lower() in url.lower() for m in PORTAL_MAP["login_page_markers"] if m.endswith(".aspx")):
            return False, "still on the login page"
        if page.locator("input[type=password]:visible").count():
            return False, "a password field is still visible"
        text = page_text(page)
        hit = next((m for m in PORTAL_MAP["authenticated_markers"] if m.lower() in text.lower()), None)
        if not hit:
            return False, "no employer menu or logout link visible yet"
        return True, f"authenticated marker found: {hit}"

    def pump(self, ms: int) -> None:
        """Let Playwright dispatch pending events (route guard, dialogs) for `ms` milliseconds."""
        try:
            self.page.wait_for_timeout(ms)
        except Exception:
            time.sleep(ms / 1000)

    def before_user_capture(self, category: str) -> None:
        """Extension point called (in the browser thread) just before a user-requested capture.
        No-op in production; the end-to-end tests override it to act as the human operator."""

    def looks_logged_out(self) -> bool:
        try:
            return self.page.locator("input[type=password]:visible").count() > 0
        except Exception:
            return True

    def close(self) -> None:
        for name in ("context", "_browser"):
            obj = getattr(self, name, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, name, None)
        pw = getattr(self, "_pw", None)
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
            self._pw = None


def page_text(page) -> str:
    parts = []
    for frame in page.frames:
        try:
            parts.append(frame.evaluate("() => document.body ? document.body.innerText : ''"))
        except Exception:
            continue
    return "\n".join(parts)


def extract_tables(page) -> list[dict]:
    tables = []
    for n, frame in enumerate(page.frames):
        try:
            for t in frame.evaluate(_TABLES_JS):
                t["frame"] = n
                tables.append(t)
        except Exception:
            continue
    return tables


_HTML_SCRUB = [
    (re.compile(r'(<input[^>]*type="?password"?[^>]*value=")[^"]*', re.I), r"\1"),
    (re.compile(r'(id="__VIEWSTATE"[^>]*value=")[^"]*', re.I), r"\1[omitted]"),
    (re.compile(r'(name="__VIEWSTATE"[^>]*value=")[^"]*', re.I), r"\1[omitted]"),
]


def capture_page(page, category: str, page_name: str) -> Capture:
    html = page.content()
    for pattern, repl in _HTML_SCRUB:
        html = pattern.sub(repl, html)
    text = page_text(page)
    shot = page.screenshot(full_page=True)
    no_records = any(m.lower() in text.lower() for m in PORTAL_MAP["no_records_markers"])
    return Capture(category, page_name, page.url, extract_tables(page), text, shot, html.encode("utf-8"), no_records)


def _find_link(page, label: str):
    for frame in page.frames:
        loc = frame.get_by_role("link", name=label, exact=True)
        try:
            if loc.count():
                return loc.first
        except Exception:
            continue
        loc = frame.get_by_text(label, exact=True)
        try:
            if loc.count():
                return loc.first
        except Exception:
            continue
    return None


def open_target(page, target: dict, settle_ms: int = 1500) -> tuple[str, str]:
    """Navigate through the menu labels of one target. Returns (outcome, detail).
    GET links are followed with page.goto (no click); postback links are clicked only
    after passing the click allow-list."""
    for label in target["menu"]:
        if not is_click_allowed(label) or label in PORTAL_MAP["never_open"]:
            return "REFUSED", f"menu label '{label}' is not on the read-only allow-list"
    final = target["menu"][-1]
    link = _find_link(page, final)
    if link is None:  # reveal it through the parent menu(s): hover first, click only plain menu headers
        for parent in target["menu"][:-1]:
            plink = _find_link(page, parent)
            if plink is None:
                continue
            try:
                plink.hover(timeout=3_000)
            except Exception:
                pass
            if _find_link(page, final) is None and not (plink.get_attribute("href") or "").strip():
                plink.click(timeout=5_000)
                page.wait_for_timeout(500)
        link = _find_link(page, final)
    if link is None:
        return "NOT_FOUND", f"menu entry '{final}' not found on the page"
    href = (link.get_attribute("href") or "").strip()
    if href and not href.lower().startswith(("javascript", "#")):
        page.goto(link.evaluate("a => a.href"), wait_until="domcontentloaded", timeout=60_000)
    else:
        link.click(timeout=10_000)  # postback link: request still passes the network guard
    page.wait_for_timeout(settle_ms)
    return "OPENED", page.url


def next_page(page) -> bool:
    """Click a pager link ('Next', '>', or the next page number) inside a results grid."""
    for frame in page.frames:
        try:
            links = frame.locator("table a").all()
        except Exception:
            continue
        current = None
        for span in frame.locator("table tr td > span").all():
            t = (span.inner_text() or "").strip()
            if t.isdigit():
                current = int(t)
                break
        for link in links:
            label = (link.inner_text() or "").strip()
            if not VIEW_ONLY.match(label) or not is_click_allowed(label):
                continue
            if (current is not None and label.isdigit() and int(label) == current + 1) or label.lower() in ("next", ">"):
                link.click(timeout=10_000)
                page.wait_for_timeout(1200)
                return True
    return False


def wait_until(predicate: Callable[[], bool], timeout_s: float, step_s: float = 0.5) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step_s)
    return False
