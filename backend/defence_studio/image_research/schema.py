"""Image Research tables.

Owned by this module. They are created with CREATE TABLE IF NOT EXISTS and do
not bump the shared schema version in database.py while that file carries
another session's uncommitted migration.
"""
from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS image_research_jobs (
    job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    topic TEXT NOT NULL,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    summary_json TEXT,
    research_topic_id TEXT,
    script_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS image_research_candidates (
    image_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    original_image_url TEXT NOT NULL,
    canonical_image_url TEXT NOT NULL,
    thumbnail_url TEXT,
    source_website TEXT,
    page_url TEXT,
    canonical_page_url TEXT,
    creator TEXT,
    title TEXT,
    caption TEXT,
    publication_date TEXT,
    acquired_at TEXT NOT NULL,
    license_name TEXT,
    license_url TEXT,
    attribution TEXT,
    commercial_use TEXT NOT NULL,
    modification TEXT NOT NULL,
    rights_status TEXT NOT NULL,
    rights_notes TEXT,
    width INTEGER,
    height INTEGER,
    file_format TEXT,
    download_status TEXT NOT NULL,
    download_error TEXT,
    content_sha256 TEXT,
    perceptual_hash TEXT,
    perceptual_hash_status TEXT,
    similarity_group_id TEXT,
    duplicate_role TEXT,
    duplicate_reason TEXT,
    relevance_score REAL,
    relevance_status TEXT NOT NULL,
    date_status TEXT NOT NULL,
    review_status TEXT NOT NULL,
    eligible INTEGER NOT NULL,
    stored_relative_path TEXT,
    category TEXT,
    FOREIGN KEY (job_id) REFERENCES image_research_jobs(job_id)
);

CREATE INDEX IF NOT EXISTS idx_image_research_candidates_job
    ON image_research_candidates(job_id);

CREATE TABLE IF NOT EXISTS image_research_reviews (
    review_id TEXT PRIMARY KEY,
    image_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);
"""


def ensure_schema(connection) -> None:
    connection.executescript(SCHEMA_SQL)
