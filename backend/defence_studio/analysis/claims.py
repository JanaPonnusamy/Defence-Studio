"""Deterministic claim and supporting-evidence extraction.

This extractor is intentionally conservative and rule-based — there is no AI
provider configured or authorized for F4 (see ``ai_schema.py`` for the strict
validation seam a future AI extractor would have to pass).

Core safety property: a claim's text is the *verbatim* sentence span taken from
the normalized source, with exact character offsets. Nothing is paraphrased, so
qualifiers ("reportedly", "according to officials"), negations, dates, units and
military designations are preserved by construction. Extraction records where a
statement came from; it never asserts the statement is true, and it only emits
explicit source spans — never inferred interpretations.
"""

from __future__ import annotations

import hashlib
import re

from defence_studio.analysis.normalize import SubtitleCue
from defence_studio.contracts.analysis import (
    EXTRACTOR_VERSION,
    AnalysisLimits,
    CandidateEntities,
    EvidencePassageV1,
    ExtractedClaimV1,
    PassageKind,
)

# Hedging/attribution markers that must never be stripped. Presence is recorded
# so a reviewer sees the statement is qualified rather than definitive.
_QUALIFIERS = (
    "reportedly", "allegedly", "purportedly", "apparently", "according to",
    "sources say", "sources said", "officials said", "official said",
    "is said to", "said to be", "claimed", "claims", "claim that",
    "estimated", "estimate", "planned", "expected", "likely", "possibly",
    "unconfirmed", "not independently confirmed", "reported that", "may ", "could ",
)
_NEGATION = re.compile(
    r"\b(not|no|never|cannot|can't|won't|didn't|doesn't|isn't|aren't|wasn't|"
    r"weren't|denied|denies|deny|rejected|refutes?)\b|இல்லை",
    re.IGNORECASE,
)
_DESIGNATION = re.compile(
    r"\b[A-Za-z]+-\d+[A-Za-z0-9-]*\b|\b[A-Z]{2,}-\d+[A-Z0-9-]*\b|\b[A-Z]{2,}\d+\b"
)
_QUANTITY = re.compile(
    r"\bMach\s?\d+(?:\.\d+)?\b|"
    r"\b\d[\d,]*(?:\.\d+)?\s?"
    r"(?:%|percent|km|kilomet(?:re|er)s?|metres?|meters?|kg|tonnes?|tons?|crore|"
    r"lakh|billion|million|thousand|units?|aircraft|jets?|fighters?|missiles?|"
    r"days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)
_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\.?\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\.?\s+\d{4}\b"
    r"|\b(?:19|20)\d{2}\b",
)
_NAMED = re.compile(r"\b[A-Z][\w.]+(?:\s+[A-Z][\w.]+)+\b|\b[A-Z]{2,}\b")
_SENTENCE = re.compile(r"[^.!?\n]*(?:[.!?]+|\n|$)")
# A match ending in an initial/abbreviation ("U.S.", "Dr.", "Lt. Gen.") is not a
# sentence end; merging avoids truncated, misleading "claims".
_ABBREV_END = re.compile(
    r"(?:\b[A-Z]|\bNo|\bSt|\bMr|\bMrs|\bMs|\bDr|\bLt|\bGen|\bCol|\bCapt|\bvs|\bapprox)\.\s*$"
)
_WORD = re.compile(r"\w+", re.UNICODE)
# Site chrome / legal / calls-to-action: never a statement about the topic.
_BOILERPLATE = re.compile(
    r"skip to (?:the )?(?:main )?content|\u00a9|\(c\)\s*\d{4}|all rights reserved|privacy policy|"
    r"terms (?:of use|and conditions)|cookie|newsletter|subscribe|sign[- ]?up|follow us|"
    r"share (?:this|on)|read more|click here|enable javascript|"
    r"\bfacebook\b|\btwitter\b|\blinkedin\b|\binstagram\b|\byoutube\b",
    re.IGNORECASE,
)
# Bare URLs / citation locators and page-title separators are references, not claims.
_URL_LIKE = re.compile(
    r"https?://|\bwww\.|\b[\w-]+\.(?:com|org|net|edu|gov|ru)\b|^[\w.-]+/\S+|\s\|\s|</?[A-Za-z][^>]*>",
    re.IGNORECASE,
)
_TERMINAL = re.compile(r"[.!?\u0964\"'\u201d\u2019]\s*$")
_MIN_WORDS = 5
_HEADING_MAX_WORDS = 8


def _is_boilerplate(sentence: str, entities: CandidateEntities, has_cues: bool) -> bool:
    """True for site chrome, URL/citation fragments and bare headings.

    A heading is a short line with no terminal punctuation and no quantity
    (specification rows such as "Warhead 143 kg" are kept). Subtitle cues are
    exempt from the heading rule because cues are rarely punctuated."""
    if _BOILERPLATE.search(sentence) or _URL_LIKE.search(sentence):
        return True
    words = _WORD.findall(sentence)
    if len(words) < _MIN_WORDS:
        return True
    return (
        not has_cues
        and len(words) <= _HEADING_MAX_WORDS
        and not _TERMINAL.search(sentence)
        and not entities.quantities
    )



def topic_terms(topic: str) -> frozenset:
    return frozenset(t.lower() for t in _WORD.findall(topic or "") if len(t) >= 3)


def _sentence_spans(text: str):
    """Yield (start, end, sentence_text) with offsets into ``text``."""
    pending_start = None
    for match in _SENTENCE.finditer(text):
        raw = match.group()
        if not raw:
            continue
        if pending_start is None:
            lead = len(raw) - len(raw.lstrip())
            if not raw.strip():
                continue
            pending_start = match.start() + lead
        end = match.start() + len(raw.rstrip())
        stripped_raw = raw.rstrip()
        decimal_point = (len(stripped_raw) >= 2 and stripped_raw[-1] == "." and stripped_raw[-2].isdigit()
                         and text[match.end():match.end() + 1].isdigit())      # "Mach 1.8", "2.5 m"
        if decimal_point or (_ABBREV_END.search(stripped_raw + " ") and not raw.endswith("\n")
                             and match.end() < len(text)):
            continue                      # abbreviation / decimal point, keep accumulating
        yield pending_start, end, text[pending_start:end]
        pending_start = None
    if pending_start is not None:
        tail = text[pending_start:].rstrip()
        if tail:
            yield pending_start, pending_start + len(tail), tail


def _cue_for_span(cues: tuple[SubtitleCue, ...], start: int, end: int):
    covering = [c for c in cues if c.text_start < end and c.text_end > start]
    if not covering:
        return None
    return min(c.start for c in covering), max(c.end for c in covering)


def _dedupe_keep_order(values):
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def _entities(sentence: str) -> CandidateEntities:
    return CandidateEntities(
        named=_dedupe_keep_order(m.group() for m in _NAMED.finditer(sentence)),
        quantities=_dedupe_keep_order(m.group().strip() for m in _QUANTITY.finditer(sentence)),
        dates=_dedupe_keep_order(m.group() for m in _DATE.finditer(sentence)),
        designations=_dedupe_keep_order(m.group() for m in _DESIGNATION.finditer(sentence)),
    )


def _qualifiers(sentence_lower: str) -> tuple[str, ...]:
    return tuple(q.strip() for q in _QUALIFIERS if q in sentence_lower)


def extract_claims(
    *,
    source_id: str,
    text: str,
    language: str,
    cues: tuple[SubtitleCue, ...],
    artifact_uri: str | None,
    source_published_at: str | None,
    acquisition_timestamp: str | None,
    topic: str,
    limits: AnalysisLimits,
) -> list[ExtractedClaimV1]:
    terms = topic_terms(topic)
    claims: list[ExtractedClaimV1] = []
    for start, end, sentence in _sentence_spans(text):
        if not (limits.min_claim_chars <= len(sentence) <= limits.max_claim_chars):
            continue
        entities = _entities(sentence)
        if _is_boilerplate(sentence, entities, bool(cues)):
            continue
        tokens = {t.lower() for t in _WORD.findall(sentence)}
        relevant = bool(terms & tokens)
        has_signal = bool(
            entities.quantities or entities.dates or entities.designations or entities.named
        )
        # A candidate claim must carry a factual signal and, when a topic is
        # given, be relevant to it. Pure boilerplate with no signal is skipped.
        if not has_signal:
            continue
        if terms and not (relevant or entities.designations):
            continue
        sentence_lower = sentence.lower()
        qualifiers = _qualifiers(sentence_lower)
        has_negation = bool(_NEGATION.search(sentence))
        cue_window = _cue_for_span(cues, start, end)
        passage = EvidencePassageV1(
            kind=PassageKind.SUBTITLE_CUE if cue_window else PassageKind.TEXT_SPAN,
            source_id=source_id,
            artifact_uri=artifact_uri,
            text=sentence,
            start_offset=start,
            end_offset=end,
            timestamp_start=cue_window[0] if cue_window else None,
            timestamp_end=cue_window[1] if cue_window else None,
        )
        warnings: list[str] = []
        if qualifiers:
            warnings.append("Statement is qualified/attributed; not independently verified by F4.")
        if has_negation:
            warnings.append("Statement contains a negation; preserve polarity in review.")
        claim_id = "claim-" + hashlib.sha256(
            f"{source_id}:{start}:{end}".encode()
        ).hexdigest()[:16]
        claims.append(ExtractedClaimV1(
            claim_id=claim_id,
            claim_text=sentence,
            source_id=source_id,
            original_language=language,
            normalized_language=language,
            passage=passage,
            source_published_at=source_published_at,
            acquisition_timestamp=acquisition_timestamp,
            entities=entities,
            qualifiers=qualifiers,
            has_negation=has_negation,
            is_explicit=True,
            warnings=tuple(warnings),
            extraction_method="deterministic-sentence-span",
            extractor_version=EXTRACTOR_VERSION,
        ))
        if len(claims) >= limits.max_claims_per_source:
            break
    return claims
