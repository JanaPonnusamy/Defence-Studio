"""FastAPI app: local, token-protected API + static UI.

Every /api route requires the local access token (header `X-ESIC-Audit-Token` or
`Authorization: Bearer`). The server is meant to bind to 127.0.0.1 only (scripts/run.ps1).
There is deliberately no endpoint that accepts credentials, OTPs, payment or portal-write
instructions.
"""
from __future__ import annotations

import hmac
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import MODULE_ROOT, Settings, load_settings
from .orchestrator import CAPTURE_CATEGORIES, AuditBusy, AuditOrchestrator, report_path
from .repository import Repository, dumps
from .rules.engine import rules_reference, rules_version


class CaptureIn(BaseModel):
    category: str = Field(pattern="^(" + "|".join(CAPTURE_CATEGORIES) + ")$")
    page_name: str | None = Field(default=None, max_length=120)


def _j(value) -> JSONResponse:
    return JSONResponse(json.loads(dumps(value)))


def _num(v):
    try:
        return Decimal(v) if v not in (None, "") else None
    except Exception:
        return None


def compare(old: dict, new: dict) -> dict:
    """Differences between two stored audit results (both read from immutable snapshots)."""
    def index(items, key):
        return {key(i): i for i in items}
    dk = lambda i: (i.get("demand_reference") or "", i.get("period") or "")
    out = {"new_demands": [], "changed_outstanding": [], "payments_recorded": [], "new_recovery_records": [],
           "new_contribution_periods": [], "corrected_or_missing": []}
    for cat in ("interest", "damages"):
        a, b = index(old.get(cat, []), dk), index(new.get(cat, []), dk)
        for k, item in b.items():
            if k not in a:
                out["new_demands"].append({"type": cat, "reference": k[0], "period": k[1], "outstanding": item.get("outstanding")})
            elif _num(a[k].get("outstanding")) != _num(item.get("outstanding")):
                out["changed_outstanding"].append({"type": cat, "reference": k[0], "period": k[1],
                                                   "before": a[k].get("outstanding"), "after": item.get("outstanding")})
            if k in a and (_num(item.get("paid")) or 0) > (_num(a[k].get("paid")) or 0):
                out["payments_recorded"].append({"type": cat, "reference": k[0], "before": a[k].get("paid"), "after": item.get("paid")})
        for k in a:
            if k not in b:
                out["corrected_or_missing"].append({"type": cat, "reference": k[0], "period": k[1], "note": "present before, not in the newer audit"})
    ra = index(old.get("recovery", []), lambda r: r.get("recovery_reference") or "")
    for k, rc in index(new.get("recovery", []), lambda r: r.get("recovery_reference") or "").items():
        if k not in ra:
            out["new_recovery_records"].append({"reference": k, "outstanding": rc.get("outstanding")})
        elif _num(ra[k].get("outstanding")) != _num(rc.get("outstanding")):
            out["changed_outstanding"].append({"type": "recovery", "reference": k, "before": ra[k].get("outstanding"), "after": rc.get("outstanding")})
    pa = {p["wage_month"]: p for p in old.get("periods", [])}
    for p in new.get("periods", []):
        if p["wage_month"] not in pa:
            out["new_contribution_periods"].append({"period": p["wage_month"], "status": p["status"]})
        elif pa[p["wage_month"]]["status"] != p["status"]:
            out["payments_recorded" if p["status"].startswith("PAID") else "corrected_or_missing"].append(
                {"type": "contribution", "period": p["wage_month"], "before": pa[p["wage_month"]]["status"], "after": p["status"]})
    keys = ("total_confirmed_outstanding", "confirmed_damages_outstanding", "confirmed_interest_outstanding",
            "confirmed_recovery_outstanding", "estimated_potential_damages_max")
    out["totals"] = {k: {"before": old["totals"].get(k), "after": new["totals"].get(k)} for k in keys}
    return out


def create_app(settings: Settings | None = None, orchestrator: AuditOrchestrator | None = None) -> FastAPI:
    settings = settings or load_settings()
    repo = orchestrator.repo if orchestrator else Repository(settings.db_path)
    orch = orchestrator or AuditOrchestrator(settings, repo)
    app = FastAPI(title="ESIC Compliance Audit", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.repo, app.state.orch = settings, repo, orch

    def auth(x_esic_audit_token: str | None = Header(default=None), authorization: str | None = Header(default=None)):
        supplied = x_esic_audit_token or (authorization[7:] if authorization and authorization.lower().startswith("bearer ") else "")
        if not supplied or not hmac.compare_digest(supplied.encode(), settings.access_token.encode()):
            raise HTTPException(401, "access token required")

    def run_or_404(audit_uuid: str) -> dict:
        run = repo.get_run(audit_uuid)
        if not run:
            raise HTTPException(404, "audit not found")
        return run

    def summary(run: dict) -> dict:
        result = repo.load_result(run["id"])
        return {**{k: run[k] for k in ("audit_uuid", "started_at", "completed_at", "status", "current_activity",
                                         "employer_identifier_masked", "records_inspected", "error_summary", "synthetic")},
                "totals": result["totals"] if result else None, "counts": result["counts"] if result else None,
                "answers": result["answers"] if result else None,
                "reports": [r["report_type"] for r in repo.reports(run["id"])],
                "running": orch.active_uuid() == run["audit_uuid"]}

    @app.get("/api/health")
    def health():
        return {"ok": True, "synthetic_portal": settings.synthetic, "rules_version": rules_version()}

    @app.get("/api/audits", dependencies=[Depends(auth)])
    def list_audits():
        return _j([summary(r) for r in repo.list_runs()])

    @app.post("/api/audits", dependencies=[Depends(auth)], status_code=201)
    def start_audit():
        try:
            run = orch.start()
        except AuditBusy as exc:
            raise HTTPException(409, str(exc))
        return _j(summary(repo.get_run(run["audit_uuid"])))

    @app.get("/api/audits/{audit_uuid}", dependencies=[Depends(auth)])
    def audit_detail(audit_uuid: str):
        run = run_or_404(audit_uuid)
        result = repo.load_result(run["id"])
        return _j({**summary(run), "portal_url": run["portal_url"], "rules_version": run["rules_version"],
                   "extraction_version": run["extraction_version"], "calculation_version": run["calculation_version"],
                   "stages": repo.stages(run["id"]), "result": result,
                   "reports": [{k: r[k] for k in ("report_type", "generated_at", "checksum")} for r in repo.reports(run["id"])],
                   "evidence_count": len(repo.evidence(run["id"]))})

    def _cmd(audit_uuid: str, name: str, **kw):
        run_or_404(audit_uuid)
        try:
            orch.command(audit_uuid, name, **kw)
        except LookupError as exc:
            raise HTTPException(409, str(exc))
        return {"accepted": name}

    @app.post("/api/audits/{audit_uuid}/confirm-login", dependencies=[Depends(auth)])
    def confirm_login(audit_uuid: str):
        return _cmd(audit_uuid, "confirm_login")

    @app.post("/api/audits/{audit_uuid}/capture", dependencies=[Depends(auth)])
    def capture(audit_uuid: str, body: CaptureIn):
        return _cmd(audit_uuid, "capture", category=body.category, page_name=body.page_name)

    @app.post("/api/audits/{audit_uuid}/finish", dependencies=[Depends(auth)])
    def finish(audit_uuid: str):
        return _cmd(audit_uuid, "finish")

    @app.post("/api/audits/{audit_uuid}/cancel", dependencies=[Depends(auth)])
    def cancel(audit_uuid: str):
        return _cmd(audit_uuid, "cancel")

    @app.get("/api/audits/{audit_uuid}/logs", dependencies=[Depends(auth)])
    def logs(audit_uuid: str):
        run = run_or_404(audit_uuid)
        return _j({"stages": repo.stages(run["id"]), "events": repo.events(run["id"])})

    @app.get("/api/audits/{audit_uuid}/evidence", dependencies=[Depends(auth)])
    def evidence(audit_uuid: str):
        run = run_or_404(audit_uuid)
        return _j([{"id": e["id"], "evidence_type": e["evidence_type"], "page_name": e["page_name"], "captured_at": e["captured_at"],
                    "content_hash": e["content_hash"], "extraction_status": e["extraction_status"]} for e in repo.evidence(run["id"])])

    @app.get("/api/audits/{audit_uuid}/evidence/{evidence_id}", dependencies=[Depends(auth)])
    def evidence_file(audit_uuid: str, evidence_id: int):
        run = run_or_404(audit_uuid)
        row = next((e for e in repo.evidence(run["id"]) if e["id"] == evidence_id), None)
        if not row:
            raise HTTPException(404, "evidence not found")
        path = Path(row["artifact_path"]).resolve()
        if settings.evidence_dir.resolve() not in path.parents or not path.exists():
            raise HTTPException(404, "evidence file unavailable")
        media = "image/png" if path.suffix == ".png" else "text/plain; charset=utf-8"  # HTML served as text, never rendered
        return FileResponse(path, media_type=media, filename=path.name)

    @app.get("/api/audits/{audit_uuid}/reports/{report_type}", dependencies=[Depends(auth)])
    def report(audit_uuid: str, report_type: str):
        run = run_or_404(audit_uuid)
        row = next((r for r in repo.reports(run["id"]) if r["report_type"] == report_type), None)
        if not row:
            raise HTTPException(404, "report not available")
        path = report_path(settings, row)
        media = "application/pdf" if report_type == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return FileResponse(path, media_type=media, filename=path.name)

    @app.get("/api/compare", dependencies=[Depends(auth)])
    def compare_audits(older: str, newer: str):
        a, b = run_or_404(older), run_or_404(newer)
        ra, rb = repo.load_result(a["id"]), repo.load_result(b["id"])
        if not ra or not rb:
            raise HTTPException(409, "both audits need a completed reconciliation")
        return _j({"older": older, "newer": newer, **compare(ra, rb)})

    @app.get("/api/rules", dependencies=[Depends(auth)])
    def rules():
        return _j(rules_reference())

    ui = MODULE_ROOT / "ui"
    app.mount("/", StaticFiles(directory=ui, html=True), name="ui")
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    settings = load_settings()
    repo = Repository(settings.db_path)
    orch = AuditOrchestrator(settings, repo)
    orch.recover_interrupted()
    return create_app(settings, orch)
