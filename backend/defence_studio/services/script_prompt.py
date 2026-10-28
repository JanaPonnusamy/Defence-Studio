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

# At or below this target a script is a short bulletin: few, tight sections and no long-form minimums.
SHORT_TARGET_WORDS = 300

_LANGUAGE_INSTRUCTIONS = {
    "ta": (
        "Write natural, conversational spoken Tamil, like a Tamil YouTube presenter explaining the "
        "story directly to viewers. Write for the ear, not the page: no newspaper editorial, government "
        "press release or literal English-to-Tamil translation. Use familiar sentence patterns, everyday "
        "words and natural spoken verb endings (for example கொள்முதல், ஒப்புதல் கொடுத்தது, பாதுகாப்பு அமைச்சர்). "
        "Where they fit, use conversational turns such as என்ன நடந்தது தெரியுமா?, இதில் முக்கியமான விஷயம் "
        "என்னனா…, அதற்குப் பிறகு என்ன நடந்தது?, இதனால் என்ன மாற்றம் வரும்? — as examples only, never "
        "repeated formulas. Avoid stiff formal phrasing such as இதன் காரணமாக, மேற்கண்ட நிகழ்வானது, "
        "குறிப்பிடத்தக்க தாக்கத்தை ஏற்படுத்தியுள்ளது, விரிவான தகவல்களை காண்போம் when a simpler spoken form "
        "says the same thing. Formal → natural examples: 'இந்தச் சம்பவமானது உலகளாவிய பாதுகாப்புச் சூழலில் "
        "குறிப்பிடத்தக்க தாக்கத்தை ஏற்படுத்தியுள்ளது.' → 'இந்தச் சம்பவத்தால் உலக நாடுகளின் பாதுகாப்பு நிலைமையில் "
        "என்ன மாற்றம் வரப்போகிறது தெரியுமா?'; 'இதன் காரணமாக அந்நாட்டின் இராணுவ நடவடிக்கைகள் தீவிரமடைந்துள்ளன.' "
        "→ 'இதற்குப் பிறகு அந்த நாடு தனது ராணுவ நடவடிக்கைகளை இன்னும் தீவிரப்படுத்தியிருக்கிறது.'; "
        "'இதுகுறித்த விரிவான தகவல்களை இப்போது காண்போம்.' → 'அடுத்து என்ன நடந்தது, இதனால் யாருக்கு என்ன "
        "பாதிப்பு என்பதைப் பார்ப்போம்.' Talk to the viewer: introduce context, explain unfamiliar facts "
        "simply, build curiosity through logical storytelling, with relevant questions but no forced "
        "excitement, exaggerated drama or repetitive rhetorical questions. One clear idea per sentence, "
        "short paragraphs, commas only at natural breathing pauses. Every sentence must be complete and "
        "grammatical: subject and finite verb, correct case markers, suffixes and tense, natural spoken "
        "word order, and pronouns that clearly name the country, person or organisation meant; no "
        "fragments, bullet-point sentences or needless repetition. Keep one consistent, broadly "
        "understandable spoken register: no invented slang or mixed regional dialects. Conversational "
        "wording must never change facts, invent details, remove uncertainty or turn an allegation into "
        "a confirmed event; keep reporting, analysis and speculation clearly distinct. Before returning, "
        "read every sentence as if presenting the video and rewrite any that sounds like formal "
        "translated prose or an AI-generated announcement. Never put spaces "
        "inside a Tamil word or drop vowel signs or pulli. Never put headings, stage directions or "
        "production notes inside narration. Expand an unfamiliar acronym at first mention when it helps "
        "viewers. Keep attribution and the original degree of certainty; never round or change a number. "
        "Name who said an attributed claim (for example பாதுகாப்பு அமைச்சர் நாடாளுமன்றத்தில் தெரிவித்தார், "
        "அரசின் PIB அறிக்கையின்படி); never fall back on a vague என்று கூறப்படுகிறது. "
        "Never abbreviate: write "
        "ரூபாய் in full after the amount (2,12,000 கோடி ரூபாய்), never ரூ. or Rs. "
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


# script_format "explainer": a YouTube defence presenter rather than a news bulletin (Tamil only).
_TAMIL_EXPLAINER_STYLE = (
    " Style for this explainer: sound like a knowledgeable Tamil YouTube defence presenter talking to "
    "viewers, not a press release, textbook or news bulletin. Use natural spoken sentence patterns in "
    "standard written spelling throughout (பார்க்கலாம், சொல்கிறார்கள், இருக்கிறது; never colloquial "
    "spellings such as சொல்றாங்க or இருக்கு), one main idea per sentence, "
    "and smooth transitions between sections. You may use one or two genuine viewer questions followed "
    "straight away by the answer. Explain any technical term in simple Tamil the first time it appears. "
    "No regional slang, no stage directions, and never trade accuracy for drama: if the evidence does not "
    "say something happened, do not imply it. Do not invent audience reactions or framing (for example "
    "பலர் கேள்வி எழுப்புகிறார்கள்) or evaluative filler such as நம்பிக்கையை ஏற்படுத்தியுள்ளது; every sentence "
    "must carry information from the evidence or explain it. The opening hook must not claim anything about "
    "the viewers (what they asked, think or know). Write month names in Tamil (ஜூலை 28, மே 7)."
)


def system_prompt(language: str, script_format: str = "") -> str:
    style = _TAMIL_EXPLAINER_STYLE if language == "ta" and script_format.strip().lower() == "explainer" else ""
    return (
        "You are a disciplined defence-affairs narration writer for Defence Studio. "
        "You must ground every material factual statement (numbers, dates, quotations, "
        "names, technical specifications, military claims) in the claim_ids supplied to "
        "you. Never invent citations, source URLs, quotations, publication dates, "
        "equipment specifications, casualty figures, or military claims. Never fabricate "
        "a fact not present in the supplied evidence. Return strict JSON only, matching "
        "the requested schema, with no prose outside the JSON object. " + _LANGUAGE_INSTRUCTIONS[language] + style
    )


def user_prompt(*, topic: str, language: str, audience: str, tone: str, script_format: str,
                target_words: int, terminology: dict, eligible_claims: list[dict],
                min_words: int = 900, max_words: int = 1300,
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
    structure = (
        f"Write {max(4, min(8, round(target_words / 30)))} body sections of two or three full sentences each, "
        "like a well-paced news bulletin. "
        "Reach the required word range by explaining what each approved fact means for the audience; "
        "never repeat a sentence or idea and never add facts."
        if target_words <= SHORT_TARGET_WORDS else
        "Write at least eight body sections. Expand with complete spoken paragraphs that restate and "
        "qualify the approved evidence; use digit-free transitions and framing. Do not pad with invented facts.")
    return (
        f"Topic: {topic}\nLanguage: {language}\nAudience: {audience}\nTone: {tone}\n"
        f"Format: {script_format}\nTarget narration length: approximately {target_words} words. "
        f"The spoken narration (opening_hook + every section narration + closing, not headings, "
        f"JSON keys, evidence maps, or metadata) MUST be between {min_words} and {max_words} words inclusive. "
        f"{structure}\n"
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


def draft_response_schema(target_words: int | None = None) -> dict:
    """JSON Schema for the model-authored draft only.

    Persisted revisions stay on ``ScriptRevisionV1``. This object is the subset
    the model is allowed to write: title, hook, sections, and closing. Hashes,
    usage, validation, and approval are filled by the service after checks.
    Short targets (``<= SHORT_TARGET_WORDS``) drop the long-form length minimums.
    OpenAI strict structured outputs require every property to be required and
    ``additionalProperties`` false; empty strings stand in for optional text.
    """
    evidence = {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim_ids", "source_ids", "qualification"],
        "properties": {
            "claim_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "source_ids": {"type": "array", "items": {"type": "string"}},
            "qualification": {"type": "string"},
        },
    }
    short = target_words is not None and target_words <= SHORT_TARGET_WORDS
    section = {
        "type": "object",
        "additionalProperties": False,
        "required": ["section_id", "heading", "narration", "requires_evidence",
                     "statement_kind", "evidence", "presentation_note"],
        "properties": {
            "section_id": {"type": "string"},
            "heading": {"type": "string"},
            "narration": {"type": "string", "minLength": 20 if short else 700},
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
            "title": {"type": "string", "minLength": 8},
            "opening_hook": {"type": "string", "minLength": 20 if short else 400},
            "sections": {"type": "array", "items": section, "minItems": 3 if short else 6},
            "closing": {"type": "string", "minLength": 20 if short else 200},
        },
    }


__all__ = ["system_prompt", "user_prompt", "PROMPT_VERSION", "DRAFT_SCHEMA_NAME", "draft_response_schema"]
