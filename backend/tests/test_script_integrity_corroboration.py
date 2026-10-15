"""P0 regression: corroboration_count must be eligibility-aware.

Before this fix, `corroboration_count` counted every claim/source textually
matching a claim group, with no check against the package's own
`eligible_claim_ids`, review decision, or staleness -- a REJECTED claim backed
by a REJECTED source counted exactly the same as an APPROVED one. These tests
build packages with realistic `review`/`is_stale` fields (the same shape
`EvidenceService._assemble` produces -- see services/evidence.py lines
157-184) and assert that only claims which are (a) in `eligible_claim_ids` and
(b) backed by an approved, current claim+source review ever contribute to the
distinct-source corroboration count.
"""
from __future__ import annotations

from defence_studio.services import script_integrity as si


def _review(decision, origin="HUMAN", sha="a" * 64):
    return {"decision": decision, "origin": origin, "reviewed_artifact_sha256": sha, "note": "n"}


def _source(sid, decision="APPROVED_FOR_USE", is_stale=False, published="2026-10-01"):
    return {
        "source_id": sid,
        "declared_metadata": {"published_at": published, "publisher": sid},
        "review": _review(decision) if decision is not None else None,
        "is_stale": is_stale,
    }


def _claim(cid, sid, text, decision="APPROVED_FOR_USE", is_stale=False, published=None):
    return {
        "claim_id": cid, "source_id": sid, "claim_text": text,
        "source_published_at": published,
        "review": _review(decision) if decision is not None else None,
        "is_stale": is_stale,
    }


def _package(sources, claims, claim_groups=None, duplicate_groups=None, eligible_claim_ids=None,
             automated_decisions=None):
    """Mirrors the real EvidenceService package shape closely enough to drive
    corroboration_count: sources/claims carry `review`/`is_stale`, and
    `eligible_claim_ids` is the authoritative snapshot a real package would
    compute from exactly those decisions."""
    return {
        "generated_from_timestamp": "2026-10-14T00:00:00+00:00",
        "sources": sources,
        "claims": claims,
        "duplicate_groups": duplicate_groups or [],
        "claim_groups": claim_groups or [],
        "conflicts": [],
        "eligible_claim_ids": eligible_claim_ids if eligible_claim_ids is not None else [
            c["claim_id"] for c in claims
            if c.get("review", {}).get("decision") == "APPROVED_FOR_USE" and not c.get("is_stale")
            and next((s for s in sources if s["source_id"] == c["source_id"]), {}).get("review", {}).get("decision") == "APPROVED_FOR_USE"
            and not next((s for s in sources if s["source_id"] == c["source_id"]), {}).get("is_stale")
        ],
        **({"automated_decisions": automated_decisions} if automated_decisions else {}),
    }


# ---------------------------------------------------------------------------
# 1. Two eligible claims from two distinct approved, current sources.
# ---------------------------------------------------------------------------

def test_two_eligible_distinct_sources_satisfy_corroboration():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets."),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}])
    assert si.corroboration_count(pkg, "c1") == 2


# ---------------------------------------------------------------------------
# 2. One approved claim + one rejected claim -> rejected does not count.
# ---------------------------------------------------------------------------

def test_rejected_claim_does_not_count():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets.", decision="REJECTED"),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}])
    # This is exactly the audit's reproduction case: pre-fix, both counted (== 2).
    assert si.corroboration_count(pkg, "c1") == 1


# ---------------------------------------------------------------------------
# 3. One approved source + one rejected source -> rejected source does not count.
# ---------------------------------------------------------------------------

def test_rejected_source_does_not_count():
    sources = [_source("s1"), _source("s2", decision="REJECTED")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets."),
    ]
    # c2's own review may still read APPROVED_FOR_USE, but its source is
    # rejected, so a real package would exclude it from eligible_claim_ids.
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}],
                   eligible_claim_ids=["c1"])
    assert si.corroboration_count(pkg, "c1") == 1


# ---------------------------------------------------------------------------
# 4. Approved claim + pending (NEEDS_MORE_EVIDENCE) claim -> pending does not count.
# ---------------------------------------------------------------------------

def test_pending_claim_does_not_count():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets.", decision="NEEDS_MORE_EVIDENCE"),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}])
    assert si.corroboration_count(pkg, "c1") == 1


# ---------------------------------------------------------------------------
# 5. Stale claim or source -> stale evidence does not count.
# ---------------------------------------------------------------------------

def test_stale_claim_does_not_count():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets.", is_stale=True),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}],
                   eligible_claim_ids=["c1"])  # a stale claim is never in the eligible snapshot
    assert si.corroboration_count(pkg, "c1") == 1


def test_stale_source_does_not_count():
    sources = [_source("s1"), _source("s2", is_stale=True)]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets."),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}],
                   eligible_claim_ids=["c1"])
    assert si.corroboration_count(pkg, "c1") == 1


# ---------------------------------------------------------------------------
# 6. Two qualifying claims from the same source -> counts as one source.
# ---------------------------------------------------------------------------

def test_two_claims_same_source_count_once():
    sources = [_source("s1")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s1", "The army ordered 36 jets, confirmed."),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}])
    assert si.corroboration_count(pkg, "c1") == 1


# ---------------------------------------------------------------------------
# 7. Duplicate claims / repeated source references cannot inflate the count.
# ---------------------------------------------------------------------------

def test_duplicate_group_membership_cannot_inflate_count():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets."),
    ]
    # c1 listed twice across overlapping groups; set semantics must prevent
    # double counting the same source cluster.
    pkg = _package(sources, claims, claim_groups=[
        {"group_id": "g1", "member_claim_ids": ["c1", "c2"]},
        {"group_id": "g2", "member_claim_ids": ["c1", "c2"]},
    ])
    assert si.corroboration_count(pkg, "c1") == 2


def test_repeated_source_reference_via_duplicate_group_counts_once():
    sources = [_source("s1"), _source("s1-copy")]
    claims = [
        _claim("c1", "s1", "Delivery is due in 2027."),
        _claim("c2", "s1-copy", "Delivery is due in 2027."),
    ]
    pkg = _package(sources, claims,
                   claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}],
                   duplicate_groups=[{"group_id": "d1", "member_source_ids": ["s1", "s1-copy"],
                                     "representative_source_id": "s1"}])
    assert si.corroboration_count(pkg, "c1") == 1  # syndicated copy, not independent


# ---------------------------------------------------------------------------
# 8. One eligible source + one ineligible source -> FACT_NOT_CORROBORATED
#    is raised when the policy requires >=2 independent sources.
# ---------------------------------------------------------------------------

def test_fact_not_corroborated_raised_with_one_eligible_one_rejected():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets.", decision="REJECTED"),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}])
    revision = {
        "sections": [{"section_id": "s1", "narration": "The army ordered 36 jets.", "requires_evidence": True,
                      "statement_kind": "VERIFIED_FACT",
                      "evidence": {"claim_ids": ["c1"], "source_ids": [], "qualification": ""}}],
        "closing": "end", "title": "t", "config": {"target_words": 100}, "word_count": {"words": 100},
    }
    issues = {i["code"]: i["severity"] for i in si.integrity_issues(revision, pkg)}
    assert issues["FACT_NOT_CORROBORATED"] == "ERROR"


# ---------------------------------------------------------------------------
# 9. Two qualifying distinct sources -> no false corroboration error.
# ---------------------------------------------------------------------------

def test_no_false_corroboration_error_with_two_qualifying_sources():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets."),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}])
    revision = {
        "sections": [{"section_id": "s1", "narration": "The army ordered 36 jets.", "requires_evidence": True,
                      "statement_kind": "VERIFIED_FACT",
                      "evidence": {"claim_ids": ["c1"], "source_ids": [], "qualification": ""}}],
        "closing": "end", "title": "t", "config": {"target_words": 100}, "word_count": {"words": 100},
    }
    issues = {i["code"]: i["severity"] for i in si.integrity_issues(revision, pkg)}
    assert "FACT_NOT_CORROBORATED" not in issues


# ---------------------------------------------------------------------------
# 10. Existing unrelated integrity violations remain correct.
# ---------------------------------------------------------------------------

def test_unrelated_integrity_checks_still_correct_alongside_the_fix():
    sources = [_source("s1"), _source("old", published="2025-01-01")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c5", "old", "Range is 300 km."),
    ]
    pkg = _package(sources, claims)
    revision = {
        "sections": [
            {"section_id": "a", "narration": "The army ordered 40 jets.", "requires_evidence": True,
             "statement_kind": "ATTRIBUTED_CLAIM",
             "evidence": {"claim_ids": ["c1"], "source_ids": [], "qualification": "note"}},
            {"section_id": "b", "narration": "Range is 300 km.", "requires_evidence": True,
             "statement_kind": "ATTRIBUTED_CLAIM",
             "evidence": {"claim_ids": ["c5"], "source_ids": [], "qualification": "note"}},
        ],
        "closing": "end", "title": "t", "config": {"target_words": 100}, "word_count": {"words": 100},
    }
    issues = {i["code"]: i["severity"] for i in si.integrity_issues(revision, pkg)}
    assert issues["UNSUPPORTED_NUMBER"] == "ERROR"   # unrelated to corroboration
    assert issues["STALE_SOURCE"] == "WARNING"        # unrelated to corroboration


# ---------------------------------------------------------------------------
# Additional: unknown/missing review state fails closed (never silently
# treated as approved); a claim entirely absent from eligible_claim_ids
# (e.g. a malformed or stale snapshot) contributes nothing even if its own
# embedded review looks approved.
# ---------------------------------------------------------------------------

def test_claim_outside_eligible_snapshot_never_counts_even_if_review_looks_approved():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets."),  # review says approved...
    ]
    # ...but the authoritative eligible snapshot omits it (e.g. computed
    # before this decision, or excluded for an unrelated policy reason).
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}],
                   eligible_claim_ids=["c1"])
    assert si.corroboration_count(pkg, "c1") == 1


def test_missing_review_field_on_eligible_claim_fails_closed():
    # Eligibility cannot substitute for a missing current review.
    sources = [_source("s1"), _source("s2")]
    c2 = _claim("c2", "s2", "The army ordered 36 jets.")
    c2["review"] = None  # no embedded review at all
    claims = [_claim("c1", "s1", "The army ordered 36 jets."), c2]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}],
                   eligible_claim_ids=["c1", "c2"])
    assert si.corroboration_count(pkg, "c1") == 1


def test_automated_policy_bonus_never_overrides_ineligibility():
    sources = [_source("s1")]
    claims = [_claim("c1", "s1", "Budget approved.", decision="REJECTED")]
    pkg = _package(sources, claims,
                   eligible_claim_ids=[],  # rejected -> never eligible
                   automated_decisions={"claims": {"c1": {"state": "AUTO_ACCEPTED", "independent_groups": 3}}})
    # Even though the (now-stale) automated policy once said 3 independent
    # groups, the claim is REJECTED and absent from eligible_claim_ids, so it
    # must count as zero -- the policy bonus never resurrects an ineligible claim.
    assert si.corroboration_count(pkg, "c1") == 0


# ---------------------------------------------------------------------------
# Audit safety: rejected claims remain present in the package (for
# auditability) while being excluded from corroboration.
# ---------------------------------------------------------------------------

def test_rejected_claim_remains_in_package_but_excluded_from_corroboration():
    sources = [_source("s1"), _source("s2")]
    claims = [
        _claim("c1", "s1", "The army ordered 36 jets."),
        _claim("c2", "s2", "The army ordered 36 jets.", decision="REJECTED"),
    ]
    pkg = _package(sources, claims, claim_groups=[{"group_id": "g1", "member_claim_ids": ["c1", "c2"]}])
    claim_ids_in_package = {c["claim_id"] for c in pkg["claims"]}
    assert "c2" in claim_ids_in_package          # still present, for audit
    assert si.corroboration_count(pkg, "c1") == 1  # but does not corroborate


# P0 completion: malformed eligibility snapshots cannot override actual state.
import copy
import pytest
from defence_studio.services.script_validation import validate_revision_body


@pytest.mark.parametrize("subject,field,value", [
    ("claim", "review", None), ("source", "review", None),
    ("claim", "review", {"decision": "REJECTED"}),
    ("source", "review", {"decision": "REJECTED"}),
    ("claim", "review", {"decision": "NEEDS_MORE_EVIDENCE"}),
    ("source", "review", {"decision": "PENDING"}),
    ("claim", "review", {"decision": "UNKNOWN"}),
    ("source", "review", {"decision": "UNKNOWN"}),
    ("claim", "is_stale", True), ("source", "is_stale", True),
    ("claim", "is_stale", None), ("source", "is_stale", None),
    ("claim", "is_stale", "false"), ("source", "is_stale", "false"),
])
def test_invalid_current_state_fails_external_fact_gate(subject, field, value):
    pkg = _current_package()
    pkg["claims" if subject == "claim" else "sources"][1][field] = value
    _assert_fact_count(pkg, 1)


def _current_package():
    return _package([_source("s1"), _source("s2")],
                    [_claim("c1", "s1", "The army ordered 36 jets."),
                     _claim("c2", "s2", "The army ordered 36 jets.")],
                    claim_groups=[{"member_claim_ids": ["c1", "c2"]}],
                    eligible_claim_ids=["c1", "c2"])


def _assert_fact_count(pkg, count):
    before = copy.deepcopy(pkg)
    revision = {"title": "Update", "closing": "End", "package_sha256": "a" * 64,
                "sections": [{"section_id": "s", "narration": "The army ordered 36 jets.",
                              "statement_kind": "VERIFIED_FACT", "requires_evidence": True,
                              "evidence": {"claim_ids": ["c1"], "qualification": "Reviewed"}}]}
    assert si.corroboration_count(pkg, "c1") == count
    result = validate_revision_body(revision, pkg)
    errors = {i["code"] for i in result["issues"] if i["severity"] == "ERROR"}
    assert ("FACT_NOT_CORROBORATED" in errors) == (count < 2)
    assert result["passed"] == (count >= 2)
    assert pkg == before  # pure validation preserves review records and audit data


@pytest.mark.parametrize("case", ["missing-source", "missing-eligibility", "same-source",
                                   "duplicate-cluster", "invalid-target", "policy-bypass", "valid"])
def test_current_source_count_external_result(case):
    pkg = _current_package()
    expected = 1
    if case == "missing-source":
        pkg["sources"].pop()
    elif case == "missing-eligibility":
        pkg["eligible_claim_ids"] = ["c1"]
    elif case == "same-source":
        pkg["claims"][1]["source_id"] = "s1"
    elif case == "duplicate-cluster":
        pkg["duplicate_groups"] = [{"member_source_ids": ["s1", "s2"],
                                    "representative_source_id": "s1"}]
    elif case == "invalid-target":
        pkg["claims"][0]["is_stale"] = True
        expected = 0
    elif case == "policy-bypass":
        pkg["claims"][1]["review"]["decision"] = "REJECTED"
        pkg["automated_decisions"] = {"claims": {"c1": {"state": "AUTO_ACCEPTED", "independent_groups": 9}}}
    else:
        expected = 2
    _assert_fact_count(pkg, expected)


def test_effective_automated_approvals_still_qualify():
    pkg = _current_package()
    for row in pkg["claims"] + pkg["sources"]:
        row["review"]["origin"] = "AUTOMATED_POLICY"
    _assert_fact_count(pkg, 2)
