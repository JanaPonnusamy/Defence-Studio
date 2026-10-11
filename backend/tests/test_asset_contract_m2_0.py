"""Phase 5 M2.0 asset-contract tests (contract only -- no network, no DB, no service).

Covers: valid asset/rights/assignment shapes; missing/malformed hashes; the
declared-vs-fabricated metadata rule; the deterministic fail-closed rights gate
over unknown / restricted / expired / non-commercial / missing-provenance cases;
required-attribution contradiction; duplicate permitted-uses; assignment binding
to the exact approved scene-plan hash/version/scene_id; deterministic digests;
and strict rejection of unexpected fields.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from defence_studio.contracts.assets import (
    ASSET_ASSIGNMENT_SCHEMA, ASSET_DESCRIPTOR_SCHEMA, RIGHTS_RECORD_SCHEMA,
    AssetAssignmentV1, AssetContentV1, AssetDescriptorV1, AssetKind,
    AssetProvenanceV1, AssetReviewStatus, CommercialUse, ContentStatus,
    DeclaredText, EffectiveRights, MetadataFactStatus, PermittedUse,
    RightsBasis, RightsRecordV1, asset_digest, assignment_digest,
    evaluate_effective_rights, rights_digest,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_PLAN = "c" * 64


def _rights(**over) -> RightsRecordV1:
    base = dict(
        schema_version=RIGHTS_RECORD_SCHEMA,
        rights_basis=RightsBasis.CREATOR_OWNED,
        attribution_required=True,
        permitted_uses=[PermittedUse.BROADCAST, PermittedUse.WEB],
        commercial_use=CommercialUse.ALLOWED,
    )
    base.update(over)
    return RightsRecordV1(**base)


def _provenance(**over) -> AssetProvenanceV1:
    base = dict(source_url="https://example.gov/press/photo-1")
    base.update(over)
    return AssetProvenanceV1(**base)


def _descriptor(**over) -> AssetDescriptorV1:
    base = dict(
        schema_version=ASSET_DESCRIPTOR_SCHEMA,
        job_id="job-1",
        asset_id=HASH_A,
        kind=AssetKind.IMAGE,
        title="Official press photo",
        provenance=_provenance(),
    )
    base.update(over)
    return AssetDescriptorV1(**base)


def _assignment(**over) -> AssetAssignmentV1:
    base = dict(
        schema_version=ASSET_ASSIGNMENT_SCHEMA,
        job_id="job-1",
        language="ta",
        version=1,
        scene_plan_sha256=HASH_PLAN,
        scene_plan_version=3,
        scene_id="sc-0002",
        asset_id=HASH_A,
        effective_rights=EffectiveRights.CLEAR,
        created_at="2026-10-11T00:00:00+00:00",
    )
    base.update(over)
    return AssetAssignmentV1(**base)


# -- 1. valid shapes -------------------------------------------------------


def test_valid_asset_descriptor_defaults_to_draft_and_referenced_only():
    d = _descriptor()
    assert d.schema_version == "defence-studio.asset-descriptor.v1"
    assert d.review_status == AssetReviewStatus.DRAFT        # ingestion != approval
    assert d.content.status == ContentStatus.REFERENCED_ONLY  # nothing downloaded
    assert d.content.content_sha256 is None


def test_valid_rights_record_and_assignment():
    r = _rights()
    assert r.schema_version == "defence-studio.asset-rights.v1"
    a = _assignment()
    assert a.scene_plan_sha256 == HASH_PLAN and a.scene_plan_version == 3
    assert a.scene_id == "sc-0002" and a.asset_id == HASH_A


# -- 2. missing / malformed hashes ----------------------------------------


def test_malformed_asset_id_rejected():
    with pytest.raises(ValidationError):
        _descriptor(asset_id="not-a-hash")
    with pytest.raises(ValidationError):
        _descriptor(asset_id="A" * 64)  # uppercase not allowed by the pattern


def test_measured_content_requires_hash_and_size_else_rejected():
    # MEASURED without a hash is rejected.
    with pytest.raises(ValidationError):
        AssetContentV1(status=ContentStatus.MEASURED)
    # A hash on a referenced-only asset (no real bytes) is rejected -- no fabricated digest.
    with pytest.raises(ValidationError):
        AssetContentV1(status=ContentStatus.REFERENCED_ONLY, content_sha256=HASH_A)
    # A valid measured content needs both hash and verified size.
    ok = AssetContentV1(status=ContentStatus.MEASURED, content_sha256=HASH_A,
                        byte_size=1234, media_type="image/jpeg")
    assert ok.content_sha256 == HASH_A and ok.byte_size == 1234


def test_malformed_content_hash_rejected():
    with pytest.raises(ValidationError):
        AssetContentV1(status=ContentStatus.MEASURED, content_sha256="xyz", byte_size=1)


# -- 3. declared-vs-fabricated metadata -----------------------------------


def test_declared_text_cannot_fabricate_or_blank():
    # A COLLECTED field must carry a value.
    with pytest.raises(ValidationError):
        DeclaredText(status=MetadataFactStatus.COLLECTED, value=None)
    with pytest.raises(ValidationError):
        DeclaredText(status=MetadataFactStatus.COLLECTED, value="   ")
    # A non-collected field cannot smuggle in a value.
    with pytest.raises(ValidationError):
        DeclaredText(status=MetadataFactStatus.UNAVAILABLE, value="Some creator")
    ok = DeclaredText(status=MetadataFactStatus.COLLECTED, value="Reuters")
    assert ok.value == "Reuters"
    assert DeclaredText().status == MetadataFactStatus.NOT_COLLECTED  # default


# -- 4. deterministic fail-closed rights gate ------------------------------


def test_rights_gate_unknown_states_are_unverified():
    # No rights record at all.
    assert evaluate_effective_rights(None, _provenance()) == EffectiveRights.UNVERIFIED
    # Unknown basis.
    assert evaluate_effective_rights(_rights(rights_basis=RightsBasis.UNKNOWN),
                                     _provenance()) == EffectiveRights.UNVERIFIED
    # Unknown commercial use.
    assert evaluate_effective_rights(_rights(commercial_use=CommercialUse.UNKNOWN),
                                     _provenance()) == EffectiveRights.UNVERIFIED
    # Missing provenance (no url, no documented origin) -> never usable.
    assert evaluate_effective_rights(_rights(), AssetProvenanceV1()) == EffectiveRights.UNVERIFIED


def test_rights_gate_non_commercial_and_prohibited_are_blocked():
    assert evaluate_effective_rights(_rights(rights_basis=RightsBasis.CC_BY_NC),
                                     _provenance(), for_commercial=True) == EffectiveRights.BLOCKED
    assert evaluate_effective_rights(_rights(commercial_use=CommercialUse.PROHIBITED),
                                     _provenance(), for_commercial=True) == EffectiveRights.BLOCKED
    # A non-commercial render may use a CC-BY-NC asset (not blocked for that use).
    assert evaluate_effective_rights(_rights(rights_basis=RightsBasis.CC_BY_NC),
                                     _provenance(), for_commercial=False) == EffectiveRights.CLEAR


def test_rights_gate_target_use_outside_permitted_is_restricted():
    r = _rights(permitted_uses=[PermittedUse.WEB])  # not broadcast
    assert evaluate_effective_rights(r, _provenance(), target_use=PermittedUse.BROADCAST) \
        == EffectiveRights.RESTRICTED
    assert evaluate_effective_rights(r, _provenance(), target_use=PermittedUse.WEB) \
        == EffectiveRights.CLEAR


def test_rights_gate_expiry_in_past_is_expired_and_malformed_fails_closed():
    r = _rights(expires_at="2020-01-01T00:00:00+00:00")
    assert evaluate_effective_rights(r, _provenance(), now_iso="2026-10-11T00:00:00+00:00") \
        == EffectiveRights.EXPIRED
    # Future expiry is fine.
    r2 = _rights(expires_at="2099-01-01T00:00:00+00:00")
    assert evaluate_effective_rights(r2, _provenance(), now_iso="2026-10-11T00:00:00+00:00") \
        == EffectiveRights.CLEAR
    # Malformed expiry fails closed (treated as expired).
    r3 = _rights(expires_at="not-a-date")
    assert evaluate_effective_rights(r3, _provenance(), now_iso="2026-10-11T00:00:00+00:00") \
        == EffectiveRights.EXPIRED


def test_rights_gate_clear_only_for_fully_specified_commercial_asset():
    assert evaluate_effective_rights(_rights(), _provenance()) == EffectiveRights.CLEAR


# -- 5. attribution / duplicate / integrity --------------------------------


def test_attribution_text_without_requirement_is_rejected():
    with pytest.raises(ValidationError):
        _rights(attribution_required=False, attribution_text="Photo: Reuters")


def test_duplicate_permitted_uses_rejected():
    with pytest.raises(ValidationError):
        _rights(permitted_uses=[PermittedUse.WEB, PermittedUse.WEB])


def test_empty_or_oversized_restriction_rejected():
    with pytest.raises(ValidationError):
        _rights(restrictions=["   "])
    with pytest.raises(ValidationError):
        _rights(restrictions=["x" * 501])


def test_approved_asset_requires_rights_record():
    # Ingestion is never approval: cannot be APPROVED with no rights to approve.
    with pytest.raises(ValidationError):
        _descriptor(review_status=AssetReviewStatus.APPROVED, rights=None)
    # With a rights record it is allowed.
    ok = _descriptor(review_status=AssetReviewStatus.APPROVED, rights=_rights())
    assert ok.review_status == AssetReviewStatus.APPROVED


# -- 6. assignment binding + invalid scene references ----------------------


def test_assignment_requires_wellformed_plan_binding():
    with pytest.raises(ValidationError):
        _assignment(scene_plan_sha256="short")
    with pytest.raises(ValidationError):
        _assignment(scene_plan_version=0)
    with pytest.raises(ValidationError):
        _assignment(scene_id="")
    with pytest.raises(ValidationError):
        _assignment(asset_id="not-a-hash")
    with pytest.raises(ValidationError):
        _assignment(language="fr")  # only ta/en


def test_assignment_carries_exact_approved_plan_identity():
    a = _assignment(scene_plan_sha256=HASH_PLAN, scene_plan_version=7, scene_id="sc-0005")
    assert a.scene_plan_sha256 == HASH_PLAN
    assert a.scene_plan_version == 7
    assert a.scene_id == "sc-0005"
    # target_use is optional.
    assert _assignment(target_use=PermittedUse.BROADCAST).target_use == PermittedUse.BROADCAST
    assert _assignment().target_use is None


# -- 7. deterministic digests ----------------------------------------------


def test_digests_are_deterministic_and_order_independent():
    r1 = _rights().model_dump(mode="json")
    r2 = _rights().model_dump(mode="json")
    assert rights_digest(r1) == rights_digest(r2)
    # Key order in the input dict must not change the digest (canonical JSON).
    reordered = {k: r1[k] for k in reversed(list(r1.keys()))}
    assert rights_digest(reordered) == rights_digest(r1)

    d = _descriptor().model_dump(mode="json")
    assert asset_digest(d) == asset_digest(dict(reversed(list(d.items()))))
    a = _assignment().model_dump(mode="json")
    assert assignment_digest(a) == assignment_digest(dict(reversed(list(a.items()))))

    # A changed field changes the digest.
    changed = {**r1, "commercial_use": CommercialUse.PROHIBITED.value}
    assert rights_digest(changed) != rights_digest(r1)


# -- 8. strict rejection of unexpected fields ------------------------------


def test_unexpected_fields_rejected_everywhere():
    with pytest.raises(ValidationError):
        _descriptor(unexpected="x")
    with pytest.raises(ValidationError):
        _rights(unexpected="x")
    with pytest.raises(ValidationError):
        _assignment(unexpected="x")
    with pytest.raises(ValidationError):
        AssetContentV1(status=ContentStatus.REFERENCED_ONLY, unexpected="x")
    with pytest.raises(ValidationError):
        AssetProvenanceV1(source_url="https://x.test", unexpected="x")


def test_wrong_schema_version_literal_rejected():
    with pytest.raises(ValidationError):
        _descriptor(schema_version="defence-studio.asset-descriptor.v2")
    with pytest.raises(ValidationError):
        _rights(schema_version="wrong")
    with pytest.raises(ValidationError):
        _assignment(schema_version="wrong")
