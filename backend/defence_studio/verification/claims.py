"""Deterministic claim fact-check.

Verdicts are rule outcomes, not probabilities. ``UNSUPPORTED`` means the
collected evidence does not meet the support standard. It does not mean the
claim is false. ``DEBUNKED`` is a separate falsity assessment and is used only
when an official primary source negates the claim and nothing independently
supports it.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

VERDICTS = (
    "VERIFIED",
    "PARTIALLY_SUPPORTED",
    "CONFLICTING",
    "NEEDS_REVIEW",
    "UNSUPPORTED",
    "OUTDATED",
)
HIGH_IMPACT = frozenset({"casualty", "weapon", "political", "conflict"})
_WORD = re.compile(r"\w+", re.UNICODE)
_NEGATION = re.compile(
    r"\b(not|no|never|cannot|didn't|doesn't|isn't|aren't|denied|denies|deny|refutes?|without)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_QUALIFIER = re.compile(r"\b(reportedly|allegedly|unconfirmed|estimated|approximately)\b", re.IGNORECASE)


def normalize_text(text: str) -> str:
    """Collapse whitespace. Hedges and negations stay in the string."""
    normalized = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", normalized).strip()


def _tokens(text: str) -> set[str]:
    return {tok.lower() for tok in _WORD.findall(text or "") if len(tok) >= 3}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _sha(text: str) -> str:
    return hashlib.sha256(normalize_text(text).lower().encode("utf-8")).hexdigest()


def _polarity(text: str) -> int:
    return -1 if _NEGATION.search(text or "") else 1


def _quantity(text: str):
    match = _NUMBER.search(text or "")
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def _parse_time(value: str | None):
    if not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _days(later, earlier) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds() / 86400


def _publisher_key(source: dict) -> str:
    url = (source.get("url") or "").lower()
    host = ""
    if "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0]
    return (source.get("publisher") or host or source.get("source_id") or "unknown").strip().lower()


def independence_groups(sources: list[dict]) -> dict[str, str]:
    """Map source_id to a group. Identical or near-copied excerpts share a group."""
    parent = {row["source_id"]: row["source_id"] for row in sources}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    hashes: dict[str, str] = {}
    for row in sources:
        digest = row.get("content_hash") or _sha(row.get("excerpt") or "")
        if digest in hashes:
            union(row["source_id"], hashes[digest])
        else:
            hashes[digest] = row["source_id"]
    for i, left in enumerate(sources):
        for right in sources[i + 1:]:
            if jaccard(_tokens(left.get("excerpt") or ""), _tokens(right.get("excerpt") or "")) >= 0.92:
                union(left["source_id"], right["source_id"])
    return {row["source_id"]: find(row["source_id"]) for row in sources}


def _reliability(source: dict) -> dict:
    """Evidence-specific factors. Category is stored beside them, not as the score."""
    factors = {
        "named_attribution": 1.0 if source.get("named_attribution") else 0.35,
        "primary_reporting": 1.0 if source.get("primary") else 0.45,
        "traceable_url": 1.0 if source.get("url") else 0.2,
        "not_retracted": 0.0 if source.get("retracted") else 1.0,
        "dated": 1.0 if source.get("published_at") else 0.4,
    }
    score = round(sum(factors.values()) / len(factors), 3)
    return {
        "score": score,
        "factors": factors,
        "category_prior": source.get("category") or "unknown",
        "note": "Category is not the reliability score.",
    }


def _relationship(claim_text: str, claim_entities: set[str], excerpt: str) -> str:
    claim_tokens = _tokens(claim_text)
    excerpt_tokens = _tokens(excerpt)
    overlap = jaccard(claim_tokens, excerpt_tokens)
    shared_entity = bool(claim_entities & excerpt_tokens) or overlap >= 0.35
    if not shared_entity and overlap < 0.25:
        return "UNRELATED"
    qty_claim, qty_excerpt = _quantity(claim_text), _quantity(excerpt)
    qty_conflict = (
        qty_claim is not None and qty_excerpt is not None
        and abs(qty_claim - qty_excerpt) >= 1 and abs(qty_claim - qty_excerpt) / max(qty_claim, 1) > 0.1
    )
    if shared_entity and (_polarity(claim_text) != _polarity(excerpt) or qty_conflict):
        return "CONTRADICTS"
    if overlap >= 0.45 and _polarity(claim_text) == _polarity(excerpt) and not qty_conflict:
        if _QUALIFIER.search(excerpt) and not _QUALIFIER.search(claim_text):
            return "PARTIAL"
        return "SUPPORTS"
    if shared_entity:
        return "PARTIAL"
    return "UNRELATED"


def _sensitivity_days(claim: dict) -> int:
    if claim.get("time_sensitivity_days"):
        return int(claim["time_sensitivity_days"])
    category = claim.get("category") or "other"
    if category in {"casualty", "conflict"}:
        return 7
    if category == "weapon":
        return 30
    return 365


def _high_impact(claim: dict) -> bool:
    if claim.get("impact") == "high":
        return True
    return (claim.get("category") or "") in HIGH_IMPACT


def _cluster(claims: list[dict]) -> dict[str, str]:
    parent = {row["claim_id"]: row["claim_id"] for row in claims}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for i, left in enumerate(claims):
        for right in claims[i + 1:]:
            same_text = normalize_text(left.get("text") or "").lower() == normalize_text(right.get("text") or "").lower()
            entities = set(map(str.lower, left.get("entities") or [])) & set(map(str.lower, right.get("entities") or []))
            if same_text or (entities and jaccard(_tokens(left.get("text") or ""), _tokens(right.get("text") or "")) >= 0.35):
                union(left["claim_id"], right["claim_id"])
    return {row["claim_id"]: find(row["claim_id"]) for row in claims}


def verify_claims(claims: list[dict], sources: list[dict], *, collection: dict | None = None, now: str | None = None) -> dict:
    """Return per-claim verdicts. ``now`` is unused by the rules; dates come from sources."""
    collection = collection or {"attempted": True, "errors": []}
    by_source = {row["source_id"]: row for row in sources}
    groups = independence_groups(sources)
    clusters = _cluster(claims)
    assessed = [_assess(claim, by_source, groups, collection) for claim in claims]
    for row in assessed:
        row["cluster_id"] = clusters.get(row["claim_id"], row["claim_id"])
    return {
        "schema": "defence-studio.factcheck.claims.v1",
        "verdicts": assessed,
        "independence_groups": groups,
        "collection": {
            "attempted": bool(collection.get("attempted")),
            "errors": list(collection.get("errors") or []),
        },
        "rules": {
            "VERIFIED": "At least two independent groups support the claim, quantities agree, and nothing material contradicts it.",
            "PARTIALLY_SUPPORTED": "One independent group supports it, or support leaves a material detail unverified.",
            "CONFLICTING": "Independent evidence materially disagrees inside the claim's time window.",
            "NEEDS_REVIEW": "Evidence is missing, ambiguous, low-confidence, or the claim is high-impact.",
            "UNSUPPORTED": "Collection ran and no independent evidence substantiates the claim. This is not a finding that the claim is false.",
            "OUTDATED": "A later source supersedes the claim outside its time-sensitivity window.",
            "DEBUNKED": "Separate from the verdict. Set only when an official primary source negates the claim and no independent source supports it.",
        },
        "checked_at": now,
    }


def _assess(claim: dict, by_source: dict, groups: dict, collection: dict) -> dict:
    text = claim.get("text") or ""
    normalized = normalize_text(text)
    entities = {item.lower() for item in (claim.get("entities") or [])}
    origin = claim.get("source_id")
    confidence = float(claim.get("extraction_confidence") if claim.get("extraction_confidence") is not None else 1)
    evidence = []
    for source_id, source in by_source.items():
        if source_id == origin:
            continue
        excerpt = source.get("excerpt") or ""
        relation = _relationship(normalized, entities, excerpt)
        evidence.append({
            "evidence_id": hashlib.sha256(f"{claim.get('claim_id')}|{source_id}|{relation}".encode()).hexdigest()[:16],
            "source_id": source_id,
            "publisher": source.get("publisher"),
            "url": source.get("url"),
            "published_at": source.get("published_at"),
            "retrieved_at": source.get("retrieved_at"),
            "retrieved_at_status": "COLLECTED" if source.get("retrieved_at") else "NOT_COLLECTED",
            "excerpt": excerpt[:500],
            "relationship": relation,
            "category": source.get("category") or "unknown",
            "independence_group": groups.get(source_id, source_id),
            "reliability": _reliability(source),
            "content_hash": source.get("content_hash") or _sha(excerpt),
            "primary": bool(source.get("primary")),
            "retrieval_error": source.get("retrieval_error"),
        })
    supports = [row for row in evidence if row["relationship"] == "SUPPORTS"]
    partials = [row for row in evidence if row["relationship"] == "PARTIAL"]
    contradicts = [row for row in evidence if row["relationship"] == "CONTRADICTS"]
    support_groups = {row["independence_group"] for row in supports}
    contradict_groups = {row["independence_group"] for row in contradicts}
    claim_time = _parse_time(claim.get("published_at") or claim.get("event_date"))
    window = _sensitivity_days(claim)
    newer_conflict = False
    for row in contradicts:
        published = _parse_time(row.get("published_at"))
        delta = _days(published, claim_time)
        if delta is not None and delta >= window:
            newer_conflict = True
    provisional = "UNSUPPORTED"
    falsity = "NONE"
    missing = []
    if not normalized:
        provisional = "NEEDS_REVIEW"
        missing.append("Claim text is empty; it was not forced into a structured assertion.")
    elif confidence < 0.4:
        provisional = "NEEDS_REVIEW"
        missing.append("Extraction confidence is below 0.4.")
    elif not collection.get("attempted"):
        provisional = "NEEDS_REVIEW"
        missing.append("Evidence collection did not run. Absence of support is not a verdict.")
    elif newer_conflict and support_groups <= contradict_groups:
        provisional = "OUTDATED"
    elif support_groups and contradict_groups:
        provisional = "CONFLICTING"
    elif contradicts and not supports and not partials:
        official = any(
            row["primary"] and str(row["category"]).startswith("official") for row in contradicts
        )
        provisional = "CONFLICTING"
        if official:
            falsity = "DEBUNKED"
    elif len(support_groups) >= 2 and not contradicts:
        provisional = "VERIFIED"
    elif support_groups or partials:
        provisional = "PARTIALLY_SUPPORTED"
        if not support_groups:
            missing.append("Related evidence does not confirm the specific wording or quantity.")
    else:
        provisional = "UNSUPPORTED"
        missing.append("No independent source substantiates this claim.")
    review_required = _high_impact(claim) or provisional in {"CONFLICTING", "NEEDS_REVIEW"} or falsity == "DEBUNKED"
    verdict = "NEEDS_REVIEW" if _high_impact(claim) else provisional
    if verdict == "NEEDS_REVIEW" and provisional != "NEEDS_REVIEW":
        missing.append("High-impact claim is held for a human reviewer regardless of the provisional result.")
    factors = _confidence_factors(provisional, support_groups, contradict_groups, confidence, evidence)
    return {
        "claim_id": claim.get("claim_id"),
        "text": text,
        "normalized_text": normalized,
        "entities": sorted(entities),
        "category": claim.get("category") or "other",
        "event_date": claim.get("event_date"),
        "published_at": claim.get("published_at"),
        "origin_source_id": origin,
        "extraction_method": claim.get("extraction_method") or "supplied",
        "extraction_confidence": confidence,
        "verdict": verdict,
        "provisional_verdict": provisional,
        "falsity_assessment": falsity,
        "review_required": review_required,
        "review_status": "PENDING",
        "supporting": [row for row in evidence if row["relationship"] in {"SUPPORTS", "PARTIAL"}],
        "contradicting": contradicts,
        "missing": missing,
        "independent_support_groups": sorted(support_groups),
        "independent_contradiction_groups": sorted(contradict_groups),
        "confidence": factors,
        "risk": "high" if review_required else ("medium" if provisional == "PARTIALLY_SUPPORTED" else "low"),
        "script_treatment": _treatment(verdict if not _high_impact(claim) else provisional, review_required),
    }


def _treatment(verdict: str, review_required: bool) -> str:
    if review_required or verdict in {"NEEDS_REVIEW", "CONFLICTING", "UNSUPPORTED", "OUTDATED"}:
        return "omit"
    if verdict == "PARTIALLY_SUPPORTED":
        return "qualify"
    if verdict == "VERIFIED":
        return "may_state"
    return "omit"


def _confidence_factors(provisional, support_groups, contradict_groups, extraction, evidence) -> dict:
    """Weighted factors. The total is not a calibrated probability."""
    relevance = 1.0 if support_groups else (0.4 if evidence else 0.0)
    quality = round(sum(row["reliability"]["score"] for row in evidence) / len(evidence), 3) if evidence else 0.0
    independence = min(len(support_groups) / 2, 1.0)
    corroboration = 1.0 if len(support_groups) >= 2 else (0.5 if support_groups else 0.0)
    contradiction = 0.0 if contradict_groups else 1.0
    temporal = 0.25 if provisional == "OUTDATED" else 1.0
    weights = {
        "relevance": (relevance, 0.2),
        "quality": (quality, 0.15),
        "independence": (independence, 0.15),
        "corroboration": (corroboration, 0.15),
        "contradiction_clear": (contradiction, 0.15),
        "temporal": (temporal, 0.1),
        "extraction": (extraction, 0.1),
    }
    total = round(sum(value * weight for value, weight in weights.values()), 3)
    return {
        "score": total,
        "calibrated": False,
        "label": "uncalibrated_weighted_factors",
        "factors": {name: value for name, (value, _weight) in weights.items()},
        "weights": {name: weight for name, (_value, weight) in weights.items()},
    }
