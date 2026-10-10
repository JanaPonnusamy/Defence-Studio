"""Deterministic, explainable candidate ranking.

Every factor is computed only from metadata the provider actually returned.
A missing publication date never becomes a fabricated one -- it yields a
neutral recency score and an explicit explanation line instead. Ranking
reflects topical/temporal/language/type match only; it never asserts that a
higher-ranked candidate is more trustworthy or that its claims are true.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from defence_studio.contracts.discovery import RankingFactors

_WORD = re.compile(r"[a-zA-Z0-9]+")
_WEIGHTS = {"relevance": 0.5, "recency": 0.2, "language": 0.15, "source_type": 0.15}


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _relevance(title: str | None, topic: str, search_terms: tuple[str, ...]) -> tuple[float, str]:
    query_tokens = _tokens(" ".join([topic, *search_terms]))
    if not title:
        return 0.0, "No title was available to score topical relevance."
    title_tokens = _tokens(title)
    if not query_tokens or not title_tokens:
        return 0.0, "No comparable tokens to score topical relevance."
    overlap = query_tokens & title_tokens
    score = len(overlap) / len(query_tokens)
    score = min(score, 1.0)
    return score, f"Title shares {len(overlap)}/{len(query_tokens)} topic token(s)."


def _recency(published_at: str | None) -> tuple[float, str]:
    if not published_at:
        return 0.5, "No publication date was available; neutral recency score."
    parsed = parse_date(published_at)
    if parsed is None:
        return 0.5, f"Publication date {published_at!r} could not be parsed; neutral recency score."
    age_days = (datetime.now(timezone.utc).date() - parsed).days
    if age_days < 0:
        age_days = 0
    score = max(0.0, 1.0 - (age_days / 365.0))
    return score, f"Published {age_days} day(s) ago."


def parse_date(value: str) -> date | None:
    text = value.strip()
    for parser in (
        lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")).date(),
        lambda v: datetime.strptime(v, "%a, %d %b %Y %H:%M:%S %z").date(),
        lambda v: datetime.strptime(v, "%a, %d %b %Y %H:%M:%S %Z").date(),
        lambda v: date.fromisoformat(v[:10]),
    ):
        try:
            return parser(text)
        except (ValueError, IndexError):
            continue
    return None


def _language_match(candidate_language: str | None, requested_language: str) -> tuple[float, str]:
    if not candidate_language or candidate_language in ("und",):
        return 0.5, "Candidate language was not determined; neutral language score."
    if candidate_language == requested_language:
        return 1.0, f"Candidate language matches the requested language {requested_language!r}."
    return 0.0, f"Candidate language {candidate_language!r} does not match requested {requested_language!r}."


def _source_type(candidate_type: str, preferred_types: tuple[str, ...]) -> tuple[float, str]:
    if not preferred_types:
        return 1.0, "No source-type preference was set."
    if candidate_type in preferred_types:
        return 1.0, f"Source type {candidate_type!r} matches a preferred type."
    return 0.3, f"Source type {candidate_type!r} is not in the preferred types {preferred_types!r}."


def score_candidate(
    title: str | None, published_at: str | None, candidate_language: str | None, candidate_type: str,
    topic: str, search_terms: tuple[str, ...], requested_language: str, preferred_source_types: tuple[str, ...],
) -> RankingFactors:
    relevance, relevance_note = _relevance(title, topic, search_terms)
    recency, recency_note = _recency(published_at)
    language, language_note = _language_match(candidate_language, requested_language)
    source_type, source_type_note = _source_type(candidate_type, preferred_source_types)
    total = (
        _WEIGHTS["relevance"] * relevance + _WEIGHTS["recency"] * recency
        + _WEIGHTS["language"] * language + _WEIGHTS["source_type"] * source_type
    )
    return RankingFactors(
        relevance_score=round(relevance, 4), recency_score=round(recency, 4),
        language_match_score=round(language, 4), source_type_score=round(source_type, 4),
        total_score=round(total, 4),
        explanation=(relevance_note, recency_note, language_note, source_type_note),
    )
