"""Audit lifecycle: one user-initiated, read-only audit at a time.

READY -> WAITING_FOR_LOGIN -> AUTHENTICATED -> INSPECTING -> EXTRACTING -> RECONCILING
      -> GENERATING_REPORTS -> COMPLETED | PARTIAL          (FAILED / CANCELLED from any live state)

Playwright's sync API is bound to one thread, so the browser lives in a dedicated worker
thread and the API talks to it through a command queue. Raw page captures are written to
the database the moment they are taken (extraction checkpoints); an interrupted audit can
be finalised from those checkpoints without opening the portal again.
"""
from __future__ import annotations

import queue
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .browser import PORTAL_MAP, BrowserSession, SessionError, capture_page, next_page, open_target
from .config import Settings
from .db import TERMINAL_STATUSES
from .masking import configure_logging, mask_employer_code
from .parsing import find_employer_code, parse_table
from .reconcile import reconcile
from .report_model import build as build_model
from .report_pdf import write_pdf
from .report_xlsx import write_xlsx
from .repository import Repository
from .rules.engine import rules_version

log = configure_logging()
IST = timezone(timedelta(hours=5, minutes=30))
CATEGORIES = ("contribution", "interest", "damages", "recovery")
CAPTURE_CATEGORIES = CATEGORIES + ("employer", "other")
LOGIN_TIMEOUT_S = 30 * 60
ASSIST_TIMEOUT_S = 60 * 60
MAX_PAGES = 50


class AuditBusy(RuntimeError):
    pass


class Cancelled(Exception):
    pass


class AuditOrchestrator:
    def __init__(self, settings: Settings, repo: Repository, session_factory=None):
        self.settings, self.repo = settings, repo
        self._session_factory = session_factory or (lambda on_event: BrowserSession(
            settings.login_url, settings.allowed_host_suffixes, settings.browser_channel, settings.headless, on_event))
        self._lock = threading.Lock()
        self._active: dict | None = None  # {"run":..., "thread":..., "queue":..., "session":...}

    # ------------------------------------------------------------------ API side
    def active_uuid(self) -> str | None:
        a = self._active
        return a["run"]["audit_uuid"] if a and a["thread"].is_alive() else None

    def start(self) -> dict:
        with self._lock:
            if self.active_uuid():
                raise AuditBusy("an audit is already running")
            run = self.repo.create_run(self.settings.login_url, self.settings.synthetic, rules_version())
            q: queue.Queue = queue.Queue()
            t = threading.Thread(target=self._worker, args=(run, q), name=f"esic-audit-{run['audit_uuid'][:8]}", daemon=True)
            self._active = {"run": run, "thread": t, "queue": q, "session": None}
            t.start()
            return run

    def command(self, audit_uuid: str, name: str, **kw) -> None:
        a = self._active
        if not a or a["run"]["audit_uuid"] != audit_uuid or not a["thread"].is_alive():
            raise LookupError("this audit is not running")
        a["queue"].put((name, kw))

    def session(self):
        a = self._active
        return a["session"] if a else None

    def wait(self, timeout: float | None = None) -> None:
        a = self._active
        if a:
            a["thread"].join(timeout)

    # --------------------------------------------------------------- worker side
    def _event(self, audit_id: int, level: str, message: str) -> None:
        self.repo.add_event(audit_id, level, message)
        log.info("[%s] %s", level, message)

    def _next(self, q: queue.Queue, timeout: float, session=None):
        """Wait for a command. While waiting, keep Playwright's event loop running: the request
        guard is a Playwright route handler, so without this the employer's own clicks (login,
        manual navigation) would hang in the browser until the next Playwright call."""
        end = time.monotonic() + timeout
        while True:
            try:
                return q.get_nowait()
            except queue.Empty:
                pass
            if time.monotonic() >= end:
                return None, {}
            if session is not None:
                session.pump(200)
            else:
                time.sleep(0.2)

    def _worker(self, run: dict, q: queue.Queue) -> None:
        audit_id = run["id"]
        session = None
        try:
            session = self._session_factory(lambda level, msg: self._event(audit_id, level, msg))
            self._active["session"] = session
            self.repo.set_status(audit_id, "WAITING_FOR_LOGIN", "Opening the official ESIC employer portal")
            try:
                session.start()
            except Exception as exc:
                raise SessionError(f"ESIC portal could not be opened: {exc}") from exc
            self.repo.update_activity(audit_id, "Log in on the ESIC portal window yourself (username, password, CAPTCHA/OTP), "
                                                "then press 'I have logged in'. Nothing is typed or submitted for you.")
            self._wait_for_login(audit_id, session, q)
            page = session.page
            from .browser import page_text
            code = find_employer_code(page_text(page))
            masked = mask_employer_code(code)
            session.lock()
            self.repo.set_status(audit_id, "AUTHENTICATED", "Login confirmed; read-only guards active", employer_masked=masked or None)
            self.repo.add_stage(audit_id, "READ_ONLY_GUARD", "ACTIVE", "click guard, network guard and dialog guard enabled")

            self.repo.set_status(audit_id, "INSPECTING", "Inspecting portal pages")
            self._auto_inspect(audit_id, session, q)
            self._assisted(audit_id, session, q)
        except Cancelled:
            self._close(audit_id, session)
            self.repo.set_status(audit_id, "CANCELLED", "Cancelled by the user; captured pages are kept but not reconciled")
            return
        except Exception as exc:
            self._close(audit_id, session)
            self._event(audit_id, "ERROR", f"{exc.__class__.__name__}: {exc}")
            if not self.repo.captures(audit_id):
                self.repo.set_status(audit_id, "FAILED", "Audit failed", error=str(exc)[:500])
                return
            self.repo.add_stage(audit_id, "INSPECTION", "INTERRUPTED", str(exc)[:300])
        self._close(audit_id, session)
        if not self.repo.captures(audit_id):
            self.repo.set_status(audit_id, "FAILED", "No portal page could be captured", error="no pages captured")
            return
        interrupted = any(s["outcome"] in ("INTERRUPTED", "SESSION_EXPIRED") for s in self.repo.stages(audit_id))
        self.finalize(audit_id, interrupted=interrupted)

    def _close(self, audit_id: int, session) -> None:
        if session is not None and not getattr(session, "_closed_by_audit", False):
            session.close()
            session._closed_by_audit = True
            self.repo.add_stage(audit_id, "BROWSER_SESSION", "CLOSED", "authenticated browser session destroyed")

    def _check_cancel(self, q: queue.Queue) -> list:
        pending = []
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                return pending
            if item[0] == "cancel":
                raise Cancelled()
            pending.append(item)

    def _wait_for_login(self, audit_id: int, session, q: queue.Queue) -> None:
        deadline = datetime.now(timezone.utc) + timedelta(seconds=LOGIN_TIMEOUT_S)
        while datetime.now(timezone.utc) < deadline:
            name, _ = self._next(q, 1.0, session)
            if name == "cancel":
                raise Cancelled()
            if not session.alive():
                raise SessionError("the portal window was closed before login was confirmed")
            if name == "confirm_login":
                ok, reason = session.verify_authenticated()
                self.repo.add_stage(audit_id, "LOGIN_CHECK", "OK" if ok else "NOT_YET", reason)
                if ok:
                    return
                self.repo.update_activity(audit_id, f"Login not detected ({reason}). Finish logging in, then press 'I have logged in' again.")
        raise SessionError("login was not completed within 30 minutes")

    def _store_capture(self, audit_id: int, cap, stage_name: str) -> int:
        """Persist one page capture (raw tables + evidence). Returns number of tables kept."""
        cid = self.repo.add_capture(audit_id, cap.category, cap.page_name, cap.url,
                                    [dict(t, no_records_marker=cap.no_records_marker) for t in cap.tables]
                                    or [{"headers": [], "rows": [], "caption": "", "no_records_marker": cap.no_records_marker}])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        base = self.settings.evidence_dir / self.repo.run_by_id(audit_id)["audit_uuid"]
        safe = "".join(ch if ch.isalnum() else "_" for ch in cap.page_name)[:60]
        status = "CAPTURED" if cid else "DUPLICATE_CONTENT"
        self.repo.add_evidence(audit_id, "screenshot", cap.page_name, base / f"{stamp}_{safe}.png", cap.screenshot, status)
        self.repo.add_evidence(audit_id, "html_snapshot", cap.page_name, base / f"{stamp}_{safe}.html", cap.html, status)
        self.repo.add_stage(audit_id, stage_name, status, f"{cap.page_name}: {len(cap.tables)} table(s)"
                            + ("; page says no records" if cap.no_records_marker else ""))
        return len(cap.tables)

    def _auto_inspect(self, audit_id: int, session, q: queue.Queue) -> bool:
        any_capture = False
        home = session.page.url
        for target in PORTAL_MAP["targets"]:
            self._check_cancel(q)
            stage = f"INSPECT:{target['id']}"
            self.repo.update_activity(audit_id, f"Opening '{target['page_name']}'")
            try:
                outcome, detail = open_target(session.page, target)
            except Exception as exc:
                outcome, detail = "ERROR", f"{exc.__class__.__name__}: {str(exc)[:200]}"
            if session.looks_logged_out():
                self.repo.add_stage(audit_id, stage, "SESSION_EXPIRED", "portal shows a login form; automatic inspection stopped")
                return any_capture
            if outcome != "OPENED":
                self.repo.add_stage(audit_id, stage, outcome, detail)
                continue
            self.repo.update_activity(audit_id, f"Reading '{target['page_name']}'")
            cap = capture_page(session.page, target["category"], target["page_name"])
            self._store_capture(audit_id, cap, stage)
            any_capture = True
            for n in range(2, MAX_PAGES + 1):
                self._check_cancel(q)
                if not next_page(session.page):
                    break
                self._store_capture(audit_id, capture_page(session.page, target["category"], f"{target['page_name']} (page {n})"), stage)
            try:
                session.page.goto(home, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                pass
        return any_capture

    def _assisted(self, audit_id: int, session, q: queue.Queue) -> bool:
        self.repo.update_activity(audit_id, "Automatic inspection finished. To add records the module could not open (for example "
                                            "a page that needs a month/year selection), open that page in the portal window, choose "
                                            "its category and press 'Capture current page'. Press 'Finish inspection' when done.")
        self.repo.add_stage(audit_id, "ASSISTED_CAPTURE", "WAITING", "waiting for user captures or finish")
        deadline = datetime.now(timezone.utc) + timedelta(seconds=ASSIST_TIMEOUT_S)
        any_capture = False
        while datetime.now(timezone.utc) < deadline:
            name, kw = self._next(q, 1.0, session)
            if name == "cancel":
                raise Cancelled()
            if name == "finish":
                self.repo.add_stage(audit_id, "ASSISTED_CAPTURE", "FINISHED", "user finished inspection")
                return any_capture
            if not session.alive():
                self.repo.add_stage(audit_id, "ASSISTED_CAPTURE", "INTERRUPTED", "portal window closed")
                return any_capture
            if name == "capture":
                category = kw.get("category")
                if category not in CAPTURE_CATEGORIES:
                    self._event(audit_id, "WARN", f"capture refused: unknown category {category!r}")
                    continue
                try:
                    session.before_user_capture(category)
                    if session.looks_logged_out():
                        self.repo.add_stage(audit_id, "ASSISTED_CAPTURE", "SESSION_EXPIRED", "portal shows a login form")
                        continue
                    page_name = (kw.get("page_name") or session.page.title() or "Manual capture")[:120]
                    self._store_capture(audit_id, capture_page(session.page, category, page_name), f"CAPTURE:{category}")
                    any_capture = True
                except SessionError:
                    raise
                except Exception as exc:  # one failed capture must not end the audit
                    self.repo.add_stage(audit_id, f"CAPTURE:{category}", "ERROR", f"{exc.__class__.__name__}: {str(exc)[:200]}")
                    self.repo.update_activity(audit_id, "That capture failed (the page may still be loading). Try 'Capture current page' "
                                                        "again, or press 'Finish inspection'.")
        self.repo.add_stage(audit_id, "ASSISTED_CAPTURE", "TIMEOUT", "no finish within 60 minutes; continuing with captured pages")
        return any_capture

    # ------------------------------------------------------------- finalisation
    def finalize(self, audit_id: int, interrupted: bool = False, as_of=None) -> str:
        """EXTRACTING -> RECONCILING -> GENERATING_REPORTS -> COMPLETED/PARTIAL, from saved captures only."""
        try:
            self.repo.set_status(audit_id, "EXTRACTING", "Parsing captured pages")
            coverage = {c: "NOT_AVAILABLE" for c in CATEGORIES}
            total_records = 0
            for cap in self.repo.captures(audit_id):
                if cap["category"] not in CATEGORIES:
                    continue
                parsed_records = []
                mapped_any = False
                for i, table in enumerate(cap["tables"]):
                    if not table.get("headers"):
                        continue
                    tp = parse_table(cap["category"], table, f"{cap['page_name']} / table {table.get('index', i)}")
                    for w in tp.warnings:
                        self._event(audit_id, "WARN", w)
                    if tp.mapped:
                        mapped_any = True
                        parsed_records.extend(tp.records)
                no_records = any(t.get("no_records_marker") for t in cap["tables"])
                inserted, dupes = self.repo.store_records(audit_id, parsed_records)
                total_records += inserted
                if dupes:
                    self._event(audit_id, "INFO", f"{cap['page_name']}: {dupes} duplicate record(s) counted once")
                if parsed_records or inserted:
                    coverage[cap["category"]] = "CAPTURED"
                elif (mapped_any or no_records) and coverage[cap["category"]] == "NOT_AVAILABLE":
                    coverage[cap["category"]] = "EMPTY"
                self.repo.add_stage(audit_id, f"EXTRACT:{cap['category']}", "OK" if parsed_records else ("EMPTY" if no_records or mapped_any else "NO_DATA_TABLE"),
                                    f"{cap['page_name']}: {inserted} record(s) stored")
            self.repo.set_status(audit_id, "RECONCILING", "Reconciling contributions, interest, damages and recovery",
                                 records_inspected=total_records)
            records = self.repo.load_records(audit_id)
            as_of = as_of or datetime.now(IST).date()
            result = reconcile(records, coverage, as_of)
            self.repo.store_result(audit_id, result)
            missing = [c for c, v in coverage.items() if v == "NOT_AVAILABLE"]
            final = "PARTIAL" if (missing or interrupted) else "COMPLETED"
            note = ("Not available: " + ", ".join(missing) + ". " if missing else "") + ("Inspection was interrupted. " if interrupted else "")
            self.repo.set_status(audit_id, "GENERATING_REPORTS", "Generating PDF and Excel reports")
            run = dict(self.repo.run_by_id(audit_id), status_for_report=final)
            model = build_model(run, result, self.repo.stages(audit_id), self.repo.evidence(audit_id), datetime.now(IST).replace(tzinfo=None))
            folder = self.settings.reports_dir / run["audit_uuid"]
            pdf = write_pdf(model, folder / f"{model['file_stem']}.pdf")
            xlsx = write_xlsx(model, folder / f"{model['file_stem']}.xlsx")
            self.repo.add_report(audit_id, "pdf", pdf)
            self.repo.add_report(audit_id, "xlsx", xlsx)
            self.repo.set_status(audit_id, final, ("Audit complete. " if final == "COMPLETED" else "Audit finished with gaps. ") + note,
                                 error=note or None)
            return final
        except Exception as exc:
            self._event(audit_id, "ERROR", f"finalisation failed: {exc.__class__.__name__}: {exc}")
            log.debug(traceback.format_exc())
            run = self.repo.run_by_id(audit_id)
            if run["status"] not in TERMINAL_STATUSES:
                self.repo.set_status(audit_id, "FAILED", "Report or reconciliation step failed", error=str(exc)[:500])
            return "FAILED"

    def recover_interrupted(self) -> list[str]:
        """On startup: audits left in a live state by a crash are finalised from checkpoints (portal not reopened)."""
        done = []
        for run in self.repo.list_runs():
            if run["status"] in TERMINAL_STATUSES or run["audit_uuid"] == self.active_uuid():
                continue
            if self.repo.captures(run["id"]):
                self.repo.add_stage(run["id"], "INSPECTION", "INTERRUPTED", "process stopped; finalising from saved checkpoints")
                self.finalize(run["id"], interrupted=True)
            else:
                self.repo.set_status(run["id"], "FAILED", "Interrupted before any page was captured", error="process stopped")
            done.append(run["audit_uuid"])
        return done


def report_path(settings: Settings, row: dict) -> Path:
    p = Path(row["file_path"]).resolve()
    if settings.reports_dir.resolve() not in p.parents:
        raise PermissionError("report path outside the reports directory")
    return p
