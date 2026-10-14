"""Phase 1A feed-discovery API-facing service.

This is the synchronous, human-paced half of discovery -- submitting a
request, browsing/filtering ranked candidates, and confirming a selection --
analogous to how F5's `ReviewService` layers human decisions on top of F3/F4
artifacts. The actual provider network calls happen only inside the durable
worker (`defence_studio.discovery.handler`), never here.

Flow:

1. `create_request` validates a `DiscoveryRequestV1`, skips the no-op
   `application_foundation` predecessor stage if still pending, and writes
   the request as a `discovery_request` artifact against that stage. Allowed
   only while the `news_feed_and_topic_discovery` stage itself is still
   `PENDING` (before a worker claims it), so a request cannot be silently
   changed out from under a run already in flight.
2. The worker claims `news_feed_and_topic_discovery`, runs discovery, and
   writes one `discovery_result` artifact (out of band from this service).
3. `list_candidates` / `get_candidate` are read-models over that artifact.
4. `select_candidates` turns an owner-approved subset of candidates into an
   `acquisition_request` artifact -- F3's own input contract -- registered
   against the (now completed) discovery stage, so F3's worker handler picks
   it up as an earlier-stage artifact exactly like any other predecessor
   output. Discovery never calls F3 directly and never marks a candidate as
   acquired, verified, or approved by itself.

No new SQLite table is introduced: both the request and the confirmed
selection are versioned, content-addressed artifacts through the existing
F1 artifact-reference mechanism, so re-submitting identical content reuses
the existing version instead of creating a duplicate.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.database import connect
from defence_studio.repositories.jobs import JobRepository

from defence_studio.contracts.discovery import (
    DISCOVERY_PREDECESSOR_STAGE,
    DISCOVERY_REQUEST_SCHEMA,
    DISCOVERY_STAGE,
    RequestValidationError,
    request_from_dict,
    request_to_dict,
)
from defence_studio.services.jobs import InvalidTransition, JobNotFound, JobService, JobValidationError
from defence_studio.storage.artifacts import ArtifactStore, StoragePathError

DISCOVERY_REQUEST_ARTIFACT_TYPE = "discovery_request"
DISCOVERY_RESULT_ARTIFACT_TYPE = "discovery_result"
ACQUISITION_REQUEST_ARTIFACT_TYPE = "acquisition_request"
ACQUISITION_REQUEST_SCHEMA_VERSION = "defence-studio.acquisition-request.v1"

MAX_SELECTION = 50
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

__all__ = [
    "DiscoveryApiService", "DiscoveryError", "DiscoveryNotFound",
    "DiscoveryValidationError", "DiscoveryConflict",
]


class DiscoveryError(Exception):
    status_code = 400


class DiscoveryNotFound(DiscoveryError):
    status_code = 404


class DiscoveryValidationError(DiscoveryError):
    status_code = 422


class DiscoveryConflict(DiscoveryError):
    status_code = 409


class DiscoveryApiService:
    def __init__(self, database_file: Path, storage_root: Path):
        self.jobs = JobService(database_file)
        self.store = ArtifactStore(storage_root)

    # -- requests -------------------------------------------------------------

    def create_request(self, job_id: str, owner_id: str, payload: dict) -> dict:
        job = self._check_ownership(job_id, owner_id)
        try:
            request = request_from_dict(payload, job_id=job_id)
        except RequestValidationError as exc:
            raise DiscoveryValidationError(str(exc)) from None

        discovery_stage = self._stage(job, DISCOVERY_STAGE)
        if discovery_stage["status"] != "PENDING" or job["status"] != "DRAFT":
            raise DiscoveryConflict(
                "Discovery has already started (or the job has been queued); "
                "the request can no longer be changed for this job."
            )

        body = json.dumps(request_to_dict(request), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        content_sha256 = hashlib.sha256(body).hexdigest()
        uri = f"jobs/{job_id}/discovery/{content_sha256}.json"

        existing = self._latest_artifact(job, DISCOVERY_REQUEST_ARTIFACT_TYPE)
        if existing is not None and existing["sha256"] == content_sha256:
            ref = existing
        else:
            stored = self.store.write_bytes(job_id, uri, body, "application/json")
            try:
                ref = self.jobs.add_pretransition_artifact(
                    job_id, owner_id, DISCOVERY_PREDECESSOR_STAGE, DISCOVERY_REQUEST_ARTIFACT_TYPE,
                    stored.uri, stored.sha256, DISCOVERY_REQUEST_SCHEMA,
                )
            except (InvalidTransition, JobValidationError) as exc:
                raise DiscoveryError(str(exc)) from None

        return {"request": request_to_dict(request), "artifact": self._artifact_view(ref)}

    def get_request(self, job_id: str, owner_id: str) -> dict:
        job = self._check_ownership(job_id, owner_id)
        ref = self._latest_artifact(job, DISCOVERY_REQUEST_ARTIFACT_TYPE)
        if ref is None:
            return {"job_id": job_id, "request": None, "artifact": None}
        return {"job_id": job_id, "request": self._read_json(job_id, ref), "artifact": self._artifact_view(ref)}

    # -- candidates -------------------------------------------------------------

    def list_candidates(self, job_id: str, owner_id: str, language: str | None = None,
                        source_type: str | None = None, provider: str | None = None,
                        min_score: float | None = None, query: str | None = None,
                        category: str | None = None, region: str | None = None,
                        source_class: str | None = None, date_from: str | None = None,
                        date_to: str | None = None, publisher: str | None = None,
                        cursor: str | None = None, page_size: int | None = None) -> dict:
        size = DEFAULT_PAGE_SIZE if page_size is None else page_size
        if size < 1 or size > MAX_PAGE_SIZE:
            raise DiscoveryValidationError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        try:
            offset = int(cursor) if cursor else 0
            if offset < 0:
                raise ValueError
        except ValueError:
            raise DiscoveryValidationError("cursor is invalid") from None
        job = self._check_ownership(job_id, owner_id)
        result_ref = self._latest_artifact(job, DISCOVERY_RESULT_ARTIFACT_TYPE)
        if result_ref is None:
            return {
                "job_id": job_id, "discovery_result_sha256": None, "discovery_result_uri": None,
                "providers": [], "candidates": [],
                "empty_reason": "Discovery has not produced results for this job yet.",
            }
        result = self._read_json(job_id, result_ref)
        selected_ids = set(self._selected_candidate_ids(job, job_id))
        candidates = []
        for candidate in result.get("candidates", []):
            declared = candidate.get("declared_metadata") or {}
            if language and declared.get("language") != language:
                continue
            if source_type and candidate.get("source_type") != source_type:
                continue
            if provider and candidate.get("provider") != provider and provider not in (candidate.get("merged_from_providers") or []):
                continue
            if min_score is not None and (candidate.get("ranking") or {}).get("total_score", 0) < min_score:
                continue
            if query:
                haystack = f"{declared.get('title') or ''} {declared.get('publisher') or ''}".lower()
                if query.lower() not in haystack:
                    continue
            # v1 artifacts lack taxonomy fields: treat as UNCLASSIFIED / UNKNOWN, flag don't drop.
            flags = list(candidate.get("filter_flags") or [])
            if category:
                cat = candidate.get("category", "UNCLASSIFIED")
                if cat != category:
                    if cat == "UNCLASSIFIED":
                        flags.append("category_filter_unverifiable")
                    else:
                        continue
            if region:
                regions = candidate.get("regions") or []
                if region not in regions:
                    if not regions:
                        flags.append("region_filter_unverifiable")
                    else:
                        continue
            if source_class:
                sc = candidate.get("source_class", "UNKNOWN")
                if sc != source_class:
                    if sc == "UNKNOWN":
                        flags.append("source_class_filter_unverifiable")
                    else:
                        continue
            if publisher and publisher.lower() not in (declared.get("publisher") or "").lower():
                continue
            if date_from or date_to:
                published = _iso_date(declared.get("published_at"))
                if published is None:
                    flags.append("date_filter_unverifiable")
                elif (date_from and published < date_from) or (date_to and published > date_to):
                    continue
            candidates.append({**candidate, "filter_flags": sorted(set(flags)),
                               "selected": candidate.get("candidate_id") in selected_ids})
        candidates.sort(key=lambda c: (
            -float((c.get("ranking") or {}).get("total_score", 0)),
            _neg_date(((c.get("declared_metadata") or {}).get("published_at"))),
            c.get("candidate_id", "")))
        total_count = len(candidates)
        page = candidates[offset: offset + size]
        next_cursor = str(offset + size) if offset + size < total_count else None
        candidates = page
        return {
            "job_id": job_id, "discovery_result_sha256": result_ref["sha256"],
            "discovery_result_uri": result_ref["uri"],
            "topic": result.get("topic"), "language": result.get("language"),
            "status": result.get("status"), "providers": result.get("providers", []),
            "dedup_notes": result.get("dedup_notes", []), "warnings": result.get("warnings", []),
            "candidates": candidates, "total_count": total_count, "next_cursor": next_cursor,
            "page_size": size, "result_schema_version": result.get("schema_version"),
            "discovered_at": result.get("discovered_at"),
            "coverage_note": "Results come only from the providers listed above; absence of a result "
                             "does not mean the web has none.",
        }

    # -- history ------------------------------------------------------------------

    def list_searches(self, owner_id: str, limit: int = 50) -> dict:
        """Owner's discovery runs, newest first. Each discovery run is a job
        (SQLite is the single source of truth); history is derived, so it can
        never disagree with the persisted request/result artifacts."""
        runs = []
        for summary in self.jobs.list_for_owner(owner_id)[: max(1, min(limit, 200))]:
            job = self.jobs.get(summary["job_id"], owner_id)
            request_ref = self._latest_artifact(job, DISCOVERY_REQUEST_ARTIFACT_TYPE)
            if request_ref is None:
                continue
            entry = {
                "search_run_id": job["job_id"], "job_id": job["job_id"], "topic": job["topic"],
                "language": job["language"], "job_status": job["status"],
                "created_at": job.get("created_at"), "request_sha256": request_ref["sha256"],
                "status": "REQUESTED", "candidate_count": None, "provider_outcomes": [],
                "filters": None, "completed_at": None,
            }
            try:
                request = self._read_json(job["job_id"], request_ref)
                entry["filters"] = request.get("filters")
            except DiscoveryError:
                entry["status"] = "UNREADABLE"
            result_ref = self._latest_artifact(job, DISCOVERY_RESULT_ARTIFACT_TYPE)
            stage = self._stage(job, DISCOVERY_STAGE)
            if result_ref is not None:
                try:
                    result = self._read_json(job["job_id"], result_ref)
                    entry.update(status=result.get("status", "COMPLETED"), candidate_count=result.get("returned"),
                                 provider_outcomes=result.get("providers", []),
                                 completed_at=result.get("discovered_at"))
                except DiscoveryError:
                    entry["status"] = "UNREADABLE"
            elif stage["status"] == "FAILED":
                entry["status"] = "FAILED"
            elif stage["status"] in ("RUNNING", "AWAITING_REVIEW") or job["status"] in ("QUEUED", "RUNNING"):
                entry["status"] = "RUNNING" if stage["status"] == "RUNNING" else "QUEUED"
            runs.append(entry)
        return {"searches": runs, "count": len(runs)}

    def rerun(self, job_id: str, owner_id: str, idempotency_key: str | None = None) -> dict:
        """Create a NEW job + identical request from a past run (history is
        immutable). The caller still queues the job explicitly."""
        job = self._check_ownership(job_id, owner_id)
        ref = self._latest_artifact(job, DISCOVERY_REQUEST_ARTIFACT_TYPE)
        if ref is None:
            raise DiscoveryNotFound("This job has no discovery request to rerun")
        request = self._read_json(job_id, ref)
        key = idempotency_key or f"rerun-{uuid.uuid4()}"
        new_job = self.jobs.create(owner_id, key, job["topic"], job["language"])
        payload = {k: request.get(k) for k in ("topic", "language", "search_terms", "filters", "limits")
                   if request.get(k) is not None}
        created = self.create_request(new_job["job_id"], owner_id, payload)
        return {"job_id": new_job["job_id"], "rerun_of": job_id, "request": created["request"]}

    def get_candidate(self, job_id: str, owner_id: str, candidate_id: str) -> dict:
        result = self.list_candidates(job_id, owner_id)
        for candidate in result["candidates"]:
            if candidate.get("candidate_id") == candidate_id:
                return candidate
        raise DiscoveryNotFound("Unknown candidate_id for this job's current discovery result")

    # -- selection / confirmation ------------------------------------------------

    def select_candidates(self, job_id: str, owner_id: str, candidate_ids: list[str]) -> dict:
        with closing(connect(self.jobs.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            repo = JobRepository(connection)
            row = repo.get_owned_job(job_id, owner_id)
            if row is None:
                raise DiscoveryNotFound("Workflow job not found")
            job = self.jobs._serialize(repo, row)
            result = self._select_candidates(connection, job, job_id, owner_id, candidate_ids)
            connection.commit()
            return result

    def _select_candidates(self, connection, job, job_id, owner_id, candidate_ids):
        discovery_stage = self._stage(job, DISCOVERY_STAGE)
        if discovery_stage["status"] != "COMPLETED":
            raise DiscoveryValidationError(
                "Discovery has not completed for this job yet; candidates cannot be selected."
            )
        result_ref = self._latest_artifact(job, DISCOVERY_RESULT_ARTIFACT_TYPE)
        if result_ref is None:
            raise DiscoveryValidationError("No discovery result exists yet for this job")
        result = self._read_json(job_id, result_ref)
        index = {c.get("candidate_id"): c for c in result.get("candidates", [])}

        if not isinstance(candidate_ids, list) or not candidate_ids:
            raise DiscoveryValidationError("candidate_ids must be a non-empty list")
        if len(candidate_ids) > MAX_SELECTION:
            raise DiscoveryValidationError(f"At most {MAX_SELECTION} candidates can be selected at once")
        if len(set(candidate_ids)) != len(candidate_ids):
            raise DiscoveryValidationError("candidate_ids must not contain duplicates")
        unknown = [cid for cid in candidate_ids if cid not in index]
        if unknown:
            raise DiscoveryValidationError(f"Unknown candidate_id(s): {unknown}")

        candidate_ids = sorted(candidate_ids)

        targets = []
        for cid in candidate_ids:
            candidate = index[cid]
            declared = candidate.get("declared_metadata") or {}
            language = declared.get("language")
            targets.append({
                "target_id": cid,
                "url": candidate.get("canonical_url"),
                "method": "auto",
                "language_preferences": [language] if language else [],
                "title_hint": declared.get("title"),
            })
        acquisition_request = {
            "job_id": job_id, "targets": targets, "schema_version": ACQUISITION_REQUEST_SCHEMA_VERSION,
        }
        body = json.dumps(acquisition_request, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        content_sha256 = hashlib.sha256(body).hexdigest()
        uri = f"jobs/{job_id}/discovery/{content_sha256}.json"

        existing = self._latest_artifact(job, ACQUISITION_REQUEST_ARTIFACT_TYPE)
        if existing is not None and existing["sha256"] == content_sha256:
            self._read_json(job_id, existing)
            ref = existing
        else:
            acquisition = self._stage(job, "topic_search_and_source_collection")
            if acquisition["status"] != "PENDING" or acquisition["attempt_count"]:
                raise DiscoveryConflict("Selection is locked once acquisition has started; create a new research job")
            stored = self.store.write_bytes(job_id, uri, body, "application/json")
            version = 1 + (existing["version"] if existing else 0)
            artifact_id = str(uuid.uuid4())
            connection.execute("INSERT INTO workflow_artifacts VALUES(?,?,?,?,?,?,?,?,?)", (
                artifact_id, job_id, DISCOVERY_STAGE, ACQUISITION_REQUEST_ARTIFACT_TYPE, version,
                stored.uri, stored.sha256, ACQUISITION_REQUEST_SCHEMA_VERSION,
                datetime.now(timezone.utc).isoformat(timespec="microseconds")))
            ref = dict(connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone())

        return {
            "job_id": job_id, "selected_candidate_ids": candidate_ids,
            "acquisition_request": acquisition_request, "artifact": self._artifact_view(ref),
        }

    def get_selection(self, job_id: str, owner_id: str) -> dict:
        job = self._check_ownership(job_id, owner_id)
        ref = self._latest_artifact(job, ACQUISITION_REQUEST_ARTIFACT_TYPE)
        if ref is None:
            return {"job_id": job_id, "selected_candidate_ids": [], "artifact": None}
        payload = self._read_json(job_id, ref)
        selected = [t.get("target_id") for t in payload.get("targets", [])]
        return {"job_id": job_id, "selected_candidate_ids": selected, "artifact": self._artifact_view(ref)}

    # -- internals ----------------------------------------------------------

    def _check_ownership(self, job_id: str, owner_id: str) -> dict:
        try:
            return self.jobs.get(job_id, owner_id)
        except JobNotFound as exc:
            raise DiscoveryNotFound(str(exc)) from None

    @staticmethod
    def _stage(job: dict, stage_key: str) -> dict:
        for stage in job.get("stages", []):
            if stage["stage_key"] == stage_key:
                return stage
        raise DiscoveryError(f"Locked stage {stage_key!r} is missing from this job")

    @staticmethod
    def _latest_artifact(job: dict, artifact_type: str) -> dict | None:
        candidates = [a for a in job.get("artifacts", []) if a["artifact_type"] == artifact_type]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a["version"])

    def _selected_candidate_ids(self, job: dict, job_id: str) -> list[str]:
        ref = self._latest_artifact(job, ACQUISITION_REQUEST_ARTIFACT_TYPE)
        if ref is None:
            return []
        payload = self._read_json(job_id, ref)
        return [t.get("target_id") for t in payload.get("targets", [])]

    def _read_json(self, job_id: str, ref: dict) -> dict:
        try:
            actual_sha = self.store.sha256_of(job_id, ref["uri"])
            if actual_sha != ref["sha256"]:
                raise DiscoveryValidationError("Stored artifact failed integrity check")
            parsed = json.loads(self.store.read_text(job_id, ref["uri"]))
        except (StoragePathError, FileNotFoundError, OSError, ValueError) as exc:
            raise DiscoveryValidationError(f"Could not read artifact {ref.get('uri')!r}: {exc}") from None
        if not isinstance(parsed, dict):
            raise DiscoveryValidationError("Artifact is not a JSON object")
        return parsed

    @staticmethod
    def _artifact_view(ref: dict) -> dict:
        return {
            "artifact_type": ref["artifact_type"], "uri": ref["uri"], "sha256": ref["sha256"],
            "schema_version": ref["schema_version"], "version": ref.get("version"),
        }


def _iso_date(value) -> str | None:
    """ISO date (YYYY-MM-DD) of a feed/API timestamp, or None if unparseable."""
    if not value or not isinstance(value, str):
        return None
    from defence_studio.discovery.ranking import parse_date
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def _neg_date(value) -> str:
    """Sort key for 'newest first' within equal scores; undated sort last."""
    iso = _iso_date(value)
    if iso is None:
        return "~"
    return "".join(chr(0x7e - (ord(ch) - 0x30)) if ch.isdigit() else ch for ch in iso)
