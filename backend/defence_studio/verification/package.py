"""Versioned evidence package. The hash covers the machine assessment, not the approval."""

from __future__ import annotations

import hashlib
import json

from defence_studio.verification.providers import capability_registry


def _canon(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_evidence_package(*, topic: str, subject_job_id: str | None, claims: dict, media: list[dict],
                           collection: dict | None = None, now: str) -> dict:
    verdicts = claims.get("verdicts") or []
    body = {
        "schema": "defence-studio.factcheck.package.v1",
        "topic": topic,
        "subject_job_id": subject_job_id,
        "checked_at": now,
        "claims": verdicts,
        "independence_groups": claims.get("independence_groups") or {},
        "media": media,
        "collection": collection or claims.get("collection") or {},
        "missing": [item for row in verdicts for item in row.get("missing") or []],
        "providers": capability_registry(),
        "editorial_policy": {
            "unresolved": "omit",
            "partial": "qualify",
            "rule": "Unverified claims are not promoted to established fact.",
        },
        "separations": [
            "Factual support is not source trust.",
            "Source trust is not independence.",
            "An authentic file is not proof of the claim.",
            "Context match is not a reuse licence.",
        ],
        "cost": {"paid_calls": 0, "currency": "INR", "measured": True, "note": "This package used deterministic checks only."},
    }
    digest = hashlib.sha256(_canon(body)).hexdigest()
    body["content_sha256"] = digest
    return body


def render_markdown(package: dict) -> str:
    lines = [
        f"# Evidence package — {package.get('topic') or 'untitled'}",
        "",
        f"Checked at {package.get('checked_at')}. Content hash `{package.get('content_sha256')}`.",
        "",
        "This report separates factual support, source reliability, independence, media context, and reuse rights.",
        "",
        "## Claims",
    ]
    for claim in package.get("claims") or []:
        lines.append(f"- `{claim.get('claim_id')}` **{claim.get('verdict')}** (provisional {claim.get('provisional_verdict')})")
        lines.append(f"  - {claim.get('normalized_text')}")
        lines.append(f"  - Falsity assessment: {claim.get('falsity_assessment')}. Script treatment: {claim.get('script_treatment')}.")
        if claim.get("missing"):
            lines.append(f"  - Missing: {'; '.join(claim['missing'])}")
    if not package.get("claims"):
        lines.append("- No claims were assessed.")
    lines.append("")
    lines.append("## Media")
    for item in package.get("media") or []:
        lines.append(f"- `{item.get('sha256', '')[:12]}` {item.get('context_status')} / authenticity {item.get('authenticity_status')} / rights {item.get('rights', {}).get('status')}")
    if not package.get("media"):
        lines.append("- No media was assessed.")
    lines.append("")
    lines.append("## Review")
    lines.append("Machine output is pending a human decision. Approval is never implied.")
    return "\n".join(lines) + "\n"


def usable_claim_ids(package: dict, overrides: dict[str, dict] | None = None) -> list[str]:
    """Claims a script may cite after a human package approval. Overrides must be explicit."""
    overrides = overrides or {}
    usable = []
    for claim in package.get("claims") or []:
        claim_id = claim.get("claim_id")
        override = overrides.get(claim_id)
        if override:
            if override.get("accepted_for_script") and override.get("verdict") in {"VERIFIED", "PARTIALLY_SUPPORTED"}:
                usable.append(claim_id)
            continue
        if claim.get("review_required"):
            continue
        if claim.get("verdict") in {"VERIFIED", "PARTIALLY_SUPPORTED"}:
            usable.append(claim_id)
    return usable
