"""Fact-check jobs on the existing workflow queue.

One verification job has one stage, ``factcheck_run``. Results are stored in
``verification_runs``. A subject research job keeps every package version.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.database import connect
from defence_studio.services.jobs import IdempotencyConflict, JobNotFound, JobValidationError
from defence_studio.verification.claims import verify_claims
from defence_studio.verification.media import file_kind, sample_video_frames, verify_media
from defence_studio.verification.package import build_evidence_package, render_markdown, usable_claim_ids
from defence_studio.verification.providers import capability_registry

STAGE = "factcheck_run"
SCHEMA = "defence-studio.factcheck-job.v1"


class FactcheckError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class FactcheckNotFound(FactcheckError):
    def __init__(self, message: str = "Fact-check record not found"):
        super().__init__(message, 404)


class PermanentVerificationError(FactcheckError):
    def __init__(self, message: str):
        super().__init__(message, 422)


class BudgetExceeded(FactcheckError):
    def __init__(self, message: str):
        super().__init__(message, 409)


def authorize_paid(estimated_inr, limit_inr: float) -> dict:
    """Refuse a paid call before it is made. Estimation is not a measured cost."""
    if estimated_inr is None:
        raise BudgetExceeded("Budget guard: cost cannot be estimated, so the paid call was not made.")
    if float(estimated_inr) > float(limit_inr):
        raise BudgetExceeded(
            f"Budget guard: estimated cost INR {float(estimated_inr):.2f} exceeds INR {float(limit_inr):.2f}."
        )
    return {"authorized": True, "estimated_inr": float(estimated_inr), "limit_inr": float(limit_inr),
            "measured": False, "note": "Authorization records the estimate only."}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canon(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def script_gate(connection, job_id: str, package: dict) -> tuple[dict, dict | None]:
    """Narrow a script package to fact-check-cleared claims when a package exists.

    Jobs with no fact-check package keep the existing evidence-approval behaviour.
    A pending or stale fact-check package blocks generation before any paid call.
    """
    row = connection.execute(
        "SELECT * FROM verification_packages WHERE subject_job_id=? ORDER BY version DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if row is None:
        return package, None
    if row["status"] != "APPROVED" or not row["approved_json"]:
        raise FactcheckError(
            "Script generation is blocked until the latest fact-check package has an explicit human approval.",
            409,
        )
    approved = json.loads(row["approved_json"])
    usable = set(approved.get("usable_claim_ids") or [])
    narrowed = dict(package)
    narrowed["eligible_claim_ids"] = [item for item in package.get("eligible_claim_ids") or [] if item in usable]
    if not narrowed["eligible_claim_ids"]:
        raise FactcheckError(
            "The approved fact-check package has no claims cleared for a script.",
            409,
        )
    return narrowed, {"package_id": row["package_id"], "package_sha256": row["content_sha256"]}


def factcheck_staleness(connection, job_id: str, language: str, version: int) -> str | None:
    latest = connection.execute(
        "SELECT status, content_sha256 FROM verification_packages WHERE subject_job_id=? ORDER BY version DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if latest is None:
        return None
    binding = connection.execute(
        "SELECT package_sha256 FROM verification_script_bindings WHERE job_id=? AND language=? AND version=?",
        (job_id, language, version),
    ).fetchone()
    if latest["status"] != "APPROVED" or binding is None or binding["package_sha256"] != latest["content_sha256"]:
        return "Fact-check evidence changed after this script revision, or the revision has no matching approval."
    return None


class FactcheckService:
    def __init__(self, database_file, storage_root):
        self.database_file = Path(database_file)
        self.storage_root = Path(storage_root)
        self.upload_dir = self.storage_root / "factcheck" / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = int(os.environ.get("DEFENCE_STUDIO_FACTCHECK_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))

    def providers(self) -> dict:
        return {"providers": capability_registry()}

    def _owned_subject(self, connection, subject_job_id: str, owner_id: str):
        row = connection.execute(
            "SELECT * FROM workflow_jobs WHERE job_id=? AND owner_id=?",
            (subject_job_id, owner_id),
        ).fetchone()
        if row is None:
            raise FactcheckNotFound("Research job not found")
        return row

    def enqueue(self, owner_id: str, idempotency_key: str, subject_job_id: str, kind: str, payload: dict) -> dict:
        if kind not in {"claims", "media", "package"}:
            raise PermanentVerificationError("kind must be claims, media, or package")
        if not idempotency_key or len(idempotency_key) > 200:
            raise JobValidationError("Idempotency-Key is required (1-200 chars)")
        digest = hashlib.sha256(_canon({"schema": SCHEMA, "kind": kind, **payload}).encode()).hexdigest()
        job_id, now = str(uuid.uuid4()), _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_subject(connection, subject_job_id, owner_id)
                existing = connection.execute(
                    "SELECT job_id, request_hash FROM workflow_jobs WHERE owner_id=? AND idempotency_key=?",
                    (owner_id, idempotency_key),
                ).fetchone()
                if existing:
                    if existing["request_hash"] != digest:
                        raise IdempotencyConflict("Idempotency key was already used with a different request")
                    connection.commit()
                    return self._job_view(connection, existing["job_id"], owner_id)
                connection.execute(
                    "INSERT INTO workflow_jobs(job_id, owner_id, idempotency_key, request_schema, request_hash, "
                    "topic, language, status, created_at, updated_at, queued_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, owner_id, idempotency_key, SCHEMA, digest, f"Fact check {kind}"[:200], "mul",
                     "QUEUED", now, now, now),
                )
                connection.execute(
                    "INSERT INTO workflow_stages(job_id, stage_key, ordinal, status, version, created_at, updated_at) "
                    "VALUES(?,?,0,'PENDING',1,?,?)",
                    (job_id, STAGE, now, now),
                )
                connection.execute(
                    "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                    "VALUES(?,?,'','PENDING',1,?)",
                    (job_id, STAGE, now),
                )
                connection.execute(
                    "INSERT INTO verification_runs(run_id, job_id, subject_job_id, owner_id, kind, input_sha256, "
                    "payload_json, progress_note, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), job_id, subject_job_id, owner_id, kind, digest, _canon(payload),
                     "Queued", now, now),
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return self.get_job(job_id, owner_id)

    def get_job(self, job_id: str, owner_id: str) -> dict:
        with closing(connect(self.database_file)) as connection:
            return self._job_view(connection, job_id, owner_id)

    def _job_view(self, connection, job_id: str, owner_id: str) -> dict:
        row = connection.execute(
            "SELECT j.status AS job_status, j.topic, v.* FROM verification_runs v "
            "JOIN workflow_jobs j ON j.job_id=v.job_id WHERE v.job_id=? AND v.owner_id=?",
            (job_id, owner_id),
        ).fetchone()
        if row is None:
            raise FactcheckNotFound()
        stage = connection.execute(
            "SELECT status, attempt_count, max_attempts, error FROM workflow_stages WHERE job_id=? AND stage_key=?",
            (job_id, STAGE),
        ).fetchone()
        result = json.loads(row["result_json"]) if row["result_json"] else None
        return {
            "job_id": row["job_id"],
            "subject_job_id": row["subject_job_id"],
            "kind": row["kind"],
            "status": row["job_status"],
            "stage": dict(stage) if stage else None,
            "progress_note": row["progress_note"],
            "error_class": row["error_class"],
            "error_detail": row["error_detail"],
            "result": result,
            "result_sha256": row["result_sha256"],
        }

    def run_pending(self, limit: int = 5) -> int:
        from defence_studio.services.worker import Worker

        worker = Worker(
            self.database_file, f"factcheck-{uuid.uuid4()}", self.handlers(),
            retry_safe_stages=set(self.handlers()), lease_seconds=30,
        )
        ran = 0
        while ran < limit and worker.run_once():
            ran += 1
        return ran

    def cancel(self, job_id: str, owner_id: str) -> dict:
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM workflow_jobs WHERE job_id=? AND owner_id=?",
                (job_id, owner_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise FactcheckNotFound()
            if row["status"] == "QUEUED":
                now = _now()
                connection.execute(
                    "UPDATE workflow_jobs SET status='CANCELLED', updated_at=? WHERE job_id=?",
                    (now, job_id),
                )
                connection.execute(
                    "UPDATE workflow_stages SET status='SKIPPED', updated_at=? WHERE job_id=? AND status='PENDING'",
                    (now, job_id),
                )
            connection.commit()
        return self.get_job(job_id, owner_id)

    def latest_package(self, subject_job_id: str, owner_id: str) -> dict:
        with closing(connect(self.database_file)) as connection:
            self._owned_subject(connection, subject_job_id, owner_id)
            rows = connection.execute(
                "SELECT * FROM verification_packages WHERE subject_job_id=? AND owner_id=? ORDER BY version",
                (subject_job_id, owner_id),
            ).fetchall()
        if not rows:
            return {"subject_job_id": subject_job_id, "package": None, "versions": []}
        current = rows[-1]
        return {
            "subject_job_id": subject_job_id,
            "package": self._package_view(current),
            "versions": [{"version": row["version"], "status": row["status"], "content_sha256": row["content_sha256"]} for row in rows],
        }

    def _package_view(self, row) -> dict:
        machine = json.loads(row["machine_json"])
        approved = json.loads(row["approved_json"]) if row["approved_json"] else None
        return {
            "package_id": row["package_id"],
            "version": row["version"],
            "status": row["status"],
            "content_sha256": row["content_sha256"],
            "machine": machine,
            "approved": approved,
            "report_markdown": row["report_markdown"],
            "reviewer_id": row["reviewer_id"],
            "approval_note": row["approval_note"],
            "created_at": row["created_at"],
        }

    def override_claim(self, subject_job_id: str, owner_id: str, package_id: str, claim_id: str,
                       verdict: str, note: str, accepted_for_script: bool) -> dict:
        if verdict not in {"VERIFIED", "PARTIALLY_SUPPORTED", "CONFLICTING", "NEEDS_REVIEW", "UNSUPPORTED", "OUTDATED"}:
            raise PermanentVerificationError("Override verdict is not one of the supported verdicts")
        if not (note or "").strip():
            raise PermanentVerificationError("A human override requires a note")
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM verification_packages WHERE package_id=? AND subject_job_id=? AND owner_id=?",
                (package_id, subject_job_id, owner_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise FactcheckNotFound("Package not found")
            if row["status"] == "APPROVED":
                connection.rollback()
                raise FactcheckError("An approved package is immutable. Run verification again to open a new version.", 409)
            machine = json.loads(row["machine_json"])
            if claim_id not in {item.get("claim_id") for item in machine.get("claims") or []}:
                connection.rollback()
                raise FactcheckNotFound("Claim is not in this package")
            connection.execute(
                "INSERT INTO verification_overrides(package_id, claim_id, verdict, accepted_for_script, note, reviewer_id, created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(package_id, claim_id) DO UPDATE SET verdict=excluded.verdict, "
                "accepted_for_script=excluded.accepted_for_script, note=excluded.note, reviewer_id=excluded.reviewer_id, "
                "created_at=excluded.created_at",
                (package_id, claim_id, verdict, 1 if accepted_for_script else 0, note.strip(), owner_id, now),
            )
            connection.execute(
                "INSERT INTO verification_review_history(history_id, package_id, action, reviewer_id, note, detail_json, occurred_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), package_id, "OVERRIDE", owner_id, note.strip(),
                 _canon({"claim_id": claim_id, "verdict": verdict, "accepted_for_script": accepted_for_script}), now),
            )
            connection.commit()
        return self.latest_package(subject_job_id, owner_id)

    def approve(self, subject_job_id: str, owner_id: str, package_id: str, note: str) -> dict:
        if not (note or "").strip():
            raise PermanentVerificationError("Approval requires a note. Silence is not approval.")
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM verification_packages WHERE package_id=? AND subject_job_id=? AND owner_id=?",
                (package_id, subject_job_id, owner_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise FactcheckNotFound("Package not found")
            if row["status"] == "STALE":
                connection.rollback()
                raise FactcheckError("This package version is stale. Approve the latest version.", 409)
            if row["status"] == "APPROVED":
                connection.commit()
                return self._package_view(row)
            overrides = {
                item["claim_id"]: dict(item)
                for item in connection.execute(
                    "SELECT * FROM verification_overrides WHERE package_id=?", (package_id,)
                ).fetchall()
            }
            machine = json.loads(row["machine_json"])
            approved = dict(machine)
            approved["usable_claim_ids"] = usable_claim_ids(machine, overrides)
            approved["human_approval"] = {"reviewer_id": owner_id, "note": note.strip(), "approved_at": now}
            approved["overrides"] = [
                {"claim_id": key, "verdict": value["verdict"], "note": value["note"],
                 "accepted_for_script": bool(value["accepted_for_script"])}
                for key, value in overrides.items()
            ]
            connection.execute(
                "UPDATE verification_packages SET status='APPROVED', approved_json=?, reviewer_id=?, approval_note=? "
                "WHERE package_id=?",
                (_canon(approved), owner_id, note.strip(), package_id),
            )
            connection.execute(
                "INSERT INTO verification_review_history(history_id, package_id, action, reviewer_id, note, detail_json, occurred_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), package_id, "APPROVE", owner_id, note.strip(),
                 _canon({"usable_claim_ids": approved["usable_claim_ids"]}), now),
            )
            connection.commit()
        return self.latest_package(subject_job_id, owner_id)["package"]

    def history(self, subject_job_id: str, owner_id: str) -> dict:
        with closing(connect(self.database_file)) as connection:
            self._owned_subject(connection, subject_job_id, owner_id)
            rows = connection.execute(
                "SELECT h.* FROM verification_review_history h JOIN verification_packages p ON p.package_id=h.package_id "
                "WHERE p.subject_job_id=? AND p.owner_id=? ORDER BY h.occurred_at",
                (subject_job_id, owner_id),
            ).fetchall()
        return {"history": [dict(row) for row in rows]}

    def store_upload(self, raw: bytes, filename: str) -> Path:
        if not raw:
            raise PermanentVerificationError("Uploaded file is empty")
        if len(raw) > self.max_upload_bytes:
            raise PermanentVerificationError("Uploaded file exceeds the configured size limit")
        safe = os.path.basename(filename or "upload").replace("\\", "")
        dest = self.upload_dir / f"{uuid.uuid4().hex}-{safe[:80]}"
        if self.upload_dir.resolve() not in dest.resolve().parents:
            raise PermanentVerificationError("Upload path escaped the storage root")
        dest.write_bytes(raw)
        return dest

    def handlers(self):
        return {STAGE: self._handle}

    def _set(self, job_id: str, **cols):
        with closing(connect(self.database_file)) as connection:
            sets = ", ".join(f"{key}=?" for key in cols)
            connection.execute(
                f"UPDATE verification_runs SET {sets}, updated_at=? WHERE job_id=?",
                (*cols.values(), _now(), job_id),
            )

    def _handle(self, context, cancel):
        with closing(connect(self.database_file)) as connection:
            row = connection.execute("SELECT * FROM verification_runs WHERE job_id=?", (context.job_id,)).fetchone()
            job = connection.execute("SELECT status FROM workflow_jobs WHERE job_id=?", (context.job_id,)).fetchone()
        if row is None:
            raise PermanentVerificationError("Verification run record is missing")
        if job and job["status"] == "CANCELLED" or cancel.is_set():
            self._set(context.job_id, progress_note="Cancelled")
            return []
        payload = json.loads(row["payload_json"])
        try:
            self._set(context.job_id, progress_note=f"Running {row['kind']}")
            if row["kind"] == "claims":
                result = verify_claims(payload.get("claims") or [], payload.get("sources") or [],
                                       collection=payload.get("collection"), now=_now())
            elif row["kind"] == "media":
                path = payload.get("stored_path")
                if not path or not Path(path).is_file():
                    raise PermanentVerificationError("Media file is missing from approved storage")
                data = Path(path).read_bytes()
                frames = payload.get("frames")
                sampling = None
                if frames is None and file_kind(data).startswith("video/"):
                    sampling = sample_video_frames(path)
                    frames = sampling.get("frames") or []
                registered = None
                asset_id = (payload.get("claim") or {}).get("asset_id") or payload.get("asset_id")
                if asset_id:
                    try:
                        from defence_studio.services.assets import AssetNotFound, AssetService
                        registered = AssetService(self.database_file, self.storage_root).get_asset(
                            row["subject_job_id"], row["owner_id"], asset_id)
                    except AssetNotFound:
                        registered = None
                result = verify_media(data, payload.get("claim") or {}, known=payload.get("known") or [],
                                      frames=frames, filename=payload.get("filename") or "upload",
                                      registered_asset=registered,
                                      live_reverse_search=bool(payload.get("live_reverse_search")))
                if sampling is not None:
                    result["frame_sampling"] = {"available": sampling["available"], "reason": sampling["reason"],
                                                "frame_count": len(sampling.get("frames") or [])}
            elif row["kind"] == "package":
                result = self._build_package(row["subject_job_id"], row["owner_id"], payload)
            else:
                raise PermanentVerificationError(f"Unknown kind {row['kind']}")
            encoded = _canon(result)
            self._set(context.job_id, progress_note="Completed", result_json=encoded,
                      result_sha256=hashlib.sha256(encoded.encode()).hexdigest(), error_class=None, error_detail=None)
            return []
        except PermanentVerificationError as exc:
            self._set(context.job_id, progress_note="Failed", error_class="PERMANENT", error_detail=str(exc))
            with closing(connect(self.database_file)) as connection:
                connection.execute(
                    "UPDATE workflow_stages SET max_attempts=attempt_count WHERE job_id=? AND stage_key=?",
                    (context.job_id, STAGE),
                )
            raise

    def _build_package(self, subject_job_id: str, owner_id: str, payload: dict) -> dict:
        with closing(connect(self.database_file)) as connection:
            claim_row = connection.execute(
                "SELECT result_json FROM verification_runs WHERE subject_job_id=? AND owner_id=? AND kind='claims' "
                "AND result_json IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                (subject_job_id, owner_id),
            ).fetchone()
            media_rows = connection.execute(
                "SELECT result_json FROM verification_runs WHERE subject_job_id=? AND owner_id=? AND kind='media' "
                "AND result_json IS NOT NULL ORDER BY created_at DESC LIMIT 20",
                (subject_job_id, owner_id),
            ).fetchall()
        if claim_row is None and not payload.get("claims_result"):
            raise PermanentVerificationError("No claim-verification result is available to package")
        claims = payload.get("claims_result") or json.loads(claim_row["result_json"])
        media = [json.loads(row["result_json"]) for row in media_rows] if not payload.get("media_results") else payload["media_results"]
        package = build_evidence_package(
            topic=payload.get("topic") or "untitled",
            subject_job_id=subject_job_id,
            claims=claims,
            media=media,
            now=_now(),
        )
        report = render_markdown(package)
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM verification_packages WHERE subject_job_id=?",
                (subject_job_id,),
            ).fetchone()[0]
            same = connection.execute(
                "SELECT package_id, version FROM verification_packages WHERE subject_job_id=? AND content_sha256=?",
                (subject_job_id, package["content_sha256"]),
            ).fetchone()
            if same:
                connection.commit()
                package["package_id"] = same["package_id"]
                package["version"] = same["version"]
                package["report_markdown"] = report
                return package
            connection.execute(
                "UPDATE verification_packages SET status='STALE' WHERE subject_job_id=? AND status IN ('PENDING_REVIEW','APPROVED')",
                (subject_job_id,),
            )
            package_id = str(uuid.uuid4())
            version = int(current) + 1
            connection.execute(
                "INSERT INTO verification_packages(package_id, subject_job_id, owner_id, version, content_sha256, "
                "status, machine_json, report_markdown, created_at) VALUES(?,?,?,?,?,'PENDING_REVIEW',?,?,?)",
                (package_id, subject_job_id, owner_id, version, package["content_sha256"], _canon(package), report, now),
            )
            connection.commit()
        package["package_id"] = package_id
        package["version"] = version
        package["report_markdown"] = report
        return package
