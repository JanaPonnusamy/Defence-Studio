"""Voice Lab tables (prefix ``vl_``), owned by this module.

They are created with CREATE TABLE IF NOT EXISTS and versioned in ``vl_schema_meta``
instead of the shared ``SCHEMA_VERSION`` in ``database.py``. That file carries other
sessions' uncommitted migrations (v14/v15), so Voice Lab must not collide with them.
This is the same pattern Image Research uses (coordination file §17/§18).

Naming reconciliation with EXT-03 (``components/voice-engine``):
* EXT-03 ``voice_profiles`` are *style* profiles; Voice Lab narrator identities are
  ``vl_voice_profiles``, and Voice Lab style profiles are ``vl_style_profiles``.
* EXT-03 ``voice_model_versions`` / ``voice_licenses`` are untouched. Voice Lab keeps
  its own ``vl_model_versions`` (per-narrator) and ``vl_model_licences`` (the separate
  code/weights/data licence register required by the spec).
No EXT-03 table is altered or written by Voice Lab.

Large audio is never stored in SQLite; rows hold a storage-relative path, SHA-256,
byte size and duration.
"""
from __future__ import annotations

import sqlite3

VL_SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vl_schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_queue (
    workflow_job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS vl_queue_ref ON vl_queue(ref_id);

CREATE TABLE IF NOT EXISTS vl_consent_records (
    consent_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    subject_role TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    document_ref TEXT NOT NULL,
    document_sha256 TEXT,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_model_licences (
    model_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    record_json TEXT NOT NULL,
    commercial_status TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_voice_profiles (
    narrator_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    gender_label TEXT,
    languages_json TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    consent_id TEXT,
    active_model_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner_id, display_name)
);

CREATE TABLE IF NOT EXISTS vl_model_versions (
    model_version_id TEXT PRIMARY KEY,
    narrator_id TEXT NOT NULL REFERENCES vl_voice_profiles(narrator_id),
    model_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    parent_version_id TEXT,
    training_job_id TEXT,
    checkpoint_rel_path TEXT,
    checkpoint_sha256 TEXT,
    config_json TEXT NOT NULL,
    metrics_json TEXT,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (narrator_id, version),
    UNIQUE (identity_key)
);

CREATE TABLE IF NOT EXISTS vl_audio_artifacts (
    artifact_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    duration_ms INTEGER,
    sample_rate INTEGER,
    channels INTEGER,
    format TEXT NOT NULL,
    retention_class TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    source_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS vl_artifacts_sha ON vl_audio_artifacts(owner_id, sha256, kind);

CREATE TABLE IF NOT EXISTS vl_voice_datasets (
    dataset_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    narrator_id TEXT NOT NULL REFERENCES vl_voice_profiles(narrator_id),
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    consent_id TEXT NOT NULL REFERENCES vl_consent_records(consent_id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (narrator_id, name)
);

CREATE TABLE IF NOT EXISTS vl_dataset_files (
    file_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES vl_voice_datasets(dataset_id),
    artifact_id TEXT NOT NULL,
    original_name TEXT NOT NULL,
    source_note TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    qc_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (dataset_id, sha256)
);

CREATE TABLE IF NOT EXISTS vl_voice_dataset_segments (
    segment_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES vl_voice_datasets(dataset_id),
    file_id TEXT NOT NULL REFERENCES vl_dataset_files(file_id),
    artifact_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    transcript TEXT,
    transcript_status TEXT NOT NULL,
    qc_json TEXT NOT NULL,
    qc_status TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    duplicate_of TEXT,
    excluded INTEGER NOT NULL DEFAULT 0,
    split TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS vl_segments_dataset ON vl_voice_dataset_segments(dataset_id, file_id, ordinal);

CREATE TABLE IF NOT EXISTS vl_dataset_versions (
    dataset_version_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES vl_voice_datasets(dataset_id),
    narrator_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    manifest_rel_path TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    stats_json TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (dataset_id, version)
);

CREATE TABLE IF NOT EXISTS vl_training_jobs (
    training_job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    narrator_id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    target TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL,
    workflow_job_id TEXT,
    resume_from TEXT,
    log_rel_path TEXT,
    checkpoint_rel_dir TEXT,
    metrics_json TEXT,
    error TEXT,
    result_model_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_reference_media (
    reference_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_url TEXT,
    authorization_ref TEXT NOT NULL,
    source_artifact_id TEXT,
    speech_artifact_id TEXT,
    sha256 TEXT,
    duration_ms INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_reference_analyses (
    analysis_id TEXT PRIMARY KEY,
    reference_id TEXT NOT NULL REFERENCES vl_reference_media(reference_id),
    version INTEGER NOT NULL,
    analysis_version TEXT NOT NULL,
    analysis_hash TEXT NOT NULL,
    frames_rel_path TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (reference_id, version)
);

CREATE TABLE IF NOT EXISTS vl_reference_segments (
    segment_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES vl_reference_analyses(analysis_id),
    ordinal INTEGER NOT NULL,
    t0_ms INTEGER NOT NULL,
    t1_ms INTEGER NOT NULL,
    classification TEXT NOT NULL,
    confidence REAL
);

CREATE TABLE IF NOT EXISTS vl_prosody_segments (
    prosody_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES vl_reference_analyses(analysis_id),
    ordinal INTEGER NOT NULL,
    t0_ms INTEGER NOT NULL,
    t1_ms INTEGER NOT NULL,
    features_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_style_profiles (
    style_profile_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL,
    features_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_style_features (
    feature_id TEXT PRIMARY KEY,
    style_profile_id TEXT NOT NULL REFERENCES vl_style_profiles(style_profile_id),
    feature_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    source_t0_ms INTEGER,
    source_t1_ms INTEGER,
    label_kind TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    UNIQUE (style_profile_id, feature_key)
);

CREATE TABLE IF NOT EXISTS vl_generation_jobs (
    generation_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    workflow_job_id TEXT,
    request_key TEXT NOT NULL,
    narrator_id TEXT NOT NULL,
    model_version_id TEXT NOT NULL,
    style_profile_id TEXT,
    language TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    target_duration_ms INTEGER,
    config_json TEXT NOT NULL,
    script_ref_json TEXT,
    status TEXT NOT NULL,
    progress_note TEXT,
    error TEXT,
    raw_artifact_id TEXT,
    artifact_id TEXT,
    timing_json TEXT,
    provenance_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS vl_generation_key ON vl_generation_jobs(owner_id, request_key);

CREATE TABLE IF NOT EXISTS vl_audio_projects (
    project_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    name TEXT NOT NULL,
    revision INTEGER NOT NULL,
    sample_rate INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_audio_tracks (
    track_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES vl_audio_projects(project_id),
    ordinal INTEGER NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    gain_db REAL NOT NULL,
    mute INTEGER NOT NULL,
    solo INTEGER NOT NULL,
    locked INTEGER NOT NULL,
    effects_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_audio_clips (
    clip_id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES vl_audio_tracks(track_id),
    project_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    in_ms INTEGER NOT NULL,
    out_ms INTEGER NOT NULL,
    gain_db REAL NOT NULL,
    fade_in_ms INTEGER NOT NULL,
    fade_out_ms INTEGER NOT NULL,
    crossfade_next_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS vl_audio_edit_operations (
    op_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES vl_audio_projects(project_id),
    seq INTEGER NOT NULL,
    op_json TEXT NOT NULL,
    doc_before_json TEXT NOT NULL,
    doc_after_json TEXT NOT NULL,
    revision_after INTEGER NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, seq)
);

CREATE TABLE IF NOT EXISTS vl_quality_reviews (
    review_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    generation_id TEXT,
    model_version_id TEXT,
    narrator_id TEXT,
    ratings_json TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create or upgrade the Voice Lab tables. Additive only; never touches other tables."""
    connection.executescript(SCHEMA_SQL)
    row = connection.execute("SELECT value FROM vl_schema_meta WHERE key='version'").fetchone()
    current = int(row[0]) if row else 0
    if current > VL_SCHEMA_VERSION:
        raise RuntimeError(f"Voice Lab schema v{current} is newer than this code (v{VL_SCHEMA_VERSION})")
    # Future upgrades: `if current < 2: ...` blocks go here, each additive.
    connection.execute("INSERT INTO vl_schema_meta(key, value) VALUES('version', ?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(VL_SCHEMA_VERSION),))
    connection.commit()
