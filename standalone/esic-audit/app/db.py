"""SQLite schema for audit snapshots.

Design rules enforced in the database itself (not only in code):
* every record row belongs to one audit run (FOREIGN KEY, NOT NULL);
* record, evidence, report, stage and capture tables are append-only (UPDATE/DELETE abort);
* an audit run in a terminal state can no longer be changed or deleted;
* duplicate records inside one audit are rejected by UNIQUE(audit_id, record_key);
* money is stored as TEXT holding an exact decimal string, never REAL.
Migrations are additive and numbered; `migrate()` is idempotent.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

TERMINAL_STATUSES = ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED")

_APPEND_ONLY = ("esic_contribution_records", "esic_interest_records", "esic_damages_records",
                "esic_recovery_records", "esic_audit_evidence", "esic_audit_reports",
                "esic_audit_stages", "esic_raw_captures", "esic_audit_results", "esic_audit_events")

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
CREATE TABLE esic_audit_runs (
    id INTEGER PRIMARY KEY,
    audit_uuid TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    current_activity TEXT,
    employer_identifier_masked TEXT,
    portal_url TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 0,
    extraction_version TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    records_inspected INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_runs_status ON esic_audit_runs(status);
CREATE INDEX ix_runs_started ON esic_audit_runs(started_at);

CREATE TABLE esic_audit_stages (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX ix_stages_audit ON esic_audit_stages(audit_id);

CREATE TABLE esic_audit_events (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX ix_events_audit ON esic_audit_events(audit_id);

CREATE TABLE esic_raw_captures (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    category TEXT NOT NULL,
    page_name TEXT NOT NULL,
    page_url TEXT,
    captured_at TEXT NOT NULL,
    tables_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE(audit_id, content_hash)
);
CREATE INDEX ix_captures_audit ON esic_raw_captures(audit_id);

CREATE TABLE esic_contribution_records (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    record_key TEXT NOT NULL,
    contribution_month TEXT,
    contribution_period_start TEXT,
    contribution_period_end TEXT,
    due_date TEXT,
    contribution_amount TEXT,
    employer_contribution TEXT,
    employee_contribution TEXT,
    challan_reference TEXT,
    challan_created_date TEXT,
    payment_date TEXT,
    payment_status TEXT,
    source_reference TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    UNIQUE(audit_id, record_key)
);
CREATE INDEX ix_contrib_audit_month ON esic_contribution_records(audit_id, contribution_month);
CREATE INDEX ix_contrib_challan ON esic_contribution_records(challan_reference);

CREATE TABLE esic_interest_records (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    record_key TEXT NOT NULL,
    contribution_period TEXT,
    assessed_amount TEXT,
    paid_amount TEXT,
    outstanding_amount TEXT,
    demand_reference TEXT,
    assessment_date TEXT,
    source_reference TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    UNIQUE(audit_id, record_key)
);
CREATE INDEX ix_interest_audit ON esic_interest_records(audit_id, contribution_period);
CREATE INDEX ix_interest_demand ON esic_interest_records(demand_reference);

CREATE TABLE esic_damages_records (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    record_key TEXT NOT NULL,
    contribution_period TEXT,
    assessed_amount TEXT,
    paid_amount TEXT,
    outstanding_amount TEXT,
    demand_reference TEXT,
    assessment_date TEXT,
    assessment_status TEXT,
    source_reference TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    UNIQUE(audit_id, record_key)
);
CREATE INDEX ix_damages_audit ON esic_damages_records(audit_id, contribution_period);
CREATE INDEX ix_damages_demand ON esic_damages_records(demand_reference);

CREATE TABLE esic_recovery_records (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    record_key TEXT NOT NULL,
    recovery_reference TEXT,
    demand_amount TEXT,
    principal_amount TEXT,
    interest_component TEXT,
    damages_component TEXT,
    paid_amount TEXT,
    outstanding_amount TEXT,
    demand_date TEXT,
    period_from TEXT,
    period_to TEXT,
    linked_references TEXT,
    recovery_status TEXT,
    source_reference TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    UNIQUE(audit_id, record_key)
);
CREATE INDEX ix_recovery_audit ON esic_recovery_records(audit_id);
CREATE INDEX ix_recovery_ref ON esic_recovery_records(recovery_reference);

CREATE TABLE esic_audit_evidence (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    evidence_type TEXT NOT NULL,
    page_name TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    extraction_status TEXT NOT NULL
);
CREATE INDEX ix_evidence_audit ON esic_audit_evidence(audit_id);

CREATE TABLE esic_audit_results (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL UNIQUE REFERENCES esic_audit_runs(id),
    result_json TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE esic_audit_reports (
    id INTEGER PRIMARY KEY,
    audit_id INTEGER NOT NULL REFERENCES esic_audit_runs(id),
    report_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    checksum TEXT NOT NULL,
    UNIQUE(audit_id, report_type)
);
CREATE INDEX ix_reports_audit ON esic_audit_reports(audit_id);
"""),
]


def _immutability_sql() -> str:
    parts = []
    for table in _APPEND_ONLY:
        parts.append(f"""
CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;""")
    terminal = ",".join(f"'{s}'" for s in TERMINAL_STATUSES)
    parts.append(f"""
CREATE TRIGGER IF NOT EXISTS trg_runs_frozen BEFORE UPDATE ON esic_audit_runs
WHEN OLD.status IN ({terminal})
BEGIN SELECT RAISE(ABORT, 'completed audit snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_runs_no_delete BEFORE DELETE ON esic_audit_runs
BEGIN SELECT RAISE(ABORT, 'audit history cannot be deleted'); END;""")
    return "\n".join(parts)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS esic_schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    current = conn.execute("SELECT COALESCE(MAX(version), 0) FROM esic_schema_version").fetchone()[0]
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in [s.strip() for s in sql.split(";") if s.strip()]:
                conn.execute(statement)
            conn.execute("INSERT INTO esic_schema_version VALUES (?, datetime('now'))", (version,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        current = version
    conn.executescript(_immutability_sql())
    return current
