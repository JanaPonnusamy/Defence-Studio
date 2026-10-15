"""Phase 3 source-grounded script generation, validation, review and approval.

Generation fails closed unless the job's evidence package is currently
APPROVED (via `EvidenceService.export`), re-validated immediately before every
generation call. Each stored revision is bound to the exact approved package
content hash it was generated from; a later evidence change does not mutate an
existing revision but does make it ineligible for a *new* approval (checked at
approval time, not just at generation time).

Tamil and English are independent lineages: `(job_id, language, version)` is
the revision identity, and approving one language never approves the other.

The OpenAI provider is called only from `generate()`, which is an explicit,
authorized action -- never from `get_status`/`list_revisions`/any read path.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import closing
from datetime import datetime, timezone

from defence_studio.contracts.evidence import canonical, digest
from defence_studio.contracts.script import (
    GenerationConfigV1, LANGUAGES, SCRIPT_REVIEW_DECISIONS, estimate_duration,
    narration_word_count, validate_revision,
)
from defence_studio.database import connect
from defence_studio.providers.openai_text import ProviderError
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.review import ReviewError, ReviewNotFound, ReviewValidationError, StaleRevision
from defence_studio.services import script_inspection
from defence_studio.services.script_prompt import system_prompt, user_prompt
from defence_studio.services.script_validation import validate_revision_body
from defence_studio.services.factcheck import FactcheckError, factcheck_staleness, script_gate
from defence_studio.services.script_integrity import (
    compare_revisions, corroboration_count, repetition_against,
)
from pydantic import ValidationError as _PydanticValidationError

__all__ = ["ScriptService", "ScriptGenerationFailed", "ScriptNotFound", "ScriptValidationError", "ScriptStaleRevision"]


class ScriptGenerationFailed(ReviewError):
    """The provider call failed in a way that must not corrupt job/review state."""
    status_code = 502


class ScriptNotFound(ReviewNotFound):
    pass


class ScriptValidationError(ReviewValidationError):
    pass


class ScriptStaleRevision(StaleRevision):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ScriptService(EvidenceService):
    """Extends `EvidenceService` to reuse its ownership checks, artifact store,
    and package assembly rather than re-implementing them."""

    def __init__(self, database_file, storage_root, *, provider=None):
        super().__init__(database_file, storage_root)
        self._provider = provider  # injected for tests; real provider built lazily otherwise

    def _resolve_provider(self, owner_id: str | None = None):
        if self._provider is not None:
            return self._provider
        import os
        if os.environ.get("DEFENCE_STUDIO_SCRIPT_PROVIDER", "").lower() == "offline":
            from defence_studio.providers.offline_text import OfflineExtractiveProvider
            return OfflineExtractiveProvider()
        from defence_studio.providers.openai_text import OpenAiTextProvider
        key = None
        if owner_id:
            try:
                from defence_studio.services import credentials
                key = credentials.resolve_credential(self.database_file, owner_id, "openai")
            except Exception:
                key = None  # unreadable/unconfigured store: fall back to the server environment key
        # Paid call: automatic HTTP retries (429/5xx/timeout) are OFF by default so
        # one generation is at most one outbound request. Opt in explicitly with
        # DEFENCE_STUDIO_SCRIPT_MAX_RETRIES (0-3). Only this path is affected.
        try:
            retries = min(3, max(0, int(os.environ.get("DEFENCE_STUDIO_SCRIPT_MAX_RETRIES", "0"))))
        except ValueError:
            retries = 0
        return OpenAiTextProvider(api_key=key, max_retries=retries) if key else OpenAiTextProvider(max_retries=retries)

    # -- reads ----------------------------------------------------------------

    def list_revisions(self, job_id: str, owner_id: str, language: str | None = None) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            query = "SELECT * FROM script_revisions WHERE job_id=?"
            params = [job_id]
            if language:
                query += " AND language=?"
                params.append(language)
            rows = [dict(r) for r in connection.execute(query + " ORDER BY language, version", params)]
        return {"job_id": job_id, "revisions": rows}

    def get_revision(self, job_id: str, owner_id: str, language: str, version: int) -> dict:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone()
        if row is None:
            raise ScriptNotFound("Unknown script revision for this job/language")
        return self._materialize(job_id, dict(row))

    def _materialize(self, job_id: str, row: dict) -> dict:
        """The stored JSON body's embedded `status` is frozen at write time
        (generation, or the rewrite `approve()` performs). A later lifecycle
        transition that does not rewrite bytes -- `request_changes`, or a
        *different* revision's approval superseding this one -- only updates
        the `script_revisions` row, so the DB column is the current source of
        truth for `status`; overlay it onto the artifact body for every read."""
        body = self._read_json_artifact(job_id, row)
        if body.get("status") != row["status"]:
            body = {**body, "status": row["status"]}
        return body

    def revision_history(self, job_id: str, owner_id: str, language: str, version: int) -> list[dict]:
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            rows = connection.execute(
                "SELECT * FROM script_review_history WHERE job_id=? AND language=? AND version=? ORDER BY occurred_at",
                (job_id, language, version),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_inspections(self, job_id: str, owner_id: str, language: str) -> dict:
        self._check_ownership(job_id, owner_id)
        return {"job_id": job_id, "language": language,
                "records": script_inspection.list_records(self.store, job_id, language)}

    def get_inspection(self, job_id: str, owner_id: str, language: str, record_id: str) -> dict:
        self._check_ownership(job_id, owner_id)
        rec = script_inspection.read(self.store, job_id, language, record_id)
        if rec is None:
            raise ScriptNotFound("Unknown inspection record")
        return rec

    # -- generation -------------------------------------------------------------

    def generate(self, job_id: str, owner_id: str, config: dict, idempotency_key: str) -> dict:
        """Explicit, authorized generation action. Fails closed on a stale,
        unapproved, or invalid package; never implicitly approves anything."""
        parsed_config = GenerationConfigV1.model_validate(config)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        cleared = None
        factcheck_binding = None
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Idempotent replay: a duplicate request never creates a second revision.
            replay = connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND idempotency_key=?",
                (job_id, parsed_config.language, idempotency_key),
            ).fetchone()
            if replay is not None:
                return self._materialize(job_id, dict(replay))
            # Revalidate the package and approval immediately before generation.
            approved_package = self._snapshot_current_approved(connection, job_id, owner_id)
            package_sha = digest(approved_package)
            try:
                cleared, factcheck_binding = script_gate(connection, job_id, approved_package)
            except FactcheckError as exc:
                connection.rollback()
                raise ScriptStaleRevision(str(exc)) from exc
            next_version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM script_revisions WHERE job_id=? AND language=?",
                (job_id, parsed_config.language),
            ).fetchone()[0]
            connection.commit()  # release the write lock before the (possibly slow) provider call

        eligible_claims = [c for c in cleared["claims"] if c["claim_id"] in set(cleared["eligible_claim_ids"])]
        request_body = {
            "schema_version": "defence-studio.script-request.v1",
            "package_sha256": package_sha,
            "language": parsed_config.language,
            "model": parsed_config.model,
            "prompt_version": parsed_config.prompt_version,
            "target_words": parsed_config.target_words,
            "terminology": parsed_config.terminology,
            "max_text_cost_inr": parsed_config.max_text_cost_inr,
        }
        request_sha = digest(request_body)

        provider = self._resolve_provider(owner_id)
        corroboration = {c["claim_id"]: corroboration_count(approved_package, c["claim_id"]) for c in eligible_claims}
        prompt_text = user_prompt(
            topic=approved_package.get("topic", ""), language=parsed_config.language,
            audience=parsed_config.audience, tone=parsed_config.tone,
            script_format=parsed_config.script_format, target_words=parsed_config.target_words,
            terminology=parsed_config.terminology, eligible_claims=eligible_claims, corroboration=corroboration,
        )
        system_text = system_prompt(parsed_config.language)
        budget_policy = {"max_text_cost_inr": parsed_config.max_text_cost_inr,
                         "guard_applies": bool(getattr(provider, "is_paid", False))}
        state = {"calls": 0, "result": None}

        def record(outcome, detail=None, *, validation=None, schema_errors=None, version=None):
            """Persist a sanitised inspection record; diagnostics must never break generation."""
            try:
                exchange = getattr(provider, "last_exchange", None) if state["calls"] else None
                rec = script_inspection.build_record(
                    job_id=job_id, language=parsed_config.language, request_sha256=request_sha,
                    package_sha256=package_sha, outcome=outcome, detail=detail, model=parsed_config.model,
                    provider_name=getattr(provider, "name", "openai"),
                    max_output_tokens=((exchange or {}).get("request_body") or {}).get("max_tokens"),
                    system_text=system_text, user_text=prompt_text, claims=eligible_claims, budget=budget_policy,
                    exchange=exchange, result=state["result"], validation=validation,
                    schema_errors=schema_errors, revision_version=version, provider_calls=state["calls"],
                    api_key=getattr(provider, "api_key", "") or "")
                script_inspection.write(self.store, rec)
            except Exception:  # noqa: BLE001 - best-effort diagnostics
                pass

        try:
            self._budget_guard(provider, parsed_config, system_text, prompt_text)
        except ScriptGenerationFailed as exc:
            record("BLOCKED_BUDGET", str(exc))
            raise
        try:
            state["calls"] = 1  # exactly one bounded call; validation failures are never retried here
            result = provider.generate(
                model=parsed_config.model,
                system_prompt=system_text,
                user_prompt=prompt_text,
            )
            state["result"] = result
        except ProviderError as exc:
            # Provider failures never corrupt job/review state: nothing was
            # written above except the released transaction's read, and the
            # idempotency key was never consumed, so a client retry is safe.
            record("PROVIDER_ERROR", f"{exc.code}: {exc.message}")
            raise ScriptGenerationFailed(f"Provider call failed ({exc.code}): {exc.message}") from exc

        try:
            parsed_body = json.loads(result.text)
        except json.JSONDecodeError as exc:
            record("MALFORMED_JSON", str(exc))
            raise ScriptGenerationFailed(f"Provider returned malformed JSON: {exc}") from exc
        if not isinstance(parsed_body, dict):
            record("MALFORMED_JSON", "Provider output must be a JSON object")
            raise ScriptGenerationFailed("Provider output must be a JSON object")
        if result.measured_text_cost_inr is not None and result.measured_text_cost_inr > parsed_config.max_text_cost_inr:
            record("COST_EXCEEDED", "Measured text generation cost exceeds the configured budget")
            raise ScriptGenerationFailed("Measured text generation cost exceeds the configured budget")

        now = _now()
        word_count = narration_word_count(parsed_body)
        revision_body = {
            "schema_version": "defence-studio.script-revision.v1",
            "job_id": job_id,
            "language": parsed_config.language,
            "script_type": "narration",
            "version": next_version,
            "package_sha256": package_sha,
            "request_sha256": request_sha,
            "title": parsed_body.get("title", ""),
            "opening_hook": parsed_body.get("opening_hook", ""),
            "sections": parsed_body.get("sections", []),
            "closing": parsed_body.get("closing", ""),
            "config": parsed_config.model_dump(),
            "usage": {
                "model": parsed_config.model, "provider": getattr(provider, "name", "openai"),
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "tokens_available": result.tokens_available, "retries": result.retries,
                "measured_text_cost_inr": result.measured_text_cost_inr,
                "cost_is_measured": result.cost_is_measured, "pricing_source": result.pricing_source,
                "currency": result.currency,
            },
            "word_count": estimate_duration(word_count),
            "eligible_claim_ids": cleared["eligible_claim_ids"],
            "created_at": now,
            "approval": None,
            "status": "VALIDATION_FAILED",  # placeholder; set for real below
            "validation": {
                "passed": False, "validator_version": "pending", "package_sha256": package_sha,
                "checked_at": now, "issues": [],
            },
        }
        validation = validate_revision_body(revision_body, cleared)
        revision_body["validation"] = validation
        revision_body["status"] = "AWAITING_REVIEW" if validation["passed"] else "VALIDATION_FAILED"
        # Validate the whole persisted shape (also re-checks status/approval integrity).
        # A schema-invalid model output fails explicitly; it never becomes an empty success.
        try:
            canonical_body = validate_revision(revision_body)
        except _PydanticValidationError as exc:
            record("SCHEMA_INVALID", f"{exc.error_count()} problem(s)", validation=validation,
                   schema_errors=[f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:20]])
            raise ScriptGenerationFailed(
                f"Provider output does not match the script schema ({exc.error_count()} problem(s)): "
                f"{'; '.join(str(e['loc']) for e in exc.errors()[:5])}") from exc

        record(canonical_body["status"], None, validation=validation, version=next_version)

        data = canonical(canonical_body)
        sha = hashlib.sha256(data).hexdigest()
        revision_sha = sha
        uri = f"jobs/{job_id}/scripts/{parsed_config.language}/{revision_sha}.json"

        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND idempotency_key=?",
                (job_id, parsed_config.language, idempotency_key),
            ).fetchone()
            if replay is not None:
                return self._materialize(job_id, dict(replay))
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM script_revisions WHERE job_id=? AND language=?",
                (job_id, parsed_config.language),
            ).fetchone()[0]
            if version != next_version:
                # Another concurrent generation landed first; recompute against it.
                canonical_body["version"] = version
                canonical_body = validate_revision(canonical_body)
                data = canonical(canonical_body)
                sha = hashlib.sha256(data).hexdigest()
                revision_sha = sha
                uri = f"jobs/{job_id}/scripts/{parsed_config.language}/{revision_sha}.json"
            self.store.write_bytes(job_id, uri, data, "application/json")
            if factcheck_binding:
                connection.execute(
                    "INSERT INTO verification_script_bindings(job_id, language, version, package_id, package_sha256) "
                    "VALUES(?,?,?,?,?)",
                    (job_id, parsed_config.language, version, factcheck_binding["package_id"],
                     factcheck_binding["package_sha256"]),
                )
            connection.execute(
                "INSERT INTO script_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, parsed_config.language, version, revision_sha, uri, sha, package_sha, request_sha,
                 canonical_body["status"], owner_id, now, idempotency_key),
            )
            connection.commit()
            return self._materialize(job_id, dict(connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND version=?",
                (job_id, parsed_config.language, version),
            ).fetchone()))

    # -- budget guard ---------------------------------------------------------

    def _budget_guard(self, provider, config, system_text: str, prompt_text: str) -> None:
        """Refuse a *paid* call whose pre-call estimate exceeds the budget, or
        whose cost cannot be estimated. Estimate only: real cost is measured
        from provider usage afterwards. Free/offline providers skip the guard."""
        if not getattr(provider, "is_paid", False):
            return
        from defence_studio.providers.openai_text import estimate_cost_inr
        chars = len(system_text) + len(prompt_text)
        # Conservative: Tamil/mixed text tokenizes at roughly 1 token / 1.5 chars.
        est_in = int(chars / 1.5) + 1
        est_out = int(config.target_words * 4) + 200
        estimate = estimate_cost_inr(config.model, est_in, est_out)
        if estimate is None:
            raise ScriptGenerationFailed(
                f"Budget guard: no pricing is known for model {config.model!r}; refusing a paid call "
                "whose cost cannot be bounded.")
        if estimate > config.max_text_cost_inr:
            raise ScriptGenerationFailed(
                f"Budget guard: estimated cost INR {estimate:.2f} exceeds the configured budget "
                f"INR {config.max_text_cost_inr:.2f}; no paid call was made.")

    # -- comparison, repetition, staleness ------------------------------------

    def compare(self, job_id: str, owner_id: str, language: str, old_version: int, new_version: int) -> dict:
        old = self.get_revision(job_id, owner_id, language, old_version)
        new = self.get_revision(job_id, owner_id, language, new_version)
        return compare_revisions(old, new)

    def repetition_report(self, job_id: str, owner_id: str, language: str, version: int) -> dict:
        """Compare a revision against this owner's other scripts (earlier
        versions of this job and any other job) to surface repeated wording."""
        current = self.get_revision(job_id, owner_id, language, version)
        others = []
        with closing(connect(self.database_file)) as connection:
            rows = connection.execute(
                "SELECT r.*, j.topic AS topic FROM script_revisions r JOIN workflow_jobs j ON j.job_id=r.job_id "
                "WHERE j.owner_id=? AND r.language=? AND NOT (r.job_id=? AND r.version=?) "
                "ORDER BY r.job_id, r.version",
                (owner_id, language, job_id, version),
            ).fetchall()
        for row in rows:
            row = dict(row)
            try:
                body = self._read_json_artifact(row["job_id"], row)
            except ReviewError:
                continue
            others.append({"job_id": row["job_id"], "version": row["version"], "topic": row["topic"], "body": body})
        matches = repetition_against(current, others)
        return {"job_id": job_id, "language": language, "version": version,
                "compared_against": len(others), "matches": matches,
                "threshold": 0.35, "method": "5-word-phrase-overlap.v1"}

    def revision_staleness(self, job_id: str, owner_id: str, language: str | None = None) -> dict:
        """Which stored revisions still rest on the current approved evidence.
        Revisions are immutable; staleness is derived, never written back."""
        self._check_ownership(job_id, owner_id)
        try:
            with closing(connect(self.database_file)) as connection:
                current = self._snapshot(connection, job_id, owner_id)
            current_sha, ready = digest(current), bool(current["ready"])
        except ReviewError:
            current_sha, ready = None, False
        out = []
        for row in self.list_revisions(job_id, owner_id, language)["revisions"]:
            if current_sha is None or not ready:
                state, reason = "STALE", "The evidence package is not currently ready/approved"
            elif row["package_sha256"] != current_sha:
                state, reason = "STALE", "Upstream evidence changed after this revision was generated"
            else:
                state, reason = "CURRENT", ""
            with closing(connect(self.database_file)) as connection:
                fact_reason = factcheck_staleness(connection, job_id, row["language"], row["version"])
            if fact_reason and state == "CURRENT":
                state, reason = "STALE", fact_reason
            out.append({"language": row["language"], "version": row["version"], "status": row["status"],
                        "state": state, "reason": reason, "package_sha256": row["package_sha256"]})
        return {"job_id": job_id, "current_package_sha256": current_sha, "evidence_ready": ready, "revisions": out}

    def _snapshot_current_approved(self, connection, job_id: str, owner_id: str) -> dict:
        """Mirrors `EvidenceService.export()`'s ledger check: `_snapshot` always
        returns a DRAFT-shaped body (status is only ever set on the stored
        artifact `approve()` writes), so freshness is proven by matching the
        *current* DRAFT snapshot's digest against the approval ledger, not by
        a status field on the snapshot itself. Returns the DRAFT snapshot (its
        digest is the `package_sha256` every revision binds to) after
        confirming a ledger entry for that exact digest exists and its stored
        artifact is genuinely APPROVED."""
        current = self._snapshot(connection, job_id, owner_id)
        if not current["ready"]:
            raise ScriptStaleRevision("Script generation requires a ready, currently approved evidence package")
        record = connection.execute(
            "SELECT * FROM evidence_packages WHERE job_id=? AND revision=?",
            (job_id, digest(current)),
        ).fetchone()
        if not record:
            raise ScriptStaleRevision("No approval exists for the current evidence and review revision")
        value = self._read_json_artifact(job_id, dict(record))
        if value["status"] != "APPROVED" or (value.get("approval") or {}).get("revision") != record["revision"]:
            raise ReviewValidationError("Package approval does not match ledger")
        return current

    # -- review & approval -----------------------------------------------------

    def request_changes(self, job_id: str, owner_id: str, language: str, version: int,
                        note: str, idempotency_key: str) -> dict:
        return self._decide(job_id, owner_id, language, version, "CHANGES_REQUESTED", note, idempotency_key)

    def approve(self, job_id: str, owner_id: str, language: str, version: int,
               revision_sha256: str, note: str, idempotency_key: str) -> dict:
        note = self._validate_note(note)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_ownership_locked(connection, job_id, owner_id)
            replay = connection.execute(
                "SELECT * FROM script_review_history WHERE job_id=? AND language=? AND version=? "
                "AND reviewer_id=? AND idempotency_key=?",
                (job_id, language, version, owner_id, idempotency_key),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone()
            if row is None:
                raise ScriptNotFound("Unknown script revision for this job/language")
            if replay is not None:
                return dict(row)
            if row["revision_sha256"] != revision_sha256:
                raise ScriptStaleRevision("revision_sha256 does not match the current revision; reload before approving")
            if row["status"] == "APPROVED":
                raise ScriptStaleRevision("This revision is already approved")
            if row["status"] == "SUPERSEDED":
                raise ScriptStaleRevision("A superseded revision cannot be approved; generate a new one")
            body = self._read_json_artifact(job_id, dict(row))
            if not body["validation"]["passed"]:
                raise ScriptValidationError("A revision that failed validation cannot be approved")
            # Package freshness: an approved revision must still rest on a
            # currently approved evidence package (a later evidence change
            # must invalidate eligibility for approval, not just at generation).
            # `package_sha256` on the revision is the digest of the DRAFT-shaped
            # snapshot at generation time (see `_snapshot_current_approved`),
            # so compare against today's DRAFT snapshot digest the same way.
            current_draft = self._snapshot(connection, job_id, owner_id)
            if not current_draft["ready"] or digest(current_draft) != body["package_sha256"]:
                raise ScriptStaleRevision("The evidence package has changed since this revision was generated; regenerate before approval")
            ledger_record = connection.execute(
                "SELECT 1 FROM evidence_packages WHERE job_id=? AND revision=?",
                (job_id, body["package_sha256"]),
            ).fetchone()
            if not ledger_record:
                raise ScriptStaleRevision("The evidence package is no longer currently approved; regenerate before approval")
            now = _now()
            connection.execute(
                "UPDATE script_revisions SET status='APPROVED' WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            )
            # Superseding earlier revisions of the same language keeps exactly
            # one APPROVED revision per language at a time.
            connection.execute(
                "UPDATE script_revisions SET status='SUPERSEDED' WHERE job_id=? AND language=? AND version<>? AND status='APPROVED'",
                (job_id, language, version),
            )
            connection.execute(
                "INSERT INTO script_review_history VALUES(?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), job_id, language, version, "APPROVED", owner_id, note, now, idempotency_key),
            )
            body["status"] = "APPROVED"
            body["approval"] = {"revision_sha256": revision_sha256, "version": version, "reviewer_id": owner_id,
                                "note": note, "approved_at": now}
            data = canonical(validate_revision(body))
            sha = hashlib.sha256(data).hexdigest()
            uri = f"jobs/{job_id}/scripts/{language}/{sha}-approved.json"
            self.store.write_bytes(job_id, uri, data, "application/json")
            connection.execute(
                "UPDATE script_revisions SET uri=?, sha256=? WHERE job_id=? AND language=? AND version=?",
                (uri, sha, job_id, language, version),
            )
            connection.commit()
            return dict(connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone())

    def _decide(self, job_id, owner_id, language, version, action, note, idempotency_key) -> dict:
        note = self._validate_note(note)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        if action not in SCRIPT_REVIEW_DECISIONS:
            raise ScriptValidationError(f"Unsupported script review action {action!r}")
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_ownership_locked(connection, job_id, owner_id)
            replay = connection.execute(
                "SELECT * FROM script_review_history WHERE job_id=? AND language=? AND version=? "
                "AND reviewer_id=? AND idempotency_key=?",
                (job_id, language, version, owner_id, idempotency_key),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone()
            if row is None:
                raise ScriptNotFound("Unknown script revision for this job/language")
            if replay is not None:
                return dict(row)
            if row["status"] in ("APPROVED", "SUPERSEDED"):
                raise ScriptStaleRevision(f"Cannot change a revision in status {row['status']}")
            now = _now()
            connection.execute(
                "UPDATE script_revisions SET status=? WHERE job_id=? AND language=? AND version=?",
                (action, job_id, language, version),
            )
            connection.execute(
                "INSERT INTO script_review_history VALUES(?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), job_id, language, version, action, owner_id, note, now, idempotency_key),
            )
            connection.commit()
            return dict(connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND version=?",
                (job_id, language, version),
            ).fetchone())

    def _check_ownership_locked(self, connection, job_id: str, owner_id: str) -> None:
        from defence_studio.repositories.jobs import JobRepository
        if JobRepository(connection).get_owned_job(job_id, owner_id) is None:
            raise ScriptNotFound("Workflow job not found")

    # -- export (Phase 4 boundary) ----------------------------------------------

    def export_approved(self, job_id: str, owner_id: str, language: str) -> dict:
        """The stable input contract for a future phase. Only ever returns an
        APPROVED revision bound to a still-approved evidence package."""
        self._check_ownership(job_id, owner_id)
        if language not in LANGUAGES:
            raise ScriptValidationError("Unsupported language")
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND status='APPROVED'",
                (job_id, language),
            ).fetchone()
            if row is None:
                raise ScriptStaleRevision("No approved script revision exists for this job/language")
            body = self._read_json_artifact(job_id, dict(row))
            current_draft = self._snapshot(connection, job_id, owner_id)
            ledger_record = connection.execute(
                "SELECT 1 FROM evidence_packages WHERE job_id=? AND revision=?",
                (job_id, body["package_sha256"]),
            ).fetchone()
            if not current_draft["ready"] or digest(current_draft) != body["package_sha256"] or not ledger_record:
                raise ScriptStaleRevision("The approved script's evidence package is no longer the current approval")
        return body
