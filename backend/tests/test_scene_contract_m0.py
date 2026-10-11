"""Phase 4 M0 scene-plan contract tests.

Contract-only, per the Phase 4 foundation plan's M0 acceptance criteria:
no network, no DB write beyond these unit tests. A scene plan is built by
deriving it from a realistically-shaped approved Phase 3 script export
(``ScriptRevisionV1``), then the contract's validation rules are exercised:
serialization round-trip, required fields, invalid values, deterministic
representation, the planned-vs-measured timing honesty rule, downstream
reference slots, and the rejection cases the plan names explicitly (unknown
field, a scene citing a claim absent from the approved package, and an
out-of-order / duplicate ``scene_id``).
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from defence_studio.contracts.evidence import digest
from defence_studio.contracts.script import ScriptRevisionV1
from defence_studio.contracts.scene import (
    SCENE_PLANNER_VERSION,
    SCENE_PLAN_REQUEST_SCHEMA,
    SCENE_PLAN_SCHEMA,
    PlannedTimingV1,
    SceneV1,
    ScenePlanRequestV1,
    ScenePlanV1,
    count_words,
    plan_digest,
    plan_scene_timing,
    validate_request,
    validate_scene_plan,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_ALIGN = "c" * 64


# ---------------------------------------------------------------------------
# A realistically-shaped approved Phase 3 export, used as the derivation source.
# ---------------------------------------------------------------------------


def _approved_export() -> dict:
    """A valid, APPROVED ``ScriptRevisionV1`` body — the real Phase 4 input.

    Validated through the actual Phase 3 contract so the scene plan below is
    genuinely derived from the shape Phase 3 exports, not an invented one.
    """
    body = {
        "schema_version": "defence-studio.script-revision.v1",
        "job_id": "job-001",
        "language": "en",
        "script_type": "narration",
        "version": 2,
        "package_sha256": HASH_A,
        "request_sha256": HASH_B,
        "title": "Rafale readiness update",
        "opening_hook": "Here is what officials have said.",
        "sections": [
            {
                "section_id": "s1",
                "heading": "Status",
                "narration": "The unit has twelve aircraft as of 2026.",
                "requires_evidence": True,
                "evidence": {"claim_ids": ["claim-1"], "source_ids": ["src-001"],
                             "qualification": "Attributed source statement"},
                "presentation_note": "",
            },
            {
                "section_id": "s2",
                "heading": "Context",
                "narration": "Officials described the squadron as operational.",
                "requires_evidence": True,
                "evidence": {"claim_ids": ["claim-2"], "source_ids": ["src-001"],
                             "qualification": "Attributed"},
                "presentation_note": "",
            },
        ],
        "closing": "That is the latest update.",
        "config": {
            "language": "en", "audience": "general", "tone": "measured",
            "script_format": "narration", "target_duration_seconds": 120,
            "target_words": 300, "model": "gpt-x",
            "prompt_version": "defence-studio.source-grounded-script.v1",
            "max_text_cost_inr": 10.0, "terminology": {},
        },
        "usage": {
            "model": "gpt-x", "provider": "openai", "input_tokens": 10,
            "output_tokens": 20, "tokens_available": True, "retries": 0,
            "measured_text_cost_inr": 1.0, "cost_is_measured": True,
            "pricing_source": "defence-studio.openai-pricing.manual.v1", "currency": "INR",
        },
        "word_count": {
            "words": 14, "words_per_minute": 150, "estimated_duration_seconds": 6,
            "method": "words-per-minute.v1", "is_estimate": True,
        },
        "validation": {
            "passed": True, "validator_version": "defence-studio.script-validator.v1",
            "package_sha256": HASH_A, "checked_at": "2026-10-11T00:00:00+00:00", "issues": [],
        },
        "status": "APPROVED",
        "created_at": "2026-10-11T00:00:00+00:00",
        "eligible_claim_ids": ["claim-1", "claim-2"],
        "approval": {
            "revision_sha256": HASH_B, "version": 2, "reviewer_id": "owner-1",
            "note": "Approved for production", "approved_at": "2026-10-11T00:00:00+00:00",
        },
    }
    # Prove it really is a valid approved Phase 3 export before deriving from it.
    return ScriptRevisionV1.model_validate(body).model_dump()


def _scene_plan_from(export: dict, **overrides) -> dict:
    """Derive an initial scene plan (one scene per narration section) from an
    approved export — the M1 default, built here by hand for a contract test."""
    scenes = []
    for index, section in enumerate(export["sections"]):
        narration = section["narration"]
        scenes.append({
            "scene_id": f"sc-{index + 1}",
            "order": index + 1,
            "source_section_id": section["section_id"],
            "narration": narration,
            "claim_ids": list(section["evidence"]["claim_ids"]),
            "purpose": section.get("heading", ""),
            "planned_timing": plan_scene_timing(narration),
        })
    plan = {
        "schema_version": SCENE_PLAN_SCHEMA,
        "job_id": export["job_id"],
        "language": export["language"],
        "version": 1,
        "planner_version": SCENE_PLANNER_VERSION,
        "script_revision_sha256": export["approval"]["revision_sha256"],
        "script_version": export["version"],
        "package_sha256": export["package_sha256"],
        "scenes": scenes,
        "eligible_claim_ids": list(export["eligible_claim_ids"]),
        "status": "DRAFT",
        "created_at": "2026-10-11T00:00:00+00:00",
    }
    plan.update(overrides)
    return plan


def _request() -> dict:
    return {
        "schema_version": SCENE_PLAN_REQUEST_SCHEMA,
        "job_id": "job-001",
        "language": "en",
        "script_revision_sha256": HASH_B,
        "script_version": 2,
        "package_sha256": HASH_A,
        "config": {"language": "en", "target_total_seconds": 120},
    }


# ---------------------------------------------------------------------------
# 1. Derivation from a real approved export + happy-path validation
# ---------------------------------------------------------------------------


def test_scene_plan_derived_from_real_approved_export_validates():
    export = _approved_export()
    plan = _scene_plan_from(export)
    validated = validate_scene_plan(plan)
    assert validated["schema_version"] == SCENE_PLAN_SCHEMA
    assert [s["order"] for s in validated["scenes"]] == [1, 2]
    # Narration is the verbatim approved text, referenced by its source section.
    assert validated["scenes"][0]["narration"] == export["sections"][0]["narration"]
    assert validated["scenes"][0]["source_section_id"] == "s1"
    # Every scene's claims stay within the approved evidence.
    allowed = set(export["eligible_claim_ids"])
    for scene in validated["scenes"]:
        assert set(scene["claim_ids"]) <= allowed
    # The plan is bound to the exact approved script revision + package.
    assert validated["script_revision_sha256"] == export["approval"]["revision_sha256"]
    assert validated["package_sha256"] == export["package_sha256"]


def test_request_validates_and_rejects_language_mismatch():
    assert validate_request(_request())["schema_version"] == SCENE_PLAN_REQUEST_SCHEMA
    bad = _request()
    bad["language"] = "ta"  # config still says "en"
    with pytest.raises(ValidationError):
        ScenePlanRequestV1.model_validate(bad)


# ---------------------------------------------------------------------------
# 2. Serialization round-trip + deterministic representation
# ---------------------------------------------------------------------------


def test_serialization_round_trips_and_is_deterministic():
    plan = _scene_plan_from(_approved_export())
    once = validate_scene_plan(plan)
    twice = validate_scene_plan(once)  # re-validating the canonical dict is stable
    assert once == twice
    # Content hash is order-independent of input key order but identity-stable.
    assert plan_digest(once) == plan_digest(twice)
    reordered_keys = {k: once[k] for k in sorted(once)}
    assert plan_digest(reordered_keys) == plan_digest(once)
    # A genuine content change changes the digest.
    changed = copy.deepcopy(once)
    changed["scenes"][0]["purpose"] = "different"
    assert plan_digest(changed) != plan_digest(once)


# ---------------------------------------------------------------------------
# 3. The three rejection cases the M0 acceptance criteria name explicitly
# ---------------------------------------------------------------------------


def test_unknown_field_is_rejected():
    plan = _scene_plan_from(_approved_export())
    plan["unexpected"] = "nope"
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(plan)
    # also at the scene level
    scene_plan = _scene_plan_from(_approved_export())
    scene_plan["scenes"][0]["extra"] = 1
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(scene_plan)


def test_scene_citing_claim_absent_from_approved_package_is_rejected():
    plan = _scene_plan_from(_approved_export())
    plan["scenes"][0]["claim_ids"] = ["claim-not-in-package"]
    with pytest.raises(ValidationError, match="eligible_claim_ids"):
        ScenePlanV1.model_validate(plan)


def test_out_of_order_scene_id_is_rejected():
    plan = _scene_plan_from(_approved_export())
    plan["scenes"][0]["order"] = 2
    plan["scenes"][1]["order"] = 1  # orders no longer match positions
    with pytest.raises(ValidationError, match="ordered"):
        ScenePlanV1.model_validate(plan)


def test_duplicate_scene_id_is_rejected():
    plan = _scene_plan_from(_approved_export())
    plan["scenes"][1]["scene_id"] = plan["scenes"][0]["scene_id"]
    with pytest.raises(ValidationError, match="Duplicate scene_id"):
        ScenePlanV1.model_validate(plan)


# ---------------------------------------------------------------------------
# 4. Required fields and invalid values
# ---------------------------------------------------------------------------


def test_required_fields_missing_are_rejected():
    for missing in ("schema_version", "job_id", "scenes", "status",
                    "script_revision_sha256", "package_sha256", "planner_version"):
        plan = _scene_plan_from(_approved_export())
        plan.pop(missing)
        with pytest.raises(ValidationError):
            ScenePlanV1.model_validate(plan)


def test_empty_scene_list_is_rejected():
    plan = _scene_plan_from(_approved_export())
    plan["scenes"] = []
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(plan)


def test_invalid_values_are_rejected():
    base = _scene_plan_from(_approved_export())
    # malformed upstream hash
    bad_hash = copy.deepcopy(base); bad_hash["package_sha256"] = "xyz"
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(bad_hash)
    # unknown status
    bad_status = copy.deepcopy(base); bad_status["status"] = "PUBLISHED"
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(bad_status)
    # wrong planner_version literal
    bad_planner = copy.deepcopy(base); bad_planner["planner_version"] = "other.v1"
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(bad_planner)
    # unsupported language
    bad_lang = copy.deepcopy(base); bad_lang["language"] = "fr"
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(bad_lang)
    # empty narration (whitespace only)
    bad_narr = copy.deepcopy(base); bad_narr["scenes"][0]["narration"] = "   "
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(bad_narr)
    # duplicate claim ids within a scene
    dup_claims = copy.deepcopy(base); dup_claims["scenes"][0]["claim_ids"] = ["claim-1", "claim-1"]
    with pytest.raises(ValidationError, match="duplicates"):
        ScenePlanV1.model_validate(dup_claims)
    # out-of-range voice direction / crop
    bad_rate = copy.deepcopy(base); bad_rate["scenes"][0]["voice_direction"] = {"rate": 5.0}
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(bad_rate)
    bad_crop = copy.deepcopy(base); bad_crop["scenes"][0]["crop"] = {"aspect_ratio": "wide"}
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(bad_crop)


# ---------------------------------------------------------------------------
# 5. Planned vs measured timing honesty rule + downstream reference slots
# ---------------------------------------------------------------------------


def test_planned_timing_is_always_an_estimate():
    timing = plan_scene_timing("one two three four five", words_per_minute=150)
    parsed = PlannedTimingV1.model_validate(timing)
    assert parsed.is_estimate is True
    assert parsed.words == 5
    assert parsed.method == "words-per-minute.v1"
    assert count_words("one two three four five") == 5
    # is_estimate can never be set to False (pinned literal).
    with pytest.raises(ValidationError):
        PlannedTimingV1.model_validate({**timing, "is_estimate": False})


def test_measured_timing_requires_a_referenced_alignment_artifact():
    plan = _scene_plan_from(_approved_export())
    # measured timing without any alignment_ref on the scene -> rejected at plan level.
    plan["scenes"][0]["measured_timing"] = {
        "start_seconds": 0.0, "duration_seconds": 3.2,
        "alignment_sha256": HASH_ALIGN, "is_synthetic": False,
    }
    with pytest.raises(ValidationError, match="alignment_ref"):
        ScenePlanV1.model_validate(plan)


def test_measured_timing_hash_must_match_the_alignment_reference():
    plan = _scene_plan_from(_approved_export())
    align_ref = {"artifact_type": "alignment", "uri": "jobs/job-001/align/sc-1.json",
                 "sha256": HASH_ALIGN, "schema_version": "defence-align.output.v1"}
    plan["scenes"][0]["alignment_ref"] = align_ref
    plan["scenes"][0]["measured_timing"] = {
        "start_seconds": 0.0, "duration_seconds": 3.2,
        "alignment_sha256": "d" * 64,  # mismatched
        "is_synthetic": False,
    }
    with pytest.raises(ValidationError, match="must match alignment_ref"):
        SceneV1.model_validate(plan["scenes"][0])
    # matching hash is accepted, and is_synthetic is carried through faithfully.
    plan["scenes"][0]["measured_timing"]["alignment_sha256"] = HASH_ALIGN
    plan["scenes"][0]["measured_timing"]["is_synthetic"] = True
    ok = validate_scene_plan(plan)
    assert ok["scenes"][0]["measured_timing"]["is_synthetic"] is True


def test_downstream_reference_slots_default_absent_and_carry_no_internals():
    plan = validate_scene_plan(_scene_plan_from(_approved_export()))
    scene = plan["scenes"][0]
    # In M0 nothing downstream has run.
    assert scene["measured_timing"] is None
    assert scene["audio_ref"] is None
    assert scene["alignment_ref"] is None
    assert scene["render_ref"] is None
    # An audio reference, when present, is a hash-identified pointer only.
    plan["scenes"][0]["audio_ref"] = {
        "artifact_type": "tts-manifest", "uri": "jobs/job-001/tts/sc-1.json",
        "sha256": HASH_ALIGN, "schema_version": "defence-studio.tts-manifest.v1",
    }
    revalidated = validate_scene_plan(plan)
    assert set(revalidated["scenes"][0]["audio_ref"]) == {
        "artifact_type", "uri", "sha256", "schema_version"
    }


# ---------------------------------------------------------------------------
# 6. Contract version identification
# ---------------------------------------------------------------------------


def test_contract_version_identifiers_are_stable_and_required():
    assert SCENE_PLAN_SCHEMA == "defence-studio.scene-plan.v1"
    assert SCENE_PLAN_REQUEST_SCHEMA == "defence-studio.scene-plan-request.v1"
    assert SCENE_PLANNER_VERSION == "defence-studio.scene-plan.v1"
    # The schema_version is a pinned literal: a different version string is rejected.
    plan = _scene_plan_from(_approved_export())
    plan["schema_version"] = "defence-studio.scene-plan.v2"
    with pytest.raises(ValidationError):
        ScenePlanV1.model_validate(plan)
    req = _request()
    req["schema_version"] = "defence-studio.scene-plan-request.v2"
    with pytest.raises(ValidationError):
        ScenePlanRequestV1.model_validate(req)
