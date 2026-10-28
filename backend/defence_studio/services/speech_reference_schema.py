"""Additive schema v14 for Tamil speech-reference knowledge.

Original transcripts stay immutable. Corrections, pronunciations and acoustic
observations are separate rows. Audio bytes are not stored in these tables.
"""

SPEECH_REFERENCE_DDL = """
CREATE TABLE IF NOT EXISTS speech_reference_sources (
    source_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('channel','video','playlist')),
    source_url TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    video_id TEXT,
    channel_id TEXT,
    playlist_id TEXT,
    title TEXT,
    published_at TEXT,
    language_preference TEXT NOT NULL,
    subtitle_preference TEXT NOT NULL,
    audio_source_method TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    intended_use TEXT NOT NULL DEFAULT '',
    acquisition_status TEXT NOT NULL,
    analysis_status TEXT NOT NULL DEFAULT 'not_started',
    last_error TEXT NOT NULL DEFAULT '',
    limitation TEXT NOT NULL DEFAULT '',
    last_processed_at TEXT,
    max_videos INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_id, canonical_key)
);
CREATE INDEX IF NOT EXISTS ix_speech_sources_owner
    ON speech_reference_sources(owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS speech_source_provenance (
    provenance_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES speech_reference_sources(source_id),
    source_owner TEXT NOT NULL DEFAULT '',
    acquisition_method TEXT NOT NULL,
    acquisition_outcome TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    caption_origin TEXT NOT NULL,
    audio_origin TEXT NOT NULL,
    permission_status TEXT NOT NULL,
    licence_reference TEXT NOT NULL DEFAULT '',
    retain_text INTEGER NOT NULL DEFAULT 0,
    acoustic_analysis_allowed INTEGER NOT NULL DEFAULT 0,
    dataset_export_allowed INTEGER NOT NULL DEFAULT 0,
    model_training_allowed INTEGER NOT NULL DEFAULT 0,
    voice_cloning_authorized INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_speech_provenance_source
    ON speech_source_provenance(source_id, imported_at);

CREATE TABLE IF NOT EXISTS speech_reference_videos (
    video_row_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES speech_reference_sources(source_id),
    video_id TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    webpage_url TEXT,
    caption_language TEXT,
    caption_availability TEXT NOT NULL,
    audio_availability TEXT NOT NULL,
    audio_limitation TEXT NOT NULL DEFAULT '',
    analysis_status TEXT NOT NULL DEFAULT 'not_started',
    sentence_count INTEGER NOT NULL DEFAULT 0,
    word_occurrence_count INTEGER NOT NULL DEFAULT 0,
    reviewed_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    last_processed_at TEXT,
    UNIQUE(source_id, video_id)
);

CREATE TABLE IF NOT EXISTS speech_reference_transcripts (
    transcript_id TEXT PRIMARY KEY,
    video_row_id TEXT NOT NULL REFERENCES speech_reference_videos(video_row_id),
    language TEXT NOT NULL,
    original_text TEXT NOT NULL,
    original_cues_json TEXT NOT NULL,
    source_asset_uri TEXT,
    source_sha256 TEXT NOT NULL,
    acquisition_method TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    normalization_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_speech_transcripts_video
    ON speech_reference_transcripts(video_row_id, imported_at);

CREATE TABLE IF NOT EXISTS speech_transcript_versions (
    version_id TEXT PRIMARY KEY,
    transcript_id TEXT NOT NULL REFERENCES speech_reference_transcripts(transcript_id),
    version INTEGER NOT NULL CHECK(version > 0),
    cues_json TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('normalized','corrected')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    UNIQUE(transcript_id, version)
);

CREATE TABLE IF NOT EXISTS speech_segments (
    segment_id TEXT PRIMARY KEY,
    video_row_id TEXT NOT NULL REFERENCES speech_reference_videos(video_row_id),
    version_id TEXT NOT NULL REFERENCES speech_transcript_versions(version_id),
    source_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    segment_text TEXT NOT NULL,
    segment_type TEXT NOT NULL,
    speaker_profile_id TEXT,
    speaking_rate REAL,
    speaking_rate_method TEXT,
    energy_contour_json TEXT,
    pause_structure_json TEXT,
    emphasis_json TEXT,
    delivery_label TEXT,
    measurement_method TEXT,
    confidence REAL,
    timestamp_kind TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'needs_review',
    CHECK(end_ms > start_ms)
);
CREATE INDEX IF NOT EXISTS ix_speech_segments_video
    ON speech_segments(video_row_id, ordinal);

CREATE TABLE IF NOT EXISTS tamil_words (
    word_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    tamil_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    transliteration TEXT,
    word_type TEXT,
    domain TEXT,
    description_ta TEXT,
    description_en TEXT,
    is_technical_term INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(owner_id, normalized_text)
);
CREATE INDEX IF NOT EXISTS ix_tamil_words_owner
    ON tamil_words(owner_id, normalized_text);

CREATE TABLE IF NOT EXISTS tamil_word_forms (
    word_form_id TEXT PRIMARY KEY,
    word_id TEXT NOT NULL REFERENCES tamil_words(word_id),
    surface_text TEXT NOT NULL,
    normalized_surface_text TEXT NOT NULL,
    morphological_features TEXT,
    context_notes TEXT,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    UNIQUE(word_id, normalized_surface_text)
);

CREATE TABLE IF NOT EXISTS tamil_word_meanings (
    meaning_id TEXT PRIMARY KEY,
    word_id TEXT NOT NULL REFERENCES tamil_words(word_id),
    meaning_ta TEXT NOT NULL,
    meaning_en TEXT,
    context_domain TEXT,
    example_sentence_ta TEXT,
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
);

CREATE TABLE IF NOT EXISTS tamil_pronunciations (
    pronunciation_id TEXT PRIMARY KEY,
    word_id TEXT NOT NULL REFERENCES tamil_words(word_id),
    word_form_id TEXT REFERENCES tamil_word_forms(word_form_id),
    meaning_id TEXT REFERENCES tamil_word_meanings(meaning_id),
    pronunciation_text TEXT NOT NULL,
    phonetic_representation TEXT,
    phonetic_scheme TEXT,
    locale TEXT NOT NULL DEFAULT 'ta-IN',
    dialect TEXT,
    context_rule TEXT,
    provider_id TEXT,
    confidence REAL,
    confidence_origin TEXT,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    provenance_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tamil_pron_word
    ON tamil_pronunciations(word_id, review_status);

CREATE TABLE IF NOT EXISTS speech_speaker_profiles (
    speaker_profile_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    identity_retained INTEGER NOT NULL DEFAULT 0,
    voice_cloning_authorized INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS speech_occurrences (
    occurrence_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    video_row_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    segment_id TEXT NOT NULL REFERENCES speech_segments(segment_id),
    word_id TEXT REFERENCES tamil_words(word_id),
    word_form_id TEXT REFERENCES tamil_word_forms(word_form_id),
    observed_text TEXT NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    duration_basis TEXT NOT NULL,
    speaker_profile_id TEXT,
    context_before TEXT,
    context_after TEXT,
    sentence_position TEXT,
    delivery_label TEXT,
    speaking_rate REAL,
    speaking_rate_method TEXT,
    pitch_median_hz REAL,
    pitch_min_hz REAL,
    pitch_max_hz REAL,
    energy_mean REAL,
    pause_before_ms INTEGER,
    pause_after_ms INTEGER,
    alignment_method TEXT NOT NULL,
    alignment_confidence REAL,
    timestamp_kind TEXT NOT NULL,
    timestamps_are_exact INTEGER NOT NULL DEFAULT 0,
    measurement_method TEXT,
    review_status TEXT NOT NULL DEFAULT 'needs_review',
    provenance_id TEXT,
    created_at TEXT NOT NULL,
    CHECK(end_ms > start_ms),
    CHECK(timestamps_are_exact = 0)
);
CREATE INDEX IF NOT EXISTS ix_speech_occ_word
    ON speech_occurrences(owner_id, word_id, review_status);
CREATE INDEX IF NOT EXISTS ix_speech_occ_observed
    ON speech_occurrences(owner_id, observed_text);

CREATE TABLE IF NOT EXISTS speech_samples (
    sample_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    asset_uri TEXT,
    asset_sha256 TEXT,
    occurrence_id TEXT,
    segment_id TEXT,
    video_row_id TEXT,
    speaker_profile_id TEXT,
    source_type TEXT NOT NULL,
    start_ms INTEGER,
    end_ms INTEGER,
    duration_ms INTEGER,
    sample_rate_hz INTEGER,
    channels INTEGER,
    audio_quality_status TEXT,
    permission_status TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'needs_review'
);

CREATE TABLE IF NOT EXISTS speech_feature_measurements (
    measurement_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence REAL,
    is_estimate INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_speech_features_target
    ON speech_feature_measurements(target_kind, target_id);

CREATE TABLE IF NOT EXISTS speech_reviews (
    review_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_speech_reviews_target
    ON speech_reviews(target_kind, target_id, occurred_at);

CREATE TABLE IF NOT EXISTS speech_corrections (
    correction_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    previous_json TEXT NOT NULL,
    new_json TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS speech_training_manifests (
    manifest_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    created_at TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    manifest_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    training_performed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS speech_reference_jobs (
    job_id TEXT PRIMARY KEY REFERENCES workflow_jobs(job_id),
    owner_id TEXT NOT NULL,
    source_id TEXT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    retry_of TEXT,
    progress_note TEXT NOT NULL DEFAULT '',
    result_json TEXT,
    error_detail TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_speech_jobs_owner
    ON speech_reference_jobs(owner_id, created_at DESC);
"""
