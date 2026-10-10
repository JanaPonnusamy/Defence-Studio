"""Deterministic deduplication and source-relationship detection.

Groups duplicate/near-duplicate source records and records *why* they matched.
It never deletes or merges a source: callers keep the full source list and use
these groups as relationships layered on top.

Three distinct, non-contradictory relationships are detected, strongest first:

1. ``SAME_CANONICAL_URL``   — the same URL acquired more than once.
2. ``IDENTICAL_CONTENT_HASH`` — byte-identical normalized content from *different*
   URLs (syndication / verbatim republication).
3. ``NEAR_DUPLICATE_TEXT``  — a slightly modified copy, by token-shingle Jaccard
   at/above a configurable threshold, excluding pairs already exact above.

Similarity is NOT independence and NOT truth: two near-duplicate copies are
*less* independent, never corroboration. Independent reports on the same subject
with different wording fall below the threshold and are deliberately left
ungrouped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from defence_studio.contracts.analysis import DuplicateBasis, DuplicateGroupV1

_WORD = re.compile(r"\w+", re.UNICODE)
_SHINGLE_K = 4


@dataclass(frozen=True)
class DedupInput:
    source_id: str
    canonical_url: str
    normalized_sha256: str
    normalized_text: str


def _shingles(text: str, k: int = _SHINGLE_K) -> frozenset:
    tokens = [t.lower() for t in _WORD.findall(text)]
    if len(tokens) < k:
        return frozenset(tokens)
    return frozenset(tuple(tokens[i : i + k]) for i in range(len(tokens) - k + 1))


def jaccard(text_a: str, text_b: str) -> float:
    a, b = _shingles(text_a), _shingles(text_b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _component_groups(edges: dict[str, set[str]], ids: list[str]) -> list[list[str]]:
    """Connected components over an undirected similarity graph (union-find)."""
    parent = {sid: sid for sid in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        parent[find(x)] = find(y)

    for node, neighbours in edges.items():
        for other in neighbours:
            union(node, other)
    groups: dict[str, list[str]] = {}
    for sid in ids:
        groups.setdefault(find(sid), []).append(sid)
    return [sorted(members) for members in groups.values() if len(members) > 1]


def find_duplicate_groups(
    sources: list[DedupInput], near_duplicate_threshold: float
) -> list[DuplicateGroupV1]:
    if not 0.0 < near_duplicate_threshold <= 1.0:
        raise ValueError("near_duplicate_threshold must be in (0, 1]")
    # Stable ordering: process in the given order, break ties by source_id.
    ordered = sorted(range(len(sources)), key=lambda i: (i, sources[i].source_id))
    groups: list[DuplicateGroupV1] = []
    counter = 0

    def new_id() -> str:
        nonlocal counter
        counter += 1
        return f"dupgroup-{counter:04d}"

    # 1. Same canonical URL.
    by_url: dict[str, list[str]] = {}
    for i in ordered:
        src = sources[i]
        if src.canonical_url:
            by_url.setdefault(src.canonical_url, []).append(src.source_id)
    url_grouped_pairs: set[frozenset] = set()
    for url, members in by_url.items():
        if len(members) > 1:
            members = sorted(members)
            groups.append(DuplicateGroupV1(
                group_id=new_id(), basis=DuplicateBasis.SAME_CANONICAL_URL,
                member_source_ids=tuple(members), representative_source_id=members[0],
                similarity=1.0,
                explanation=f"{len(members)} source records share the canonical URL {url}.",
            ))
            for a in members:
                for b in members:
                    if a < b:
                        url_grouped_pairs.add(frozenset((a, b)))

    # 2. Identical normalized content from different URLs.
    by_hash: dict[str, list[str]] = {}
    for i in ordered:
        src = sources[i]
        if src.normalized_sha256:
            by_hash.setdefault(src.normalized_sha256, []).append(src.source_id)
    identical_pairs: set[frozenset] = set()
    for digest, members in by_hash.items():
        members = sorted(members)
        for a in members:
            for b in members:
                if a < b:
                    identical_pairs.add(frozenset((a, b)))
        urls = {s.canonical_url for s in sources if s.source_id in members}
        # Only report as a distinct relationship when it crosses URLs (otherwise
        # the SAME_CANONICAL_URL group above already captures it).
        if len(members) > 1 and len([u for u in urls if u]) > 1:
            groups.append(DuplicateGroupV1(
                group_id=new_id(), basis=DuplicateBasis.IDENTICAL_CONTENT_HASH,
                member_source_ids=tuple(members), representative_source_id=members[0],
                similarity=1.0,
                explanation="Byte-identical normalized content published under "
                            f"{len([u for u in urls if u])} different URLs.",
            ))

    # 3. Near-duplicate text (exclude pairs already exact by URL or content hash).
    edges: dict[str, set[str]] = {s.source_id: set() for s in sources}
    pair_scores: dict[frozenset, float] = {}
    for idx_a in range(len(ordered)):
        for idx_b in range(idx_a + 1, len(ordered)):
            a_src, b_src = sources[ordered[idx_a]], sources[ordered[idx_b]]
            pair = frozenset((a_src.source_id, b_src.source_id))
            if pair in url_grouped_pairs or pair in identical_pairs:
                continue
            score = jaccard(a_src.normalized_text, b_src.normalized_text)
            if score >= near_duplicate_threshold:
                edges[a_src.source_id].add(b_src.source_id)
                edges[b_src.source_id].add(a_src.source_id)
                pair_scores[pair] = score
    for members in _component_groups(edges, [s.source_id for s in sources]):
        representative = members[0]
        rep_scores = [pair_scores[frozenset((representative, m))]
                      for m in members[1:] if frozenset((representative, m)) in pair_scores]
        similarity = round(min(rep_scores), 4) if rep_scores else round(near_duplicate_threshold, 4)
        groups.append(DuplicateGroupV1(
            group_id=new_id(), basis=DuplicateBasis.NEAR_DUPLICATE_TEXT,
            member_source_ids=tuple(members), representative_source_id=representative,
            similarity=similarity,
            explanation=f"{len(members)} source records are near-duplicate text at or above "
                        f"Jaccard {near_duplicate_threshold}; grouping is not corroboration.",
        ))
    return groups
