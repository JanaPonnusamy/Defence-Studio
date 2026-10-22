"""Deterministic prompt construction for Phase 3 source-grounded script drafting.

The prompt embeds only the approved claim texts (never rejected/unresolved
evidence) and instructs the model to return strict JSON matching
`ScriptRevisionV1`'s body shape, with citation IDs kept out of narration.
Terminology preservation (English technical terms inside Tamil narration) is
passed through explicitly rather than left to the model's judgement alone.
"""
from __future__ import annotations

import json

from defence_studio.contracts.script import PROMPT_VERSION
from defence_studio.services.script_integrity import numbers_in

_LANGUAGE_INSTRUCTIONS = {
    "ta": (
        "Write natural, clear spoken Tamil narration suitable for voice narration. "
        "Preserve English technical terms, acronyms, equipment names, unit names, "
        "model designations, and organization names exactly as given when translating "
        "them would reduce accuracy. Avoid awkward literal translation, unnecessary "
        "English mixing beyond required terminology, sensationalism, and unsupported "
        "patriotic or political embellishment. Use Tamil script for narration."
    ),
    "en": (
        "Write a complete, standalone English narration script. Do not mix in Tamil."
    ),
}


def system_prompt(language: str) -> str:
    return (
        "You are a disciplined defence-affairs narration writer for Defence Studio. "
        "You must ground every material factual statement (numbers, dates, quotations, "
        "names, technical specifications, military claims) in the claim_ids supplied to "
        "you. Never invent citations, source URLs, quotations, publication dates, "
        "equipment specifications, casualty figures, or military claims. Never fabricate "
        "a fact not present in the supplied evidence. Return strict JSON only, matching "
        "the requested schema, with no prose outside the JSON object. " + _LANGUAGE_INSTRUCTIONS[language]
    )


def user_prompt(*, topic: str, language: str, audience: str, tone: str, script_format: str,
                target_words: int, terminology: dict, eligible_claims: list[dict],
                corroboration: dict | None = None) -> str:
    corroboration = corroboration or {}
    claims_payload = [
        {"claim_id": c["claim_id"], "text": c.get("claim_text", ""), "source_id": c.get("source_id", ""),
         "independent_sources": corroboration.get(c["claim_id"], 1),
         # Exactly what the validator will accept as digits for a section citing this claim.
         "digits_allowed": numbers_in(c.get("claim_text", "")),
         # Same function the validator uses, so prompt and validator can never disagree.
         "verified_fact_allowed": corroboration.get(c["claim_id"], 1) >= 2,
         "allowed_statement_kinds": (["VERIFIED_FACT", "ATTRIBUTED_CLAIM", "ANALYSIS"]
                                     if corroboration.get(c["claim_id"], 1) >= 2
                                     else ["ATTRIBUTED_CLAIM", "ANALYSIS"])}
        for c in eligible_claims
    ]
    schema_hint = {
        "title": "string",
        "opening_hook": "string (optional, may be empty)",
        "sections": [{
            "section_id": "string, unique",
            "heading": "string, may be empty",
            "narration": "non-empty spoken narration, no citation IDs inside it",
            "requires_evidence": "boolean",
            "statement_kind": "one of VERIFIED_FACT (only if independent_sources >= 2), ATTRIBUTED_CLAIM, ANALYSIS, UNRESOLVED",
            "evidence": {
                "claim_ids": ["exact claim_id values from the approved evidence"],
                "source_ids": ["exact source_id values from the approved evidence; never a placeholder"],
                "qualification": "short human-readable note on what is and isn't established",
            },
            "presentation_note": "string, may be empty",
        }],
        "closing": "one non-empty sentence; do not add facts beyond the evidence",
    }
    return (
        f"Topic: {topic}\nLanguage: {language}\nAudience: {audience}\nTone: {tone}\n"
        f"Format: {script_format}\nTarget narration length: approximately {target_words} words.\n"
        f"Preserve these terms exactly where used: {json.dumps(terminology, ensure_ascii=False)}\n\n"
        "Statement-kind rules (a validator rejects the whole script otherwise):\n"
        "- Use VERIFIED_FACT ONLY when EVERY claim the section cites has verified_fact_allowed=true. "
        "Never combine several single-source claims to reach two sources.\n"
        "- A claim with verified_fact_allowed=false may only be presented as ATTRIBUTED_CLAIM "
        "(say who states it, e.g. 'according to the source') or ANALYSIS, never as established fact.\n"
        "- Give each such claim its own ATTRIBUTED_CLAIM section; keep numbers exactly as in the claim.\n"
        "- title, every narration, and closing must be non-empty.\n"
        "- Do not write any digit unless that same digit sequence appears in a cited claim. "
        "Do not introduce counts, years, or designations that are not in the cited claim text.\n"
        "- A name that contains a digit (for example an equipment designation such as GSAT-7C) may appear in a "
        "section's narration ONLY if the claim_id cited by THAT section contains it. If the claim that names it "
        "is not cited by the section, either cite that claim too or refer to it generically (the satellite, the torpedo).\n"
        "- Each section's evidence.claim_ids must list EVERY claim whose facts the narration states.\n"
        "- evidence.source_ids must be copied from the supplied source_id values. "
        "Never invent a source id and never write the word informational as an id.\n\n"
        "Approved evidence (the ONLY facts you may cite; do not use outside knowledge for "
        "any material claim):\n" + json.dumps(claims_payload, ensure_ascii=False, indent=2) + "\n\n"
        "Return a single JSON object with exactly this shape (no markdown fences, no extra keys):\n"
        + json.dumps(schema_hint, ensure_ascii=False, indent=2)
    )


DRAFT_SCHEMA_NAME = "defence_studio_script_draft_v1"


def draft_response_schema() -> dict:
    """JSON Schema for the model-authored draft only.

    Persisted revisions stay on ``ScriptRevisionV1``. This object is the subset
    the model is allowed to write: title, hook, sections, and closing. Hashes,
    usage, validation, and approval are filled by the service after checks.
    OpenAI strict structured outputs require every property to be required and
    ``additionalProperties`` false; empty strings stand in for optional text.
    """
    evidence = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim_ids", "source_ids", "qualification"],
        "properties": {
            "claim_ids": {"type": "array", "items": {"type": "string"}},
            "source_ids": {"type": "array", "items": {"type": "string"}},
            "qualification": {"type": "string"},
        },
    }
    section = {
        "type": "object",
        "additionalProperties": False,
        "required": ["section_id", "heading", "narration", "requires_evidence",
                     "statement_kind", "evidence", "presentation_note"],
        "properties": {
            "section_id": {"type": "string"},
            "heading": {"type": "string"},
            "narration": {"type": "string"},
            "requires_evidence": {"type": "boolean"},
            "statement_kind": {
                "type": "string",
                "enum": ["VERIFIED_FACT", "ATTRIBUTED_CLAIM", "ANALYSIS", "UNRESOLVED"],
            },
            "evidence": evidence,
            "presentation_note": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "opening_hook", "sections", "closing"],
        "properties": {
            "title": {"type": "string"},
            "opening_hook": {"type": "string"},
            "sections": {"type": "array", "items": section},
            "closing": {"type": "string"},
        },
    }


__all__ = ["system_prompt", "user_prompt", "PROMPT_VERSION", "DRAFT_SCHEMA_NAME", "draft_response_schema"]
