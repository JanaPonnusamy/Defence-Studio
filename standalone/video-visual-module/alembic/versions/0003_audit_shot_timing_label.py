"""Audit fix (2026-10-12): finish the v1.0 timing-label rename for shots and stored timelines.

Migration 0002 renamed scenes.timing_source PROVIDED -> SUPPLIED, but v1.0 also copied the scene's
timing_source into every shot's narration_anchor (shots.narration_anchor JSON and the stored
visual_plans.timeline_json). Those kept the retired value "PROVIDED", so GET /shots/{plan} and
GET /visual-plans/{plan}/timeline served a label outside the v1.1 contract (ESTIMATED | SUPPLIED).

Data-only, idempotent: rewrites narration_anchor.timing_source PROVIDED -> SUPPLIED in both places.
No schema change. Plan JSON files already written under storage/plans/ are not rewritten (the API
serves the DB copy; see KNOWN_LIMITATIONS.md).

Revision ID: 0003_audit_timing
Revises: 0002_phase2
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_audit_timing"
down_revision: Union[str, None] = "0002_phase2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fix_anchor(anchor) -> bool:
    if isinstance(anchor, dict) and anchor.get("timing_source") == "PROVIDED":
        anchor["timing_source"] = "SUPPLIED"
        return True
    return False


def upgrade() -> None:
    conn = op.get_bind()
    rows = list(conn.execute(sa.text("SELECT shot_id, narration_anchor FROM shots WHERE narration_anchor LIKE '%PROVIDED%'")))
    for shot_id, raw in rows:
        try:
            anchor = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if _fix_anchor(anchor):
            conn.execute(sa.text("UPDATE shots SET narration_anchor=:a WHERE shot_id=:s"), {"a": json.dumps(anchor), "s": shot_id})

    rows = list(conn.execute(sa.text(
        "SELECT visual_plan_id, timeline_json FROM visual_plans WHERE timeline_json IS NOT NULL AND timeline_json LIKE '%PROVIDED%'")))
    for plan_id, raw in rows:
        try:
            tl = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(tl, dict):
            continue
        changed = False
        for s in tl.get("shots") or []:
            if isinstance(s, dict):
                changed = _fix_anchor(s.get("narration_anchor")) or changed
        timing = tl.get("timing")
        if isinstance(timing, dict):
            if timing.get("timing_source") == "PROVIDED":
                timing["timing_source"] = "SUPPLIED"
                changed = True
            if isinstance(timing.get("sources"), list) and "PROVIDED" in timing["sources"]:
                timing["sources"] = sorted({"SUPPLIED" if x == "PROVIDED" else x for x in timing["sources"]})
                changed = True
        if changed:
            tl["timing_relabelled_by_migration"] = "0003_audit_timing"
            conn.execute(sa.text("UPDATE visual_plans SET timeline_json=:t WHERE visual_plan_id=:p"),
                         {"t": json.dumps(tl), "p": plan_id})


def downgrade() -> None:
    # "PROVIDED" is a retired v1.0 label; nothing to restore.
    pass
