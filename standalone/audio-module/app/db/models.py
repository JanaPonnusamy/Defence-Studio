"""
SQLAlchemy ORM models / schema for the standalone Audio Module database.

This is this module's OWN database -- it owns and migrates this schema
independently and has no knowledge of, or dependency on, any parent
application's database.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Reference(Base):
    __tablename__ = "references"

    reference_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_type: Mapped[str] = mapped_column(String(32))  # "upload" | "url"
    source_location: Mapped[str] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=True)
    duration: Mapped[float] = mapped_column(Float, nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="en")
    audio_id: Mapped[str] = mapped_column(String(36), nullable=True)
    transcript_id: Mapped[str] = mapped_column(String(36), nullable=True)
    alignment_id: Mapped[str] = mapped_column(String(36), nullable=True)
    analysis_id: Mapped[str] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    status: Mapped[str] = mapped_column(String(32), default="REGISTERED")
    rights_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class AudioAsset(Base):
    __tablename__ = "audio_assets"

    audio_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=True)
    file_path: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=True)
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=True)
    channels: Mapped[int] = mapped_column(Integer, nullable=True)
    bit_depth: Mapped[int] = mapped_column(Integer, nullable=True)
    duration: Mapped[float] = mapped_column(Float, nullable=True)
    loudness_lufs: Mapped[float] = mapped_column(Float, nullable=True)
    peak_dbfs: Mapped[float] = mapped_column(Float, nullable=True)
    format: Mapped[str] = mapped_column(String(16), nullable=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=True)
    is_normalized: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    waveform_path: Mapped[str] = mapped_column(Text, nullable=True)


class Transcript(Base):
    __tablename__ = "transcripts"

    transcript_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=True)
    source_format: Mapped[str] = mapped_column(String(16))  # srt|vtt|plain|provider|manual
    file_path: Mapped[str] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Alignment(Base):
    __tablename__ = "alignments"

    alignment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=True)
    audio_id: Mapped[str] = mapped_column(String(36), nullable=True)
    transcript_id: Mapped[str] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    quality_label: Mapped[str] = mapped_column(String(32))  # e.g. "approximate" | "high"
    mean_confidence: Mapped[float] = mapped_column(Float, nullable=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Analysis(Base):
    __tablename__ = "analyses"

    analysis_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=True)
    audio_id: Mapped[str] = mapped_column(String(36), nullable=True)
    alignment_id: Mapped[str] = mapped_column(String(36), nullable=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Profile(Base):
    __tablename__ = "profiles"

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    profile_name: Mapped[str] = mapped_column(String(255))
    source_analysis_ids: Mapped[list] = mapped_column(JSON, default=list)
    file_path: Mapped[str] = mapped_column(Text, nullable=True)
    tempo: Mapped[str] = mapped_column(String(32), nullable=True)
    pause_style: Mapped[str] = mapped_column(String(32), nullable=True)
    energy: Mapped[str] = mapped_column(String(32), nullable=True)
    emphasis: Mapped[str] = mapped_column(String(32), nullable=True)
    pitch_style: Mapped[str] = mapped_column(String(32), nullable=True)
    transition_pause: Mapped[str] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    result_id: Mapped[str] = mapped_column(String(36), nullable=True)


class GeneratedAudio(Base):
    __tablename__ = "generated_audio"

    generated_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(36), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    voice_id: Mapped[str] = mapped_column(String(128), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="en")
    performance_plan_id: Mapped[str] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128), nullable=True)
    output_format: Mapped[str] = mapped_column(String(16), default="wav")
    file_path: Mapped[str] = mapped_column(Text, nullable=True)
    duration: Mapped[float] = mapped_column(Float, nullable=True)
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=True)
    channels: Mapped[int] = mapped_column(Integer, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=True)
    validation_details: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PerformancePlan(Base):
    __tablename__ = "performance_plans"

    plan_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    profile_id: Mapped[str] = mapped_column(String(36), nullable=True)
    script_text: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16), default="en")
    instructions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
