"""F5 evidence review & approval service.

Reads the real, already-persisted F3 `acquisition_manifest` and F4
`analysis_result` artifacts (never mutates them) and layers explicit,
authenticated human review decisions on top, per `contracts/review.py`.

Key guarantees enforced here (see contracts/review.py for the full rationale):

* Every decision is pinned to the exact artifact SHA-256 the reviewer saw.
  Submitting against anything but the job's *current* artifact is rejected
  as `StaleRevision` -- a claim/source cannot be approved against evidence
  that has since been superseded by a newer analysis/acquisition run.
* Every decision carries `expected_revision`; a mismatch against the
  currently stored revision is rejected as `RevisionConflict` rather than
  silently overwriting a concurrent reviewer's change.
* Every accepted decision is mirrored into an append-only history table
  inside the same transaction as the current-state upsert, so the audit
  trail can never fall out of sync with current state.
* A duplicate submission (same reviewer, subject, and client-supplied
  Idempotency-Key) returns the existing recorded decision unchanged instead
  of creating a second history row.

This service verifies job ownership on every call (via `JobService.get`),
so a caller acting for the wrong owner gets the same 404-equivalent
treatment as every other Defence Studio boundary.
"""

from __future__ import annotations

import json
import hashlib
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.contracts.analysis import ACQUISITION_RESULT_ARTIFACT, ANALYSIS_RESULT_ARTIFACT
from defence_studio.contracts.review import REVIEW_DECISIONS
from defence_studio.database import connect
from defence_studio.repositories.jobs import JobRepository
from defence_studio.repositories.review import ReviewRepository
from defence_studio.services.jobs import JobNotFound, JobService
from defence_studio.storage.artifacts import ArtifactStore, StoragePathError

__all__ = [
    "ReviewService", "ReviewError", "ReviewNotFound", "ReviewValidationError",
    "StaleRevision", "RevisionConflict",
]


class ReviewError(Exception):
    status_code = 400


class ReviewNotFound(ReviewError):
    status_code = 404


class ReviewValidationError(ReviewError):
    status_code = 422


class StaleRevision(ReviewError):
    """The reviewer's `reviewed_artifact_sha256` no longer matches the job's
    current artifact -- a newer analysis/acquisition run has superseded what
    they looked at. They must reload and explicitly review the latest."""

    status_code = 409


class RevisionConflict(ReviewError):
    """`expected_revision` did not match the current stored revision -- another
    decision (or device/tab) changed this subject first."""

    status_code = 409


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ReviewService:
    def __init__(self, database_file: Path, storage_root: Path):
        self.database_file = database_file
        self.jobs = JobService(database_file)
        self.store = ArtifactStore(storage_root)

    def _check_ownership(self, job_id: str, owner_id: str) -> None:
        """Translate the job service's own 404-equivalent into ReviewNotFound,
        so every public method on this service raises from one exception
        family regardless of which internal call first detects the mismatch."""
        try:
            self.jobs.get(job_id, owner_id)
        except JobNotFound as exc:
            raise ReviewNotFound(str(exc)) from None

    # -- claims -------------------------------------------------------------

    def list_claims(self, job_id: str, owner_id: str, status: str | None = None,
                    conflict_only: bool = False, source_id: str | None = None) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            latest_ref = self._latest_artifact(connection, job_id, ANALYSIS_RESULT_ARTIFACT)
            if latest_ref is None:
                return {
                    "job_id": job_id, "analysis_artifact_sha256": None, "analysis_artifact_uri": None,
                    "duplicate_groups": [], "claim_groups": [], "conflicts": [], "claims": [],
                    "empty_reason": "No analysis result has been produced for this job yet.",
                }
            analysis = self._read_json_artifact(job_id, latest_ref)
            repo = ReviewRepository(connection)
            reviews = {row["claim_id"]: row for row in repo.list_claim_reviews(job_id)}
            conflicted = set()
            for conflict in analysis.get("conflicts", []):
                conflicted.add(conflict.get("claim_id_a"))
                conflicted.add(conflict.get("claim_id_b"))
            claims = []
            for claim in analysis.get("claims", []):
                if source_id and claim.get("source_id") != source_id:
                    continue
                review = reviews.get(claim.get("claim_id"))
                current_status = review["decision"] if review else "PENDING"
                if status and current_status != status:
                    continue
                if conflict_only and claim.get("claim_id") not in conflicted:
                    continue
                claims.append({
                    **claim,
                    "has_conflict": claim.get("claim_id") in conflicted,
                    "review": self._record_from_row(review, "claim_id"),
                    "review_status": current_status,
                    "is_stale": bool(review) and review["reviewed_artifact_sha256"] != latest_ref["sha256"],
                })
            return {
                "job_id": job_id,
                "analysis_artifact_sha256": latest_ref["sha256"],
                "analysis_artifact_uri": latest_ref["uri"],
                "duplicate_groups": analysis.get("duplicate_groups", []),
                "claim_groups": analysis.get("claim_groups", []),
                "conflicts": analysis.get("conflicts", []),
                "claims": claims,
            }

    def get_claim(self, job_id: str, owner_id: str, claim_id: str) -> dict:
        result = self.list_claims(job_id, owner_id)
        for claim in result["claims"]:
            if claim.get("claim_id") == claim_id:
                return claim
        raise ReviewNotFound("Unknown claim_id for this job's current analysis result")

    def claim_history(self, job_id: str, owner_id: str, claim_id: str) -> list[dict]:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            repo = ReviewRepository(connection)
            return [self._record_from_row(row, "claim_id") for row in repo.claim_history(job_id, claim_id)]

    def decide_claim(self, job_id: str, owner_id: str, claim_id: str, decision: str,
                     reviewed_artifact_sha256: str, expected_revision: int,
                     note: str, idempotency_key: str) -> dict:
        self._validate_decision(decision)
        note = self._validate_note(note)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if JobRepository(connection).get_owned_job(job_id, owner_id) is None:
                raise ReviewNotFound("Workflow job not found")
            repo = ReviewRepository(connection)

            replay = repo.find_claim_history_by_idempotency_key(job_id, claim_id, owner_id, idempotency_key)
            if replay is not None:
                result = self._replay(repo, replay, "claim", decision, reviewed_artifact_sha256, expected_revision, note)
                connection.commit()
                return result

            latest_ref = self._latest_artifact(connection, job_id, ANALYSIS_RESULT_ARTIFACT)
            if latest_ref is None:
                raise ReviewValidationError("No analysis result exists yet for this job")
            if reviewed_artifact_sha256 != latest_ref["sha256"]:
                raise StaleRevision(
                    "The analysis revision you reviewed is no longer current for this job; "
                    "reload and review the latest revision before deciding"
                )
            claims_index = self._claims_index(job_id, latest_ref)
            if claim_id not in claims_index:
                raise ReviewValidationError("Unknown claim_id for this job's current analysis result")

            current = repo.get_claim_review(job_id, claim_id)
            current_revision = current["revision"] if current else 0
            previous_decision = current["decision"] if current else None
            if expected_revision != current_revision:
                raise RevisionConflict(
                    f"This claim's review was already changed (current revision {current_revision}); "
                    "reload before submitting a decision"
                )
            next_revision = current_revision + 1
            repo.upsert_claim_review(job_id, claim_id, decision, ANALYSIS_RESULT_ARTIFACT,
                                     reviewed_artifact_sha256, owner_id, note, next_revision, now)
            repo.insert_claim_history(str(uuid.uuid4()), job_id, claim_id, decision,
                                      ANALYSIS_RESULT_ARTIFACT, reviewed_artifact_sha256, owner_id,
                                      note, next_revision, previous_decision, now, idempotency_key)
            connection.commit()
            return self._record_from_row(repo.get_claim_review(job_id, claim_id), "claim_id")

    # -- sources --------------------------------------------------------------

    def list_sources(self, job_id: str, owner_id: str, status: str | None = None) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            acquisition_ref = self._latest_artifact(connection, job_id, ACQUISITION_RESULT_ARTIFACT)
            if acquisition_ref is None:
                return {
                    "job_id": job_id, "acquisition_artifact_sha256": None,
                    "acquisition_artifact_uri": None, "sources": [],
                    "empty_reason": "No source has been acquired for this job yet.",
                }
            acquisition = self._read_json_artifact(job_id, acquisition_ref)
            analysis_ref = self._latest_artifact(connection, job_id, ANALYSIS_RESULT_ARTIFACT)
            normalized_by_id: dict = {}
            duplicate_groups: list = []
            if analysis_ref is not None:
                analysis = self._read_json_artifact(job_id, analysis_ref)
                normalized_by_id = {n["source_id"]: n for n in analysis.get("normalized_sources", [])}
                duplicate_groups = analysis.get("duplicate_groups", [])
            repo = ReviewRepository(connection)
            reviews = {row["source_id"]: row for row in repo.list_source_reviews(job_id)}
            sources = []
            for source in acquisition.get("sources", []):
                sid = source.get("source_id")
                review = reviews.get(sid)
                current_status = review["decision"] if review else "PENDING"
                if status and current_status != status:
                    continue
                sources.append({
                    **source,
                    "normalized": normalized_by_id.get(sid),
                    "duplicate_groups": [g for g in duplicate_groups
                                        if sid in (g.get("member_source_ids") or [])],
                    "review": self._record_from_row(review, "source_id"),
                    "review_status": current_status,
                    "is_stale": bool(review) and review["reviewed_artifact_sha256"] != acquisition_ref["sha256"],
                })
            return {
                "job_id": job_id,
                "acquisition_artifact_sha256": acquisition_ref["sha256"],
                "acquisition_artifact_uri": acquisition_ref["uri"],
                "sources": sources,
            }

    def get_source(self, job_id: str, owner_id: str, source_id: str) -> dict:
        result = self.list_sources(job_id, owner_id)
        for source in result["sources"]:
            if source.get("source_id") == source_id:
                return source
        raise ReviewNotFound("Unknown source_id for this job's current acquisition result")

    def source_history(self, job_id: str, owner_id: str, source_id: str) -> list[dict]:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            repo = ReviewRepository(connection)
            return [self._record_from_row(row, "source_id") for row in repo.source_history(job_id, source_id)]

    def decide_source(self, job_id: str, owner_id: str, source_id: str, decision: str,
                      reviewed_artifact_sha256: str, expected_revision: int,
                      note: str, idempotency_key: str) -> dict:
        self._validate_decision(decision)
        note = self._validate_note(note)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if JobRepository(connection).get_owned_job(job_id, owner_id) is None:
                raise ReviewNotFound("Workflow job not found")
            repo = ReviewRepository(connection)

            replay = repo.find_source_history_by_idempotency_key(job_id, source_id, owner_id, idempotency_key)
            if replay is not None:
                result = self._replay(repo, replay, "source", decision, reviewed_artifact_sha256, expected_revision, note)
                connection.commit()
                return result

            latest_ref = self._latest_artifact(connection, job_id, ACQUISITION_RESULT_ARTIFACT)
            if latest_ref is None:
                raise ReviewValidationError("No source has been acquired yet for this job")
            if reviewed_artifact_sha256 != latest_ref["sha256"]:
                raise StaleRevision(
                    "The acquisition revision you reviewed is no longer current for this job; "
                    "reload and review the latest revision before deciding"
                )
            sources_index = self._sources_index(job_id, latest_ref)
            if source_id not in sources_index:
                raise ReviewValidationError("Unknown source_id for this job's current acquisition result")

            current = repo.get_source_review(job_id, source_id)
            current_revision = current["revision"] if current else 0
            previous_decision = current["decision"] if current else None
            if expected_revision != current_revision:
                raise RevisionConflict(
                    f"This source's review was already changed (current revision {current_revision}); "
                    "reload before submitting a decision"
                )
            next_revision = current_revision + 1
            repo.upsert_source_review(job_id, source_id, decision, ACQUISITION_RESULT_ARTIFACT,
                                      reviewed_artifact_sha256, owner_id, note, next_revision, now)
            repo.insert_source_history(str(uuid.uuid4()), job_id, source_id, decision,
                                       ACQUISITION_RESULT_ARTIFACT, reviewed_artifact_sha256, owner_id,
                                       note, next_revision, previous_decision, now, idempotency_key)
            connection.commit()
            return self._record_from_row(repo.get_source_review(job_id, source_id), "source_id")

    # -- internals ------------------------------------------------------------

    def _replay(self, repo, row, kind, decision, sha, expected_revision, note):
        if (row["decision"], row["reviewed_artifact_sha256"], row["revision"], row["note"]) != (decision, sha, expected_revision + 1, note):
            raise RevisionConflict("Idempotency key was already used for a different review")
        key = kind + "_id"
        history = getattr(repo, kind + "_history")(row["job_id"], row[key])
        original = dict(row)
        original["created_at"] = min(h["occurred_at"] for h in history)
        original["updated_at"] = original.pop("occurred_at")
        return self._record_from_row(original, key)

    @staticmethod
    def _validate_decision(decision: str) -> None:
        if decision not in REVIEW_DECISIONS:
            raise ReviewValidationError(f"Unsupported decision {decision!r}")

    @staticmethod
    def _validate_note(note: str) -> str:
        note = (note or "").strip()
        if not note:
            raise ReviewValidationError("A review explanation is required")
        if len(note) > 4000:
            raise ReviewValidationError("note must be at most 4000 characters")
        return note

    @staticmethod
    def _validate_idempotency_key(key: str) -> str:
        key = (key or "").strip()
        if not key or len(key) > 200:
            raise ReviewValidationError("Idempotency-Key header is required and must be 1-200 characters")
        return key

    @staticmethod
    def _latest_artifact(connection, job_id: str, artifact_type: str) -> dict | None:
        row = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE job_id = ? AND artifact_type = ? "
            "ORDER BY version DESC LIMIT 1",
            (job_id, artifact_type),
        ).fetchone()
        return dict(row) if row else None

    def _read_json_artifact(self, job_id: str, ref: dict) -> dict:
        try:
            data = self.store.read_bytes(job_id, ref["uri"], max_bytes=128 * 1024 * 1024)
            actual_sha = hashlib.sha256(data).hexdigest()
            if actual_sha != ref["sha256"]:
                raise ReviewValidationError("Stored artifact failed integrity check")
            parsed = json.loads(data)
        except (StoragePathError, FileNotFoundError, OSError, ValueError) as exc:
            raise ReviewValidationError(f"Could not read artifact {ref.get('uri')!r}: {exc}") from None
        if not isinstance(parsed, dict):
            raise ReviewValidationError("Artifact is not a JSON object")
        return parsed

    def _claims_index(self, job_id: str, ref: dict) -> dict:
        analysis = self._read_json_artifact(job_id, ref)
        return {c.get("claim_id"): c for c in analysis.get("claims", [])}

    def _sources_index(self, job_id: str, ref: dict) -> dict:
        acquisition = self._read_json_artifact(job_id, ref)
        return {s.get("source_id"): s for s in acquisition.get("sources", [])}

    @staticmethod
    def _record_from_row(row, subject_key: str) -> dict | None:
        if row is None:
            return None
        return {
            "job_id": row["job_id"],
            "subject_id": row[subject_key],
            "decision": row["decision"],
            "reviewed_artifact_type": row["reviewed_artifact_type"],
            "reviewed_artifact_sha256": row["reviewed_artifact_sha256"],
            "reviewer_id": row["reviewer_id"],
            "note": row["note"],
            "revision": row["revision"],
            **({"previous_decision": row["previous_decision"],
                "occurred_at": row["occurred_at"],
                "idempotency_key": row["idempotency_key"]}
               if "occurred_at" in row.keys() else
               {"created_at": row["created_at"], "updated_at": row["updated_at"]}),
        }
