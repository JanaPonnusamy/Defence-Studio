"""Phase 5 M2.1 asset-management & rights-provenance service (reference-only).

Turns the accepted M2.0 contracts (`contracts/assets.py`) into a working,
human-paced service: register reference-only assets, attach immutable rights
revisions, deterministically re-evaluate effective rights, assign an asset to a
scene of an **approved** scene plan (pinned to that plan's exact hash/version/
scene_id), and record an auditable human rights decision. Synchronous HTTP like
F5/Phase 2/3/M1 -- **no worker stage, no byte download, no provider fetch**: an
asset is a reference until a later, separately-authorized milestone measures it.

`AssetService` subclasses `SceneService` (-> `ScriptService` -> `EvidenceService`
-> `ReviewService`) purely to reuse ownership checks, the artifact byte store,
the note / idempotency-key validators, JSON-artifact integrity reads, and the
approved-scene-plan accessor. No second execution architecture.

Guarantees enforced here (see contracts/assets.py for the contract rationale):

* **Registration is never approval.** A registered asset is `DRAFT`; only an
  explicit `review_asset(...APPROVED...)` reaches an approved state, and only
  when the re-derived effective rights allow it.
* **Rights verdicts are never trusted blindly.** `effective_rights` is always
  re-derived from the stored rights record via the pure
  `evaluate_effective_rights` predicate -- at register, set-rights, assign,
  review and read time -- never read back from a client-supplied value.
* **Assignments pin the exact approved plan.** An assignment binds to the
  current APPROVED scene plan's `plan_sha256` + `version` + a `scene_id` that
  actually exists in it; a later plan re-approval is detectable (`STALE_PLAN`).
* **Approval binds to exact digests + optimistic concurrency.** A decision is
  pinned to the exact asset+rights digests the reviewer saw (stale -> 409) and
  to an `expected_revision` (conflict -> 409); duplicates replay; current state
  and an append-only history row are written in the same transaction.
* **Unknown / expired / blocked rights fail closed** and can never be approved
  or assigned for a commercial render.
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import closing
from datetime import datetime, timezone

from pydantic import ValidationError

from defence_studio.contracts.evidence import canonical
from defence_studio.contracts.assets import (
    ASSET_ASSIGNMENT_SCHEMA, ASSET_DESCRIPTOR_SCHEMA, RIGHTS_RECORD_SCHEMA,
    AssetAssignmentV1, AssetDescriptorV1, AssetProvenanceV1, AssetReviewStatus,
    EffectiveRights, PermittedUse, RightsRecordV1, asset_digest, assignment_digest,
    evaluate_effective_rights, rights_digest,
)
from defence_studio.database import connect
from defence_studio.services.review import (
    ReviewNotFound, ReviewValidationError, RevisionConflict, StaleRevision,
)
from defence_studio.services.scene import SceneService

__all__ = [
    "AssetService", "AssetError", "AssetNotFound", "AssetValidationError",
    "AssetStaleRevision", "AssetRevisionConflict",
]


class AssetError(ReviewValidationError):
    pass


class AssetNotFound(ReviewNotFound):
    pass


class AssetValidationError(ReviewValidationError):
    pass


class AssetStaleRevision(StaleRevision):
    pass


class AssetRevisionConflict(RevisionConflict):
    pass


REVIEW_DECISIONS = ("APPROVED", "REJECTED", "RESTRICTED", "CHANGES_REQUESTED")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _as_validation_error(exc: ValidationError) -> AssetValidationError:
    return AssetValidationError(f"Asset payload failed contract validation: {exc.errors()[:3]}")


class AssetService(SceneService):
    """Reference-only asset registration, rights evaluation, scene assignment,
    and human rights review. Reuses `SceneService` machinery; adds no fetch."""

    # -- rights derivation (never trusts a stored/claimed verdict) ------------

    def _derive_effective_rights(self, descriptor: dict, *, now_iso: str | None = None,
                                 for_commercial: bool = True,
                                 target_use: PermittedUse | None = None) -> EffectiveRights:
        rights_raw = descriptor.get("rights")
        prov_raw = descriptor.get("provenance")
        rights = RightsRecordV1.model_validate(rights_raw) if rights_raw else None
        provenance = AssetProvenanceV1.model_validate(prov_raw) if prov_raw else None
        return evaluate_effective_rights(
            rights, provenance, now_iso=now_iso or _now(),
            for_commercial=for_commercial, target_use=target_use,
        )

    # -- registration & retrieval (reference-only) ----------------------------

    def register_asset(self, job_id: str, owner_id: str, descriptor_input: dict,
                       idempotency_key: str) -> dict:
        """Register a reference-only asset. The caller supplies a descriptor body
        *without* a trustworthy `asset_id`; the service computes it as the digest
        of the id-less body so byte-identical descriptors dedup. Never downloads
        bytes; content stays REFERENCED_ONLY. Starts DRAFT -- never approved."""
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        self._check_ownership(job_id, owner_id)
        if not isinstance(descriptor_input, dict):
            raise AssetValidationError("Asset descriptor must be an object")

        # Compute the content-addressed id over the id-less body, then validate
        # the full descriptor (which requires the id field to be present).
        body = {k: v for k, v in descriptor_input.items() if k != "asset_id"}
        body.setdefault("schema_version", ASSET_DESCRIPTOR_SCHEMA)
        body["job_id"] = job_id
        body.setdefault("review_status", AssetReviewStatus.DRAFT.value)
        asset_id = asset_digest(body)
        candidate = {**body, "asset_id": asset_id}
        try:
            descriptor = AssetDescriptorV1.model_validate(candidate).model_dump(mode="json")
        except ValidationError as exc:
            raise _as_validation_error(exc) from exc

        effective = self._derive_effective_rights(descriptor)
        data = canonical(descriptor)
        sha = hashlib.sha256(data).hexdigest()
        uri = f"jobs/{job_id}/assets/{asset_id}.json"

        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._check_ownership_locked(connection, job_id, owner_id)
                replay = connection.execute(
                    "SELECT * FROM assets WHERE job_id=? AND idempotency_key=?",
                    (job_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    connection.commit()
                    return self.get_asset(job_id, owner_id, replay["asset_id"])
                existing = connection.execute(
                    "SELECT * FROM assets WHERE job_id=? AND asset_id=?",
                    (job_id, asset_id),
                ).fetchone()
                if existing is not None:
                    # Byte-identical descriptor already registered under another
                    # key: dedup to the existing row rather than creating a twin.
                    connection.commit()
                    return self.get_asset(job_id, owner_id, asset_id)
                self.store.write_bytes(job_id, uri, data, "application/json")
                now = _now()
                connection.execute(
                    "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, asset_id, descriptor["kind"], uri, sha,
                     descriptor["review_status"], effective.value, None,
                     owner_id, now, now, idempotency_key),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_asset(job_id, owner_id, asset_id)

    def get_asset(self, job_id: str, owner_id: str, asset_id: str) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE job_id=? AND asset_id=?", (job_id, asset_id),
            ).fetchone()
        if row is None:
            raise AssetNotFound("Unknown asset for this job")
        descriptor = self._read_json_artifact(job_id, dict(row))
        return {
            "asset_id": asset_id, "job_id": job_id, "kind": row["kind"],
            "review_status": row["review_status"], "effective_rights": row["effective_rights"],
            "rights_sha256": row["rights_sha256"], "descriptor": descriptor,
            "asset_sha256": row["sha256"], "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_assets(self, job_id: str, owner_id: str, *, review_status: str | None = None,
                    effective_rights: str | None = None, kind: str | None = None) -> dict:
        self._check_ownership(job_id, owner_id)
        query = "SELECT asset_id, kind, review_status, effective_rights, rights_sha256, created_at, updated_at FROM assets WHERE job_id=?"
        params: list = [job_id]
        if review_status:
            query += " AND review_status=?"; params.append(review_status)
        if effective_rights:
            query += " AND effective_rights=?"; params.append(effective_rights)
        if kind:
            query += " AND kind=?"; params.append(kind)
        with closing(connect(self.database_file)) as connection:
            rows = [dict(r) for r in connection.execute(query + " ORDER BY created_at", params)]
        return {"job_id": job_id, "assets": rows}

    # -- immutable rights revisions + re-evaluation ---------------------------

    def set_rights(self, job_id: str, owner_id: str, asset_id: str, rights_input: dict,
                   idempotency_key: str) -> dict:
        """Attach an immutable rights revision and re-derive effective rights.
        Setting rights never approves the asset; a prior APPROVED decision is
        invalidated (back to PENDING_REVIEW) because the reviewed rights changed."""
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        self._check_ownership(job_id, owner_id)
        try:
            rights = RightsRecordV1.model_validate({**rights_input, "schema_version":
                                                    rights_input.get("schema_version", RIGHTS_RECORD_SCHEMA)})
            rights_body = rights.model_dump(mode="json")
        except ValidationError as exc:
            raise _as_validation_error(exc) from exc
        r_sha = rights_digest(rights_body)
        # Keep the stored path well under Windows MAX_PATH: the full hashes live
        # in the DB rows; the filesystem name uses bounded prefixes only.
        r_uri = f"jobs/{job_id}/assets/{asset_id[:16]}/rights-{r_sha[:16]}.json"

        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._check_ownership_locked(connection, job_id, owner_id)
                row = connection.execute(
                    "SELECT * FROM assets WHERE job_id=? AND asset_id=?", (job_id, asset_id),
                ).fetchone()
                if row is None:
                    raise AssetNotFound("Unknown asset for this job")
                descriptor = self._read_json_artifact(job_id, dict(row))
                descriptor = {**descriptor, "rights": rights_body}
                # Re-derive the verdict from the new rights record.
                effective = self._derive_effective_rights(descriptor)
                # Persist the new descriptor (now carrying rights) + rights revision.
                data = canonical(descriptor)
                d_sha = hashlib.sha256(data).hexdigest()
                self.store.write_bytes(job_id, row["uri"], data, "application/json")
                if not self.store.exists(job_id, r_uri):
                    self.store.write_bytes(job_id, r_uri, canonical(rights_body), "application/json")
                connection.execute(
                    "INSERT OR IGNORE INTO asset_rights VALUES(?,?,?,?,?,?)",
                    (job_id, asset_id, r_sha, r_uri, hashlib.sha256(canonical(rights_body)).hexdigest(), _now()),
                )
                # Setting rights invalidates a prior approval (the reviewed rights changed).
                new_status = row["review_status"]
                if new_status == AssetReviewStatus.APPROVED.value:
                    new_status = AssetReviewStatus.PENDING_REVIEW.value
                connection.execute(
                    "UPDATE assets SET sha256=?, rights_sha256=?, effective_rights=?, review_status=?, updated_at=? "
                    "WHERE job_id=? AND asset_id=?",
                    (d_sha, r_sha, effective.value, new_status, _now(), job_id, asset_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_asset(job_id, owner_id, asset_id)

    # -- scene assignment (bound to the exact approved plan) ------------------

    def _current_approved_plan(self, connection, job_id: str, language: str) -> dict | None:
        row = connection.execute(
            "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND status='APPROVED'",
            (job_id, language),
        ).fetchone()
        return dict(row) if row else None

    def assign_asset(self, job_id: str, owner_id: str, language: str, scene_id: str,
                     asset_id: str, idempotency_key: str, *, target_use: str | None = None,
                     for_commercial: bool = True) -> dict:
        """Bind an asset to a scene of the current APPROVED scene plan. Fails
        closed: no approved plan -> 409; unknown scene_id -> 422; a non-CLEAR
        effective-rights verdict refuses assignment (RESTRICTED is allowed only
        for its permitted use). Idempotent per (job, language, key)."""
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        self._check_ownership(job_id, owner_id)
        if language not in ("ta", "en"):
            raise AssetValidationError("Unsupported language")
        tu = PermittedUse(target_use) if target_use else None

        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._check_ownership_locked(connection, job_id, owner_id)
                replay = connection.execute(
                    "SELECT * FROM asset_assignments WHERE job_id=? AND language=? AND idempotency_key=?",
                    (job_id, language, idempotency_key),
                ).fetchone()
                if replay is not None:
                    connection.commit()
                    return self._assignment_view(dict(replay))

                plan_row = self._current_approved_plan(connection, job_id, language)
                if plan_row is None:
                    raise AssetStaleRevision("No approved scene plan for this job/language; approve a plan first")
                plan = self._read_json_artifact(job_id, plan_row)
                scene_ids = {s["scene_id"] for s in plan["scenes"]}
                if scene_id not in scene_ids:
                    raise AssetValidationError(f"scene_id {scene_id!r} is not in the approved scene plan")

                asset_row = connection.execute(
                    "SELECT * FROM assets WHERE job_id=? AND asset_id=?", (job_id, asset_id),
                ).fetchone()
                if asset_row is None:
                    raise AssetNotFound("Unknown asset for this job")
                descriptor = self._read_json_artifact(job_id, dict(asset_row))
                effective = self._derive_effective_rights(
                    descriptor, for_commercial=for_commercial, target_use=tu)
                if effective not in (EffectiveRights.CLEAR, EffectiveRights.RESTRICTED):
                    raise AssetValidationError(
                        f"Asset cannot be assigned: effective rights are {effective.value} "
                        "(unknown/expired/blocked rights fail closed)"
                    )

                version = connection.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM asset_assignments "
                    "WHERE job_id=? AND language=? AND scene_id=? AND asset_id=?",
                    (job_id, language, scene_id, asset_id),
                ).fetchone()[0]
                now = _now()
                assignment = {
                    "schema_version": ASSET_ASSIGNMENT_SCHEMA, "job_id": job_id,
                    "language": language, "version": version,
                    "scene_plan_sha256": plan_row["plan_sha256"],
                    "scene_plan_version": plan_row["version"], "scene_id": scene_id,
                    "asset_id": asset_id, "effective_rights": effective.value,
                    "target_use": tu.value if tu else None, "created_at": now,
                }
                try:
                    assignment = AssetAssignmentV1.model_validate(assignment).model_dump(mode="json")
                except ValidationError as exc:
                    raise _as_validation_error(exc) from exc
                a_sha = assignment_digest(assignment)
                a_uri = f"jobs/{job_id}/assets/assignments/{language}/{scene_id}-{asset_id[:16]}-{version}.json"
                self.store.write_bytes(job_id, a_uri, canonical(assignment), "application/json")
                # Supersede any prior ACTIVE assignment of this asset to this scene.
                connection.execute(
                    "UPDATE asset_assignments SET status='REMOVED' "
                    "WHERE job_id=? AND language=? AND scene_id=? AND asset_id=? AND status='ACTIVE'",
                    (job_id, language, scene_id, asset_id),
                )
                connection.execute(
                    "INSERT INTO asset_assignments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, language, scene_id, asset_id, version, a_sha, a_uri,
                     plan_row["plan_sha256"], plan_row["version"], effective.value,
                     tu.value if tu else None, "ACTIVE", owner_id, now, idempotency_key),
                )
                connection.commit()
                return self._assignment_view(connection.execute(
                    "SELECT * FROM asset_assignments WHERE job_id=? AND language=? AND scene_id=? AND asset_id=? AND version=?",
                    (job_id, language, scene_id, asset_id, version),
                ).fetchone())
            except BaseException:
                connection.rollback()
                raise

    def _assignment_view(self, row) -> dict:
        row = dict(row)
        return {
            "job_id": row["job_id"], "language": row["language"], "scene_id": row["scene_id"],
            "asset_id": row["asset_id"], "version": row["version"],
            "assignment_sha256": row["assignment_sha256"],
            "scene_plan_sha256": row["scene_plan_sha256"],
            "scene_plan_version": row["scene_plan_version"],
            "effective_rights": row["effective_rights"], "target_use": row["target_use"],
            "status": row["status"], "created_at": row["created_at"],
        }

    def list_assignments(self, job_id: str, owner_id: str, language: str,
                         *, active_only: bool = True) -> dict:
        self._check_ownership(job_id, owner_id)
        query = "SELECT * FROM asset_assignments WHERE job_id=? AND language=?"
        params: list = [job_id, language]
        if active_only:
            query += " AND status='ACTIVE'"
        with closing(connect(self.database_file)) as connection:
            rows = [self._assignment_view(r) for r in connection.execute(query + " ORDER BY scene_id, version", params)]
            plan_row = self._current_approved_plan(connection, job_id, language)
        current_plan_sha = plan_row["plan_sha256"] if plan_row else None
        for r in rows:
            r["stale_plan"] = (current_plan_sha is None) or (r["scene_plan_sha256"] != current_plan_sha)
        return {"job_id": job_id, "language": language,
                "current_approved_plan_sha256": current_plan_sha, "assignments": rows}

    def remove_assignment(self, job_id: str, owner_id: str, language: str, scene_id: str,
                          asset_id: str, idempotency_key: str) -> dict:
        """Deactivate the current ACTIVE assignment of an asset to a scene. Never
        rewrites the scene plan or the script; never re-approves rights."""
        self._validate_idempotency_key(idempotency_key)
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._check_ownership_locked(connection, job_id, owner_id)
                cur = connection.execute(
                    "UPDATE asset_assignments SET status='REMOVED' "
                    "WHERE job_id=? AND language=? AND scene_id=? AND asset_id=? AND status='ACTIVE'",
                    (job_id, language, scene_id, asset_id),
                )
                connection.commit()
                removed = cur.rowcount
            except BaseException:
                connection.rollback()
                raise
        return {"job_id": job_id, "language": language, "scene_id": scene_id,
                "asset_id": asset_id, "removed": removed}

    # -- human rights review + append-only audit ------------------------------

    def review_asset(self, job_id: str, owner_id: str, asset_id: str, decision: str,
                     reviewed_asset_sha256: str, reviewed_rights_sha256: str,
                     expected_revision: int, note: str, idempotency_key: str) -> dict:
        """Record an explicit human rights decision, pinned to the exact asset +
        rights digests the reviewer saw (stale -> 409) and to `expected_revision`
        (conflict -> 409). Duplicate (asset, reviewer, key) replays. Current state
        and an append-only history row are written in the same transaction. An
        asset whose re-derived rights are not CLEAR/RESTRICTED cannot be APPROVED."""
        note = self._validate_note(note)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        self._check_ownership(job_id, owner_id)
        if decision not in REVIEW_DECISIONS:
            raise AssetValidationError(f"decision must be one of {REVIEW_DECISIONS}")

        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._check_ownership_locked(connection, job_id, owner_id)
                replay = connection.execute(
                    "SELECT * FROM asset_review_history WHERE job_id=? AND asset_id=? AND reviewer_id=? AND idempotency_key=?",
                    (job_id, asset_id, owner_id, idempotency_key),
                ).fetchone()
                row = connection.execute(
                    "SELECT * FROM assets WHERE job_id=? AND asset_id=?", (job_id, asset_id),
                ).fetchone()
                if row is None:
                    raise AssetNotFound("Unknown asset for this job")
                if replay is not None:
                    connection.commit()
                    return self.get_asset(job_id, owner_id, asset_id)

                # Stale-digest check: the reviewer must have seen the current
                # asset descriptor and rights record.
                if row["sha256"] != reviewed_asset_sha256:
                    raise AssetStaleRevision("reviewed_asset_sha256 does not match the current asset; reload")
                if (row["rights_sha256"] or "") != reviewed_rights_sha256:
                    raise AssetStaleRevision("reviewed_rights_sha256 does not match the current rights; reload")

                current = connection.execute(
                    "SELECT revision FROM asset_reviews WHERE job_id=? AND asset_id=?",
                    (job_id, asset_id),
                ).fetchone()
                current_rev = current["revision"] if current else 0
                if expected_revision != current_rev:
                    raise AssetRevisionConflict(
                        f"expected_revision {expected_revision} != current {current_rev}; another decision raced this"
                    )

                # Re-derive the verdict; an APPROVED decision requires CLEAR/RESTRICTED.
                descriptor = self._read_json_artifact(job_id, dict(row))
                effective = self._derive_effective_rights(descriptor)
                if decision == "APPROVED" and effective not in (EffectiveRights.CLEAR, EffectiveRights.RESTRICTED):
                    raise AssetValidationError(
                        f"Cannot approve: effective rights are {effective.value} (fail closed)"
                    )

                status_map = {"APPROVED": AssetReviewStatus.APPROVED.value,
                              "REJECTED": AssetReviewStatus.REJECTED.value,
                              "RESTRICTED": AssetReviewStatus.RESTRICTED.value,
                              "CHANGES_REQUESTED": AssetReviewStatus.PENDING_REVIEW.value}
                now = _now()
                new_rev = current_rev + 1
                connection.execute(
                    "INSERT INTO asset_reviews VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(job_id, asset_id) DO UPDATE SET decision=excluded.decision, "
                    "reviewed_asset_sha256=excluded.reviewed_asset_sha256, "
                    "reviewed_rights_sha256=excluded.reviewed_rights_sha256, "
                    "reviewer_id=excluded.reviewer_id, note=excluded.note, "
                    "revision=excluded.revision, updated_at=excluded.updated_at",
                    (job_id, asset_id, decision, reviewed_asset_sha256, reviewed_rights_sha256,
                     owner_id, note, new_rev, now),
                )
                connection.execute(
                    "INSERT INTO asset_review_history VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), job_id, asset_id, decision, reviewed_asset_sha256,
                     reviewed_rights_sha256, owner_id, note, now, idempotency_key),
                )
                connection.execute(
                    "UPDATE assets SET review_status=?, updated_at=? WHERE job_id=? AND asset_id=?",
                    (status_map[decision], now, job_id, asset_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_asset(job_id, owner_id, asset_id)

    def asset_review_history(self, job_id: str, owner_id: str, asset_id: str) -> list[dict]:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            rows = connection.execute(
                "SELECT * FROM asset_review_history WHERE job_id=? AND asset_id=? ORDER BY occurred_at",
                (job_id, asset_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- readiness gate (read-only; specific blockers) ------------------------

    def assignment_readiness(self, job_id: str, owner_id: str, language: str) -> dict:
        """Per approved-plan scene, list specific rights/assignment blockers. A
        precursor to the Phase 8 QC rights check; does not change any state."""
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            plan_row = self._current_approved_plan(connection, job_id, language)
            if plan_row is None:
                return {"job_id": job_id, "language": language, "ready": False,
                        "blockers": ["No approved scene plan for this job/language"], "scenes": []}
            plan = self._read_json_artifact(job_id, plan_row)
            assignments = {}
            for r in connection.execute(
                "SELECT * FROM asset_assignments WHERE job_id=? AND language=? AND status='ACTIVE'",
                (job_id, language),
            ):
                assignments.setdefault(r["scene_id"], []).append(dict(r))
            review_rows = {r["asset_id"]: dict(r) for r in connection.execute(
                "SELECT * FROM asset_reviews WHERE job_id=?", (job_id,))}
            asset_rows = {r["asset_id"]: dict(r) for r in connection.execute(
                "SELECT * FROM assets WHERE job_id=?", (job_id,))}

        blockers: list[str] = []
        scenes_out = []
        current_sha = plan_row["plan_sha256"]
        for scene in plan["scenes"]:
            sid = scene["scene_id"]
            scene_assigns = assignments.get(sid, [])
            scene_blockers = []
            if not scene_assigns:
                scene_blockers.append("no asset assigned")
            for a in scene_assigns:
                aid = a["asset_id"]
                if a["scene_plan_sha256"] != current_sha:
                    scene_blockers.append(f"asset {aid}: STALE_PLAN (assigned against an older plan)")
                asset = asset_rows.get(aid)
                if asset is None:
                    scene_blockers.append(f"asset {aid}: MISSING descriptor")
                    continue
                if asset["effective_rights"] != EffectiveRights.CLEAR.value:
                    scene_blockers.append(f"asset {aid}: effective rights {asset['effective_rights']} (not CLEAR)")
                rev = review_rows.get(aid)
                if rev is None or rev["decision"] != "APPROVED":
                    scene_blockers.append(f"asset {aid}: rights not APPROVED")
            scenes_out.append({"scene_id": sid, "blockers": scene_blockers})
            blockers.extend(f"scene {sid}: {b}" for b in scene_blockers)

        return {"job_id": job_id, "language": language, "approved_plan_sha256": current_sha,
                "ready": not blockers, "blockers": blockers, "scenes": scenes_out}
