"""Deterministic offline assessment of extracted claims.

A matching text span proves the claim was copied from the acquired passage.
It does not establish that the statement is true. This module never emits
``SUPPORTED`` and never calls a model or writes human review rows.
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import closing
from datetime import datetime, timezone

from defence_studio.database import connect

POLICY_VERSION = "defence-studio.evidence-assessment.v1"
ASSESSOR_VERSION = "deterministic-span-integrity.v1"
ACTOR = "SYSTEM_EVIDENCE_REVIEW"
SPAN_LIMITATION = (
    "A matching text span proves extraction integrity, not factual truth."
)


class AssessmentInputError(ValueError):
    pass


def _passage_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _conflict_partners(claim_id: str, conflicts: list) -> list[dict]:
    partners = []
    for conflict in conflicts or []:
        if not isinstance(conflict, dict):
            continue
        a, b = conflict.get("claim_id_a"), conflict.get("claim_id_b")
        if claim_id not in (a, b):
            continue
        other = b if claim_id == a else a
        partners.append({
            "group_id": conflict.get("group_id") or "",
            "other_claim_id": other or "",
        })
    return partners


def _integrity(claim: dict, normalized: dict | None) -> tuple[bool, str, str]:
    """Return (ok, explanation, matched_span). The span is empty when invalid."""
    claim_id = claim.get("claim_id") or ""
    passage = claim.get("passage") if isinstance(claim.get("passage"), dict) else {}
    text = (normalized or {}).get("normalized_text") or ""
    start, end = passage.get("start_offset"), passage.get("end_offset")
    claim_text = claim.get("claim_text")
    if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(text)):
        return False, (
            f"Passage integrity failed for {claim_id}: offsets {start}:{end} "
            f"are outside the normalized text of length {len(text)}. "
            "No quotation was taken from outside that span."
        ), ""
    span = text[start:end]
    if span != claim_text or passage.get("text") != claim_text or passage.get("source_id") != claim.get("source_id"):
        return False, (
            f"Passage integrity failed for {claim_id}: the normalized slice at "
            f"{start}:{end} does not equal the stored claim text. "
            "No quotation was taken from outside that span."
        ), ""
    return True, "", span


def _high_risk_reasons(claim: dict, normalized_count: int) -> list[str]:
    reasons = []
    if claim.get("qualifiers"):
        reasons.append("an attribution hedge is present")
    if claim.get("has_negation"):
        reasons.append("a negation is present")
    entities = claim.get("entities") if isinstance(claim.get("entities"), dict) else {}
    if entities.get("quantities"):
        reasons.append("a quantity is present")
    if entities.get("designations"):
        reasons.append("a designation is present")
    if not claim.get("source_published_at"):
        reasons.append("the source has no publication date")
    if normalized_count < 2:
        reasons.append("fewer than two normalized sources are available")
    return reasons


def assess_claim(claim: dict, analysis: dict) -> dict:
    """Classify one claim. ``SUPPORTED`` is not a possible return value."""
    if not isinstance(claim, dict) or not claim.get("claim_id"):
        raise AssessmentInputError("A claim needs a claim_id")
    sources = analysis.get("normalized_sources") or []
    by_id = {row.get("source_id"): row for row in sources if isinstance(row, dict)}
    normalized = by_id.get(claim.get("source_id"))
    normalized_count = sum(1 for row in by_id.values() if row.get("status") == "NORMALIZED" and row.get("normalized_text"))
    ok, failure, span = _integrity(claim, normalized if isinstance(normalized, dict) else None)
    passage = claim.get("passage") if isinstance(claim.get("passage"), dict) else {}
    start = passage.get("start_offset") if isinstance(passage.get("start_offset"), int) else None
    end = passage.get("end_offset") if isinstance(passage.get("end_offset"), int) else None
    base = {
        "claim_id": claim["claim_id"],
        "source_id": claim.get("source_id") or "",
        "start_offset": start,
        "end_offset": end,
        "passage_sha256": _passage_sha(span) if ok else "",
        "actor": ACTOR,
        "assessor_version": ASSESSOR_VERSION,
        "policy_version": POLICY_VERSION,
    }
    if not ok or normalized is None or normalized.get("status") != "NORMALIZED":
        explanation = failure or (
            f"Passage integrity failed for {claim['claim_id']}: source "
            f"{claim.get('source_id')} has no normalized text."
        )
        return {**base, "outcome": "UNSUPPORTED", "explanation": explanation,
                "limitations": "The stored passage could not be verified against the normalized source."}
    partners = _conflict_partners(claim["claim_id"], analysis.get("conflicts") or [])
    if partners:
        others = []
        claims_by_id = {row.get("claim_id"): row for row in (analysis.get("claims") or []) if isinstance(row, dict)}
        for partner in partners:
            other = claims_by_id.get(partner["other_claim_id"]) or {}
            others.append(
                f"group {partner['group_id']} claim {partner['other_claim_id']}: "
                f"\"{other.get('claim_text') or ''}\""
            )
        explanation = (
            f"Claim {claim['claim_id']} conflicts with stored evidence. "
            f"This passage at {start}:{end}: \"{span}\". "
            + " ".join(others)
            + " Neither side is verified."
        )
        return {**base, "outcome": "CONTRADICTED", "explanation": explanation,
                "limitations": "A stored conflict is not a decision about which passage is true."}
    reasons = _high_risk_reasons(claim, normalized_count)
    quoted = (
        f"Claim {claim['claim_id']} matches passage offsets {start}-{end} in source "
        f"{claim.get('source_id')}: \"{span}\". {SPAN_LIMITATION}"
    )
    if reasons:
        return {**base, "outcome": "NEEDS_REVIEW",
                "explanation": quoted + " Left for review because " + "; ".join(reasons) + ".",
                "limitations": SPAN_LIMITATION}
    return {**base, "outcome": "PARTIALLY_SUPPORTED",
            "explanation": quoted + " No qualifier, negation, quantity, designation, missing date, "
            "single-source limit, or stored conflict was found. This is not a factual verification.",
            "limitations": SPAN_LIMITATION}


def assess_and_store(database_file, job_id: str, analysis: dict, analysis_sha256: str) -> list[dict]:
    """Persist one row per claim. A repeat on the same analysis hash is a no-op."""
    if not isinstance(analysis_sha256, str) or len(analysis_sha256) != 64:
        raise AssessmentInputError("analysis_sha256 must be a SHA-256 hex digest")
    claims = analysis.get("claims") or []
    if not isinstance(claims, list):
        raise AssessmentInputError("analysis claims must be a list")
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    stored = []
    with closing(connect(database_file)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = connection.execute("SELECT 1 FROM workflow_jobs WHERE job_id=?", (job_id,)).fetchone()
        if job is None:
            connection.rollback()
            raise AssessmentInputError("Workflow job not found")
        for claim in claims:
            row = assess_claim(claim, analysis)
            if row["outcome"] == "SUPPORTED":
                connection.rollback()
                raise AssessmentInputError("The deterministic checker must not emit SUPPORTED")
            existing = connection.execute(
                "SELECT * FROM claim_assessments WHERE job_id=? AND claim_id=? AND analysis_sha256=? AND policy_version=?",
                (job_id, row["claim_id"], analysis_sha256, POLICY_VERSION),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO claim_assessments(assessment_id,job_id,claim_id,analysis_sha256,policy_version,"
                    "outcome,source_id,passage_sha256,start_offset,end_offset,explanation,limitations,actor,"
                    "assessor_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), job_id, row["claim_id"], analysis_sha256, POLICY_VERSION,
                     row["outcome"], row["source_id"], row["passage_sha256"], row["start_offset"], row["end_offset"],
                     row["explanation"], row["limitations"], row["actor"], row["assessor_version"], now),
                )
            saved = connection.execute(
                "SELECT * FROM claim_assessments WHERE job_id=? AND claim_id=? AND analysis_sha256=? AND policy_version=?",
                (job_id, row["claim_id"], analysis_sha256, POLICY_VERSION),
            ).fetchone()
            stored.append(dict(saved))
        connection.commit()
    return stored
