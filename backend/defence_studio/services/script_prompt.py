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
                target_words: int, terminology: dict, eligible_claims: list[dict]) -> str:
    claims_payload = [
        {"claim_id": c["claim_id"], "text": c.get("claim_text", ""), "source_id": c.get("source_id", "")}
        for c in eligible_claims
    ]
    schema_hint = {
        "title": "string",
        "opening_hook": "string (optional, may be empty)",
        "sections": [{
            "section_id": "string, unique",
            "heading": "string (optional)",
            "narration": "string, spoken narration only, no citation IDs inside it",
            "requires_evidence": "boolean",
            "evidence": {"claim_ids": ["only from the supplied claim_id list"], "source_ids": ["informational"],
                        "qualification": "short human-readable note on what is and isn't established"},
            "presentation_note": "string (optional)",
        }],
        "closing": "string",
    }
    return (
        f"Topic: {topic}\nLanguage: {language}\nAudience: {audience}\nTone: {tone}\n"
        f"Format: {script_format}\nTarget narration length: approximately {target_words} words.\n"
        f"Preserve these terms exactly where used: {json.dumps(terminology, ensure_ascii=False)}\n\n"
        "Approved evidence (the ONLY facts you may cite; do not use outside knowledge for "
        "any material claim):\n" + json.dumps(claims_payload, ensure_ascii=False, indent=2) + "\n\n"
        "Return a single JSON object with exactly this shape (no markdown fences, no extra keys):\n"
        + json.dumps(schema_hint, ensure_ascii=False, indent=2)
    )


__all__ = ["system_prompt", "user_prompt", "PROMPT_VERSION"]
