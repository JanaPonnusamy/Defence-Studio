"""Phase 2: generation truthfulness, explicit cost status, provenance/rights fields, timing labels.

Data migration (truthfulness of rows written by v1.0):
  * images from the old "local-procedural" provider are PLACEHOLDERS, not AI:
    asset_type PLACEHOLDER_IMAGE, source_type SYSTEM_GENERATED, editorial PLACEHOLDER,
    generation_kind PLACEHOLDER, provider renamed placeholder-procedural, and the
    invented synthetic cost is removed (cost_status NOT_APPLICABLE, amount NULL)
  * mock rows -> generation_kind TEST_FIXTURE, cost NOT_APPLICABLE
  * other AI_GENERATED assets (imports declared as AI) -> generation_kind AI_GENERATED
  * shots / stored timeline JSON referencing reclassified assets are relabelled
  * scene/shot timing_source PROVIDED -> SUPPLIED

Revision ID: 0002_phase2
Revises: 0001_initial
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase2"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as b:
        b.add_column(sa.Column("generation_kind", sa.String(length=24), server_default="NOT_GENERATED", nullable=False))
        b.add_column(sa.Column("cost_status", sa.String(length=24), nullable=True))
        b.add_column(sa.Column("is_fallback", sa.Boolean(), server_default="0", nullable=False))
        b.add_column(sa.Column("fallback_from", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("fallback_reason", sa.Text(), nullable=True))
        b.add_column(sa.Column("provider_request_id", sa.String(length=128), nullable=True))
        b.add_column(sa.Column("source_identity", sa.String(length=255), nullable=True))
        b.add_column(sa.Column("licence_url", sa.Text(), nullable=True))
        b.add_column(sa.Column("usage_rights", sa.Text(), nullable=True))
        b.add_column(sa.Column("attribution_required", sa.Boolean(), server_default="0", nullable=False))
        b.add_column(sa.Column("source_retrieved_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("perceptual_hash", sa.String(length=16), nullable=True))
        b.add_column(sa.Column("duplicate_of", sa.String(length=36), nullable=True))
        b.create_index(b.f("ix_assets_perceptual_hash"), ["perceptual_hash"], unique=False)

    with op.batch_alter_table("generation_records", schema=None) as b:
        b.alter_column("estimated_cost", existing_type=sa.Float(), nullable=True)
        b.add_column(sa.Column("generation_kind", sa.String(length=24), server_default="NOT_GENERATED", nullable=False))
        b.add_column(sa.Column("provider_state", sa.String(length=24), nullable=True))
        b.add_column(sa.Column("cost_status", sa.String(length=24), nullable=True))
        b.add_column(sa.Column("pre_call_estimate_usd", sa.Float(), nullable=True))
        b.add_column(sa.Column("provider_request_id", sa.String(length=128), nullable=True))
        b.add_column(sa.Column("is_fallback", sa.Boolean(), server_default="0", nullable=False))
        b.add_column(sa.Column("fallback_from", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("fallback_reason", sa.Text(), nullable=True))

    with op.batch_alter_table("cost_records", schema=None) as b:
        b.alter_column("estimated_cost", existing_type=sa.Float(), nullable=True)
        b.add_column(sa.Column("generation_kind", sa.String(length=24), server_default="NOT_GENERATED", nullable=False))
        b.add_column(sa.Column("cost_status", sa.String(length=24), server_default="UNKNOWN", nullable=False))
        b.add_column(sa.Column("cost_basis", sa.Text(), nullable=True))
        b.add_column(sa.Column("usage", sa.JSON(), nullable=True))
        b.add_column(sa.Column("pre_call_estimate_usd", sa.Float(), nullable=True))
        b.add_column(sa.Column("provider_request_id", sa.String(length=128), nullable=True))
        b.add_column(sa.Column("is_fallback", sa.Boolean(), server_default="0", nullable=False))

    _migrate_data()


def _migrate_data() -> None:
    conn = op.get_bind()
    ex = lambda sql, **kw: conn.execute(sa.text(sql), kw)  # noqa: E731

    # ---- placeholder reclassification (old local-procedural provider)
    placeholder_ids = [r[0] for r in ex("SELECT asset_id FROM assets WHERE provider = 'local-procedural'")]
    ex("UPDATE assets SET generation_kind='PLACEHOLDER', asset_type='PLACEHOLDER_IMAGE', source_type='SYSTEM_GENERATED', "
       "editorial_category='PLACEHOLDER', provider='placeholder-procedural', generation_cost=NULL, "
       "cost_status='NOT_APPLICABLE', licence='Module-generated placeholder (no third-party rights)', "
       "attribution='PLACEHOLDER image (procedural, not AI, not evidence)' WHERE provider='local-procedural'")
    ex("UPDATE generation_records SET generation_kind='PLACEHOLDER', provider='placeholder-procedural', "
       "estimated_cost=NULL, cost_status='NOT_APPLICABLE' WHERE provider='local-procedural'")
    ex("UPDATE cost_records SET generation_kind='PLACEHOLDER', provider='placeholder-procedural', estimated_cost=NULL, "
       "cost_status='NOT_APPLICABLE', cost_basis='reclassified by migration 0002: placeholder renderer, invented "
       "synthetic rate removed' WHERE provider='local-procedural'")
    # ---- mocks
    ex("UPDATE assets SET generation_kind='TEST_FIXTURE', generation_cost=NULL, cost_status='NOT_APPLICABLE' "
       "WHERE is_mock = 1")
    ex("UPDATE generation_records SET generation_kind='TEST_FIXTURE', estimated_cost=NULL, cost_status='NOT_APPLICABLE' "
       "WHERE is_mock = 1")
    ex("UPDATE cost_records SET generation_kind='TEST_FIXTURE', estimated_cost=NULL, cost_status='NOT_APPLICABLE', "
       "cost_basis='test fixture' WHERE is_mock = 1")
    # ---- remaining AI-declared assets (imports explicitly declared AI_GENERATED by the caller)
    ex("UPDATE assets SET generation_kind='AI_GENERATED' WHERE source_type='AI_GENERATED' AND generation_kind='NOT_GENERATED'")
    # ---- generation record states
    ex("UPDATE generation_records SET provider_state=status WHERE status IN ('SUCCEEDED','FAILED')")
    # ---- timing label rename
    ex("UPDATE scenes SET timing_source='SUPPLIED' WHERE timing_source='PROVIDED'")

    if placeholder_ids:
        ids = set(placeholder_ids)
        for aid in ids:
            ex("UPDATE shots SET visual_type='PLACEHOLDER_IMAGE', source_type='SYSTEM_GENERATED', "
               "editorial_label='PLACEHOLDER' WHERE asset_id=:a", a=aid)
        rows = list(ex("SELECT visual_plan_id, timeline_json FROM visual_plans WHERE timeline_json IS NOT NULL"))
        for plan_id, raw in rows:
            try:
                tl = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                continue
            if not isinstance(tl, dict) or not (ids & set((tl.get("assets") or {}).keys())):
                continue
            for aid, ref in (tl.get("assets") or {}).items():
                if aid in ids:
                    ref.update(asset_type="PLACEHOLDER_IMAGE", source_type="SYSTEM_GENERATED",
                               editorial_label="PLACEHOLDER", generation_kind="PLACEHOLDER")
            for s in tl.get("shots") or []:
                if s.get("asset_id") in ids:
                    s.update(visual_type="PLACEHOLDER_IMAGE", source_type="SYSTEM_GENERATED", editorial_label="PLACEHOLDER")
                    for o in s.get("overlays") or []:
                        if o.get("type") == "editorial_label":
                            o["text"] = "PLACEHOLDER IMAGE - NOT AI - NOT EVIDENCE"
            tl["reclassified_by_migration"] = "0002_phase2"
            ex("UPDATE visual_plans SET timeline_json=:t WHERE visual_plan_id=:p", t=json.dumps(tl), p=plan_id)


def downgrade() -> None:
    with op.batch_alter_table("cost_records", schema=None) as b:
        for c in ("is_fallback", "provider_request_id", "pre_call_estimate_usd", "usage", "cost_basis", "cost_status",
                  "generation_kind"):
            b.drop_column(c)
    with op.batch_alter_table("generation_records", schema=None) as b:
        for c in ("fallback_reason", "fallback_from", "is_fallback", "provider_request_id", "pre_call_estimate_usd",
                  "cost_status", "provider_state", "generation_kind"):
            b.drop_column(c)
    with op.batch_alter_table("assets", schema=None) as b:
        b.drop_index(b.f("ix_assets_perceptual_hash"))
        for c in ("duplicate_of", "perceptual_hash", "source_retrieved_at", "attribution_required", "usage_rights",
                  "licence_url", "source_identity", "provider_request_id", "fallback_reason", "fallback_from",
                  "is_fallback", "cost_status", "generation_kind"):
            b.drop_column(c)
    # NOTE: downgrade cannot restore the removed (invented) synthetic costs; they were not real.
