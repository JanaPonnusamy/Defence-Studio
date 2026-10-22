"""Strict validation seam for optional AI-assisted claim extraction.

F4 ships a deterministic extractor and calls no paid AI service: none is
configured or authorized for this phase. This module exists so that if an AI
extractor is wired in later through the provider abstraction, its output is
forced through a strict schema and malformed or incomplete output is *rejected
or quarantined* — never silently coerced into an empty object or accepted as
partial evidence.

Every AI-proposed claim must still point at a real supporting passage with
offsets into the normalized source; validation here does not confer truth, only
structural trust.
"""

from __future__ import annotations

from dataclasses import dataclass


class AiOutputError(ValueError):
    """AI output did not satisfy the strict claim schema and is rejected."""


@dataclass(frozen=True)
class ValidatedAiClaim:
    claim_text: str
    start_offset: int
    end_offset: int
    qualifiers: tuple[str, ...]
    has_negation: bool


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AiOutputError(message)


def validate_ai_claim(item: object, *, text_length: int) -> ValidatedAiClaim:
    """Validate one AI-proposed claim against ``text_length`` of normalized text.

    Rejects: non-dict items, missing/blank ``claim_text``, non-integer or
    out-of-range offsets, inverted spans, and wrong types for flags/qualifiers.
    """
    _require(isinstance(item, dict), "AI claim must be an object")
    assert isinstance(item, dict)

    claim_text = item.get("claim_text")
    _require(isinstance(claim_text, str) and claim_text.strip() != "",
             "AI claim is missing non-empty 'claim_text'")

    start, end = item.get("start_offset"), item.get("end_offset")
    _require(isinstance(start, int) and not isinstance(start, bool), "start_offset must be an int")
    _require(isinstance(end, int) and not isinstance(end, bool), "end_offset must be an int")
    _require(0 <= start < end <= text_length, "offsets out of range or inverted")

    qualifiers = item.get("qualifiers", [])
    _require(isinstance(qualifiers, list) and all(isinstance(q, str) for q in qualifiers),
             "qualifiers must be a list of strings")

    has_negation = item.get("has_negation", False)
    _require(isinstance(has_negation, bool), "has_negation must be a bool")

    return ValidatedAiClaim(
        claim_text=claim_text,
        start_offset=start,
        end_offset=end,
        qualifiers=tuple(qualifiers),
        has_negation=has_negation,
    )


def validate_ai_payload(payload: object, *, text_length: int) -> list[ValidatedAiClaim]:
    """Validate a whole AI response. The payload must be a list of claim objects.

    Raises AiOutputError on any malformed item: the whole payload is quarantined
    rather than partially accepted, so a single bad claim cannot smuggle
    unverified evidence through alongside good ones.
    """
    _require(isinstance(payload, list), "AI payload must be a list of claim objects")
    assert isinstance(payload, list)
    return [validate_ai_claim(item, text_length=text_length) for item in payload]


def ground_ai_claim(claim: ValidatedAiClaim, source_text: str) -> tuple[ValidatedAiClaim, str]:
    """Prove an AI-proposed claim is a verbatim span of the real normalized source.

    Returns ``(claim, resolution)`` where resolution is ``"offsets"`` (the stated
    offsets reproduce the text exactly) or ``"text_search"`` (the offsets were
    wrong but the text occurs exactly once, so the true offsets were recovered).
    Raises AiOutputError if the text is absent or ambiguous: a model can select a
    span, never author one, and an unlocatable "claim" is rejected, not repaired.
    """
    start, end = claim.start_offset, claim.end_offset
    if source_text[start:end] == claim.claim_text:
        return claim, "offsets"
    found = source_text.find(claim.claim_text)
    _require(found >= 0, "claim_text is not present verbatim in the normalized source")
    _require(source_text.find(claim.claim_text, found + 1) < 0,
             "claim_text occurs more than once and the stated offsets do not identify which")
    return ValidatedAiClaim(
        claim_text=claim.claim_text, start_offset=found, end_offset=found + len(claim.claim_text),
        qualifiers=claim.qualifiers, has_negation=claim.has_negation,
    ), "text_search"
