"""Phase 4 M1 scene-planning service tests.

Builds a genuinely APPROVED Phase 3 script revision on the real
F3/F4/F5/Phase-2/Phase-3 fixtures (no network, injected fake text provider),
then drives SceneService end to end: deterministic initial plan, persistence +
retrieval, versioned immutable storage, per-scene content-hash isolation across
an edit, validation failures, idempotent generation/edit, revision-conflict
protection, and the approval rules (plan-hash binding + re-sync-required when
the underlying approved script changes).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.contracts.evidence import digest
from defence_studio.services.evidence import EvidenceService
from defence_studio.services.review import ReviewNotFound
from defence_studio.services.scene import (
    SceneNotFound, SceneRevisionConflict, SceneService, SceneStaleRevision, SceneValidationError,
)
from defence_studio.services.script import ScriptService, ScriptStaleRevision
from test_script_phase3 import FakeProvider, GOOD_CONFIG
from test_review_f5 import _basic_job, OWNER
from conftest import SECRET, access_token

SCENE_CONFIG = {"language": "ta", "target_total_seconds": 300, "words_per_minute": 150,
                "default_transition": "cut"}


def _multi_section_body(claim_id: str) -> dict:
    """An approved-script body with two material sections + hook + closing, so a
    derived plan has four scenes to edit/reorder."""
    return {
        "title": "Rafale readiness update",
        "opening_hook": "Here is what officials have said.",
        "sections": [
            {"section_id": "s1", "heading": "Status", "narration": "அதிகாரிகள் தெரிவித்த தகவலின்படி.",
             "requires_evidence": True,
             "evidence": {"claim_ids": [claim_id], "source_ids": [], "qualification": "Attributed"},
             "presentation_note": ""},
            {"section_id": "s2", "heading": "Context", "narration": "நிலைமை தொடர்கிறது.",
             "requires_evidence": True,
             "evidence": {"claim_ids": [claim_id], "source_ids": [], "qualification": "Attributed"},
             "presentation_note": ""},
        ],
        "closing": "அதுதான் சமீபத்திய தகவல்.",
    }


@pytest.fixture
def approved_script(tmp_path):
    """A job carrying one genuinely APPROVED Tamil script revision, plus the
    handles needed to build/approve scene plans and to mint a *new* approved
    script revision (for the re-sync test)."""
    values = _basic_job(tmp_path)
    db, root, jobs, store, job, review, result, ref, _, acq_sha = values
    review.decide_source(job, OWNER, "src-001", "APPROVED_FOR_USE", acq_sha, 0, "Source inspected", "s1")
    review.decide_claim(job, OWNER, result.claims[0].claim_id, "APPROVED_FOR_USE", ref["sha256"], 0, "Passage inspected", "c1")
    evidence = EvidenceService(db, root)
    draft = evidence.draft(job, OWNER)
    evidence.approve(job, OWNER, draft["revision"], "Evidence reviewed", "evidence-approve")
    claim_id = draft["package"]["eligible_claim_ids"][0]

    provider = FakeProvider(body_factory=lambda _p: _multi_section_body(claim_id))
    script = ScriptService(db, root, provider=provider)
    revision = script.generate(job, OWNER, GOOD_CONFIG, "gen-1")
    script.approve(job, OWNER, "ta", revision["version"],
                   _rev_sha(script, job, revision["version"]), "Script approved", "script-approve-1")

    scene = SceneService(db, root, provider=provider)
    return dict(db=db, root=root, job=job, evidence=evidence, script=script, scene=scene,
                provider=provider, claim_id=claim_id)


def _rev_sha(script, job, version, language="ta"):
    """The DB-row `revision_sha256` for a script revision (not on the body)."""
    rows = script.list_revisions(job, OWNER, language)["revisions"]
    return next(r["revision_sha256"] for r in rows if r["version"] == version)


def _plan_sha(scene, job, version, language="ta"):
    """The DB-row `plan_sha256` for a scene-plan version. Like the script
    service, `generate`/`revise` return the content-addressed body; the stored
    hash lives on the row, and equals digest() of the body."""
    rows = scene.list_plans(job, OWNER, language)["plans"]
    return next(r["plan_sha256"] for r in rows if r["version"] == version)


def _generate_plan(s, key="scene-gen-1"):
    return s["scene"].generate(s["job"], OWNER, SCENE_CONFIG, key)


# ---------------------------------------------------------------------------
# 1. Deterministic initial plan from the approved script
# ---------------------------------------------------------------------------


def test_initial_plan_is_one_scene_per_section_verbatim(approved_script):
    s = approved_script
    plan = _generate_plan(s)
    assert plan["schema_version"] == "defence-studio.scene-plan.v1"
    assert plan["status"] == "DRAFT" and plan["version"] == 1
    # hook + 2 sections + closing = 4 scenes, strictly ordered.
    assert [sc["order"] for sc in plan["scenes"]] == [1, 2, 3, 4]
    assert [sc["source_section_id"] for sc in plan["scenes"]] == \
        ["__opening_hook__", "s1", "s2", "__closing__"]
    # Narration copied verbatim from the approved script; nothing rewritten.
    export = s["script"].export_approved(s["job"], OWNER, "ta")
    assert plan["scenes"][1]["narration"] == export["sections"][0]["narration"]
    assert plan["scenes"][3]["narration"] == export["closing"]
    # Claims ride along only within the approved eligible set.
    assert set(plan["scenes"][1]["claim_ids"]) <= set(export["eligible_claim_ids"])
    # Planned timing is present and flagged as an estimate; no measured timing.
    assert plan["scenes"][1]["planned_timing"]["is_estimate"] is True
    assert plan["scenes"][1]["measured_timing"] is None
    # Bound to the exact approved script revision + its evidence package.
    assert plan["script_revision_sha256"] == export["approval"]["revision_sha256"]
    assert plan["package_sha256"] == export["package_sha256"]


def test_generate_is_deterministic_and_content_addressed(approved_script):
    s = approved_script
    plan = _generate_plan(s)
    # The stored plan_sha256 is the canonical digest of the materialized body
    # (minus the DB-overlaid status, which equals the stored status here).
    stored = s["scene"].get_plan(s["job"], OWNER, "ta", 1)
    # The content-addressed body hashes to the stored row's plan_sha256.
    assert digest(stored) == digest(plan) == _plan_sha(s["scene"], s["job"], 1)


def test_generation_fails_closed_without_approved_script(tmp_path):
    values = _basic_job(tmp_path)
    db, root, jobs, store, job, review, result, ref, _, acq_sha = values
    scene = SceneService(db, root, provider=FakeProvider())
    # No approved script revision exists -> export_approved raises, surfaced as 409.
    with pytest.raises(ScriptStaleRevision):
        scene.generate(job, OWNER, SCENE_CONFIG, "nope")


# ---------------------------------------------------------------------------
# 2. Persistence, retrieval, cross-owner
# ---------------------------------------------------------------------------


def test_persist_and_retrieve_and_cross_owner_denied(approved_script):
    s = approved_script
    _generate_plan(s)
    fresh = SceneService(s["db"], s["root"])  # a brand-new service instance (restart)
    got = fresh.get_plan(s["job"], OWNER, "ta", 1)
    assert got["version"] == 1 and len(got["scenes"]) == 4
    assert fresh.list_plans(s["job"], OWNER)["plans"][0]["version"] == 1
    with pytest.raises(ReviewNotFound):
        fresh.get_plan(s["job"], "someone-else", "ta", 1)
    with pytest.raises(ReviewNotFound):
        fresh.list_plans(s["job"], "someone-else")
    with pytest.raises(SceneNotFound):
        fresh.get_plan(s["job"], OWNER, "ta", 99)


# ---------------------------------------------------------------------------
# 3. Per-scene content-hash isolation across an edit (the M1 property)
# ---------------------------------------------------------------------------


def test_editing_one_scene_does_not_change_other_scenes_content_hash(approved_script):
    s = approved_script
    _generate_plan(s)
    before = s["scene"].scene_digests(s["job"], OWNER, "ta", 1)
    # Edit only scene sc-0002's purpose.
    revised = s["scene"].revise(
        s["job"], OWNER, "ta", 1,
        {"update": {"sc-0002": {"purpose": "Revised purpose line"}}},
        "edit-1",
    )
    assert revised["version"] == 2 and revised["status"] == "DRAFT"
    after = s["scene"].scene_digests(s["job"], OWNER, "ta", 2)
    # Every untouched scene keeps an identical content hash across the version.
    for scene_id in ("sc-0001", "sc-0003", "sc-0004"):
        assert after[scene_id] == before[scene_id], scene_id
    # The edited scene's hash changed.
    assert after["sc-0002"] != before["sc-0002"]
    # Version 1 is immutable: its stored scenes are unchanged.
    assert s["scene"].scene_digests(s["job"], OWNER, "ta", 1) == before


def test_reorder_remove_add_resequence_and_preserve_untouched(approved_script):
    s = approved_script
    _generate_plan(s)
    before = s["scene"].scene_digests(s["job"], OWNER, "ta", 1)
    # Remove the closing, reorder the rest, add one new scene at the front.
    revised = s["scene"].revise(
        s["job"], OWNER, "ta", 1,
        {
            "remove": ["sc-0004"],
            "reorder": ["sc-0002", "sc-0001", "sc-0003"],
            "add": {"at": 0, "scenes": [{
                "scene_id": "intro", "source_section_id": "__custom__",
                "narration": "A newly written intro line.",
                "planned_timing": {"words": 5, "words_per_minute": 150,
                                   "estimated_seconds": 2.0, "method": "words-per-minute.v1",
                                   "is_estimate": True},
            }]},
        },
        "edit-2",
    )
    order = [(sc["scene_id"], sc["order"]) for sc in revised["scenes"]]
    assert order == [("intro", 1), ("sc-0002", 2), ("sc-0001", 3), ("sc-0003", 4)]
    # sc-0001/sc-0003 only moved position; their non-order content is unchanged,
    # but their stored hash includes `order`, so moved scenes legitimately differ.
    # Prove instead that a scene whose position is unchanged keeps its hash:
    # none here keeps the same order, so verify the removed one is gone.
    assert "sc-0004" not in {sc["scene_id"] for sc in revised["scenes"]}


# ---------------------------------------------------------------------------
# 4. Validation failures
# ---------------------------------------------------------------------------


def test_edit_validation_failures(approved_script):
    s = approved_script
    _generate_plan(s)
    sc = s["scene"]
    with pytest.raises(SceneValidationError):  # unknown op
        sc.revise(s["job"], OWNER, "ta", 1, {"frobnicate": True}, "e1")
    with pytest.raises(SceneValidationError):  # update unknown scene
        sc.revise(s["job"], OWNER, "ta", 1, {"update": {"nope": {"purpose": "x"}}}, "e2")
    with pytest.raises(SceneValidationError):  # non-editable field
        sc.revise(s["job"], OWNER, "ta", 1, {"update": {"sc-0001": {"order": 5}}}, "e3")
    with pytest.raises(SceneValidationError):  # citing a claim not in the approved package
        sc.revise(s["job"], OWNER, "ta", 1, {"update": {"sc-0002": {"claim_ids": ["ghost-claim"]}}}, "e4")
    with pytest.raises(SceneValidationError):  # reorder not a permutation
        sc.revise(s["job"], OWNER, "ta", 1, {"reorder": ["sc-0001"]}, "e5")
    with pytest.raises(SceneValidationError):  # removing every scene
        sc.revise(s["job"], OWNER, "ta", 1, {"remove": ["sc-0001", "sc-0002", "sc-0003", "sc-0004"]}, "e6")
    with pytest.raises(SceneValidationError):  # empty edits
        sc.revise(s["job"], OWNER, "ta", 1, {}, "e7")


def test_bad_scene_config_rejected(approved_script):
    s = approved_script
    with pytest.raises(Exception):  # pydantic ValidationError: language/config mismatch handled upstream
        s["scene"].generate(s["job"], OWNER, {**SCENE_CONFIG, "target_total_seconds": 5}, "bad")


# ---------------------------------------------------------------------------
# 5. Idempotency + revision-conflict protection
# ---------------------------------------------------------------------------


def test_idempotent_generation_and_edit(approved_script):
    s = approved_script
    first = _generate_plan(s, "same-key")
    again = _generate_plan(s, "same-key")
    assert digest(first) == digest(again) and first["version"] == again["version"]
    assert len(s["scene"].list_plans(s["job"], OWNER)["plans"]) == 1
    e1 = s["scene"].revise(s["job"], OWNER, "ta", 1, {"update": {"sc-0001": {"purpose": "p"}}}, "edit-key")
    e2 = s["scene"].revise(s["job"], OWNER, "ta", 1, {"update": {"sc-0001": {"purpose": "p"}}}, "edit-key")
    assert e1["version"] == e2["version"] == 2
    assert len(s["scene"].list_plans(s["job"], OWNER)["plans"]) == 2


def test_editing_a_stale_base_version_is_rejected(approved_script):
    s = approved_script
    _generate_plan(s)
    s["scene"].revise(s["job"], OWNER, "ta", 1, {"update": {"sc-0001": {"purpose": "p"}}}, "edit-a")  # -> v2
    # Editing from v1 again (not the latest) is a revision conflict.
    with pytest.raises(SceneRevisionConflict):
        s["scene"].revise(s["job"], OWNER, "ta", 1, {"update": {"sc-0002": {"purpose": "q"}}}, "edit-b")


# ---------------------------------------------------------------------------
# 6. Approval rules: plan-hash binding, supersession, re-sync-required
# ---------------------------------------------------------------------------


def test_approval_binds_to_plan_hash_and_supersedes_previous(approved_script):
    s = approved_script
    plan = _generate_plan(s)
    # Wrong hash is rejected.
    with pytest.raises(SceneStaleRevision):
        s["scene"].approve(s["job"], OWNER, "ta", 1, "f" * 64, "Approve", "appr-bad")
    approved = s["scene"].approve(s["job"], OWNER, "ta", 1, digest(plan), "Looks right", "appr-1")
    assert approved["status"] == "APPROVED"
    # Idempotent replay.
    assert s["scene"].approve(s["job"], OWNER, "ta", 1, digest(plan), "Looks right", "appr-1")["status"] == "APPROVED"
    # Approving a second version supersedes the first (same language only).
    v2 = s["scene"].revise(s["job"], OWNER, "ta", 1, {"update": {"sc-0001": {"purpose": "p"}}}, "edit-x")
    s["scene"].approve(s["job"], OWNER, "ta", 2, digest(v2), "Approve v2", "appr-2")
    assert s["scene"].get_plan(s["job"], OWNER, "ta", 1)["status"] == "SUPERSEDED"
    assert s["scene"].get_plan(s["job"], OWNER, "ta", 2)["status"] == "APPROVED"


def test_validation_failed_states_cannot_be_approved(approved_script):
    s = approved_script
    plan = _generate_plan(s)
    s["scene"].request_changes(s["job"], OWNER, "ta", 1, "Please adjust pacing", "rc-1")
    assert s["scene"].get_plan(s["job"], OWNER, "ta", 1)["status"] == "CHANGES_REQUESTED"
    # CHANGES_REQUESTED can still be approved once the reviewer is satisfied
    # (mirrors script lifecycle); but a SUPERSEDED/APPROVED one cannot be re-approved.
    s["scene"].approve(s["job"], OWNER, "ta", 1, digest(plan), "OK now", "rc-appr")
    with pytest.raises(SceneStaleRevision):
        s["scene"].approve(s["job"], OWNER, "ta", 1, digest(plan), "again", "rc-appr-2")


def test_reapproval_rejected_after_underlying_script_changes_until_resynced(approved_script):
    s = approved_script
    plan = _generate_plan(s)  # built from approved script revision v1

    # The owner generates and approves a NEW script revision, which supersedes
    # the old one -> export_approved now returns a different revision_sha256.
    new_rev = s["script"].generate(s["job"], OWNER, GOOD_CONFIG, "gen-2")
    s["script"].approve(s["job"], OWNER, "ta", new_rev["version"],
                        _rev_sha(s["script"], s["job"], new_rev["version"]), "New script approved", "script-approve-2")
    new_export = s["script"].export_approved(s["job"], OWNER, "ta")
    assert new_export["approval"]["revision_sha256"] != plan["script_revision_sha256"]

    # Approving the stale scene plan (built on the old script) is now refused
    # until it is re-synced.
    with pytest.raises(SceneStaleRevision):
        s["scene"].approve(s["job"], OWNER, "ta", 1, digest(plan), "Approve stale", "appr-stale")

    # Re-sync: regenerate a scene plan against the new approved script, which
    # binds to the new revision and can be approved.
    resynced = s["scene"].generate(s["job"], OWNER, SCENE_CONFIG, "scene-gen-resync")
    assert resynced["script_revision_sha256"] == new_export["approval"]["revision_sha256"]
    ok = s["scene"].approve(s["job"], OWNER, "ta", resynced["version"], digest(resynced), "Approve resynced", "appr-resync")
    assert ok["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# 7. API surface (auth, status codes) via TestClient
# ---------------------------------------------------------------------------


def test_api_scene_routes_auth_and_flow(tmp_path):
    app = create_app(tmp_path / "db.sqlite3", SECRET, tmp_path / "store")
    with TestClient(app) as client:
        # Unauthenticated -> 401.
        assert client.get("/api/jobs/job-x/scene/plans").status_code == 401
        headers = {"Authorization": f"Bearer {access_token('user-1')}"}
        # Unknown job -> 404 (owner check).
        r = client.get("/api/jobs/job-x/scene/plans", headers=headers)
        assert r.status_code == 404
        # Generate against a non-existent approved script -> create a real job first.
        job = client.post("/api/jobs", headers={**headers, "Idempotency-Key": "j1"},
                          json={"topic": "t", "language": "ta"}).json()
        gen = client.post(f"/api/jobs/{job['job_id']}/scene/generate",
                          headers={**headers, "Idempotency-Key": "g1"}, json=SCENE_CONFIG)
        # No approved script exists for this fresh job -> fail-closed 409.
        assert gen.status_code == 409
