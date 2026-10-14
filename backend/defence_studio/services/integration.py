"""Durable state for cross-module integration artifacts (RM-3..RM-7).

One generic, additive table (`integration_artifacts`, schema v8) records each
output of a standalone module (visual timeline, audio, alignment, render
package, render, QC report, publish package) with the exact upstream hashes it
was built from (`input_binding`). SQLite is the single source of truth; bytes
live in the artifact store and are identified by SHA-256. Work runs in
backend threads, never in the browser request; a restart marks any
QUEUED/RUNNING row INTERRUPTED so nothing is silently lost or silently
re-run (callers retry explicitly, idempotently).
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone

from defence_studio.contracts.evidence import canonical
from defence_studio.database import connect
from defence_studio.services.assets import AssetService
from defence_studio.services.review import ReviewError, ReviewNotFound, ReviewValidationError, StaleRevision

TERMINAL = ("SUCCEEDED", "FAILED", "INTERRUPTED", "CANCELLED", "APPROVED", "REJECTED", "SUPERSEDED")
ACTIVE = ("QUEUED", "RUNNING")


class IntegrationError(ReviewError):
    status_code = 502


class IntegrationNotFound(ReviewNotFound):
    pass


class IntegrationValidationError(ReviewValidationError):
    pass


class IntegrationStale(StaleRevision):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def binding_digest(binding: dict) -> str:
    return hashlib.sha256(canonical(binding)).hexdigest()


class IntegrationService(AssetService):
    # -- row access -----------------------------------------------------------

    def _row_view(self, row) -> dict:
        view = dict(row)
        view["input_binding"] = json.loads(view["input_binding"])
        return view

    def _integration_rows(self, job_id: str, language: str | None, kind: str | None) -> list[dict]:
        query, params = "SELECT * FROM integration_artifacts WHERE job_id=?", [job_id]
        if language:
            query += " AND language=?"; params.append(language)
        if kind:
            query += " AND kind=?"; params.append(kind)
        with closing(connect(self.database_file)) as connection:
            return [self._row_view(r) for r in connection.execute(query + " ORDER BY language, kind, version", params)]

    def list_integration(self, job_id: str, owner_id: str, language: str | None = None,
                         kind: str | None = None) -> dict:
        self._check_ownership(job_id, owner_id)
        return {"job_id": job_id, "items": self._integration_rows(job_id, language, kind)}

    def get_integration(self, job_id: str, owner_id: str, language: str, kind: str, version: int) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT * FROM integration_artifacts WHERE job_id=? AND language=? AND kind=? AND version=?",
                (job_id, language, kind, version)).fetchone()
        if row is None:
            raise IntegrationNotFound("Unknown integration artifact")
        return self._row_view(row)

    def integration_events(self, job_id: str, owner_id: str, language: str, kind: str, version: int) -> list[dict]:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            return [dict(r) for r in connection.execute(
                "SELECT * FROM integration_events WHERE job_id=? AND language=? AND kind=? AND version=? "
                "ORDER BY occurred_at, rowid", (job_id, language, kind, version))]

    def read_integration_body(self, job_id: str, owner_id: str, language: str, kind: str, version: int) -> dict:
        row = self.get_integration(job_id, owner_id, language, kind, version)
        if not row.get("uri") or not row.get("sha256"):
            raise IntegrationNotFound("This artifact has no stored output (status %s)" % row["status"])
        return self._read_json_artifact(job_id, row)

    def _approved_plan(self, job_id: str, owner_id: str, language: str) -> tuple[dict, dict]:
        """Current APPROVED plan (row + body), re-checked against the current
        approved script. Fails closed (409) when there is none or it is stale."""
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND status='APPROVED'",
                (job_id, language)).fetchone()
        if row is None:
            raise IntegrationStale("No approved scene plan for this job/language; approve a plan first")
        row = dict(row)
        export = self.export_approved(job_id, owner_id, language)  # raises if script no longer current
        if export["approval"]["revision_sha256"] != row["script_revision_sha256"]:
            raise IntegrationStale("The approved scene plan was built on a superseded script; re-sync it first")
        return row, self._read_json_artifact(job_id, row)

    # -- state machine --------------------------------------------------------

    def _create_row(self, job_id: str, owner_id: str, language: str, kind: str, binding: dict,
                    idempotency_key: str, *, reuse_active: bool = True) -> tuple[dict, bool]:
        """Insert a QUEUED row. Returns (row, created). A duplicate idempotency
        key, or an identical binding already SUCCEEDED/active, replays that row
        instead of duplicating work (no double spend, no duplicate output)."""
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        bind_json = canonical(binding).decode("utf-8")
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT * FROM integration_artifacts WHERE job_id=? AND language=? AND kind=? AND idempotency_key=?",
                    (job_id, language, kind, idempotency_key)).fetchone()
                if replay is not None:
                    connection.commit()
                    return self._row_view(replay), False
                same = connection.execute(
                    "SELECT * FROM integration_artifacts WHERE job_id=? AND language=? AND kind=? AND input_binding=? "
                    "AND status IN ('QUEUED','RUNNING','SUCCEEDED','AWAITING_REVIEW','APPROVED') "
                    "ORDER BY version DESC LIMIT 1", (job_id, language, kind, bind_json)).fetchone()
                if same is not None and reuse_active:
                    connection.commit()
                    return self._row_view(same), False
                version = connection.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM integration_artifacts WHERE job_id=? AND language=? AND kind=?",
                    (job_id, language, kind)).fetchone()[0]
                now = _now()
                connection.execute(
                    "INSERT INTO integration_artifacts(job_id,language,kind,version,status,uri,sha256,input_binding,"
                    "detail,progress,created_by,created_at,updated_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, language, kind, version, "QUEUED", None, None, bind_json, "", 0.0, owner_id, now, now,
                     idempotency_key))
                self._event(connection, job_id, language, kind, version, "QUEUED", "", owner_id)
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM integration_artifacts WHERE job_id=? AND language=? AND kind=? AND version=?",
                    (job_id, language, kind, version)).fetchone()
                return self._row_view(row), True
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _event(connection, job_id, language, kind, version, event, detail, actor) -> None:
        connection.execute(
            "INSERT INTO integration_events VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), job_id, language, kind, version, event, detail[:2000], actor, _now()))

    def _transition(self, job_id: str, language: str, kind: str, version: int, status: str, *,
                    detail: str = "", progress: float | None = None, uri: str | None = None,
                    sha256: str | None = None, actor: str = "system", allowed_from: tuple[str, ...] | None = None) -> None:
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status FROM integration_artifacts WHERE job_id=? AND language=? AND kind=? AND version=?",
                    (job_id, language, kind, version)).fetchone()
                if row is None:
                    raise IntegrationNotFound("Unknown integration artifact")
                if allowed_from is not None and row["status"] not in allowed_from:
                    raise IntegrationStale(f"Cannot move {kind} v{version} from {row['status']} to {status}")
                sets, params = ["status=?", "detail=?", "updated_at=?"], [status, detail[:2000], _now()]
                if progress is not None:
                    sets.append("progress=?"); params.append(progress)
                if uri is not None:
                    sets.append("uri=?"); params.append(uri)
                if sha256 is not None:
                    sets.append("sha256=?"); params.append(sha256)
                params += [job_id, language, kind, version]
                connection.execute(
                    f"UPDATE integration_artifacts SET {', '.join(sets)} WHERE job_id=? AND language=? AND kind=? AND version=?",
                    params)
                self._event(connection, job_id, language, kind, version, status, detail, actor)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _store_json(self, job_id: str, relative: str, body: dict) -> tuple[str, str]:
        data = canonical(body)
        sha = hashlib.sha256(data).hexdigest()
        uri = f"jobs/{job_id}/{relative}/{sha}.json"
        self.store.write_bytes(job_id, uri, data, "application/json")
        return uri, sha

    # -- recovery / execution -------------------------------------------------

    def recover_interrupted(self) -> int:
        """Startup sweep: rows left QUEUED/RUNNING by a dead process become
        INTERRUPTED (explicit, auditable); they are never silently resumed."""
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id,language,kind,version FROM integration_artifacts WHERE status IN ('QUEUED','RUNNING')"
            ).fetchall()
            now = _now()
            for r in rows:
                connection.execute(
                    "UPDATE integration_artifacts SET status='INTERRUPTED', detail=?, updated_at=? "
                    "WHERE job_id=? AND language=? AND kind=? AND version=?",
                    ("Process stopped before completion; retry to run again.", now, r["job_id"], r["language"],
                     r["kind"], r["version"]))
                self._event(connection, r["job_id"], r["language"], r["kind"], r["version"], "INTERRUPTED",
                            "Recovered at startup", "system")
            connection.commit()
            return len(rows)

    def _launch(self, fn, background: bool) -> threading.Thread | None:
        if not background:
            fn()
            return None
        thread = threading.Thread(target=fn, daemon=True, name="integration-runner")
        thread.start()
        return thread
