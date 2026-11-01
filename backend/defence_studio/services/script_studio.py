"""Script Studio: chapter metadata, human editing, reordering and single-section regeneration.

Every operation creates a NEW immutable revision (``lineage`` records the parent, the actor, the
operations and the evidence diff); the parent is never modified. A derived revision is bound to
the same, still currently human-approved evidence package and is re-validated by the same
validator as a generated one. It is never approved here: approval stays human-only
(``ScriptService.approve``). Regenerating one section leaves every other section byte-identical,
and any evidence reference the regenerated section drops is recorded as a visible WARNING and in
``lineage.evidence_diff`` (never silently removed).
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from defence_studio.contracts.evidence import canonical, digest
from defence_studio.contracts.script import (
    VISUAL_TYPES, GenerationConfigV1, count_words, estimate_duration, narration_word_count, validate_revision,
)
from defence_studio.database import connect
from defence_studio.providers.openai_text import ProviderError
from defence_studio.services.factcheck import FactcheckError, script_gate
from defence_studio.services.review import RevisionConflict
from defence_studio.services.script import (
    ScriptGenerationFailed, ScriptNotFound, ScriptService, ScriptStaleRevision, ScriptValidationError,
    _now, draft_shape_problems, load_provider_json,
)
from defence_studio.services.script_integrity import corroboration_count
from defence_studio.services.script_prompt import system_prompt
from defence_studio.services.script_validation import validate_revision_body

STUDIO_VERSION = "script-studio.v1"
_TEMPLATES_FILE = Path(__file__).resolve().parents[2] / "config" / "script_chapter_templates.json"


def load_chapter_templates(path: Path | None = None) -> dict:
    data = json.loads((path or _TEMPLATES_FILE).read_text(encoding="utf-8"))
    ids = set()
    for t in data.get("templates", []):
        if not t.get("id") or t["id"] in ids:
            raise ValueError("chapter templates need unique ids")
        if t.get("default_visual_type") not in VISUAL_TYPES:
            raise ValueError(f"{t['id']}: unknown default_visual_type")
        if not (0 < int(t["min_words"]) <= int(t["max_words"])):
            raise ValueError(f"{t['id']}: invalid word range")
        ids.add(t["id"])
    return data


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SectionEdit(_Strict):
    section_id: str = Field(min_length=1, max_length=120)
    heading: str | None = Field(default=None, max_length=400)
    narration: str | None = Field(default=None, min_length=1, max_length=20000)
    presentation_note: str | None = Field(default=None, max_length=2000)
    visual_type: str | None = None
    chapter_template: str | None = Field(default=None, min_length=1, max_length=80)


class EditRequest(_Strict):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    opening_hook: str | None = Field(default=None, max_length=4000)
    closing: str | None = Field(default=None, min_length=1, max_length=8000)
    order: list[str] | None = None
    sections: list[SectionEdit] = Field(default_factory=list, max_length=200)
    note: str = Field(default="", max_length=2000)


def _claims(section: dict) -> set:
    return set((section.get("evidence") or {}).get("claim_ids") or [])


def evidence_diff(parent: dict, child: dict, eligible: set) -> dict:
    old = {s["section_id"]: s for s in parent.get("sections", [])}
    new = {s["section_id"]: s for s in child.get("sections", [])}
    sections = []
    for sid in [s["section_id"] for s in child.get("sections", [])]:
        before, after = _claims(old.get(sid, {})), _claims(new[sid])
        if before != after:
            removed = sorted(before - after)
            sections.append({"section_id": sid, "claims_added": sorted(after - before), "claims_removed": removed,
                             "valid_claims_removed": [c for c in removed if c in eligible]})
    return {"sections": sections,
            "order_changed": [s["section_id"] for s in parent.get("sections", [])]
                             != [s["section_id"] for s in child.get("sections", [])],
            "valid_claims_removed": sorted({c for s in sections for c in s["valid_claims_removed"]})}


class ScriptStudioService(ScriptService):
    """Script Studio operations on top of the Phase 3 script service."""

    # -- chapter metadata (read-only) -------------------------------------------------------------

    def chapters(self, job_id: str, owner_id: str, language: str, version: int) -> dict:
        revision = self.get_revision(job_id, owner_id, language, version)
        package = self.draft(job_id, owner_id)["package"]
        eligible = set(package.get("eligible_claim_ids", []))
        by_claim = {c["claim_id"]: c for c in package.get("claims", []) if isinstance(c, dict) and "claim_id" in c}
        templates = {t["id"]: t for t in load_chapter_templates()["templates"]}
        wpm = (revision.get("word_count") or {}).get("words_per_minute") or 150
        issues = (revision.get("validation") or {}).get("issues", [])
        chapters = []
        for index, s in enumerate(revision.get("sections", [])):
            words = count_words(s.get("narration", ""))
            refs, warnings = [], []
            for cid in (s.get("evidence") or {}).get("claim_ids") or []:
                claim = by_claim.get(cid, {})
                corroboration = corroboration_count(package, cid) if cid in eligible else 0
                refs.append({"claim_id": cid, "source_id": claim.get("source_id"),
                             "text": (claim.get("text") or "")[:300], "eligible": cid in eligible,
                             "independent_sources": corroboration})
                if cid not in eligible:
                    warnings.append({"code": "CLAIM_NOT_ELIGIBLE", "detail": f"{cid} is no longer an eligible claim"})
                elif corroboration <= 1:
                    warnings.append({"code": "SINGLE_SOURCE", "detail": f"{cid} rests on one independent source; keep it attributed"})
            kind = s.get("statement_kind")
            if kind == "UNRESOLVED":
                warnings.append({"code": "UNRESOLVED_STATEMENT", "detail": "Section presents an unresolved point"})
            elif kind is None:
                warnings.append({"code": "STATEMENT_KIND_UNDECLARED", "detail": "Epistemic status not declared"})
            if s.get("requires_evidence", True) and not refs:
                warnings.append({"code": "NO_EVIDENCE", "detail": "Factual section cites no claim"})
            warnings += [{"code": i["code"], "detail": i["detail"], "severity": i["severity"]}
                         for i in issues if i.get("section_id") == s.get("section_id")]
            template_id = s.get("chapter_template")
            template = templates.get(template_id) if template_id else None
            template_check = {"template": template_id, "status": "UNASSIGNED" if not template_id else
                              "UNKNOWN_TEMPLATE" if template is None else "OK", "issues": []}
            if template:
                if not template["min_words"] <= words <= template["max_words"]:
                    template_check["issues"].append(
                        f"{words} words outside template range {template['min_words']}-{template['max_words']}")
                if template["requires_evidence"] and not refs:
                    template_check["issues"].append("template requires cited evidence")
                if template_check["issues"]:
                    template_check["status"] = "OUT_OF_TEMPLATE"
            chapters.append({
                "index": index, "section_id": s.get("section_id"), "heading": s.get("heading", ""),
                "word_count": words, "estimated_duration_seconds": round(words / wpm * 60),
                "visual_type": s.get("visual_type"),
                "suggested_visual_type": s.get("visual_type") or (template or {}).get("default_visual_type"),
                "statement_kind": kind, "evidence": refs, "source_ids": (s.get("evidence") or {}).get("source_ids", []),
                "qualification": (s.get("evidence") or {}).get("qualification", ""),
                "uncertainty_warnings": warnings, "template_check": template_check})
        return {"job_id": job_id, "language": language, "version": version, "status": revision["status"],
                "words_per_minute": wpm, "duration_is_estimate": True,
                "total_words": (revision.get("word_count") or {}).get("words"),
                "estimated_duration_seconds": (revision.get("word_count") or {}).get("estimated_duration_seconds"),
                "chapters": chapters, "lineage": revision.get("lineage"),
                "templates_version": load_chapter_templates()["version"], "studio_version": STUDIO_VERSION}

    # -- human edit / reorder ---------------------------------------------------------------------

    def edit(self, job_id: str, owner_id: str, language: str, version: int, request: dict,
             idempotency_key: str) -> dict:
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        try:
            parsed = EditRequest.model_validate(request)
        except ValidationError as exc:
            raise ScriptValidationError(f"Invalid edit: {exc.error_count()} problem(s)") from exc
        request_digest = hashlib.sha256(canonical(parsed.model_dump())).hexdigest()
        replay = self._replay_derived(job_id, owner_id, language, idempotency_key, request_digest)
        if replay is not None:
            return replay
        parent = self.get_revision(job_id, owner_id, language, version)
        child, ops = copy.deepcopy(parent), []
        sections = {s["section_id"]: s for s in child["sections"]}
        templates = {t["id"] for t in load_chapter_templates()["templates"]}
        for field in ("title", "opening_hook", "closing"):
            value = getattr(parsed, field)
            if value is not None and value != child.get(field):
                child[field] = value
                ops.append({"op": f"edit_{field}"})
        for edit in parsed.sections:
            section = sections.get(edit.section_id)
            if section is None:
                raise ScriptValidationError(f"Unknown section {edit.section_id}")
            if edit.visual_type is not None and edit.visual_type not in VISUAL_TYPES:
                raise ScriptValidationError(f"Unknown visual_type {edit.visual_type}")
            if edit.chapter_template is not None and edit.chapter_template not in templates:
                raise ScriptValidationError(f"Unknown chapter_template {edit.chapter_template}")
            for field in ("heading", "narration", "presentation_note", "visual_type", "chapter_template"):
                value = getattr(edit, field)
                if value is not None and value != section.get(field):
                    section[field] = value
                    ops.append({"op": f"edit_{field}", "section_id": edit.section_id})
        if parsed.order is not None:
            current = [s["section_id"] for s in child["sections"]]
            if sorted(parsed.order) != sorted(current) or len(set(parsed.order)) != len(parsed.order):
                raise ScriptValidationError("order must be a permutation of the existing section ids")
            if parsed.order != current:
                child["sections"] = [sections[sid] for sid in parsed.order]
                ops.append({"op": "reorder", "from": current, "to": parsed.order})
        if not ops:
            raise ScriptValidationError("The edit changes nothing")
        usage = {"model": parent["usage"]["model"], "provider": "human-edit", "input_tokens": None,
                 "output_tokens": None, "tokens_available": False, "retries": 0, "measured_text_cost_inr": 0.0,
                 "cost_is_measured": True, "pricing_source": "no provider call", "currency": "INR"}
        return self._persist_derived(job_id, owner_id, parent, child, usage, idempotency_key, {
            "kind": "HUMAN_EDIT", "operations": ops, "note": parsed.note, "request_digest": request_digest})

    # -- regenerate one section -------------------------------------------------------------------

    def regenerate_section(self, job_id: str, owner_id: str, language: str, version: int, section_id: str,
                           idempotency_key: str, instruction: str = "") -> dict:
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        if len(instruction or "") > 1000:
            raise ScriptValidationError("instruction is limited to 1000 characters")
        request_digest = hashlib.sha256(canonical({"section_id": section_id, "instruction": instruction or "",
                                                   "version": version})).hexdigest()
        replay = self._replay_derived(job_id, owner_id, language, idempotency_key, request_digest)
        if replay is not None:
            return replay
        parent = self.get_revision(job_id, owner_id, language, version)
        index = next((i for i, s in enumerate(parent["sections"]) if s["section_id"] == section_id), None)
        if index is None:
            raise ScriptNotFound(f"Unknown section {section_id}")
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN")
            package = self._snapshot_current_approved(connection, job_id, owner_id)
            if digest(package) != parent["package_sha256"]:
                raise ScriptStaleRevision("Evidence changed since this revision; regenerate the script first")
            try:
                cleared, _ = script_gate(connection, job_id, package)
            except FactcheckError as exc:
                raise ScriptStaleRevision(str(exc)) from exc
        config = GenerationConfigV1.model_validate(parent["config"])
        eligible = [c for c in cleared["claims"] if c["claim_id"] in set(cleared["eligible_claim_ids"])]
        old = parent["sections"][index]
        claims_text = "\n".join(f"- {c['claim_id']}: {c.get('text', '')}" for c in eligible[:80])
        user = (f"Rewrite ONLY the section '{section_id}' of an existing narration script about "
                f"{package.get('topic', '')!r} in language {language}. Keep the same purpose and cite only claim ids "
                f"from the approved list. Keep every claim the current section cites unless it is unsupported by its "
                f"narration. Do not add facts that are not in the approved claims. Do not copy source wording.\n"
                f"Current section JSON:\n{json.dumps(old, ensure_ascii=False)}\n"
                f"Approved claims:\n{claims_text}\n"
                + (f"Reviewer instruction: {instruction}\n" if instruction else "")
                + 'Return JSON: {"section": {section_id, heading, narration, requires_evidence, statement_kind, '
                  'evidence: {claim_ids, source_ids, qualification}, presentation_note}}')
        system = system_prompt(language, config.script_format)
        provider = self._resolve_provider(owner_id)
        self._budget_guard(provider, config, system, user, None)
        call = {"model": config.model, "system_prompt": system, "user_prompt": user}
        try:
            result = provider.generate(**call)
        except ProviderError as exc:
            raise ScriptGenerationFailed(f"Provider call failed ({exc.code}): {exc.message}") from exc
        if result.measured_text_cost_inr and result.measured_text_cost_inr > config.max_text_cost_inr:
            raise ScriptGenerationFailed("Measured text generation cost exceeds the configured budget")
        try:
            body = load_provider_json(result.text, getattr(provider, "last_exchange", None))
        except json.JSONDecodeError as exc:
            raise ScriptGenerationFailed("Provider returned non-JSON for the section") from exc
        new = body.get("section") if isinstance(body, dict) and isinstance(body.get("section"), dict) else \
            (body.get("sections") or [None])[0] if isinstance(body, dict) and body.get("sections") else body
        if not isinstance(new, dict):
            raise ScriptGenerationFailed("Provider returned no section")
        new = {**new, "section_id": section_id}
        for keep in ("visual_type", "chapter_template"):
            if old.get(keep) is not None:
                new[keep] = old[keep]
        problems = draft_shape_problems({"title": parent["title"], "opening_hook": parent.get("opening_hook", ""),
                                         "closing": parent["closing"], "sections": [new]})
        if problems:
            raise ScriptGenerationFailed("Regenerated section has the wrong shape: " + "; ".join(problems[:5]))
        child = copy.deepcopy(parent)
        child["sections"][index] = new
        usage = {"model": config.model, "provider": getattr(provider, "name", "openai"),
                 "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                 "tokens_available": result.tokens_available, "retries": result.retries,
                 "measured_text_cost_inr": result.measured_text_cost_inr, "cost_is_measured": result.cost_is_measured,
                 "pricing_source": result.pricing_source, "currency": result.currency}
        return self._persist_derived(job_id, owner_id, parent, child, usage, idempotency_key, {
            "kind": "SECTION_REGENERATION", "operations": [{"op": "regenerate_section", "section_id": section_id}],
            "note": instruction or "", "request_digest": request_digest})

    # -- shared persistence ------------------------------------------------------------------------

    def _replay_derived(self, job_id, owner_id, language, idempotency_key, request_digest):
        self._check_ownership(job_id, owner_id)
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND idempotency_key=?",
                (job_id, language, idempotency_key)).fetchone()
        if row is None:
            return None
        body = self._materialize(job_id, dict(row))
        if (body.get("lineage") or {}).get("request_digest") != request_digest:
            raise RevisionConflict("This idempotency key was already used for a different script operation")
        return body

    def _persist_derived(self, job_id, owner_id, parent, child, usage, idempotency_key, lineage) -> dict:
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_ownership_locked(connection, job_id, owner_id)
            package = self._snapshot_current_approved(connection, job_id, owner_id)
            package_sha = digest(package)
            if package_sha != parent["package_sha256"]:
                raise ScriptStaleRevision("Evidence changed since this revision; regenerate before editing")
            try:
                cleared, binding = script_gate(connection, job_id, package)
            except FactcheckError as exc:
                raise ScriptStaleRevision(str(exc)) from exc
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM script_revisions WHERE job_id=? AND language=?",
                (job_id, parent["language"])).fetchone()[0]
            parent_row = connection.execute(
                "SELECT revision_sha256, request_sha256 FROM script_revisions WHERE job_id=? AND language=? AND version=?",
                (job_id, parent["language"], parent["version"])).fetchone()
            now = _now()
            wpm = (parent.get("word_count") or {}).get("words_per_minute") or 150
            child.update(version=version, created_at=now, approval=None, usage=usage,
                         eligible_claim_ids=cleared["eligible_claim_ids"],
                         word_count=estimate_duration(narration_word_count(child), wpm))
            child["status"] = "VALIDATION_FAILED"
            child["validation"] = {"passed": False, "validator_version": "pending", "package_sha256": package_sha,
                                   "checked_at": now, "issues": []}
            validation = validate_revision_body(child, cleared)
            diff = evidence_diff(parent, child, set(cleared["eligible_claim_ids"]))
            for sid_diff in diff["sections"]:
                for cid in sid_diff["valid_claims_removed"]:
                    validation["issues"].append({
                        "code": "EVIDENCE_REFERENCE_REMOVED", "severity": "WARNING", "section_id": sid_diff["section_id"],
                        "detail": f"Valid evidence reference {cid} was removed from this section; confirm before approval"})
            child["validation"] = validation
            child["status"] = "AWAITING_REVIEW" if validation["passed"] else "VALIDATION_FAILED"
            child["lineage"] = {**lineage, "parent_version": parent["version"],
                                "parent_revision_sha256": parent_row["revision_sha256"], "edited_by": owner_id,
                                "edited_at": now, "evidence_diff": diff, "studio_version": STUDIO_VERSION}
            body = validate_revision(child)
            data = canonical(body)
            sha = hashlib.sha256(data).hexdigest()
            uri = f"jobs/{job_id}/scripts/{parent['language']}/{sha}.json"
            self.store.write_bytes(job_id, uri, data, "application/json")
            if binding:
                connection.execute(
                    "INSERT INTO verification_script_bindings(job_id, language, version, package_id, package_sha256) "
                    "VALUES(?,?,?,?,?)", (job_id, parent["language"], version, binding["package_id"],
                                          binding["package_sha256"]))
            connection.execute(
                "INSERT INTO script_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, parent["language"], version, sha, uri, sha, package_sha, parent_row["request_sha256"],
                 body["status"], owner_id, now, idempotency_key))
            saved = self._materialize(job_id, dict(connection.execute(
                "SELECT * FROM script_revisions WHERE job_id=? AND language=? AND version=?",
                (job_id, parent["language"], version)).fetchone()))
            connection.commit()
        return saved
