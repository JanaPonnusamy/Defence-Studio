"""Phase 5 M2.1 asset-service + migration tests (no network, no download).

Builds a genuinely approved scene plan on the real F3/F4/F5/Phase-2/Phase-3/M1
fixtures (reusing test_scene_service_m1's approved_script fixture), then drives
AssetService end to end: reference-only registration + idempotency/dedup, rights
revisions + deterministic re-evaluation, approved-plan-bound assignment, stale
plan/digest handling, human review + append-only audit, readiness blockers,
cross-owner isolation, API error codes, and the additive v6->v7 migration.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import defence_studio.database as D
from defence_studio.api.app import create_app
from defence_studio.contracts.assets import (
    ASSET_DESCRIPTOR_SCHEMA, RIGHTS_RECORD_SCHEMA, asset_digest,
)
from defence_studio.services.assets import (
    AssetNotFound, AssetRevisionConflict, AssetService, AssetStaleRevision, AssetValidationError,
)
from conftest import SECRET, access_token
from test_scene_service_m1 import approved_script, SCENE_CONFIG, _plan_sha  # noqa: F401
from test_review_f5 import OWNER


# ---------------------------------------------------------------------------
# Fixture: a job with an APPROVED scene plan + an AssetService.
# ---------------------------------------------------------------------------

@pytest.fixture
def approved_plan(approved_script):
    s = approved_script
    plan = s["scene"].generate(s["job"], OWNER, SCENE_CONFIG, "scene-gen-assets")
    from defence_studio.contracts.evidence import digest
    s["scene"].approve(s["job"], OWNER, "ta", 1, digest(plan), "Plan approved", "plan-appr-assets")
    assets = AssetService(s["db"], s["root"])
    scene_ids = [sc["scene_id"] for sc in plan["scenes"]]
    return dict(s, assets=assets, plan=plan, scene_ids=scene_ids)


def _descriptor(**over):
    base = dict(
        schema_version=ASSET_DESCRIPTOR_SCHEMA, kind="image", title="Press photo",
        provenance={"source_url": "https://example.gov/press/1"},
    )
    base.update(over)
    return base


def _rights(**over):
    base = dict(
        schema_version=RIGHTS_RECORD_SCHEMA, rights_basis="creator_owned",
        attribution_required=True, permitted_uses=["broadcast", "web"],
        commercial_use="allowed",
    )
    base.update(over)
    return base


def _register(s, key="reg-1", **over):
    return s["assets"].register_asset(s["job"], OWNER, _descriptor(**over), key)


# ---------------------------------------------------------------------------
# 1. Registration (reference-only) + idempotency + dedup
# ---------------------------------------------------------------------------

def test_register_is_reference_only_and_draft(approved_plan):
    a = _register(approved_plan)
    assert a["review_status"] == "DRAFT"               # ingestion != approval
    assert a["descriptor"]["content"]["status"] == "REFERENCED_ONLY"
    assert a["descriptor"]["content"]["content_sha256"] is None
    # asset_id is the digest of the id-less body.
    assert len(a["asset_id"]) == 64


def test_register_idempotent_and_dedup(approved_plan):
    s = approved_plan
    a1 = _register(s, "reg-k")
    a2 = _register(s, "reg-k")                          # same key -> replay
    assert a1["asset_id"] == a2["asset_id"]
    assert len(s["assets"].list_assets(s["job"], OWNER)["assets"]) == 1
    # Same descriptor, different key -> dedup to the same content-addressed row.
    a3 = _register(s, "reg-k2")
    assert a3["asset_id"] == a1["asset_id"]
    assert len(s["assets"].list_assets(s["job"], OWNER)["assets"]) == 1


def test_register_cross_owner_denied(approved_plan):
    s = approved_plan
    # _check_ownership raises the 404-family ReviewNotFound (AssetNotFound's
    # parent); either maps to 404, which is the contract that matters here.
    from defence_studio.services.review import ReviewNotFound
    with pytest.raises(ReviewNotFound):
        s["assets"].register_asset(s["job"], "someone-else", _descriptor(), "x")


# ---------------------------------------------------------------------------
# 2. Rights revisions + deterministic re-evaluation
# ---------------------------------------------------------------------------

def test_no_rights_is_unverified(approved_plan):
    a = _register(approved_plan)
    assert a["effective_rights"] == "UNVERIFIED"        # no rights record yet


def test_set_rights_reevaluates_states(approved_plan):
    s = approved_plan
    _register(s, "reg-r")
    aid = s["assets"].list_assets(s["job"], OWNER)["assets"][0]["asset_id"]
    clear = s["assets"].set_rights(s["job"], OWNER, aid, _rights(), "rights-1")
    assert clear["effective_rights"] == "CLEAR"
    blocked = s["assets"].set_rights(s["job"], OWNER, aid, _rights(rights_basis="cc_by_nc"), "rights-2")
    assert blocked["effective_rights"] == "BLOCKED"     # non-commercial
    unknown = s["assets"].set_rights(s["job"], OWNER, aid, _rights(commercial_use="unknown"), "rights-3")
    assert unknown["effective_rights"] == "UNVERIFIED"
    expired = s["assets"].set_rights(s["job"], OWNER, aid,
                                     _rights(expires_at="2000-01-01T00:00:00+00:00"), "rights-4")
    assert expired["effective_rights"] == "EXPIRED"


def test_setting_rights_invalidates_prior_approval(approved_plan):
    s = approved_plan
    _register(s, "reg-inv")
    aid = s["assets"].list_assets(s["job"], OWNER)["assets"][0]["asset_id"]
    a = s["assets"].set_rights(s["job"], OWNER, aid, _rights(), "r1")
    s["assets"].review_asset(s["job"], OWNER, aid, "APPROVED", a["asset_sha256"],
                             a["rights_sha256"], 0, "ok", "rev-1")
    assert s["assets"].get_asset(s["job"], OWNER, aid)["review_status"] == "APPROVED"
    # Changing rights pushes it back to PENDING_REVIEW (reviewed rights changed).
    s["assets"].set_rights(s["job"], OWNER, aid, _rights(attribution_required=False), "r2")
    assert s["assets"].get_asset(s["job"], OWNER, aid)["review_status"] == "PENDING_REVIEW"


# ---------------------------------------------------------------------------
# 3. Assignment bound to the exact approved plan
# ---------------------------------------------------------------------------

def _clear_asset(s, key="reg-a"):
    _register(s, key)
    aid = [x for x in s["assets"].list_assets(s["job"], OWNER)["assets"]][-1]["asset_id"]
    s["assets"].set_rights(s["job"], OWNER, aid, _rights(), key + "-rights")
    return aid


def test_assignment_binds_to_approved_plan(approved_plan):
    s = approved_plan
    aid = _clear_asset(s)
    scene_id = s["scene_ids"][0]
    asn = s["assets"].assign_asset(s["job"], OWNER, "ta", scene_id, aid, "assign-1")
    assert asn["scene_plan_sha256"] == _plan_sha(s["scene"], s["job"], 1)
    assert asn["scene_plan_version"] == 1 and asn["scene_id"] == scene_id
    assert asn["effective_rights"] == "CLEAR" and asn["status"] == "ACTIVE"
    # Idempotent replay.
    again = s["assets"].assign_asset(s["job"], OWNER, "ta", scene_id, aid, "assign-1")
    assert again["version"] == asn["version"]


def test_assign_unknown_scene_rejected(approved_plan):
    s = approved_plan
    aid = _clear_asset(s)
    with pytest.raises(AssetValidationError):
        s["assets"].assign_asset(s["job"], OWNER, "ta", "sc-9999", aid, "assign-bad")


def test_assign_requires_approved_plan(approved_script):
    # A job whose scene plan is NOT approved cannot receive assignments.
    s = approved_script
    s["scene"].generate(s["job"], OWNER, SCENE_CONFIG, "plan-draft")  # DRAFT, not approved
    assets = AssetService(s["db"], s["root"])
    a = assets.register_asset(s["job"], OWNER, _descriptor(), "reg-x")
    assets.set_rights(s["job"], OWNER, a["asset_id"], _rights(), "r-x")
    with pytest.raises(AssetStaleRevision):
        assets.assign_asset(s["job"], OWNER, "ta", "sc-0001", a["asset_id"], "assign-noplan")


def test_non_clear_asset_cannot_be_assigned(approved_plan):
    s = approved_plan
    _register(s, "reg-nc")
    aid = s["assets"].list_assets(s["job"], OWNER)["assets"][-1]["asset_id"]
    s["assets"].set_rights(s["job"], OWNER, aid, _rights(rights_basis="cc_by_nc"), "r-nc")  # BLOCKED
    with pytest.raises(AssetValidationError):
        s["assets"].assign_asset(s["job"], OWNER, "ta", s["scene_ids"][0], aid, "assign-nc")


def test_assignment_flagged_stale_after_plan_reapproval(approved_plan):
    s = approved_plan
    aid = _clear_asset(s)
    s["assets"].assign_asset(s["job"], OWNER, "ta", s["scene_ids"][0], aid, "assign-stale")
    # Revise + approve a new plan version -> the old assignment is now STALE_PLAN.
    from defence_studio.contracts.evidence import digest
    v2 = s["scene"].revise(s["job"], OWNER, "ta", 1, {"update": {"sc-0001": {"purpose": "p"}}}, "edit-stale")
    s["scene"].approve(s["job"], OWNER, "ta", 2, digest(v2), "v2 approved", "appr-v2-stale")
    listing = s["assets"].list_assignments(s["job"], OWNER, "ta")
    assert listing["assignments"][0]["stale_plan"] is True


def test_remove_assignment_does_not_touch_plan(approved_plan):
    s = approved_plan
    aid = _clear_asset(s)
    sid = s["scene_ids"][0]
    s["assets"].assign_asset(s["job"], OWNER, "ta", sid, aid, "assign-rm")
    before = s["scene"].scene_digests(s["job"], OWNER, "ta", 1)
    out = s["assets"].remove_assignment(s["job"], OWNER, "ta", sid, aid, "rm-1")
    assert out["removed"] == 1
    assert s["assets"].list_assignments(s["job"], OWNER, "ta")["assignments"] == []
    assert s["scene"].scene_digests(s["job"], OWNER, "ta", 1) == before  # plan untouched


# ---------------------------------------------------------------------------
# 4. Human review + append-only audit + stale/conflict
# ---------------------------------------------------------------------------

def test_review_pins_digests_and_conflict(approved_plan):
    s = approved_plan
    _register(s, "reg-rev")
    aid = s["assets"].list_assets(s["job"], OWNER)["assets"][-1]["asset_id"]
    a = s["assets"].set_rights(s["job"], OWNER, aid, _rights(), "r-rev")
    # Wrong asset digest -> stale.
    with pytest.raises(AssetStaleRevision):
        s["assets"].review_asset(s["job"], OWNER, aid, "APPROVED", "f" * 64,
                                 a["rights_sha256"], 0, "n", "k1")
    # Wrong rights digest -> stale.
    with pytest.raises(AssetStaleRevision):
        s["assets"].review_asset(s["job"], OWNER, aid, "APPROVED", a["asset_sha256"],
                                 "e" * 64, 0, "n", "k2")
    # Correct digests -> approved; history recorded.
    ok = s["assets"].review_asset(s["job"], OWNER, aid, "APPROVED", a["asset_sha256"],
                                  a["rights_sha256"], 0, "looks right", "k3")
    assert ok["review_status"] == "APPROVED"
    hist = s["assets"].asset_review_history(s["job"], OWNER, aid)
    assert len(hist) == 1 and hist[0]["decision"] == "APPROVED"
    assert hist[0]["reviewed_asset_sha256"] == a["asset_sha256"]
    # Idempotent replay (same key) -> no second history row.
    s["assets"].review_asset(s["job"], OWNER, aid, "APPROVED", a["asset_sha256"],
                             a["rights_sha256"], 1, "dup", "k3")
    assert len(s["assets"].asset_review_history(s["job"], OWNER, aid)) == 1
    # A fresh decision with a stale expected_revision -> conflict.
    with pytest.raises(AssetRevisionConflict):
        s["assets"].review_asset(s["job"], OWNER, aid, "RESTRICTED", a["asset_sha256"],
                                 a["rights_sha256"], 0, "race", "k4")


def test_cannot_approve_blocked_rights(approved_plan):
    s = approved_plan
    _register(s, "reg-blk")
    aid = s["assets"].list_assets(s["job"], OWNER)["assets"][-1]["asset_id"]
    a = s["assets"].set_rights(s["job"], OWNER, aid, _rights(commercial_use="prohibited"), "r-blk")
    with pytest.raises(AssetValidationError):
        s["assets"].review_asset(s["job"], OWNER, aid, "APPROVED", a["asset_sha256"],
                                 a["rights_sha256"], 0, "no", "k-blk")


# ---------------------------------------------------------------------------
# 5. Readiness blockers
# ---------------------------------------------------------------------------

def test_readiness_reports_specific_blockers(approved_plan):
    s = approved_plan
    r0 = s["assets"].assignment_readiness(s["job"], OWNER, "ta")
    assert r0["ready"] is False
    assert any("no asset assigned" in b for b in r0["blockers"])
    # Assign + approve a CLEAR asset to every scene -> readiness improves per scene.
    aid = _clear_asset(s, "reg-ready")
    a = s["assets"].get_asset(s["job"], OWNER, aid)
    s["assets"].assign_asset(s["job"], OWNER, "ta", s["scene_ids"][0], aid, "assign-ready")
    s["assets"].review_asset(s["job"], OWNER, aid, "APPROVED", a["asset_sha256"],
                             a["rights_sha256"], 0, "ok", "rev-ready")
    r1 = s["assets"].assignment_readiness(s["job"], OWNER, "ta")
    # Scene 0 now has an approved CLEAR asset; later scenes still blocked.
    sc0 = next(x for x in r1["scenes"] if x["scene_id"] == s["scene_ids"][0])
    assert sc0["blockers"] == []
    assert r1["ready"] is False  # other scenes still unassigned


# ---------------------------------------------------------------------------
# 6. API surface (auth, codes) + migration safety
# ---------------------------------------------------------------------------

def test_api_asset_routes_auth_and_errors(tmp_path):
    app = create_app(tmp_path / "db.sqlite3", SECRET, tmp_path / "store")
    with TestClient(app) as client:
        assert client.get("/api/jobs/job-x/assets").status_code == 401
        headers = {"Authorization": f"Bearer {access_token('user-1')}"}
        assert client.get("/api/jobs/job-x/assets", headers=headers).status_code == 404
        job = client.post("/api/jobs", headers={**headers, "Idempotency-Key": "j1"},
                          json={"topic": "t", "language": "ta"}).json()
        # Register a reference-only asset over HTTP.
        reg = client.post(f"/api/jobs/{job['job_id']}/assets",
                          headers={**headers, "Idempotency-Key": "a1"},
                          json=_descriptor())
        assert reg.status_code == 201 and reg.json()["review_status"] == "DRAFT"
        aid = reg.json()["asset_id"]
        # Assign with no approved plan -> 409 (fail closed).
        asn = client.post(f"/api/jobs/{job['job_id']}/scenes/sc-0001/assets",
                          headers={**headers, "Idempotency-Key": "as1"},
                          json={"language": "ta", "asset_id": aid})
        assert asn.status_code == 409


def test_migration_v6_to_v7_additive_preserves_data_and_fails_closed():
    tmp = Path(tempfile.mkdtemp())
    dbp = tmp / "v6.sqlite3"
    D.initialize_database(dbp)
    conn = D.connect(dbp)
    conn.execute("INSERT INTO workflow_jobs(job_id,owner_id,idempotency_key,request_schema,request_hash,"
                 "topic,language,status,created_at,updated_at) VALUES"
                 "('j1','o1','k1','job.v1','h1','t','ta','DRAFT','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')")
    conn.commit()
    # Simulate a pre-v7 DB: drop the v7 tables, roll schema back to 6.
    for t in ("assets", "asset_rights", "asset_assignments", "asset_reviews", "asset_review_history"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("UPDATE schema_info SET version=6 WHERE singleton=1")
    conn.commit(); conn.close()

    D.initialize_database(dbp)  # must additively upgrade 6->7
    conn2 = D.connect(dbp)
    assert conn2.execute("SELECT version FROM schema_info").fetchone()[0] == 7
    assert conn2.execute("SELECT job_id FROM workflow_jobs").fetchone()[0] == "j1"  # preserved
    for t in ("assets", "asset_rights", "asset_assignments", "asset_reviews", "asset_review_history"):
        assert conn2.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
    # Unsupported version fails closed.
    conn2.execute("UPDATE schema_info SET version=99 WHERE singleton=1"); conn2.commit(); conn2.close()
    with pytest.raises(RuntimeError):
        D.initialize_database(dbp)
    import shutil; shutil.rmtree(tmp, ignore_errors=True)
