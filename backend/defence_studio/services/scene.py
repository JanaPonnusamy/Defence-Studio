"""Phase 4 M1 scene planning, persistence, editing and approval.

Turns an **approved** Phase 3 script revision into an editable, versioned scene
plan (`contracts/scene.py`), then lets the owner add / remove / reorder / edit
individual scenes and finally approve a specific plan version. Human-paced like
F5 / Phase 2 / Phase 3 (synchronous HTTP, not a worker stage). M1 does **no**
TTS, alignment, render, asset, or rights work -- those are later milestones; a
scene's downstream reference slots stay empty here.

This service subclasses `ScriptService` purely to reuse already-built, tested
machinery -- job-ownership checks, the artifact byte store, the note /
idempotency-key validators, and `export_approved()` (the Phase 3->4 boundary
that returns the one currently-approved, still-fresh script revision). It adds
no second execution architecture.

Guarantees enforced here, mirroring the Phase 2/3 patterns:

* **Deterministic initial plan.** `generate()` builds exactly one scene per
  approved-script section (plus the opening hook / closing as their own scenes
  when present), copying the approved spoken narration **verbatim** and carrying
  each section's approved `claim_ids`. No narration is rewritten, no claim is
  invented, no timing is measured -- only an explicit words-per-minute
  *estimate* is attached per scene.
* **Stable identity + digest.** A plan is content-addressed: its canonical-JSON
  digest (`plan_digest`) is its `plan_sha256`, and `(job_id, language, version)`
  is its row identity. Byte-identical plans collide by design.
* **Upstream binding.** Every plan records the exact approved-script
  `revision_sha256` and its `package_sha256`. Editing/reordering preserves that
  binding; generating re-reads the current approved export.
* **Per-scene isolation.** Editing one scene re-serializes only that scene; the
  stored canonical bytes of every untouched scene are identical across the new
  version (the M1 acceptance property), because an untouched scene's dict is
  carried through unchanged and canonical JSON is deterministic.
* **Revision-conflict protection.** Editing supplies the `expected_base_version`
  it edited from; a concurrent new version is rejected (`RevisionConflict`, 409)
  rather than silently overwriting. Approval binds to the exact `plan_sha256`
  and re-checks that the plan's `script_revision_sha256` is *still* the current
  approved script export -- if the script changed, approval is refused
  (`SceneStaleRevision`, 409) until the plan is re-synced (regenerated/re-based).
* **Explicit human approval.** Approval is never implied by generation or
  editing; it is an explicit action, bound to a plan hash, replay-idempotent,
  and it supersedes the previously-approved plan of the **same language** only.
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import closing
from datetime import datetime, timezone

from pydantic import ValidationError

from defence_studio.contracts.evidence import canonical, digest
from defence_studio.contracts.scene import (
    LANGUAGES, SCENE_PLANNER_VERSION, SCENE_PLAN_SCHEMA,
    SceneV1, ScenePlanConfigV1, ScenePlanRequestV1,
    plan_scene_timing, validate_scene_plan,
)
from defence_studio.database import connect
from defence_studio.repositories.jobs import JobRepository
from defence_studio.services.review import (
    ReviewNotFound, ReviewValidationError, RevisionConflict, StaleRevision,
)
from defence_studio.services.script import ScriptService, ScriptStaleRevision
from defence_studio.services.stage_sync import complete_stage_locked

__all__ = [
    "SceneService", "SceneNotFound", "SceneValidationError",
    "SceneStaleRevision", "SceneRevisionConflict",
]


class SceneNotFound(ReviewNotFound):
    pass


class SceneValidationError(ReviewValidationError):
    pass


class SceneStaleRevision(StaleRevision):
    pass


class SceneRevisionConflict(RevisionConflict):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _scene_digest(scene: dict) -> str:
    """Canonical-JSON content hash of a single scene dict. Deterministic, so two
    byte-identical scenes hash identically -- the basis for proving that editing
    one scene leaves every other scene's stored content unchanged."""
    return hashlib.sha256(canonical(scene)).hexdigest()


def _as_validation_error(exc: ValidationError) -> SceneValidationError:
    """Surface a contract-level pydantic failure as the service's 422 family so
    the API returns a clean 422 (not an unhandled 500) and callers see one
    exception family regardless of which layer rejected the input."""
    return SceneValidationError(f"Scene plan failed contract validation: {exc.errors()[:3]}")


class SceneService(ScriptService):
    """Scene planning/persistence/approval reusing `ScriptService` machinery."""

    # -- reads ----------------------------------------------------------------

    def list_plans(self, job_id: str, owner_id: str, language: str | None = None) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            query = "SELECT * FROM scene_plans WHERE job_id=?"
            params: list = [job_id]
            if language:
                query += " AND language=?"
                params.append(language)
            rows = [dict(r) for r in connection.execute(query + " ORDER BY language, version", params)]
        return {"job_id": job_id, "plans": rows}

    def get_plan(self, job_id: str, owner_id: str, language: str, version: int) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone()
        if row is None:
            raise SceneNotFound("Unknown scene plan for this job/language")
        return self._materialize_plan(job_id, dict(row))

    def _materialize_plan(self, job_id: str, row: dict) -> dict:
        """Overlay the authoritative DB `status` onto the stored plan body, the
        same pattern `ScriptService._materialize` uses: a lifecycle change that
        does not rewrite bytes (a *different* plan's approval superseding this
        one, or `request_changes`) lives only in the row."""
        body = self._read_json_artifact(job_id, row)
        if body.get("status") != row["status"]:
            body = {**body, "status": row["status"]}
        return body

    def plan_history(self, job_id: str, owner_id: str, language: str, version: int) -> list[dict]:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            rows = connection.execute(
                "SELECT * FROM scene_plan_review_history WHERE job_id=? AND language=? AND version=? "
                "ORDER BY occurred_at",
                (job_id, language, version),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- deterministic initial plan from the approved script ------------------

    def _scenes_from_script(self, export: dict, config: ScenePlanConfigV1) -> list[dict]:
        """One scene per approved-script section, in order, plus the opening
        hook and closing as their own scenes when present. Narration is copied
        verbatim; each section's approved claim IDs ride along. Nothing is
        rewritten, measured, or fabricated."""
        wpm = config.words_per_minute
        allowed = set(export.get("eligible_claim_ids", []))
        ordered: list[tuple[str, str, list[str], str]] = []  # (source_section_id, narration, claim_ids, purpose)
        hook = (export.get("opening_hook") or "").strip()
        if hook:
            ordered.append(("__opening_hook__", export["opening_hook"], [], "Opening hook"))
        for section in export.get("sections", []):
            claim_ids = [c for c in (section.get("evidence", {}) or {}).get("claim_ids", []) if c in allowed]
            ordered.append((section["section_id"], section["narration"],
                            claim_ids, section.get("heading", "")))
        closing_text = (export.get("closing") or "").strip()
        if closing_text:
            ordered.append(("__closing__", export["closing"], [], "Closing"))

        scenes: list[dict] = []
        for index, (section_id, narration, claim_ids, purpose) in enumerate(ordered):
            scene = {
                "scene_id": f"sc-{index + 1:04d}",
                "order": index + 1,
                "source_section_id": section_id,
                "narration": narration,
                "claim_ids": claim_ids,
                "purpose": purpose,
                "transition_in": config.default_transition,
                "planned_timing": plan_scene_timing(narration, words_per_minute=wpm),
            }
            # Validate each scene as it is built so a malformed approved section
            # fails loudly here rather than at whole-plan assembly.
            try:
                scenes.append(SceneV1.model_validate(scene).model_dump())
            except ValidationError as exc:
                raise _as_validation_error(exc) from exc
        return scenes

    def _resequence(self, scenes: list[dict]) -> list[dict]:
        """Re-stamp `order` to match list position (1-based) after an edit/
        reorder/add/remove, leaving every other field of each scene untouched so
        unchanged scenes keep identical canonical bytes."""
        out = []
        for index, scene in enumerate(scenes):
            if scene["order"] == index + 1:
                out.append(scene)
            else:
                out.append({**scene, "order": index + 1})
        return out

    def _assemble_plan(self, job_id: str, language: str, version: int, export: dict,
                       scenes: list[dict], status: str, now: str) -> dict:
        body = {
            "schema_version": SCENE_PLAN_SCHEMA,
            "job_id": job_id,
            "language": language,
            "version": version,
            "planner_version": SCENE_PLANNER_VERSION,
            "script_revision_sha256": export["approval"]["revision_sha256"],
            "script_version": export["version"],
            "package_sha256": export["package_sha256"],
            "scenes": scenes,
            "eligible_claim_ids": list(export.get("eligible_claim_ids", [])),
            "status": status,
            "created_at": now,
        }
        try:
            return validate_scene_plan(body)
        except ValidationError as exc:
            raise _as_validation_error(exc) from exc

    # -- generation (deterministic; no provider call) -------------------------

    def generate(self, job_id: str, owner_id: str, config: dict, idempotency_key: str) -> dict:
        """Build the initial scene plan from the current approved script export.

        Deterministic and offline: unlike `ScriptService.generate`, no text
        provider is ever called. Fails closed if no currently-approved,
        still-fresh script revision exists for the language (`export_approved`
        raises `ScriptStaleRevision`). A duplicate idempotency key replays the
        existing plan instead of creating a second version."""
        parsed = ScenePlanConfigV1.model_validate(config)
        language = parsed.language
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        self._check_ownership(job_id, owner_id)

        # The Phase 3->4 boundary. Raises ScriptStaleRevision (409) if there is
        # no approved revision or its evidence is no longer current.
        export = self.export_approved(job_id, owner_id, language)
        scenes = self._scenes_from_script(export, parsed)

        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_ownership_locked(connection, job_id, owner_id)
            replay = connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND idempotency_key=?",
                (job_id, language, idempotency_key),
            ).fetchone()
            if replay is not None:
                return self._materialize_plan(job_id, dict(replay))
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM scene_plans WHERE job_id=? AND language=?",
                (job_id, language),
            ).fetchone()[0]
            now = _now()
            body = self._assemble_plan(job_id, language, version, export, scenes, "DRAFT", now)
            return self._persist_new_version(connection, job_id, owner_id, language, version,
                                             body, export, idempotency_key, now)

    # -- editing / reorder / add / remove -------------------------------------

    def revise(self, job_id: str, owner_id: str, language: str, base_version: int,
               edits: dict, idempotency_key: str) -> dict:
        """Create a new DRAFT plan version from ``base_version``, applying only
        the requested changes to individual scenes. Untouched scenes are carried
        through byte-for-byte (their canonical content is unchanged), satisfying
        the M1 isolation property. Supported ``edits`` keys:

        * ``reorder``: full list of existing ``scene_id``s in the new order.
        * ``remove``: list of ``scene_id``s to drop.
        * ``update``: ``{scene_id: {field: value, ...}}`` -- only the editable
          presentation/narration fields of that scene are replaced; ``order`` is
          recomputed, never caller-set.
        * ``add``: list of new scene dicts (without ``order``) inserted at an
          optional ``at`` index (default: appended).

        Binds the new version to the **same** approved-script revision the base
        version was built from (editing does not re-pull the script); approval
        later re-checks that binding is still current.
        """
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        self._check_ownership(job_id, owner_id)
        if not isinstance(edits, dict) or not edits:
            raise SceneValidationError("No scene edits supplied")

        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_ownership_locked(connection, job_id, owner_id)
            replay = connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND idempotency_key=?",
                (job_id, language, idempotency_key),
            ).fetchone()
            if replay is not None:
                return self._materialize_plan(job_id, dict(replay))

            base_row = connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND version=?",
                (job_id, language, base_version),
            ).fetchone()
            if base_row is None:
                raise SceneNotFound("Unknown base scene-plan version to edit")
            # Revision-conflict protection: only the latest version may be edited.
            latest = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM scene_plans WHERE job_id=? AND language=?",
                (job_id, language),
            ).fetchone()[0]
            if base_version != latest:
                raise SceneRevisionConflict(
                    f"Scene plan was already revised (current version {latest}); reload before editing"
                )
            base = self._read_json_artifact(job_id, dict(base_row))
            new_scenes = self._apply_edits(base["scenes"], edits)
            next_version = latest + 1
            now = _now()
            body = {
                **base,
                "version": next_version,
                "scenes": new_scenes,
                "status": "DRAFT",
                "created_at": now,
            }
            try:
                body = validate_scene_plan(body)
            except ValidationError as exc:
                raise _as_validation_error(exc) from exc
            # Reuse the base row's recorded upstream binding for the ledger columns.
            export_like = {
                "approval": {"revision_sha256": base_row["script_revision_sha256"]},
                "version": base_row["script_version"],
                "package_sha256": base_row["package_sha256"],
            }
            return self._persist_new_version(connection, job_id, owner_id, language, next_version,
                                             body, export_like, idempotency_key, now)

    def _apply_edits(self, scenes: list[dict], edits: dict) -> list[dict]:
        by_id = {s["scene_id"]: s for s in scenes}
        if len(by_id) != len(scenes):
            raise SceneValidationError("Base plan has duplicate scene_ids")
        order = [s["scene_id"] for s in scenes]

        unknown_keys = set(edits) - {"reorder", "remove", "update", "add"}
        if unknown_keys:
            raise SceneValidationError(f"Unsupported edit operations: {sorted(unknown_keys)}")

        # update -- replace only editable fields of an existing scene.
        editable = {"narration", "claim_ids", "purpose", "voice_direction",
                    "visual_suggestions", "captions", "transition_in", "crop", "music"}
        for scene_id, changes in (edits.get("update") or {}).items():
            if scene_id not in by_id:
                raise SceneValidationError(f"Cannot update unknown scene_id {scene_id!r}")
            if not isinstance(changes, dict) or not changes:
                raise SceneValidationError(f"No changes supplied for scene {scene_id!r}")
            bad = set(changes) - editable
            if bad:
                raise SceneValidationError(f"Fields not editable on a scene: {sorted(bad)}")
            merged = {**by_id[scene_id], **changes}
            # Re-validate the single edited scene; order re-stamped later.
            try:
                by_id[scene_id] = SceneV1.model_validate(merged).model_dump()
            except ValidationError as exc:
                raise _as_validation_error(exc) from exc

        # remove
        for scene_id in (edits.get("remove") or []):
            if scene_id not in by_id:
                raise SceneValidationError(f"Cannot remove unknown scene_id {scene_id!r}")
            del by_id[scene_id]
            order.remove(scene_id)

        # reorder -- must be a permutation of the surviving scene_ids.
        if "reorder" in edits:
            requested = list(edits["reorder"])
            if sorted(requested) != sorted(order):
                raise SceneValidationError("reorder must list exactly the current scene_ids once each")
            order = requested

        # add -- new scenes inserted at an optional index (default append).
        add = edits.get("add")
        if add is not None:
            if not isinstance(add, dict) or "scenes" not in add:
                raise SceneValidationError("add must be an object with a 'scenes' list")
            at = add.get("at", len(order))
            if not isinstance(at, int) or not 0 <= at <= len(order):
                raise SceneValidationError("add.at is out of range")
            new_ids = []
            for raw in add["scenes"]:
                if not isinstance(raw, dict):
                    raise SceneValidationError("Each added scene must be an object")
                candidate = {**raw, "order": 1}  # placeholder order, re-stamped below
                try:
                    scene = SceneV1.model_validate(candidate).model_dump()
                except ValidationError as exc:
                    raise _as_validation_error(exc) from exc
                if scene["scene_id"] in by_id:
                    raise SceneValidationError(f"Added scene_id {scene['scene_id']!r} already exists")
                by_id[scene["scene_id"]] = scene
                new_ids.append(scene["scene_id"])
            order = order[:at] + new_ids + order[at:]

        if not order:
            raise SceneValidationError("A scene plan must contain at least one scene")
        return self._resequence([by_id[sid] for sid in order])

    # -- shared persistence ----------------------------------------------------

    def _persist_new_version(self, connection, job_id: str, owner_id: str, language: str,
                             version: int, body: dict, export: dict,
                             idempotency_key: str, now: str) -> dict:
        """Content-address and store one new plan version, then insert its row
        inside the already-open BEGIN IMMEDIATE transaction."""
        data = canonical(body)
        sha = hashlib.sha256(data).hexdigest()
        plan_sha = sha
        uri = f"jobs/{job_id}/scenes/{language}/{plan_sha}.json"
        self.store.write_bytes(job_id, uri, data, "application/json")
        connection.execute(
            "INSERT INTO scene_plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, language, version, plan_sha, uri, sha,
             export["approval"]["revision_sha256"], export["version"], export["package_sha256"],
             body["status"], owner_id, now, idempotency_key),
        )
        connection.commit()
        return self._materialize_plan(job_id, dict(connection.execute(
            "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND version=?",
            (job_id, language, version),
        ).fetchone()))

    # -- per-scene content hashing (M1 isolation evidence) --------------------

    def scene_digests(self, job_id: str, owner_id: str, language: str, version: int) -> dict:
        """Map of ``scene_id`` -> canonical content hash for a stored plan
        version. Editing one scene leaves every other scene's hash unchanged in
        the next version; this read makes that property observable/testable."""
        plan = self.get_plan(job_id, owner_id, language, version)
        return {scene["scene_id"]: _scene_digest(scene) for scene in plan["scenes"]}

    # -- review & approval -----------------------------------------------------

    def request_changes(self, job_id: str, owner_id: str, language: str, version: int,
                        note: str, idempotency_key: str) -> dict:
        note = self._validate_note(note)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_ownership_locked(connection, job_id, owner_id)
            replay = connection.execute(
                "SELECT * FROM scene_plan_review_history WHERE job_id=? AND language=? AND version=? "
                "AND reviewer_id=? AND idempotency_key=?",
                (job_id, language, version, owner_id, idempotency_key),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone()
            if row is None:
                raise SceneNotFound("Unknown scene plan for this job/language")
            if replay is not None:
                return dict(row)
            if row["status"] in ("APPROVED", "SUPERSEDED"):
                raise SceneStaleRevision(f"Cannot change a scene plan in status {row['status']}")
            now = _now()
            connection.execute(
                "UPDATE scene_plans SET status='CHANGES_REQUESTED' WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            )
            connection.execute(
                "INSERT INTO scene_plan_review_history VALUES(?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), job_id, language, version, "CHANGES_REQUESTED", owner_id, note, now, idempotency_key),
            )
            connection.commit()
            return dict(connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone())

    def approve(self, job_id: str, owner_id: str, language: str, version: int,
                plan_sha256: str, note: str, idempotency_key: str) -> dict:
        """Explicit human approval, bound to the exact ``plan_sha256`` and to the
        approved-script revision the plan was built from. If the current
        approved script export no longer matches that revision (the script was
        re-approved/changed), approval is refused until the plan is re-synced."""
        note = self._validate_note(note)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        if language not in LANGUAGES:
            raise SceneValidationError("Unsupported language")
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_ownership_locked(connection, job_id, owner_id)
            replay = connection.execute(
                "SELECT * FROM scene_plan_review_history WHERE job_id=? AND language=? AND version=? "
                "AND reviewer_id=? AND idempotency_key=?",
                (job_id, language, version, owner_id, idempotency_key),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone()
            if row is None:
                raise SceneNotFound("Unknown scene plan for this job/language")
            if replay is not None:
                return dict(row)
            if row["plan_sha256"] != plan_sha256:
                raise SceneStaleRevision("plan_sha256 does not match the current plan; reload before approving")
            if row["status"] == "APPROVED":
                raise SceneStaleRevision("This scene plan is already approved")
            if row["status"] == "SUPERSEDED":
                raise SceneStaleRevision("A superseded scene plan cannot be approved; revise and resubmit")
            # Re-sync gate: the approved script this plan was built from must
            # still be the current approved export. A changed/re-approved script
            # blocks approval (re-sync required), not just at generation time.
            try:
                export = self.export_approved(job_id, owner_id, language)
            except ScriptStaleRevision as exc:
                raise SceneStaleRevision(
                    "The approved script is no longer current; re-sync the scene plan before approval"
                ) from exc
            if export["approval"]["revision_sha256"] != row["script_revision_sha256"]:
                raise SceneStaleRevision(
                    "The approved script revision changed since this plan was built; "
                    "regenerate/re-sync the scene plan before approval"
                )
            now = _now()
            connection.execute(
                "UPDATE scene_plans SET status='APPROVED' WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            )
            # Keep exactly one APPROVED plan per language.
            connection.execute(
                "UPDATE scene_plans SET status='SUPERSEDED' WHERE job_id=? AND language=? AND version<>? AND status='APPROVED'",
                (job_id, language, version),
            )
            connection.execute(
                "INSERT INTO scene_plan_review_history VALUES(?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), job_id, language, version, "APPROVED", owner_id, note, now, idempotency_key),
            )
            complete_stage_locked(connection, job_id, "scene_creation_and_voice_direction", now)
            connection.commit()
            return dict(connection.execute(
                "SELECT * FROM scene_plans WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone())
