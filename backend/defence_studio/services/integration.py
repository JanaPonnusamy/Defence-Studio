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
from defence_studio.contracts.assets import PermittedUse
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

    # -- asset byte ingestion (rights-reviewed stills) ---------------------------

    MAX_ASSET_BYTES = 15 * 1024 * 1024
    _IMG_EXT = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}

    def ingest_asset_upload(self, job_id: str, owner_id: str, data: bytes, metadata: dict,
                            idempotency_key: str) -> dict:
        """Store real image bytes (verified decodable still: PNG/JPEG/WebP),
        measure SHA-256, and register a DRAFT asset descriptor with MEASURED
        content. Ingestion is never approval: rights review + assignment follow
        through the existing asset service."""
        import io
        from PIL import Image
        self._check_ownership(job_id, owner_id)
        if not data or len(data) > self.MAX_ASSET_BYTES:
            raise IntegrationValidationError(f"Asset must be 1 byte to {self.MAX_ASSET_BYTES // (1024 * 1024)} MiB")
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                fmt, size = im.format, im.size
        except Exception:
            raise IntegrationValidationError("The file is not a valid, complete image") from None
        if fmt not in self._IMG_EXT:
            raise IntegrationValidationError(f"Unsupported image format {fmt!r}; use PNG, JPEG or WebP")
        ext, mime = self._IMG_EXT[fmt]
        title = (metadata.get("title") or "").strip()
        if not title:
            raise IntegrationValidationError("An asset title is required")
        sha = hashlib.sha256(data).hexdigest()
        uri = f"jobs/{job_id}/assets/files/{sha}.{ext}"
        self.store.write_bytes(job_id, uri, data, mime)
        declared = lambda v: ({"value": v, "status": "COLLECTED"} if v else {"value": None, "status": "NOT_COLLECTED"})
        descriptor = {
            "kind": "image", "title": title, "description": (metadata.get("description") or "")[:4000],
            "content": {"status": "MEASURED", "content_sha256": sha, "media_type": mime, "byte_size": len(data)},
            "provenance": {"source_url": metadata.get("source_url") or None,
                           "documented_origin": metadata.get("documented_origin") or None,
                           "source_title": declared(metadata.get("source_title")),
                           "creator": declared(metadata.get("creator")),
                           "publisher": declared(metadata.get("publisher")),
                           "acquired_at": _now()},
            "review_status": "DRAFT",
        }
        asset = self.register_asset(job_id, owner_id, descriptor, idempotency_key)
        return {**asset, "file": {"uri": uri, "sha256": sha, "media_type": mime, "width": size[0], "height": size[1]}}

    def asset_file_uri(self, job_id: str, descriptor: dict) -> str | None:
        c = descriptor.get("content") or {}
        if c.get("status") != "MEASURED":
            return None
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(c.get("media_type"))
        return f"jobs/{job_id}/assets/files/{c['content_sha256']}.{ext}" if ext else None

    def assigned_assets(self, job_id: str, language: str, plan_sha256: str) -> list[dict]:
        """ACTIVE assignments on the current approved plan whose asset has real
        bytes, an APPROVED review and current rights permitting commercial web use."""
        with closing(connect(self.database_file)) as c:
            rows = c.execute(
                "SELECT a.scene_id, a.asset_id, a.version FROM asset_assignments a WHERE a.job_id=? AND a.language=? "
                "AND a.scene_plan_sha256=? AND a.status='ACTIVE' ORDER BY a.scene_id, a.asset_id, a.version",
                (job_id, language, plan_sha256)).fetchall()
            latest: dict = {}
            for r in rows:
                latest[(r["scene_id"], r["asset_id"])] = r
            out: dict = {}
            for (scene_id, asset_id), _r in latest.items():
                ar = c.execute("SELECT * FROM assets WHERE job_id=? AND asset_id=?", (job_id, asset_id)).fetchone()
                if ar is None or ar["review_status"] != "APPROVED" or ar["effective_rights"] not in ("CLEAR", "RESTRICTED"):
                    continue
                entry = out.setdefault(asset_id, {"asset_id": asset_id, "scene_ids": [], "row": dict(ar)})
                entry["scene_ids"].append(scene_id)
        result = []
        for asset_id, e in out.items():
            descriptor = self._read_json_artifact(job_id, e["row"])
            if self._derive_effective_rights(descriptor, target_use=PermittedUse.WEB).value != "CLEAR":
                continue  # Expiry must be evaluated at use time, not cached at review time.
            uri = self.asset_file_uri(job_id, descriptor)
            if not uri:
                continue
            rights = None
            if e["row"].get("rights_sha256"):
                with closing(connect(self.database_file)) as c:
                    rr = c.execute("SELECT * FROM asset_rights WHERE job_id=? AND asset_id=? AND rights_sha256=?",
                                   (job_id, asset_id, e["row"]["rights_sha256"])).fetchone()
                rights = self._read_json_artifact(job_id, dict(rr)) if rr else None
            result.append({"asset_id": asset_id, "scene_ids": sorted(e["scene_ids"]), "descriptor": descriptor,
                           "rights": rights, "uri": uri, "asset_sha256": e["row"]["sha256"],
                           "effective_rights": e["row"]["effective_rights"]})
        return sorted(result, key=lambda x: x["asset_id"])

    def verify_visual_asset_rights(self, job_id: str, language: str, visual_binding: dict) -> None:
        """Reject a timeline whose imported assets lost their reviewed rights/assignment."""
        expected = visual_binding.get("assets", [])
        if not expected:
            return  # A timeline of explicit GAP cards contains no imported assets.
        current = {a["asset_id"]: a for a in self.assigned_assets(
            job_id, language, visual_binding["scene_plan_sha256"])}
        for old in expected:
            asset = current.get(old["asset_id"])
            if (asset is None or asset["asset_sha256"] != old["sha256"]
                    or not set(old["scenes"]).issubset(asset["scene_ids"])):
                raise IntegrationStale("Visual asset rights or assignments changed; rebuild the visual timeline")

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
