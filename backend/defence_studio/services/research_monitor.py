"""Read-only research monitor and evidence explorer.

Everything here is derived from durable state: ``workflow_jobs`` / ``workflow_stages`` /
``workflow_transitions``, the stored acquisition and analysis artifacts, and the latest
``evidence_policy_runs`` result. Nothing is simulated and nothing is written.

Pipeline mapping (the contract's 13 stage keys are authoritative; this view does not invent stages):
  Topic analysis / query planning  -> news_feed_and_topic_discovery
  Controlled search + acquisition  -> topic_search_and_source_collection
  Extraction and deduplication     -> extraction_validation_normalization_deduplication
  Claim assessment, corroboration,
  contradiction, readiness         -> evidence_analysis_and_review (+ evidence policy run)
  Script generation / validation   -> script_generation_and_approval
  Scene planning                   -> scene_creation_and_voice_direction
  Downstream production            -> remaining stages
"""

from __future__ import annotations

import csv
import io
import re
import statistics
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from defence_studio.acquisition.source_policy import default_policy
from defence_studio.contracts.analysis import ACQUISITION_RESULT_ARTIFACT, ANALYSIS_RESULT_ARTIFACT
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.database import connect
from defence_studio.repositories.jobs import JobRepository
from defence_studio.services.evidence_policy import EvidencePolicyService, load_evidence_policy
from defence_studio.services.review import ReviewNotFound, ReviewService, ReviewValidationError

STAGE_LABELS = {
    "application_foundation": "Application foundation",
    "news_feed_and_topic_discovery": "Topic analysis and discovery",
    "topic_search_and_source_collection": "Controlled search and acquisition",
    "extraction_validation_normalization_deduplication": "Extraction and deduplication",
    "evidence_analysis_and_review": "Claim assessment, corroboration and readiness",
    "script_generation_and_approval": "Script generation and validation",
    "scene_creation_and_voice_direction": "Scene planning",
    "asset_rights_review_and_assignment": "Asset rights and assignment",
    "voice_audio_finishing_and_alignment": "Voice and audio",
    "video_rendering": "Video rendering",
    "quality_control_final_review_and_export": "Quality control and export",
    "channel_branding_and_publishing_package": "Branding and publishing package",
    "cross_domain_and_format_reuse": "Format reuse",
}
# Conservative documented defaults (seconds) used only while fewer than MIN_HISTORY completed
# comparable stages exist. They are placeholders, labelled "default", never presented as measured.
DEFAULT_STAGE_SECONDS = {
    "news_feed_and_topic_discovery": 60, "topic_search_and_source_collection": 300,
    "extraction_validation_normalization_deduplication": 120, "evidence_analysis_and_review": 180,
}
MIN_HISTORY = 3
_DONE = {"COMPLETED", "SKIPPED"}
_TERMINAL = {"COMPLETED", "FAILED", "SKIPPED"}
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _parse(ts):
    try:
        return datetime.fromisoformat(ts) if ts else None
    except (TypeError, ValueError):
        return None


def _secs(a, b):
    return round((b - a).total_seconds(), 3) if a and b else None


def display_state(job_status: str, stages: list[dict]) -> str:
    """Distinct operator-facing state; never collapses retrying/awaiting into 'running'."""
    now = datetime.now(timezone.utc)
    if job_status in ("COMPLETED", "FAILED", "CANCELLED", "PAUSED"):
        return job_status
    if any(s["status"] == "AWAITING_REVIEW" for s in stages) or job_status == "AWAITING_REVIEW":
        return "AWAITING_READINESS"
    for s in stages:
        ra = _parse(s.get("retry_after"))
        if s["status"] in ("PENDING", "RUNNING", "FAILED") and s.get("attempt_count", 0) > 0 and ra and ra > now:
            return "WAITING_RETRY"
    if job_status == "RUNNING" or any(s["status"] == "RUNNING" for s in stages):
        return "RUNNING"
    return {"QUEUED": "QUEUED", "SCHEDULED": "QUEUED"}.get(job_status, "DRAFT")


class ResearchMonitorService:
    def __init__(self, database_file: Path, storage_root: Path):
        self.database_file = Path(database_file)
        self.storage_root = Path(storage_root)
        self.review = ReviewService(self.database_file, self.storage_root)
        self.policy_service = EvidencePolicyService(self.database_file, self.storage_root)
        self.registry = default_policy()

    # -- shared loading ------------------------------------------------------
    def _job(self, connection, job_id, owner_id):
        job = JobRepository(connection).get_owned_job(job_id, owner_id)
        if job is None:
            raise ReviewNotFound("Workflow job not found")
        return dict(job)

    def _artifacts(self, job_id, owner_id):
        with closing(connect(self.database_file)) as c:
            self._job(c, job_id, owner_id)
            acq = ReviewService._latest_artifact(c, job_id, ACQUISITION_RESULT_ARTIFACT)
            ana = ReviewService._latest_artifact(c, job_id, ANALYSIS_RESULT_ARTIFACT)
        acq_body = self.review._read_json_artifact(job_id, acq) if acq else None
        ana_body = self.review._read_json_artifact(job_id, ana) if ana else None
        return acq, ana, acq_body, ana_body

    def _latest_policy(self, job_id, owner_id):
        try:
            return self.policy_service.latest(job_id, owner_id)
        except ReviewValidationError:
            return None

    # -- monitor -------------------------------------------------------------
    def monitor(self, job_id: str, owner_id: str) -> dict:
        with closing(connect(self.database_file)) as c:
            job = self._job(c, job_id, owner_id)
            repo = JobRepository(c)
            stages = [dict(s) for s in repo.stages(job_id)]
            trans = [dict(r) for r in c.execute(
                "SELECT stage_key, from_status, to_status, occurred_at, error FROM workflow_transitions "
                "WHERE job_id=? ORDER BY transition_id", (job_id,)).fetchall()]
            history_rows = [dict(r) for r in c.execute(
                "SELECT t.stage_key, t.to_status, t.occurred_at, t.job_id FROM workflow_transitions t "
                "JOIN workflow_jobs j ON j.job_id=t.job_id WHERE j.owner_id=? AND t.job_id<>? "
                "ORDER BY t.job_id, t.transition_id", (owner_id, job_id)).fetchall()]
        now = datetime.now(timezone.utc)
        out_stages = []
        for s in stages:
            mine = [t for t in trans if t["stage_key"] == s["stage_key"]]
            # A retry starts a new attempt; never include the prior run or the
            # time spent waiting for configuration in this attempt's duration.
            reset = next((i for i in range(len(mine) - 1, -1, -1)
                          if mine[i]["to_status"] == "PENDING"), -1)
            mine = mine[reset + 1:]
            started = next((_parse(t["occurred_at"]) for t in reversed(mine) if t["to_status"] == "RUNNING"), None)
            ended = next((_parse(t["occurred_at"]) for t in reversed(mine) if t["to_status"] in _TERMINAL), None) \
                if s["status"] in _TERMINAL else None
            running = s["status"] == "RUNNING"
            out_stages.append({
                "stage_key": s["stage_key"], "label": STAGE_LABELS.get(s["stage_key"], s["stage_key"]),
                "ordinal": s["ordinal"], "status": s["status"],
                "started_at": started.isoformat() if started else None,
                "ended_at": ended.isoformat() if ended else None,
                "duration_seconds": _secs(started, ended),
                "elapsed_seconds": _secs(started, now) if running else None,
                "attempt_count": s["attempt_count"], "max_attempts": s["max_attempts"],
                "retry_after": s["retry_after"], "error": s["error"] or None,
                "progress": None,          # filled below where a work denominator exists
            })
        counts = self._source_counts(job_id, owner_id, out_stages)
        created, updated = _parse(job["created_at"]), _parse(job["updated_at"])
        first_run = min((_parse(s["started_at"]) for s in out_stages if s["started_at"]), default=None)
        terminal = job["status"] in ("COMPLETED", "FAILED", "CANCELLED")
        state = display_state(job["status"], stages)
        current = next((s for s in out_stages if s["status"] in ("RUNNING", "AWAITING_REVIEW", "FAILED")), None)
        estimate = self._estimate(out_stages, history_rows)
        if job["status"] in {"AWAITING_REVIEW", "PAUSED"}:
            # Human decisions and missing evidence have no measurable deadline.
            estimate = {"remaining_seconds": None, "basis": job["status"].lower(),
                        "confidence": "n/a", "samples": 0}
        return {
            "job_id": job_id, "topic": job["topic"], "language": job["language"],
            "job_status": job["status"], "display_state": state,
            "created_at": job["created_at"], "updated_at": job["updated_at"], "queued_at": job.get("queued_at"),
            "started_at": first_run.isoformat() if first_run else None,
            "elapsed_seconds": _secs(first_run, updated if terminal else now),
            "current_stage": current["stage_key"] if current else None,
            "stages": out_stages, "stages_total": len(out_stages),
            "stages_done": sum(1 for s in out_stages if s["status"] in _DONE),
            "sources": counts["sources"], "counts": counts["counts"],
            "coverage": self.coverage_report(job_id, owner_id),
            "estimate": estimate,
            "controls": {"start": job["status"] in ("DRAFT",), "pause": False, "resume": False, "cancel": False,
                         "retry": False,
                         "note": "Pause, resume, cancel and retry are not implemented by the backend."},
            "pipeline_mapping": STAGE_LABELS,
        }

    def _source_counts(self, job_id, owner_id, out_stages):
        try:
            _, _, acq, _ = self._artifacts(job_id, owner_id)
        except ReviewValidationError:
            acq = None
        pol = self._latest_policy(job_id, owner_id)
        pol_state = {s["source_id"]: s for s in (pol or {}).get("sources", [])}
        rows, c = [], {"approved": 0, "fetched": 0, "failed": 0, "rejected": 0, "duplicate": 0, "pending": 0}
        if acq:
            for s in acq.get("sources", []):
                url = s.get("final_url") or s.get("canonical_url") or s.get("requested_url") or ""
                host = urlsplit(url).hostname
                ps = pol_state.get(s["source_id"])
                entry = self.registry.entry_for(url) if self.registry and url else None
                meta = s.get("declared_metadata") or {}
                ok = s.get("status") == "ACQUIRED"
                c["fetched"] += ok
                c["failed"] += not ok
                if ps:
                    c["approved"] += ps["state"] == "AUTO_ACCEPTED"
                    c["rejected"] += ps["state"] in ("REJECTED", "SOURCE_BLOCKED")
                rows.append({
                    "source_id": s["source_id"], "hostname": host, "url": url or None,
                    "title": meta.get("title"), "acquisition_status": s.get("status"),
                    "policy_state": ps["state"] if ps else None,
                    "category": (ps or {}).get("category") or (entry.category if entry else None),
                    "independence_group": (ps or {}).get("independence_group") or (entry.independence_group if entry else None),
                    "fetch_duration_seconds": None,     # not recorded by the acquisition contract
                    "retry_count": None, "error": s.get("error"), "warnings": s.get("warnings") or [],
                    "acquired_at": s.get("acquisition_timestamp"),
                    "requested_url": s.get("requested_url"),
                    "final_url": s.get("final_url") or s.get("canonical_url"),
                    "published_at": meta.get("published_at") or (ps or {}).get("published_at"),
                    "publisher": (ps or {}).get("publisher") or meta.get("publisher"),
                })
            c["duplicate"] = int(acq.get("skipped_duplicate") or 0)
            c["pending"] = max(0, int(acq.get("requested") or 0) - int(acq.get("succeeded") or 0)
                               - int(acq.get("failed") or 0) - c["duplicate"]) if acq.get("requested") else 0
            acq_stage = next((s for s in out_stages if s["stage_key"] == "topic_search_and_source_collection"), None)
            if acq_stage and acq.get("requested"):
                done = int(acq.get("succeeded") or 0) + int(acq.get("failed") or 0) + c["duplicate"]
                acq_stage["progress"] = {"done": done, "total": int(acq["requested"]), "unit": "sources"}
        return {"sources": rows, "counts": c}

    def coverage_report(self, job_id: str, owner_id: str) -> dict:
        """Counts from stored discovery/acquisition/analysis/policy artifacts only."""
        from defence_studio.services.discovery import (
            DISCOVERY_REQUEST_ARTIFACT_TYPE, DISCOVERY_RESULT_ARTIFACT_TYPE)
        with closing(connect(self.database_file)) as c:
            self._job(c, job_id, owner_id)
            req_ref = ReviewService._latest_artifact(c, job_id, DISCOVERY_REQUEST_ARTIFACT_TYPE)
            disc_ref = ReviewService._latest_artifact(c, job_id, DISCOVERY_RESULT_ARTIFACT_TYPE)
        request = self.review._read_json_artifact(job_id, req_ref) if req_ref else {}
        disc = self.review._read_json_artifact(job_id, disc_ref) if disc_ref else {}
        try:
            _, _, acq, ana = self._artifacts(job_id, owner_id)
        except ReviewValidationError:
            acq, ana = {}, {}
        return build_coverage(request, disc, acq or {}, ana or {}, self._latest_policy(job_id, owner_id) or {})

    def _estimate(self, out_stages, history_rows) -> dict:
        durations: dict[str, list[float]] = {}
        open_run: dict[tuple, datetime] = {}
        for r in history_rows:
            key, ts = (r["job_id"], r["stage_key"]), _parse(r["occurred_at"])
            if r["to_status"] == "RUNNING":
                open_run[key] = ts
            elif r["to_status"] == "COMPLETED" and key in open_run and ts:
                durations.setdefault(r["stage_key"], []).append((ts - open_run.pop(key)).total_seconds())
        remaining, basis, missing = 0.0, "history", False
        for s in out_stages:
            if s["status"] in _TERMINAL or s["stage_key"] not in DEFAULT_STAGE_SECONDS:
                continue
            hist = durations.get(s["stage_key"], [])
            if len(hist) >= MIN_HISTORY:
                est = statistics.median(hist)
            else:
                est, basis = DEFAULT_STAGE_SECONDS[s["stage_key"]], "default"
            spent = s["elapsed_seconds"] or 0
            remaining += max(0.0, est - spent)
        if all(s["status"] in _TERMINAL for s in out_stages if s["stage_key"] in DEFAULT_STAGE_SECONDS):
            return {"remaining_seconds": 0, "basis": "complete", "confidence": "n/a", "samples": 0}
        samples = min((len(durations.get(k, [])) for k in DEFAULT_STAGE_SECONDS), default=0)
        return {"remaining_seconds": round(remaining), "basis": basis,
                "confidence": "medium" if basis == "history" and samples >= 10 else "low", "samples": samples,
                "note": "Estimate only; not derived from elapsed time alone. Stages without history use "
                        "documented default durations."}

    # -- explorer ------------------------------------------------------------
    def explorer(self, job_id: str, owner_id: str) -> dict:
        acq_ref, ana_ref, acq, ana = self._artifacts(job_id, owner_id)
        pol = self._latest_policy(job_id, owner_id)
        current_policy = load_evidence_policy()["version"]
        if pol is None:
            return {"job_id": job_id, "policy_run": None, "claims": [], "sources": [],
                    "readiness": {"ready": False, "reasons": ["The automatic evidence policy has not been run for this job."]},
                    "freshness": {"status": "NOT_EVALUATED"}}
        stale = []
        if ana_ref and ana_ref["sha256"] != pol.get("analysis_sha256"):
            stale.append("analysis changed since the policy run")
        if acq_ref and acq_ref["sha256"] != pol.get("acquisition_sha256"):
            stale.append("acquisition changed since the policy run")
        if pol.get("policy_version") != current_policy:
            stale.append(f"policy version changed ({pol.get('policy_version')} -> {current_policy})")
        norm = {n["source_id"]: n for n in (ana or {}).get("normalized_sources", [])}
        src_meta = {s["source_id"]: s for s in (acq or {}).get("sources", [])}
        analysis_claims = {c["claim_id"]: c for c in (ana or {}).get("claims", [])}
        psrc = {s["source_id"]: s for s in pol["sources"]}
        claims = []
        for c in pol["claims"]:
            src = psrc.get(c["source_id"], {})
            ac = analysis_claims.get(c["claim_id"], {})
            text_all = (norm.get(c["source_id"]) or {}).get("normalized_text", "")
            s0, e0 = c["passage"]["start"], c["passage"]["end"]
            ctx = None
            if isinstance(s0, int) and isinstance(e0, int) and 0 <= s0 < e0 <= len(text_all):
                ctx = {"before": text_all[max(0, s0 - 300):s0], "passage": text_all[s0:e0],
                       "after": text_all[e0:e0 + 300]}
            corr = c.get("corroboration") or {}
            qty = bool(corr.get("applicable"))
            claims.append({
                "claim_id": c["claim_id"], "text": c["text"], "category": ac.get("evidence_class"),
                "state": c["state"], "reasons": c["reasons"], "flags": c["flags"],
                "score": c.get("score"), "source_id": c["source_id"],
                "hostname": urlsplit(src.get("final_url") or src.get("canonical_url") or "").hostname,
                "source_category": src.get("category"), "independence_group": src.get("independence_group"),
                "passage_ref": {"start": s0, "end": e0}, "context": ctx,
                "corroboration": corr, "contradicting": c.get("contradicting_evidence", []),
                "contradicted": bool(c.get("contradicting_evidence")),
                "single_source": bool(corr.get("single_source")) if qty else None,
                "quantitative": qty, "high_impact": "strict_class" in c["flags"],
                "analysis_sha256": pol.get("analysis_sha256"),
                "policy_version": pol.get("policy_version"),
                "scoring_policy_version": pol.get("scoring_policy_version"),
            })
        sources = []
        for sid, s in psrc.items():
            meta = (src_meta.get(sid) or {}).get("declared_metadata") or {}
            sources.append({**s, "title": meta.get("title"), "claims": [c["claim_id"] for c in claims if c["source_id"] == sid],
                            "error": (src_meta.get(sid) or {}).get("error"),
                            "acquisition_status": (src_meta.get(sid) or {}).get("status"),
                            "published_at": s.get("published_at") or "Unknown"})
        ready = bool(pol.get("ready_for_generation"))
        reasons = [f"{f['criterion']}: required {f['required']}, actual {f['actual']} — {f['action']}"
                   for f in pol.get("policy_criteria_failed", [])] + list(pol.get("package_blockers", []))
        if stale:
            ready, reasons = False, reasons + ["Evidence is stale: " + "; ".join(stale)]
        return {"job_id": job_id, "claims": claims, "sources": sources,
                "counts": pol["counts"],
                "policy_run": {"run_id": pol.get("run_id"), "policy_version": pol.get("policy_version"),
                               "scoring_policy_version": pol.get("scoring_policy_version"),
                               "source_policy_version": pol.get("source_policy_version"),
                               "result_sha256": pol.get("result_sha256"),
                               "analysis_sha256": pol.get("analysis_sha256"),
                               "analysis_versions": pol.get("analysis_versions")},
                "readiness": {"ready": ready, "script_generation_permitted": ready, "reasons": reasons,
                              "package_approval": pol.get("package_approval")},
                "freshness": {"status": "STALE" if stale else "CURRENT", "reasons": stale}}

    def source_detail(self, job_id: str, owner_id: str, source_id: str) -> dict:
        if not _ID.match(source_id or ""):
            raise ReviewNotFound("Source not found")
        data = self.explorer(job_id, owner_id)
        src = next((s for s in data["sources"] if s["source_id"] == source_id), None)
        if src is None:
            raise ReviewNotFound("Source not found")
        return {**src, "claim_details": [c for c in data["claims"] if c["source_id"] == source_id],
                "freshness": data["freshness"]}

    def export(self, job_id: str, owner_id: str, fmt: str) -> tuple[str, str, str]:
        data = self.explorer(job_id, owner_id)
        if fmt == "json":
            import json
            return json.dumps(data, ensure_ascii=False, indent=2), "application/json", "evidence-report.json"
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["claim_id", "state", "score", "category", "text", "source_id", "hostname", "source_category",
                    "independence_group", "passage_start", "passage_end", "corroborating_groups", "contradicted",
                    "reasons", "policy_version", "scoring_policy_version", "analysis_sha256"])
        for c in data["claims"]:
            w.writerow([c["claim_id"], c["state"], (c["score"] or {}).get("total"), c["category"], c["text"],
                        c["source_id"], c["hostname"], c["source_category"], c["independence_group"],
                        c["passage_ref"]["start"], c["passage_ref"]["end"],
                        ";".join((c["corroboration"] or {}).get("independent_groups", [])), c["contradicted"],
                        " | ".join(c["reasons"]), c["policy_version"], c["scoring_policy_version"], c["analysis_sha256"]])
        text = buf.getvalue()
        # neutralise spreadsheet formula injection from fetched content
        text = re.sub(r"(?m)(^|,)\"?([=+@])", lambda m: m.group(0)[:-1] + "'" + m.group(2), text)
        return text, "text/csv", "evidence-report.csv"


_NOT_CONFIGURED = {"UNAVAILABLE_MISSING_CREDENTIALS", "UNAVAILABLE_NOT_CONFIGURED",
                   "UNAVAILABLE_OPTIONAL_DEPENDENCY_MISSING", "DISABLED"}
_RAN = {"OK", "PARTIAL", "COMPLETED_NO_RESULTS", "ERROR"}


def _provider_view(p: dict) -> dict:
    """Provider fields from the stored outcome. `configured` is inferred only from
    the outcome status; a status that does not establish it is reported as None."""
    status = p.get("status")
    configured = False if status in _NOT_CONFIGURED else (True if status in _RAN else None)
    return {"name": p.get("provider") or p.get("name"), "status": status, "message": p.get("message"),
            "configured": configured, "requires_credentials": p.get("requires_credentials"),
            "candidates_returned": p.get("candidates_returned"), "http_attempts": p.get("http_attempts"),
            "duration_ms": p.get("duration_ms")}


def build_coverage(request: dict, disc: dict, acq: dict, ana: dict, pol: dict) -> dict:
    """Pure derivation of coverage counts from stored artifacts. Discovered URLs,
    attempted URLs, fetched sources and normalized documents are kept separate;
    anything the artifacts do not record is None, never a guess."""
    terms = request.get("search_terms") if isinstance(request.get("search_terms"), list) else None
    providers = [_provider_view(p) for p in (disc.get("providers") or []) if isinstance(p, dict)]
    attempts = [p["http_attempts"] for p in providers if isinstance(p["http_attempts"], int)]
    attempted, fetched, rejected, errors = set(), 0, 0, 0
    for s in acq.get("sources") or []:
        url = s.get("requested_url") or s.get("canonical_url") or s.get("final_url")
        if url:
            attempted.add(url)
        err = str(s.get("error") or "")
        if "SOURCE_NOT_ALLOWLISTED" in err or "UrlValidation" in err:
            rejected += 1
        elif s.get("status") == "ACQUIRED":
            fetched += 1
        else:
            errors += 1
    normalized = ana.get("sources_normalized")
    if not isinstance(normalized, int):
        normalized = len(ana.get("normalized_sources") or [])
    publishers = sorted({s["publisher"] for s in pol.get("sources") or [] if s.get("publisher")})
    groups = sorted({s["independence_group"] for s in pol.get("sources") or [] if s.get("independence_group")})
    claim_counts = (pol.get("counts") or {}).get("claims") or {}
    return {
        "search_terms": terms,
        "search_terms_count": len(terms) if terms is not None else None,
        "provider_http_attempts": sum(attempts) if attempts else None,
        "providers": providers,
        "providers_total": len(providers),
        "providers_not_configured": sum(1 for p in providers if p["configured"] is False),
        "candidate_urls_discovered": len(disc.get("candidates") or []) if disc else None,
        "unique_urls_attempted": len(attempted),
        "sources_fetched": fetched,
        "sources_rejected_by_policy": rejected,
        "sources_errors_or_unsupported": errors,
        "sources_normalized": normalized,
        "duplicate_urls_removed": int(acq.get("skipped_duplicate") or 0),
        "duplicate_document_groups": len(ana.get("duplicate_groups") or []),
        "unique_publishers": publishers,
        "independent_source_groups": groups,
        "claims_extracted": len(ana.get("claims") or []),
        "claims_auto_accepted": int(claim_counts.get("AUTO_ACCEPTED") or 0),
        "claims_needs_review": int(claim_counts.get("NEEDS_REVIEW") or 0),
        "claims_rejected": int(claim_counts.get("REJECTED") or 0) + int(claim_counts.get("SOURCE_BLOCKED") or 0),
        "ready_for_generation": bool(pol.get("ready_for_generation")),
        "policy_version": pol.get("policy_version"),
        "readiness_failures": pol.get("policy_criteria_failed") or [],
        "unsupported_discovery": [
            "X trends / social-media discovery has no configured provider",
            "YouTube search discovery is not implemented; subtitle acquisition is separate",
            "Hosted web search runs only when DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL and key are set",
        ],
        "images_and_assets": "NOT_IMPLEMENTED: no image acquisition, licence or attribution capture (gap G3)",
    }
