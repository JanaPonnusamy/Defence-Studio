"""Topic-independent evidence platform: one pipeline, many topics, no topic-specific code.

Every case runs through the same EvidencePolicyService / readiness evaluator / script gate. Topic
behaviour comes only from configuration (registry entries, subject categories, claim classes).
Offline: no network, no paid provider.
"""
import copy
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.api.app import create_app
from defence_studio.contracts.jobs import JOB_STAGES
from defence_studio.contracts.evidence import digest
from defence_studio.database import SCHEMA_VERSION
from defence_studio.repositories import policy_decisions
from defence_studio.services.analysis import AnalysisService
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.evidence_policy import EvidencePolicyService, load_evidence_policy
from defence_studio.services.evidence_readiness import PolicyConfigError, validate_policy_config
from defence_studio.services.job_workspace import JobWorkspaceService
from defence_studio.services.review import ReviewService, StaleRevision
from defence_studio.services.script import ScriptService
from conftest import SECRET, access_token
from test_review_f5 import (ACQUISITION_STAGE, ACQUISITION_RESULT_ARTIFACT, ACQUISITION_RESULT_SCHEMA, ANALYSIS_STAGE,
                            OWNER, _advance, _complete, _setup, _source, _store_acquisition)
from test_script_phase3 import FakeProvider, GOOD_CONFIG


def entry(eid, domain, category, group, subjects=(), quant="CORROBORATION_REQUIRED", types=("text/plain",), publisher=True):
    return {"id": eid, "domain": domain, "category": category, "independence_group": group,
            "publisher": f"{eid} publisher" if publisher else "", "enabled": True,
            "supported_subjects": list(subjects), "allowed_content_types": list(types),
            "trust": {"descriptive_claims": True, "quantitative_claims": quant}}


REGISTRY = {"version": "defence-studio.source-policy.v1", "sources": [
    entry("inst1", "inst1.example", "RESEARCH_INSTITUTION", "g-inst1", ["MILITARY_EQUIPMENT", "AIR_DEFENCE_MISSILES", "CONFLICT_OPERATIONS"]),
    entry("inst2", "inst2.example", "RESEARCH_INSTITUTION", "g-inst2", ["MILITARY_EQUIPMENT", "AIR_DEFENCE_MISSILES", "CONFLICT_OPERATIONS", "DEFENCE_POLICY"]),
    entry("gov", "gov.example", "GOVERNMENT_OFFICIAL", "g-gov", ["INDIA_DEFENCE", "DEFENCE_POLICY"], quant="ATTRIBUTED_ONLY"),
    entry("news", "news.example", "INDEPENDENT_REPORTING", "g-news", ["INDIA_DEFENCE", "DEFENCE_POLICY", "CONFLICT_OPERATIONS", "MILITARY_EQUIPMENT"]),
    entry("maker", "maker.example", "MANUFACTURER", "g-maker", ["MILITARY_EQUIPMENT"], quant="ATTRIBUTED_ONLY"),
    entry("space", "space.example", "RESEARCH_INSTITUTION", "g-space", ["SPACE_LAUNCH"]),
]}


def policy(**readiness):
    pol = copy.deepcopy(load_evidence_policy())
    pol["readiness"].update({"min_accepted_claims": 2, "min_accepted_sources": 2}, **readiness)
    pol["relevance"]["min_source_anchor_mentions"] = 1       # tiny fixtures; production default is 3
    return pol


def build(tmp_path, topic, sources, *, pol=None, registry=None):
    db, root, jobs, store = _setup(tmp_path)
    job = jobs.create(OWNER, f"job-{abs(hash(topic)) % 10**6}", topic, "en")["job_id"]
    jobs.transition(job, OWNER, JOB_STAGES[0], "SKIPPED")
    jobs.transition(job, OWNER, JOB_STAGES[1], "SKIPPED")
    _advance(jobs, job, ACQUISITION_STAGE)
    built = [_source(store, job, sid, url, text) for sid, url, text in sources]
    uri, sha = _store_acquisition(store, job, built)
    jobs.add_artifact(job, OWNER, ACQUISITION_STAGE, ACQUISITION_RESULT_ARTIFACT, uri, sha, ACQUISITION_RESULT_SCHEMA)
    _complete(jobs, job, ACQUISITION_STAGE)
    _advance(jobs, job, ANALYSIS_STAGE)
    ref, _ = AnalysisService(db, root).run_and_store(job, OWNER, uri, sha, topic=topic)
    jobs.add_artifact(job, OWNER, ANALYSIS_STAGE, ref["artifact_type"], ref["uri"], ref["sha256"], ref["schema_version"])
    _complete(jobs, job, ANALYSIS_STAGE)
    svc = EvidencePolicyService(db, root, policy=pol or policy(), source_policy=SourcePolicy.from_dict(registry or REGISTRY),
                                workspace=JobWorkspaceService(db, tmp_path / "ws"))
    return svc, job, db, root


def state_of(res, needle, source=None):
    rows = [c for c in res["claims"] if needle in c["text"] and (source is None or c["source_id"] == source)]
    assert rows, needle
    return rows[0]


# 1 ─ equipment technical specification ────────────────────────────────────────────────────────────
def test_equipment_spec_topic_accepts_corroborated_figures_and_rejects_manufacturer_only(tmp_path):
    spec = "The Rafale M carrier fighter has a maximum speed of Mach 1.8 according to the Naval Analysts. "
    desc = "The Rafale M is a carrier-capable multirole fighter built by Dassault Aviation for naval operations. "
    svc, job, *_ = build(tmp_path, "Rafale M carrier fighter technical specifications", [
        ("a", "https://inst1.example/rafale", desc + spec),
        ("b", "https://inst2.example/rafale", desc + spec),
        ("m", "https://maker.example/rafale", "The Rafale M fighter reaches a maximum speed of Mach 1.9 in the maker brochure. ")])
    res = svc.evaluate(job, OWNER)
    fig = state_of(res, "Mach 1.8", "a")
    assert fig["state"] == "AUTO_ACCEPTED" and fig["claim_class"] == "PERFORMANCE_FIGURE" and fig["independent_groups"] >= 2
    maker = state_of(res, "Mach 1.9", "m")
    assert maker["state"] != "AUTO_ACCEPTED"                      # manufacturer never counts as verification
    assert state_of(res, "carrier-capable multirole", "a")["claim_class"] == "DESCRIPTIVE"
    assert state_of(res, "carrier-capable multirole", "a")["state"] == "AUTO_ACCEPTED"   # low-risk, one source ok


# 2 ─ India defence announcement ───────────────────────────────────────────────────────────────────
def test_india_announcement_topic_uses_attribution_and_subject_eligibility(tmp_path):
    svc, job, *_ = build(tmp_path, "India defence ministry procurement announcement for naval helicopters", [
        ("g", "https://gov.example/release", "The defence ministry announced the procurement of naval helicopters for the Indian Navy. "
                                             "Officials said the contract covers delivery over several years. "),
        ("n", "https://news.example/story", "The defence ministry announced the procurement of naval helicopters for the Indian Navy this week. "),
        ("s", "https://inst1.example/other", "The ministry procurement of naval helicopters was discussed by the institute analysts. ")])
    res = svc.evaluate(job, OWNER)
    assert "INDIA_DEFENCE" in res["subject_categories"] and "DEFENCE_POLICY" in res["subject_categories"]
    src = {s["source_id"]: s for s in res["sources"]}
    assert src["g"]["state"] == "AUTO_ACCEPTED" and src["n"]["state"] == "AUTO_ACCEPTED"
    assert src["s"]["state"] == "REJECTED" and "not configured for this topic" in src["s"]["reasons"][0]
    assert state_of(res, "announced the procurement", "g")["state"] == "AUTO_ACCEPTED"


# 3 ─ current military news ───────────────────────────────────────────────────────────────────────
def test_current_event_claims_need_corroboration_or_attribution(tmp_path):
    svc, job, *_ = build(tmp_path, "Navy frigate commissioning news", [
        ("n", "https://news.example/a", "The frigate Shivalik Class is currently undergoing sea trials at Mazagon Dock in 2026. "
                                        "Officials said the frigate commissioning at Mazagon Dock is planned for 2027. "),
        ("a", "https://inst1.example/b", "The Navy frigate programme has a long procurement history across several shipyards. ")])
    res = svc.evaluate(job, OWNER)
    cur = state_of(res, "currently undergoing")
    assert cur["claim_class"] == "CURRENT_EVENT" and cur["state"] == "NEEDS_REVIEW"
    attributed = state_of(res, "Officials said")
    assert attributed["state"] in ("AUTO_ACCEPTED", "NEEDS_REVIEW")      # never silently promoted to fact elsewhere


# 4 ─ conflict / operational development ─────────────────────────────────────────────────────────
def test_operational_and_casualty_claims_are_strict(tmp_path):
    svc, job, *_ = build(tmp_path, "Ukraine drone strike operational development", [
        ("a", "https://inst1.example/x", "The Ukrainian drone strike hit a refinery depot in Belgorod Oblast near the Russian border. "
                                          "Casualties from the Ukrainian drone strike in Belgorod Oblast were reported by local officials. "),
        ("b", "https://inst2.example/x", "The Ukrainian drone strike hit a refinery depot in Belgorod Oblast near the Russian border overnight. ")])
    res = svc.evaluate(job, OWNER)
    strike = state_of(res, "hit a refinery", "a")
    assert strike["claim_class"] in ("OPERATIONAL", "DESCRIPTIVE") and strike["independent_groups"] >= 2
    cas = state_of(res, "Casualties", "a")
    assert cas["claim_class"] == "CASUALTY_LOSS"
    assert cas["state"] == "AUTO_ACCEPTED" and "local officials" in cas["text"] or cas["state"] == "NEEDS_REVIEW"


# 5 ─ insufficient corroboration stops before any provider call ──────────────────────────────────
def test_insufficient_corroboration_blocks_generation_with_zero_provider_calls(tmp_path):
    svc, job, db, root = build(tmp_path, "Barak missile range assessment", [
        ("a", "https://inst1.example/barak", "The Barak missile has a maximum range of 70 km according to the institute data. ")],
        pol=policy(min_accepted_claims=1, min_accepted_sources=1))
    res = svc.run(job, OWNER)
    fig = state_of(res, "70 km")
    assert fig["state"] == "NEEDS_REVIEW" and "policy requires 2" in fig["reasons"][0]
    assert not res["ready_for_generation"] and res["package_approval"] is None
    assert {f["criterion"] for f in res["policy_criteria_failed"]} >= {"min_accepted_claims"}
    provider = FakeProvider()
    with pytest.raises(StaleRevision):
        ScriptService(db, root, provider=provider).generate(job, OWNER, GOOD_CONFIG, "g1")
    assert provider.calls == 0


# 6 ─ conflicting evidence ────────────────────────────────────────────────────────────────────────
def test_conflicting_figures_are_never_accepted_and_block_nothing_silently(tmp_path):
    svc, job, db, root = build(tmp_path, "Zorya-1 interceptor range", [
        ("a", "https://inst1.example/z", "The Zorya-1 interceptor has a maximum range of 150 km according to analysts. "),
        ("b", "https://inst2.example/z", "The Zorya-1 interceptor has a maximum range of 250 km according to analysts. ")],
        pol=policy(min_accepted_claims=1, min_accepted_sources=1))
    res = svc.evaluate(job, OWNER)
    rng = [c for c in res["claims"] if "km" in c["text"]]
    assert len(rng) == 2
    assert all(c["state"] != "AUTO_ACCEPTED" for c in rng)           # disputed / single-source: excluded
    out = svc.run(job, OWNER)
    assert not out["ready_for_generation"]


# 7 ─ unknown / untrusted sources ─────────────────────────────────────────────────────────────────
def test_unknown_and_disabled_sources_are_blocked_and_never_independent(tmp_path):
    reg = copy.deepcopy(REGISTRY)
    reg["sources"].append({**entry("off", "off.example", "RESEARCH_INSTITUTION", "g-off", ["MILITARY_EQUIPMENT"]),
                           "enabled": False})
    text = "The Harop loitering munition was developed by Israel Aerospace Industries as a drone for attacking radar targets. "
    svc, job, *_ = build(tmp_path, "Harop loitering munition overview", [
        ("a", "https://inst1.example/h", text), ("u", "https://unknown.example/h", text),
        ("d", "https://off.example/h", text)], registry=reg)
    res = svc.evaluate(job, OWNER)
    s = {x["source_id"]: x for x in res["sources"]}
    assert s["u"]["state"] == "SOURCE_BLOCKED" and s["d"]["state"] == "SOURCE_BLOCKED"
    assert all(c["state"] == "SOURCE_BLOCKED" for c in res["claims"] if c["source_id"] in ("u", "d"))
    assert s["u"]["independence_group"] is None            # unknown sources are never an independent group


# 8 ─ a brand-new category needs only configuration ────────────────────────────────────────────────
def test_new_topic_category_requires_no_code_change(tmp_path):
    pol = policy(min_accepted_claims=1, min_accepted_sources=1)
    pol["subject_categories"].append({"id": "SPACE_LAUNCH", "keywords": ["launch vehicle", "orbital", "satellite"]})
    svc, job, *_ = build(tmp_path, "Orbital launch vehicle reusability", [
        ("s", "https://space.example/lv", "The orbital launch vehicle from Space Vendor uses a reusable first stage that returns to a landing site in 2026. ")], pol=pol)
    res = svc.run(job, OWNER)
    assert res["subject_categories"] == ["SPACE_LAUNCH"]
    assert [x["state"] for x in res["sources"]] == ["AUTO_ACCEPTED"]
    assert res["ready_for_generation"], res.get("explanation")


# ── separation of automated decisions from human review ────────────────────────────────────────────
def two_source_job(tmp_path, **kw):
    desc = "The Harop loitering munition was developed by Israel Aerospace Industries as a drone for attacking radar targets in 2009. "
    more = "The Harop loitering munition is operated by several armed forces for suppression of air defences. "
    return build(tmp_path, "Harop loitering munition overview", [
        ("a", "https://inst1.example/h", desc + more), ("b", "https://inst2.example/h", desc)], **kw)


def test_automation_never_writes_human_ledger_and_human_wins(tmp_path):
    svc, job, db, root = two_source_job(tmp_path)
    out = svc.run(job, OWNER)
    assert out["ready_for_generation"] and out["package_approval"]["approval_kind"] == "AUTOMATED_POLICY"
    with closing(sqlite3.connect(db)) as c:
        for table in ("claim_reviews", "claim_review_history", "source_reviews", "source_review_history"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM evidence_policy_decisions").fetchone()[0] > 0
        assert c.execute("SELECT reviewer_id FROM evidence_packages WHERE job_id=?", (job,)).fetchone()[0] == "AUTOMATED_POLICY"
    # a person overrides one accepted claim; the package changes and the old approval no longer applies
    review = ReviewService(db, root)
    listing = review.list_claims(job, OWNER)
    victim = next(c for c in out["claims"] if c["state"] == "AUTO_ACCEPTED")["claim_id"]
    review.decide_claim(job, OWNER, victim, "REJECTED", listing["analysis_artifact_sha256"], 0, "Not wanted", "hum-1")
    ev = EvidenceService(db, root)
    draft = ev.draft(job, OWNER)
    assert victim not in draft["package"]["eligible_claim_ids"]
    assert draft["current_approval"] is None                      # human change invalidates automated approval
    rec = [c for c in review.list_claims(job, OWNER)["claims"] if c["claim_id"] == victim][0]["review"]
    assert rec["reviewer_id"] == OWNER and rec["note"] == "Not wanted"


def test_decisions_table_is_append_only(tmp_path):
    svc, job, db, root = two_source_job(tmp_path)
    svc.run(job, OWNER)
    with closing(sqlite3.connect(db)) as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE evidence_policy_decisions SET state='AUTO_ACCEPTED'")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("DELETE FROM evidence_policy_decisions")


def test_stale_analysis_invalidates_automated_approval(tmp_path):
    svc, job, db, root = two_source_job(tmp_path)
    first = svc.run(job, OWNER)
    ev = EvidenceService(db, root)
    assert ev.draft(job, OWNER)["current_approval"] is not None
    from defence_studio.database import connect
    with closing(connect(db)) as c:
        # decisions are bound to the exact artifacts: any other analysis hash sees none of them
        assert policy_decisions.latest_run(c, job, first["acquisition_sha256"], first["analysis_sha256"])
        assert policy_decisions.latest_run(c, job, first["acquisition_sha256"], "0" * 64) == []
        # simulate "re-analysis produced different bytes": the stored decisions no longer match
        c.execute("UPDATE workflow_artifacts SET sha256=? WHERE job_id=? AND artifact_type='analysis_result'",
                  ("0" * 64, job))
        c.commit()
    from defence_studio.services.review import ReviewValidationError
    with pytest.raises(ReviewValidationError):               # stored bytes no longer match the recorded hash
        ev.draft(job, OWNER)


def test_policy_version_change_creates_new_versioned_records(tmp_path):
    svc, job, db, root = two_source_job(tmp_path)
    svc.run(job, OWNER)
    pol = policy()
    pol["version"] = "defence-studio.evidence-policy.v9"
    v9 = EvidencePolicyService(db, root, policy=pol, source_policy=svc.sources, workspace=svc.workspace)
    v9.run(job, OWNER)
    with closing(sqlite3.connect(db)) as c:
        assert {r[0] for r in c.execute("SELECT policy_version FROM evidence_policy_decisions")} == {
            "defence-studio.evidence-policy.v1", "defence-studio.evidence-policy.v9"}


def test_invalid_policy_configuration_is_rejected():
    bad = copy.deepcopy(load_evidence_policy())
    bad["readiness"]["min_accepted_claims"] = 0
    with pytest.raises(PolicyConfigError):
        validate_policy_config(bad)
    bad = copy.deepcopy(load_evidence_policy())
    bad["scoring"]["weights"]["relevance"] += 5
    with pytest.raises(PolicyConfigError):
        validate_policy_config(bad)


# ── script contract: prompt, validator and policy agree ─────────────────────────────────────────────
def _body(claim_ids, kind, narration="அதிகாரிகள் தெரிவித்த தகவலின்படி."):
    return {"title": "Harop overview", "opening_hook": "Overview.", "closing": "End.", "sections": [{
        "section_id": "s1", "heading": "Status", "narration": narration, "requires_evidence": True,
        "statement_kind": kind,
        "evidence": {"claim_ids": claim_ids, "source_ids": [], "qualification": "single source: attributed"}}]}


def test_prompt_exposes_the_validator_rule_and_single_source_claims_validate_as_attributed(tmp_path):
    svc, job, db, root = two_source_job(tmp_path)
    out = svc.run(job, OWNER)
    assert out["ready_for_generation"]
    pkg = EvidenceService(db, root).export(job, OWNER)
    from defence_studio.services.script_integrity import corroboration_count
    single = next(c for c in out["claims"] if c["state"] == "AUTO_ACCEPTED" and "suppression" in c["text"])["claim_id"]
    multi = next(c for c in out["claims"] if c["state"] == "AUTO_ACCEPTED" and "Israel Aerospace Industries" in c["text"]
                 and c["independent_groups"] >= 2)["claim_id"]
    assert corroboration_count(pkg, single) == 1 and corroboration_count(pkg, multi) >= 2
    seen = {}

    def factory(prompt):
        seen["prompt"] = prompt
        return _body([single], "ATTRIBUTED_CLAIM")
    provider = FakeProvider(body_factory=factory)
    rev = ScriptService(db, root, provider=provider).generate(job, OWNER, GOOD_CONFIG, "ok-1")
    assert provider.calls == 1 and rev["status"] != "VALIDATION_FAILED", rev["validation"]
    assert '"verified_fact_allowed": false' in seen["prompt"] and '"verified_fact_allowed": true' in seen["prompt"]
    assert "Never combine several single-source claims" in seen["prompt"]
    # unsupported promotion to VERIFIED_FACT is rejected by the validator
    bad = ScriptService(db, root, provider=FakeProvider(body_factory=lambda p: _body([single], "VERIFIED_FACT")))
    rev2 = bad.generate(job, OWNER, GOOD_CONFIG, "bad-1")
    assert rev2["status"] == "VALIDATION_FAILED"
    assert any(i["code"] == "FACT_NOT_CORROBORATED" for i in rev2["validation"]["issues"])
    # a policy-corroborated claim may be a VERIFIED_FACT
    good = ScriptService(db, root, provider=FakeProvider(body_factory=lambda p: _body([multi], "VERIFIED_FACT")))
    rev3 = good.generate(job, OWNER, GOOD_CONFIG, "good-1")
    assert not any(i["code"] == "FACT_NOT_CORROBORATED" for i in rev3["validation"]["issues"])


def test_api_generation_entry_point_is_gated_and_makes_no_provider_call(tmp_path):
    svc, job, db, root = build(tmp_path, "Barak missile range assessment", [
        ("a", "https://inst1.example/barak", "The Barak missile has a maximum range of 70 km according to the institute data. ")],
        pol=policy(min_accepted_claims=1, min_accepted_sources=1))
    svc.run(job, OWNER)
    app = create_app(db, SECRET, storage_root_path=root)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {access_token(OWNER)}", "Idempotency-Key": "api-gen-1"}
        r = client.post(f"/api/jobs/{job}/script/generate", headers=headers, json={**GOOD_CONFIG})
        assert r.status_code == 409, r.text
        assert client.get(f"/api/jobs/{job}/script/revisions", headers=headers).json()["revisions"] == []
        # the policy API is owner scoped
        other = {"Authorization": f"Bearer {access_token('someone-else')}"}
        assert client.get(f"/api/jobs/{job}/evidence/policy", headers=other).status_code == 404
        mine = client.get(f"/api/jobs/{job}/evidence/policy", headers={"Authorization": headers["Authorization"]})
        assert mine.status_code == 200 and mine.json()["ready_for_generation"] is False
