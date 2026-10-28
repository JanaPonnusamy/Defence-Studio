"""Cross-cutting read models: pipeline overview, usage/cost report and
per-owner provider credentials (RM-9).

* ``pipeline_overview`` is derived from persisted artifacts only, so it is
  identical after a refresh, restart or on another device.
* ``usage_report`` keeps every cost category separate and never sums an
  unmeasured figure as zero; the INR 10 text-cost target is reported as a
  measurement against a budget objective, not as a promise.
* Credentials are stored only as Fernet ciphertext and are never returned;
  status exposes ``configured`` and the last four characters.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone

from defence_studio.database import connect

TEXT_COST_TARGET_INR = 10.0
CREDENTIAL_PROVIDERS = ("openai",)


class ReportsMixin:
    # -- pipeline ---------------------------------------------------------------

    def pipeline_overview(self, job_id: str, owner_id: str, language: str) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as c:
            def one(sql, *p):
                r = c.execute(sql, p).fetchone()
                return dict(r) if r else None
            workflow = one("SELECT status FROM workflow_jobs WHERE job_id=?", job_id) or {"status": "NONE"}
            workflow_stages = [dict(r) for r in c.execute(
                "SELECT stage_key,status FROM workflow_stages WHERE job_id=? ORDER BY ordinal", (job_id,))]
            workflow["stages_completed"] = sum(s["status"] in ("COMPLETED", "SKIPPED") for s in workflow_stages)
            workflow["stages_total"] = len(workflow_stages)
            workflow["next_stage"] = next((s["stage_key"] for s in workflow_stages
                                           if s["status"] not in ("COMPLETED", "SKIPPED")), None)
            evidence = one("SELECT version FROM evidence_packages WHERE job_id=? ORDER BY version DESC LIMIT 1", job_id)
            script = one("SELECT version,status FROM script_revisions WHERE job_id=? AND language=? "
                         "ORDER BY version DESC LIMIT 1", job_id, language)
            script_ok = one("SELECT version FROM script_revisions WHERE job_id=? AND language=? AND status='APPROVED'",
                            job_id, language)
            plan = one("SELECT version,status FROM scene_plans WHERE job_id=? AND language=? ORDER BY version DESC LIMIT 1",
                       job_id, language)
            assets = one("SELECT COUNT(*) n, SUM(review_status='APPROVED') ok FROM assets WHERE job_id=?", job_id)
            assigned = one("SELECT COUNT(DISTINCT scene_id) n FROM asset_assignments WHERE job_id=? AND language=? "
                           "AND status='ACTIVE'", job_id, language)
            publish = [dict(r) for r in c.execute(
                "SELECT publish_id,state,visibility,youtube_video_id FROM publishing_jobs WHERE job_id=? AND language=? "
                "ORDER BY created_at DESC", (job_id, language))]
        def latest(kind):
            rows = self._integration_rows(job_id, language, kind)
            return rows[-1] if rows else None
        def view(row):
            return None if row is None else {"version": row["version"], "status": row["status"],
                                              "detail": row.get("detail", "")}
        stages = [
            {"stage": "evidence_package", "status": "APPROVED" if evidence else "NONE",
             "approved_version": (evidence or {}).get("version")},
            {"stage": "script", "status": (script or {}).get("status", "NONE"),
             "version": (script or {}).get("version"), "approved_version": (script_ok or {}).get("version")},
            {"stage": "scene_plan", "status": (plan or {}).get("status", "NONE"), "version": (plan or {}).get("version")},
            {"stage": "assets", "status": "NONE" if not assets or not assets["n"] else "PRESENT",
             "registered": (assets or {}).get("n") or 0, "approved": int((assets or {}).get("ok") or 0),
             "scenes_with_assignment": (assigned or {}).get("n") or 0},
        ]
        # Same freshness rules the per-stage services apply (audio/visual/render
        # bind to the approved plan hash; a package binds to the approved
        # render+QC). Kinds with no such rule carry no "stale" key.
        try:
            plan_sha = self._approved_plan(job_id, owner_id, language)[0]["plan_sha256"]
        except Exception:
            plan_sha = None
        try:
            gate = self.approved_render(job_id, owner_id, language)
        except Exception:
            gate = None

        def stale(kind, row):
            b = row["input_binding"]
            if kind in ("audio_set", "visual_timeline", "render"):
                return plan_sha is None or b.get("scene_plan_sha256") != plan_sha
            if kind == "publish_package":
                return gate is None or b.get("qc_version") != gate["qc_version"] \
                    or b.get("render_version") != gate["render_version"]
            return None

        for kind in ("audio_set", "visual_timeline", "render", "qc_report", "final_approval", "publish_package"):
            row = latest(kind)
            entry = {"stage": kind, **(view(row) or {"status": "NONE"})}
            if row is not None and stale(kind, row) is not None:
                entry["stale"] = stale(kind, row)
            stages.append(entry)
        stages.append({"stage": "publishing", "status": publish[0]["state"] if publish else "NONE",
                       "jobs": publish})
        blocked = [s["stage"] for s in stages if s["status"] in
                   ("FAILED", "FAILED_FINAL", "FAILED_RETRYABLE", "REAUTH_REQUIRED", "UPLOAD_PAUSED",
                    "INTERRUPTED", "REJECTED", "VALIDATION_FAILED")]
        if workflow["status"] in ("PAUSED", "AWAITING_REVIEW", "FAILED"):
            blocked.insert(0, "workflow")
        return {"job_id": job_id, "language": language, "workflow": workflow,
                "stages": stages, "needs_attention": blocked,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    # -- usage / cost ------------------------------------------------------------

    def usage_report(self, job_id: str, owner_id: str) -> dict:
        self._check_ownership(job_id, owner_id)
        text, unmeasured = [], 0
        for r in self.list_revisions(job_id, owner_id)["revisions"]:
            try:
                body = self._read_json_artifact(job_id, r)
            except Exception:
                continue
            u = body["usage"]
            measured = u.get("cost_is_measured") and u.get("measured_text_cost_inr") is not None
            if not measured:
                unmeasured += 1
            text.append({"language": r["language"], "version": r["version"], "provider": u["provider"], "model": u["model"],
                         "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                         "retries": u["retries"], "cost_inr": u.get("measured_text_cost_inr") if measured else None,
                         "cost_is_measured": bool(measured)})
        known = [t["cost_inr"] for t in text if t["cost_is_measured"]]
        text_total = round(sum(known), 4) if known else None
        tts = []
        for row in self._integration_rows(job_id, None, "audio_set"):
            if not row.get("uri"):
                continue
            body = self._read_json_artifact(job_id, row)
            tts.append({"language": row["language"], "version": row["version"],
                        "provider": body["binding"]["provider"], "cost_inr": body["cost"]["tts_cost_inr"],
                        "cost_is_measured": body["cost"]["is_measured"],
                        "seconds": body["summary"]["total_duration_seconds"]})
        with closing(connect(self.database_file)) as c:
            yt = c.execute("SELECT COALESCE(SUM(quota_units_used),0) FROM publishing_jobs WHERE job_id=?",
                           (job_id,)).fetchone()[0]
        return {
            "job_id": job_id, "currency": "INR",
            "text_generation": {"calls": text, "total_inr": text_total, "unmeasured_calls": unmeasured,
                                "target_inr": TEXT_COST_TARGET_INR,
                                "within_target": None if text_total is None else text_total <= TEXT_COST_TARGET_INR,
                                "note": "A budget objective measured from provider usage, not a guarantee."},
            "tts": {"runs": tts, "total_inr": round(sum(t["cost_inr"] for t in tts if t["cost_is_measured"]), 4)
                    if any(t["cost_is_measured"] for t in tts) else None},
            "image_generation": {"enabled": False, "total_inr": 0.0,
                                 "note": "AI image generation is not enabled from the pipeline; no image was billed."},
            "youtube_quota_units_estimate": int(yt),
        }

    # -- per-owner credentials (see services/credentials.py) ---------------------------

    def credential_status(self, owner_id: str) -> dict:
        from defence_studio.services import credentials
        return {"credentials": credentials.status(self.database_file, owner_id)}

    def set_credential(self, owner_id: str, provider: str, value: str) -> dict:
        from defence_studio.services import credentials
        from defence_studio.services.integration import IntegrationValidationError
        try:
            credentials.set_credential(self.database_file, owner_id, provider, value)
        except credentials.CredentialConfigError as exc:
            raise IntegrationValidationError(str(exc)) from None
        return self.credential_status(owner_id)

    def delete_credential(self, owner_id: str, provider: str) -> dict:
        from defence_studio.services import credentials
        credentials.delete_credential(self.database_file, owner_id, provider)
        return self.credential_status(owner_id)
