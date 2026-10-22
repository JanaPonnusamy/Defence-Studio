"""Evidence-grounded factual integrity checks for a draft script revision.

Does not ask the model whether its own output is correct. Every structural and
evidence check here is deterministic and re-derivable from the approved
evidence package and the revision body alone.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from defence_studio.contracts.script import ValidationIssueV1, ValidationResultV1
from defence_studio.services.script_integrity import integrity_issues

VALIDATOR_VERSION = "defence-studio.script-validator.v2"

# A material-fact heuristic: sections whose narration contains a number, a
# quotation mark, or a 4-digit year are treated as needing evidence support
# even if the caller did not mark them `requires_evidence`. This never grants
# a pass without evidence; it only ever adds a stricter check.
_NUMBER_RE = re.compile(r"\d")
_QUOTE_RE = re.compile(r"[\"“”]")


def _looks_factual(narration: str) -> bool:
    return bool(_NUMBER_RE.search(narration) or _QUOTE_RE.search(narration))


def validate_revision_body(revision: dict, approved_package: dict) -> dict:
    """Return a `ValidationResultV1`-shaped dict (not yet embedded in the revision).

    `approved_package` must be the exact APPROVED package this revision claims
    to be bound to (the caller is responsible for the package_sha256 match --
    this function only checks evidence *content*, not binding).
    """
    issues: list[dict] = []
    eligible = set(approved_package.get("eligible_claim_ids", []))
    claims_by_id = {c["claim_id"]: c for c in approved_package.get("claims", [])}
    sources_by_id = {s["source_id"]: s for s in approved_package.get("sources", [])}

    sections = revision.get("sections", [])
    if not sections:
        issues.append(dict(code="NO_SECTIONS", severity="ERROR", detail="A script revision must have at least one narration section"))

    seen_ids = set()
    for section in sections:
        sid = section.get("section_id", "")
        if sid in seen_ids:
            issues.append(dict(code="DUPLICATE_SECTION_ID", severity="ERROR", section_id=sid,
                               detail=f"Duplicate section_id {sid!r}"))
        seen_ids.add(sid)
        narration = section.get("narration", "")
        if not narration.strip():
            issues.append(dict(code="EMPTY_NARRATION", severity="ERROR", section_id=sid, detail="Section narration is empty"))
        evidence = section.get("evidence") or {}
        claim_ids = evidence.get("claim_ids", [])
        requires_evidence = section.get("requires_evidence", True) or _looks_factual(narration)
        if requires_evidence:
            if not claim_ids:
                issues.append(dict(code="MISSING_EVIDENCE", severity="ERROR", section_id=sid,
                                   detail="A factual section cites no claim_ids"))
            unknown = [c for c in claim_ids if c not in claims_by_id]
            if unknown:
                issues.append(dict(code="UNKNOWN_CLAIM_REFERENCE", severity="ERROR", section_id=sid,
                                   detail=f"References claim(s) not present in the approved package: {unknown}"))
            unapproved = [c for c in claim_ids if c in claims_by_id and c not in eligible]
            if unapproved:
                issues.append(dict(code="UNAPPROVED_CLAIM_REFERENCE", severity="ERROR", section_id=sid,
                                   detail=f"References claim(s) not approved for use: {unapproved}"))
            for cid in claim_ids:
                claim = claims_by_id.get(cid)
                if claim and claim.get("source_id") not in sources_by_id:
                    issues.append(dict(code="DANGLING_SOURCE_REFERENCE", severity="ERROR", section_id=sid,
                                       detail=f"Claim {cid} references a source outside the approved package"))
            if not evidence.get("qualification", "").strip():
                issues.append(dict(code="MISSING_QUALIFICATION", severity="WARNING", section_id=sid,
                                   detail="A factual section has no qualification note for human review"))
        source_ids = evidence.get("source_ids") or []
        if source_ids and not isinstance(source_ids, list):
            issues.append(dict(code="UNKNOWN_SOURCE_REFERENCE", severity="ERROR", section_id=sid,
                               detail="evidence.source_ids must be a list of approved source ids"))
        elif isinstance(source_ids, list):
            unknown_sources = [s for s in source_ids if s not in sources_by_id]
            if unknown_sources:
                issues.append(dict(code="UNKNOWN_SOURCE_REFERENCE", severity="ERROR", section_id=sid,
                                   detail=f"References source(s) not present in the approved package: {unknown_sources}"))
        # Citation IDs must never be spoken aloud in narration.
        for cid in claim_ids:
            if cid and cid in narration:
                issues.append(dict(code="CITATION_LEAKED_INTO_NARRATION", severity="ERROR", section_id=sid,
                                   detail=f"Claim ID {cid!r} appears inside spoken narration text"))

    if not (revision.get("closing") or "").strip():
        issues.append(dict(code="MISSING_CLOSING", severity="ERROR", detail="A script revision must have a closing section"))
    if not (revision.get("title") or "").strip():
        issues.append(dict(code="MISSING_TITLE", severity="ERROR", detail="A script revision must have a title"))

    issues.extend(integrity_issues(revision, approved_package))
    passed = not any(i["severity"] == "ERROR" for i in issues)
    result = dict(
        passed=passed, validator_version=VALIDATOR_VERSION,
        package_sha256=revision.get("package_sha256", ""),
        checked_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        issues=[ValidationIssueV1.model_validate(i).model_dump() for i in issues],
    )
    return ValidationResultV1.model_validate(result).model_dump()
