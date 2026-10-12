"""
SQLAlchemy ORM models for the standalone Video Visual / Scene Composition
Module. This is this module's OWN database. It has no knowledge of, and no
dependency on, any parent application's or sibling module's database.

Schema changes must be accompanied by an Alembic revision in alembic/versions
(tests/test_migrations.py fails if the migration and these models drift).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_image_budget_usd: Mapped[float] = mapped_column(Float, default=5.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Asset(Base):
    """Spec §5/§6: supported asset types + minimum metadata model."""
    __tablename__ = "assets"

    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    asset_type: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # relative to storage_root (posix)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    creator: Mapped[str | None] = mapped_column(String(255), nullable=True)
    licence: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OPEN, LICENSED, PERMITTED, UNKNOWN, RESTRICTED. UNKNOWN is never shown as open/reusable.
    rights_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation_cost: Mapped[float | None] = mapped_column(Float, nullable=True)  # KNOWN cost only, else NULL
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    # FACTUAL_EVIDENCE, ILLUSTRATIVE_VISUAL, AI_GENERATED, PLACEHOLDER, UNKNOWN (spec §34)
    editorial_category: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    # structured payload: tags, map spec, stat data, document regions, clip points, protected regions, variant info
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    # ---- Phase 2 (migration 0002): generation truthfulness
    # AI_GENERATED (real model) | PLACEHOLDER | TEST_FIXTURE | NOT_GENERATED
    generation_kind: Mapped[str] = mapped_column(String(24), default="NOT_GENERATED", server_default="NOT_GENERATED")
    cost_status: Mapped[str | None] = mapped_column(String(24), nullable=True)  # KNOWN | UNKNOWN | NOT_APPLICABLE
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    fallback_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # ---- Phase 2: provenance and rights
    source_identity: Mapped[str | None] = mapped_column(String(255), nullable=True)   # publisher / archive / record id
    licence_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_rights: Mapped[str | None] = mapped_column(Text, nullable=True)             # usage terms / restrictions
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    source_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)  # 64-bit dHash (hex)
    duplicate_of: Mapped[str | None] = mapped_column(String(36), nullable=True)                # near-duplicate asset


class Scene(Base):
    """Spec §13."""
    __tablename__ = "scenes"

    scene_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    narration_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{segment_id, text, start, end}] -- timing contract input (spec §30)
    narration_segments: Mapped[list] = mapped_column(JSON, default=list)
    narration_segment_ids: Mapped[list] = mapped_column(JSON, default=list)
    timing_source: Mapped[str] = mapped_column(String(16), default="ESTIMATED")  # ESTIMATED | PROVIDED
    visual_intents: Mapped[list] = mapped_column(JSON, default=list)
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    shot_ids: Mapped[list] = mapped_column(JSON, default=list)
    transition: Mapped[str] = mapped_column(String(32), default="CUT")
    importance: Mapped[str] = mapped_column(String(16), default="NORMAL")  # LOW, NORMAL, HIGH
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VisualPlan(Base):
    """Aggregate output of the pipeline (spec §3) + renderer timeline (spec §29)."""
    __tablename__ = "visual_plans"

    visual_plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scene_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="BUILDING")  # BUILDING, BUILT, FAILED
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    timeline_json: Mapped[dict] = mapped_column(JSON, default=dict)
    timeline_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Shot(Base):
    """Spec §15."""
    __tablename__ = "shots"

    shot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    visual_plan_id: Mapped[str] = mapped_column(String(36), index=True)
    scene_id: Mapped[str] = mapped_column(String(36))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    visual_type: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(32))
    editorial_label: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="READY")  # READY | GAP
    gap_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    narration_anchor: Mapped[dict] = mapped_column(JSON, default=dict)  # {segment_ids, text, start, end}
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    framing: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reuse_index: Mapped[int] = mapped_column(Integer, default=0)
    motion: Mapped[dict] = mapped_column(JSON, default=dict)
    crop: Mapped[dict] = mapped_column(JSON, default=dict)
    transition: Mapped[str] = mapped_column(String(32), default="CUT")
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    overlays: Mapped[list] = mapped_column(JSON, default=list)
    editorial_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Job(Base):
    """Spec §25."""
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_type: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class GenerationRecord(Base):
    """Spec §22/§23: result of an AIImageProvider generation call."""
    __tablename__ = "generation_records"

    generation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9")
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quality: Mapped[str] = mapped_column(String(32), default="standard")
    purpose: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reference_assets: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(64))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING/SUCCEEDED/FAILED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)  # KNOWN cost only, else NULL
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    provider_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # ---- Phase 2
    generation_kind: Mapped[str] = mapped_column(String(24), default="NOT_GENERATED", server_default="NOT_GENERATED")
    provider_state: Mapped[str | None] = mapped_column(String(24), nullable=True)  # NOT_CONFIGURED|UNAVAILABLE|FAILED|SUCCEEDED
    cost_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    pre_call_estimate_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    fallback_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class CostRecord(Base):
    """Spec §24: every (paid or zero-cost) image generation."""
    __tablename__ = "cost_records"

    cost_record_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # KNOWN cost only; NULL when cost_status is UNKNOWN or NOT_APPLICABLE (never a made-up number)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # ---- Phase 2
    generation_kind: Mapped[str] = mapped_column(String(24), default="NOT_GENERATED", server_default="NOT_GENERATED")
    cost_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", server_default="UNKNOWN")
    cost_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pre_call_estimate_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
