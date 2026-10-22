"""AI-assisted claim *selection* over deterministic normalized source text.

Boundary: deterministic normalization (``normalize.py``) and the deterministic
extractor (``claims.py``) are untouched. This stage sends the real normalized
text of each source to the text provider and asks it only to SELECT verbatim
spans that state checkable facts. Every returned span is then proven against the
same normalized text (``ai_schema.ground_ai_claim``); anything not found verbatim
is rejected and reported. Entities, qualifiers and negation are recomputed
deterministically from the span -- the model contributes a selection, never
wording, never citations, never truth.

Output claims reuse ``ExtractedClaimV1`` (same ``claim_id`` scheme as the
deterministic extractor, so an identical span is recognisable as a duplicate)
and are stored in a separate artifact; nothing here writes the analysis result,
evidence reviews or approvals.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from defence_studio.analysis import claims as det
from defence_studio.analysis.ai_schema import AiOutputError, ground_ai_claim, validate_ai_payload
from defence_studio.contracts.analysis import AnalysisLimits, EvidencePassageV1, ExtractedClaimV1, PassageKind

AI_EXTRACTION_SCHEMA = "defence-studio.ai-claim-extraction.v1"
AI_EXTRACTOR_VERSION = "defence-studio.claim-extractor.ai-span.v1"
AI_PROMPT_VERSION = "defence-studio.ai-claim-span-prompt.v1"
AI_EXTRACTION_METHOD = "ai-assisted-span-selection"

# Strict API-level schema. Offsets are requested but only advisory: grounding
# recomputes them from the text when they are wrong.
AI_CLAIMS_JSON_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["claims"],
    "properties": {"claims": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["claim_text", "start_offset", "end_offset"],
        "properties": {"claim_text": {"type": "string"},
                       "start_offset": {"type": "integer"}, "end_offset": {"type": "integer"}}}}},
}

SYSTEM_PROMPT = (
    "You select checkable factual statements from a source document for a defence-affairs "
    "research team. You must NOT write, paraphrase, merge, translate, correct or summarise. "
    "For each statement, copy the exact sentence from the document, character for character, "
    "and give its start/end character offsets in the document. Skip navigation, legal text, "
    "advertising and opinion without attribution. Do not use outside knowledge. "
    "Return only the JSON object requested."
)


def user_prompt(*, topic: str, source_id: str, text: str, max_claims: int) -> str:
    return (
        f"Topic: {topic}\nSource id: {source_id}\n"
        f"Select at most {max_claims} verbatim sentences from the document below that state "
        "specific, checkable facts relevant to the topic (numbers, dates, named equipment, "
        "actions, attributed statements). Offsets are 0-based character positions into the "
        "document text exactly as given, end exclusive.\n\n"
        "DOCUMENT START\n" + text + "\nDOCUMENT END\n\n"
        'Return JSON: {"claims": [{"claim_text": "...", "start_offset": 0, "end_offset": 0}]}'
    )


def parse_provider_text(text: str) -> object:
    """Parse the provider's JSON and unwrap ``{"claims": [...]}``; strict, no repair."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AiOutputError(f"response is not valid JSON: {exc}") from None
    if not isinstance(payload, dict) or set(payload) != {"claims"}:
        raise AiOutputError("response must be an object with exactly one key, 'claims'")
    return payload["claims"]


def build_claims(*, payload: object, source: dict, limits: AnalysisLimits,
                 known_claim_ids: set) -> tuple[list[dict], list[dict]]:
    """Validate a parsed payload against one normalized source.

    Structural failure raises AiOutputError (the whole source is quarantined).
    Per-claim grounding/bounds failures are returned in ``rejected`` with the
    reason, so nothing the model said disappears silently."""
    text = source["normalized_text"]
    validated = validate_ai_payload(payload, text_length=len(text))
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: set = set()
    for item in validated:
        try:
            claim, resolution = ground_ai_claim(item, text)
        except AiOutputError as exc:
            rejected.append({"claim_text": item.claim_text[:300], "reason": str(exc)})
            continue
        if not (limits.min_claim_chars <= len(claim.claim_text) <= limits.max_claim_chars):
            rejected.append({"claim_text": claim.claim_text[:300], "reason": "claim length outside configured bounds"})
            continue
        claim_id = "claim-" + hashlib.sha256(
            f"{source['source_id']}:{claim.start_offset}:{claim.end_offset}".encode()).hexdigest()[:16]
        if claim_id in seen:
            rejected.append({"claim_text": claim.claim_text[:300], "reason": "duplicate span in model output"})
            continue
        seen.add(claim_id)
        sentence = claim.claim_text
        qualifiers = det._qualifiers(sentence.lower())
        negation = bool(det._NEGATION.search(sentence))
        warnings = ["Selected by an AI model; the span was verified verbatim against the normalized source. "
                    "This is not independent verification of truth."]
        if resolution == "text_search":
            warnings.append("Model offsets were wrong; offsets were recovered by exact text search.")
        if qualifiers:
            warnings.append("Statement is qualified/attributed; not independently verified.")
        if negation:
            warnings.append("Statement contains a negation; preserve polarity in review.")
        artifact = source.get("original_artifact") or {}
        dates = source.get("dates") or {}
        extracted = ExtractedClaimV1(
            claim_id=claim_id, claim_text=sentence, source_id=source["source_id"],
            original_language=source["language_detected"], normalized_language=source["language_detected"],
            passage=EvidencePassageV1(
                kind=PassageKind.TEXT_SPAN, source_id=source["source_id"], artifact_uri=artifact.get("uri"),
                text=sentence, start_offset=claim.start_offset, end_offset=claim.end_offset),
            source_published_at=dates.get("published_at"), acquisition_timestamp=dates.get("acquired_at"),
            entities=det._entities(sentence), qualifiers=qualifiers, has_negation=negation, is_explicit=True,
            warnings=tuple(warnings), extraction_method=AI_EXTRACTION_METHOD,
            extractor_version=AI_EXTRACTOR_VERSION,
        )
        record = json.loads(json.dumps(asdict(extracted), default=str))
        record["offset_resolution"] = resolution
        record["duplicates_deterministic_claim"] = claim_id in known_claim_ids
        accepted.append(record)
    return accepted, rejected
