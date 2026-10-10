"""Claim-level relationships: textual grouping and conflict flagging.

These relationships are evidence-organizing aids for the later human-review
phase (F5). They are explicitly NOT verification:

* Grouping records that claims state the same thing in similar words. Multiple
  copies of one report are *not* independent corroboration.
* Conflict detection flags apparent contradictions (opposite polarity or
  mismatched quantities on the same subject) for a human to adjudicate. It
  resolves nothing and discards neither side.

Both passes use entity "blocking" so cost stays bounded: claims are only
compared when they share a candidate designation or named entity, never all
pairs.
"""

from __future__ import annotations

from collections import defaultdict

from defence_studio.analysis.dedup import jaccard
from defence_studio.contracts.analysis import (
    ClaimConflictV1,
    ClaimGroupBasis,
    ClaimGroupV1,
    ConflictBasis,
    ExtractedClaimV1,
)

_SUBJECT_OVERLAP = 0.30


def _subject_keys(claim: ExtractedClaimV1) -> set[str]:
    keys = {d.lower() for d in claim.entities.designations}
    keys |= {n.lower() for n in claim.entities.named}
    return keys


def _blocks(claims: list[ExtractedClaimV1]) -> dict[str, list[int]]:
    blocks: dict[str, list[int]] = defaultdict(list)
    for index, claim in enumerate(claims):
        for key in _subject_keys(claim):
            blocks[key].append(index)
    return blocks


def group_claims(
    claims: list[ExtractedClaimV1], near_duplicate_threshold: float
) -> list[ClaimGroupV1]:
    parent = list(range(len(claims)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    # Exact normalized-text equality (verbatim syndication of the same sentence).
    by_text: dict[str, list[int]] = defaultdict(list)
    for index, claim in enumerate(claims):
        by_text[claim.claim_text.strip().lower()].append(index)
    exact_pairs: set[tuple[int, int]] = set()
    for members in by_text.values():
        for other in members[1:]:
            union(members[0], other)
            exact_pairs.add((min(members[0], other), max(members[0], other)))

    # Near-duplicate text within the same subject block.
    scores: dict[tuple[int, int], float] = {}
    compared: set[tuple[int, int]] = set()
    for members in _blocks(claims).values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = sorted((members[i], members[j]))
                if (a, b) in compared or (a, b) in exact_pairs:
                    continue
                compared.add((a, b))
                score = jaccard(claims[a].claim_text, claims[b].claim_text)
                if score >= near_duplicate_threshold:
                    union(a, b)
                    scores[(a, b)] = score

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(claims)):
        components[find(index)].append(index)

    groups: list[ClaimGroupV1] = []
    counter = 0
    for members in components.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        counter += 1
        all_exact = all(
            (min(a, b), max(a, b)) in exact_pairs
            for a in members for b in members if a < b
        )
        rep = members[0]
        near = [scores[(min(rep, m), max(rep, m))]
                for m in members[1:] if (min(rep, m), max(rep, m)) in scores]
        groups.append(ClaimGroupV1(
            group_id=f"claimgroup-{counter:04d}",
            basis=ClaimGroupBasis.SAME_NORMALIZED_TEXT if all_exact
            else ClaimGroupBasis.NEAR_DUPLICATE_TEXT,
            member_claim_ids=tuple(claims[m].claim_id for m in members),
            similarity=None if all_exact else (round(min(near), 4) if near else None),
        ))
    return groups


def find_conflicts(claims: list[ExtractedClaimV1]) -> list[ClaimConflictV1]:
    conflicts: list[ClaimConflictV1] = []
    seen: set[tuple[str, str]] = set()
    counter = 0
    for members in _blocks(claims).values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = claims[members[i]], claims[members[j]]
                if a.source_id == b.source_id:
                    continue  # within one source is not a cross-source conflict
                key = tuple(sorted((a.claim_id, b.claim_id)))
                if key in seen:
                    continue
                # "Same subject" guard so we only flag genuine disagreements.
                if jaccard(a.claim_text, b.claim_text) < _SUBJECT_OVERLAP:
                    continue
                basis = None
                explanation = ""
                if a.has_negation != b.has_negation:
                    basis = ConflictBasis.NEGATION_MISMATCH
                    explanation = "Two sources state the same subject with opposite polarity."
                elif (a.entities.quantities and b.entities.quantities
                      and set(a.entities.quantities) != set(b.entities.quantities)):
                    basis = ConflictBasis.QUANTITY_MISMATCH
                    explanation = "Two sources report different quantities for the same subject."
                if basis is None:
                    continue
                seen.add(key)
                counter += 1
                conflicts.append(ClaimConflictV1(
                    group_id=f"conflict-{counter:04d}", basis=basis,
                    claim_id_a=key[0], claim_id_b=key[1], explanation=explanation,
                ))
    return conflicts
