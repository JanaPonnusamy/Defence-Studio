"""RM-2: research-to-script integrity gate (offline, deterministic)."""
import json

import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.services.review import StaleRevision
from defence_studio.services.script import ScriptGenerationFailed, ScriptService
from defence_studio.services import script_integrity as si
from defence_studio.services.script_validation import validate_revision_body

from test_script_phase3 import (  # noqa: F401  (fixture re-export)
    GOOD_CONFIG, FakeProvider, _default_body, approved, _Result,
)
from test_review_f5 import OWNER
from conftest import SECRET, access_token


def _package():
    """Two independent publishers state c1/c2 alike; c3 is a lone claim; c4/c5 conflict;
    s1 and s1-copy are duplicates (syndicated)."""
    def src(sid, published="2026-10-01"):
        return {"source_id": sid, "review": {"decision": "APPROVED_FOR_USE"}, "is_stale": False, "declared_metadata": {"published_at": published, "publisher": sid}}
    def claim(cid, sid, text, published=None):
        return {"claim_id": cid, "source_id": sid, "claim_text": text, "source_published_at": published,
                "review": {"decision": "APPROVED_FOR_USE"}, "is_stale": False}
    return {
        "generated_from_timestamp": "2026-10-14T00:00:00+00:00",
        "sources": [src("s1"), src("s2"), src("s1-copy"), src("old", "2025-01-01"), src("nodate", None)],
        "duplicate_groups": [{"group_id": "d1", "member_source_ids": ["s1", "s1-copy"], "representative_source_id": "s1"}],
        "claims": [
            claim("c1", "s1", "The army ordered 36 jets."),
            claim("c2", "s2", "The army ordered 36 jets."),
            claim("c3", "s1", "Delivery is due in 2027."),
            claim("c3b", "s1-copy", "Delivery is due in 2027."),
            claim("c4", "s2", "Range is 500 km."),
            claim("c5", "old", "Range is 300 km."),
            claim("c6", "nodate", "Budget approved."),
        ],
        "claim_groups": [
            {"group_id": "g1", "member_claim_ids": ["c1", "c2"]},
            {"group_id": "g2", "member_claim_ids": ["c3", "c3b"]},
        ],
        "conflicts": [{"group_id": "x", "claim_id_a": "c4", "claim_id_b": "c5"}],
        "eligible_claim_ids": ["c1", "c2", "c3", "c3b", "c4", "c5", "c6"],
    }


def _rev(sections, **extra):
    return {"sections": sections, "closing": "end", "title": "t", "config": {"target_words": 100},
            "word_count": {"words": 100}, **extra}


def _sec(kind, claims, narration, qualification="note", sid="s"):
    return {"section_id": sid, "narration": narration, "requires_evidence": True, "statement_kind": kind,
            "evidence": {"claim_ids": claims, "source_ids": [], "qualification": qualification}}


def _codes(revision, package=None):
    return {i["code"]: i["severity"] for i in si.integrity_issues(revision, package or _package())}


def test_verified_fact_requires_independent_corroboration():
    pkg = _package()
    assert si.corroboration_count(pkg, "c1") == 2          # s1 + s2 are independent
    assert si.corroboration_count(pkg, "c3") == 1          # syndicated copy counts once
    ok = _codes(_rev([_sec("VERIFIED_FACT", ["c1"], "The army ordered 36 jets.")]), pkg)
    assert "FACT_NOT_CORROBORATED" not in ok
    bad = _codes(_rev([_sec("VERIFIED_FACT", ["c3"], "Delivery is due in 2027.")]), pkg)
    assert bad["FACT_NOT_CORROBORATED"] == "ERROR"


def test_conflicted_claims_cannot_be_stated_as_fact_but_can_be_attributed():
    fact = _codes(_rev([_sec("VERIFIED_FACT", ["c4"], "Range is 500 km.")]))
    assert fact["CONTRADICTED_CLAIM_AS_FACT"] == "ERROR"
    attributed = _codes(_rev([_sec("ATTRIBUTED_CLAIM", ["c4"], "One source says range is 500 km.")]))
    assert "CONTRADICTED_CLAIM_AS_FACT" not in attributed
    undeclared = _codes(_rev([_sec(None, ["c4"], "Range is 500 km.")]))
    assert undeclared["CONTRADICTED_CLAIM_AS_FACT"] == "WARNING"


def test_unsupported_numbers_are_rejected_supported_ones_pass():
    assert _codes(_rev([_sec("ATTRIBUTED_CLAIM", ["c1"], "The army ordered 40 jets.")]))["UNSUPPORTED_NUMBER"] == "ERROR"
    assert "UNSUPPORTED_NUMBER" not in _codes(_rev([_sec("ATTRIBUTED_CLAIM", ["c1"], "It ordered 36 jets.")]))


def test_analysis_and_unresolved_must_be_qualified():
    assert _codes(_rev([_sec("ANALYSIS", ["c1"], "This signals intent.", qualification="")]))["UNQUALIFIED_ANALYSIS"] == "ERROR"
    assert _codes(_rev([_sec("UNRESOLVED", ["c4"], "It is unclear.", qualification="")]))["UNQUALIFIED_UNRESOLVED"] == "ERROR"
    assert "UNQUALIFIED_ANALYSIS" not in _codes(_rev([_sec("ANALYSIS", ["c1"], "This signals intent.")]))


def test_stale_and_undated_sources_and_unknown_kind_and_missing_kind():
    codes = _codes(_rev([_sec("ATTRIBUTED_CLAIM", ["c5"], "Range is 300 km."), _sec(None, ["c6"], "Budget approved.", sid="b"),
                         _sec("MAYBE", ["c1"], "x", sid="m")]))
    assert codes["STALE_SOURCE"] == "WARNING"
    assert codes["SOURCE_DATE_UNKNOWN"] == "WARNING"
    assert codes["STATEMENT_KIND_MISSING"] == "WARNING"
    assert codes["UNKNOWN_STATEMENT_KIND"] == "ERROR"


def test_length_target_warning():
    rev = _rev([_sec("ATTRIBUTED_CLAIM", ["c1"], "It ordered 36 jets.")])
    rev["word_count"] = {"words": 10}
    assert _codes(rev)["TARGET_LENGTH_MISSED"] == "WARNING"


def test_validator_rejects_deterministically_and_is_versioned():
    pkg = _package()
    rev = _rev([_sec("VERIFIED_FACT", ["c3"], "Delivery is due in 2027.")], package_sha256="a" * 64)
    result = validate_revision_body(rev, pkg)
    assert result["passed"] is False and result["validator_version"].endswith("v2")
    assert validate_revision_body(rev, pkg)["issues"] == result["issues"]  # deterministic


def test_comparison_and_repetition_are_deterministic():
    a = {"version": 1, "title": "A", "package_sha256": "p1", "opening_hook": "", "closing": "the end of the story is here now",
         "sections": [{"section_id": "s1", "narration": "the army ordered thirty six jets from the maker last week",
                       "evidence": {"claim_ids": ["c1"]}}], "word_count": {"words": 20}}
    b = json.loads(json.dumps(a))
    b["version"], b["package_sha256"] = 2, "p2"
    b["sections"].append({"section_id": "s2", "narration": "something new entirely different here today",
                          "evidence": {"claim_ids": ["c2"]}})
    diff = si.compare_revisions(a, b)
    assert diff["sections_added"] == ["s2"] and diff["claims_added"] == ["c2"] and diff["package_changed"] is True
    assert diff["overlap_ratio"] > 0.7
    hits = si.repetition_against(b, [{"job_id": "j9", "version": 1, "body": a}])
    assert hits and hits[0]["flag"] == "REPEATED_WORDING"
    unrelated = {"version": 1, "sections": [{"section_id": "x", "narration": "completely unrelated words about weather and cooking"}],
                 "closing": "bye", "opening_hook": ""}
    assert si.repetition_against(b, [{"job_id": "j9", "version": 1, "body": unrelated}]) == []


# -- service level ----------------------------------------------------------


def test_offline_provider_generates_attributed_script_end_to_end(approved, monkeypatch):
    s = approved
    monkeypatch.setenv("DEFENCE_STUDIO_SCRIPT_PROVIDER", "offline")
    service = ScriptService(s["db"], s["root"])  # no injected provider -> env selects offline
    revision = service.generate(s["job"], OWNER, {**GOOD_CONFIG, "target_words": 100}, "off-1")
    assert revision["usage"]["provider"] == "offline-extractive"
    assert revision["usage"]["measured_text_cost_inr"] == 0.0
    assert revision["sections"][0]["statement_kind"] == "ATTRIBUTED_CLAIM"
    assert revision["status"] in ("APPROVED", "VALIDATION_FAILED")
    assert not any(i["code"] == "FACT_NOT_CORROBORATED" for i in revision["validation"]["issues"])


def test_budget_guard_blocks_paid_call_before_any_request(approved):
    s = approved

    class Paid(FakeProvider):
        is_paid = True
        name = "openai"

    paid = Paid(body_factory=lambda _p: _default_body(s["claim_id"]))
    service = ScriptService(s["db"], s["root"], provider=paid)
    with pytest.raises(ScriptGenerationFailed, match="Budget guard"):
        service.generate(s["job"], OWNER, {**GOOD_CONFIG, "max_text_cost_inr": 0.0001}, "bg-1")
    with pytest.raises(ScriptGenerationFailed, match="no pricing"):
        service.generate(s["job"], OWNER, {**GOOD_CONFIG, "model": "unknown-model"}, "bg-2")
    assert paid.calls == 0
    service.generate(s["job"], OWNER, GOOD_CONFIG, "bg-3")  # normal budget proceeds
    assert paid.calls == 1


def test_schema_invalid_model_output_fails_explicitly_and_writes_nothing(approved):
    s = approved
    bad = FakeProvider(body_factory=lambda _p: {"title": "t", "sections": [{"section_id": "s1"}], "closing": "c"})
    service = ScriptService(s["db"], s["root"], provider=bad)
    with pytest.raises(ScriptGenerationFailed, match="schema"):
        service.generate(s["job"], OWNER, GOOD_CONFIG, "bad-1")
    assert service.list_revisions(s["job"], OWNER)["revisions"] == []
    not_object = FakeProvider(body_factory=lambda _p: ["nope"])
    with pytest.raises(ScriptGenerationFailed):
        ScriptService(s["db"], s["root"], provider=not_object).generate(s["job"], OWNER, GOOD_CONFIG, "bad-2")


def test_compare_repetition_and_staleness_after_upstream_change(approved):
    s = approved
    service = s["service"]
    r1 = service.generate(s["job"], OWNER, GOOD_CONFIG, "v1")
    r2 = service.generate(s["job"], OWNER, GOOD_CONFIG, "v2")
    diff = service.compare(s["job"], OWNER, "ta", r1["version"], r2["version"])
    assert diff["overlap_ratio"] == 1.0 and diff["package_changed"] is False
    rep = service.repetition_report(s["job"], OWNER, "ta", r2["version"])
    assert rep["compared_against"] == 1 and rep["matches"] and rep["matches"][0]["flag"] == "REPEATED_WORDING"
    fresh = service.revision_staleness(s["job"], OWNER)
    assert fresh["evidence_ready"] and {r["state"] for r in fresh["revisions"]} == {"CURRENT"}
    claims = s["evidence"].draft(s["job"], OWNER)["package"]["claims"]
    s["evidence"].decide_claim(s["job"], OWNER, claims[0]["claim_id"], "NEEDS_MORE_EVIDENCE",
                               claims[0]["review"]["reviewed_artifact_sha256"], 1, "New concern", "re-review")
    stale = service.revision_staleness(s["job"], OWNER)
    assert {r["state"] for r in stale["revisions"]} == {"STALE"}
    assert all(r["reason"] for r in stale["revisions"])


def test_http_rm2_routes_auth_and_owner_isolation(approved):
    s = approved
    s["service"].generate(s["job"], OWNER, GOOD_CONFIG, "http-1")
    app = create_app(s["db"], SECRET, s["root"])
    mine = {"Authorization": f"Bearer {access_token(OWNER)}"}
    other = {"Authorization": f"Bearer {access_token('intruder')}"}
    paths = [f"/api/jobs/{s['job']}/script/staleness",
             f"/api/jobs/{s['job']}/script/revisions/ta/1/repetition",
             f"/api/jobs/{s['job']}/script/compare/ta?old=1&new=1"]
    with TestClient(app) as client:
        for path in paths:
            assert client.get(path).status_code == 401
            assert client.get(path, headers=other).status_code == 404
            assert client.get(path, headers=mine).status_code == 200
