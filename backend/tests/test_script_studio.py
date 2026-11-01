"""Script Studio: chapter metadata, human edit/reorder, single-section regeneration, compare, lineage."""
import json

import pytest

from defence_studio.services.review import RevisionConflict
from defence_studio.services.script import ScriptStaleRevision, ScriptValidationError
from defence_studio.services.script_studio import ScriptStudioService, load_chapter_templates
from test_script_phase3 import GOOD_CONFIG, FakeProvider, _Result, _default_body, approved, OWNER  # noqa: F401


def two_section_body(claim_id):
    body = _default_body(claim_id)
    second = json.loads(json.dumps(body["sections"][0]))
    second.update(section_id="s2", heading="Second", narration="Officials said a second patrol was routine.")
    body["sections"].append(second)
    return body


@pytest.fixture
def studio(approved):
    s = approved
    s["provider"] = FakeProvider(body_factory=lambda _p: two_section_body(s["claim_id"]))
    service = ScriptStudioService(s["db"], s["root"], provider=s["provider"])
    base = service.generate(s["job"], OWNER, GOOD_CONFIG, "studio-gen")
    return s, service, base


def _sha(service, s, version, language="ta"):
    return next(r["revision_sha256"] for r in service.list_revisions(s["job"], OWNER, language)["revisions"]
                if r["version"] == version)


def test_chapter_metadata_reports_words_duration_evidence_uncertainty_and_template(studio):
    s, service, base = studio
    view = service.chapters(s["job"], OWNER, "ta", 1)
    assert [c["section_id"] for c in view["chapters"]] == [x["section_id"] for x in base["sections"]]
    first = view["chapters"][0]
    assert first["word_count"] > 0 and first["estimated_duration_seconds"] >= 0 and view["duration_is_estimate"]
    assert first["evidence"] and first["evidence"][0]["claim_id"] == s["claim_id"] and first["evidence"][0]["eligible"]
    codes = {w["code"] for w in first["uncertainty_warnings"]}
    assert "SINGLE_SOURCE" in codes                     # one approved source: must stay attributed
    assert first["template_check"]["status"] == "UNASSIGNED" and first["visual_type"] is None
    assert sum(c["word_count"] for c in view["chapters"]) <= view["total_words"]


def test_human_edit_creates_new_revision_and_keeps_parent_and_evidence(studio):
    s, service, base = studio
    edited = service.edit(s["job"], OWNER, "ta", 1, {
        "sections": [{"section_id": "s2", "narration": "Officials said the second patrol was routine.",
                      "visual_type": "MAP", "chapter_template": "OPERATIONS"}],
        "note": "tightened wording"}, "edit-1")
    assert edited["version"] == 2 and edited["status"] in ("AWAITING_REVIEW", "VALIDATION_FAILED")
    assert edited["approval"] is None                                        # never approved by editing
    lin = edited["lineage"]
    assert lin["kind"] == "HUMAN_EDIT" and lin["parent_version"] == 1 and lin["edited_by"] == OWNER
    assert {o["op"] for o in lin["operations"]} == {"edit_narration", "edit_visual_type", "edit_chapter_template"}
    assert lin["evidence_diff"]["sections"] == [] and lin["evidence_diff"]["valid_claims_removed"] == []
    s2 = next(x for x in edited["sections"] if x["section_id"] == "s2")
    assert s2["evidence"] == next(x for x in base["sections"] if x["section_id"] == "s2")["evidence"]
    assert service.get_revision(s["job"], OWNER, "ta", 1) == base                       # parent unchanged
    chapter = next(c for c in service.chapters(s["job"], OWNER, "ta", 2)["chapters"] if c["section_id"] == "s2")
    assert chapter["visual_type"] == "MAP" and chapter["template_check"]["template"] == "OPERATIONS"
    # idempotent replay; same key with a different edit is a conflict
    assert service.edit(s["job"], OWNER, "ta", 1, {
        "sections": [{"section_id": "s2", "narration": "Officials said the second patrol was routine.",
                      "visual_type": "MAP", "chapter_template": "OPERATIONS"}], "note": "tightened wording"},
        "edit-1") == edited
    with pytest.raises(RevisionConflict):
        service.edit(s["job"], OWNER, "ta", 1, {"title": "Other"}, "edit-1")


def test_reorder_is_a_permutation_and_compare_shows_it(studio):
    s, service, base = studio
    ids = [x["section_id"] for x in base["sections"]]
    moved = service.edit(s["job"], OWNER, "ta", 1, {"order": list(reversed(ids))}, "reorder-1")
    assert [x["section_id"] for x in moved["sections"]] == list(reversed(ids))
    diff = service.compare(s["job"], OWNER, "ta", 1, 2)
    assert diff["order_changed"] and diff["sections_changed"] == [] and diff["new_lineage"]["kind"] == "HUMAN_EDIT"
    for bad in ([ids[0]], ids + ["zz"], [ids[0], ids[0]]):
        with pytest.raises(ScriptValidationError):
            service.edit(s["job"], OWNER, "ta", 1, {"order": bad}, f"bad-{len(bad)}")


def test_invalid_edits_are_rejected(studio):
    s, service, _ = studio
    for edit in ({"sections": [{"section_id": "nope", "narration": "x"}]},
                 {"sections": [{"section_id": "s2", "visual_type": "HOLOGRAM"}]},
                 {"sections": [{"section_id": "s2", "chapter_template": "NOT_A_TEMPLATE"}]},
                 {"sections": [{"section_id": "s2", "claim_ids": ["c"]}]},          # evidence not editable here
                 {}):
        with pytest.raises(ScriptValidationError):
            service.edit(s["job"], OWNER, "ta", 1, edit, f"bad-{json.dumps(edit)[:40]}")


def test_edit_adding_unsupported_number_fails_validation_not_silently(studio):
    s, service, _ = studio
    out = service.edit(s["job"], OWNER, "ta", 1, {"sections": [
        {"section_id": "s2", "narration": "Officials said 4096 patrols were routine."}]}, "num-1")
    assert out["status"] == "VALIDATION_FAILED" and not out["validation"]["passed"]


def test_regenerate_one_section_preserves_others_and_flags_dropped_evidence(studio):
    s, service, base = studio
    dropped = json.loads(json.dumps(base["sections"][1]))
    dropped.update(narration="Officials described the patrol.", heading="Second (regenerated)")
    dropped["evidence"] = {**dropped["evidence"], "claim_ids": []}
    dropped["requires_evidence"] = False
    service._provider = FakeProvider(body_factory=lambda _p: {"section": dropped})
    out = service.regenerate_section(s["job"], OWNER, "ta", 1, "s2", "regen-1")
    assert out["sections"][0] == base["sections"][0]                              # other section byte-identical
    assert out["sections"][1]["heading"] == "Second (regenerated)"
    assert out["lineage"]["kind"] == "SECTION_REGENERATION"
    assert out["lineage"]["evidence_diff"]["valid_claims_removed"] == [s["claim_id"]]
    assert any(i["code"] == "EVIDENCE_REFERENCE_REMOVED" and i["section_id"] == "s2"
               for i in out["validation"]["issues"])                               # visible, not silent
    assert out["usage"]["measured_text_cost_inr"] == 0.05 and out["approval"] is None
    diff = service.compare(s["job"], OWNER, "ta", 1, 2)
    assert diff["sections_changed"] == ["s2"]
    assert diff["section_evidence_changes"] == [{"section_id": "s2", "claims_added": [],
                                                 "claims_removed": [s["claim_id"]]}]


def test_regeneration_and_edit_refuse_stale_evidence(studio):
    s, service, _ = studio
    claim = s["evidence"].draft(s["job"], OWNER)["package"]["claims"][0]
    s["evidence"].decide_claim(s["job"], OWNER, claim["claim_id"], "NEEDS_MORE_EVIDENCE",
                               claim["review"]["reviewed_artifact_sha256"], 1, "changed", "stale-1")
    calls = s["provider"].calls
    with pytest.raises(ScriptStaleRevision):
        service.regenerate_section(s["job"], OWNER, "ta", 1, "s2", "regen-stale")
    with pytest.raises(ScriptStaleRevision):
        service.edit(s["job"], OWNER, "ta", 1, {"title": "New"}, "edit-stale")
    assert s["provider"].calls == calls                                             # no paid call on stale evidence


def test_edited_revision_still_needs_human_approval_and_then_exports(studio):
    s, service, _ = studio
    edited = service.edit(s["job"], OWNER, "ta", 1, {"title": "Rafale readiness, edited"}, "edit-ok")
    assert edited["status"] == "AWAITING_REVIEW"
    with pytest.raises(ScriptStaleRevision):
        service.export_approved(s["job"], OWNER, "ta")
    service.approve(s["job"], OWNER, "ta", 2, _sha(service, s, 2), "Approved after edit", "approve-edit")
    exported = service.export_approved(s["job"], OWNER, "ta")
    assert exported["version"] == 2 and exported["lineage"]["kind"] == "HUMAN_EDIT"
    assert exported["approval"]["approval_kind"] == "HUMAN"
    history = service.list_revisions(s["job"], OWNER, "ta")["revisions"]
    assert [r["version"] for r in history] == [1, 2]


def test_templates_config_loads_and_api_routes_work(studio, monkeypatch):
    from fastapi.testclient import TestClient
    from defence_studio.api.app import create_app
    from conftest import SECRET, access_token
    s, service, base = studio
    assert {t["id"] for t in load_chapter_templates()["templates"]} >= {"HOOK", "CLOSING"}
    headers = {"Authorization": "Bearer " + access_token(OWNER)}
    with TestClient(create_app(s["db"], SECRET, s["root"])) as client:
        root = f"/api/jobs/{s['job']}/script/revisions/ta/1"
        assert client.get("/api/script/chapter-templates", headers=headers).status_code == 200
        chapters = client.get(root + "/chapters", headers=headers)
        assert chapters.status_code == 200 and len(chapters.json()["chapters"]) == len(base["sections"])
        r = client.post(root + "/edit", headers={**headers, "Idempotency-Key": "api-edit"},
                        json={"sections": [{"section_id": "s2", "heading": "Renamed"}]})
        assert r.status_code == 201, r.text
        assert client.post(root + "/edit", headers={**headers, "Idempotency-Key": "api-bad"},
                           json={"order": ["x"]}).status_code == 422
        other = {"Authorization": "Bearer " + access_token("someone-else")}
        assert client.get(root + "/chapters", headers=other).status_code == 404
