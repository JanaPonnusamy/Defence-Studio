"""Append-only store for AUTOMATED evidence-policy decisions.

Deliberately separate from ``claim_reviews`` / ``source_reviews`` (human decisions). Automation
never writes to the human ledger and never carries a reviewer identity: a row here has a policy
version and run id, not a person. Rows are immutable (UPDATE/DELETE abort). Each run is bound to the
exact acquisition and analysis artifact hashes it evaluated, so a changed analysis or policy
invalidates prior automated outcomes by construction (readiness only trusts the latest run whose
hashes match the current artifacts).
"""

from __future__ import annotations

import json

DDL = (
    """CREATE TABLE IF NOT EXISTS evidence_policy_decisions (
      decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id TEXT NOT NULL,
      run_id TEXT NOT NULL,
      policy_version TEXT NOT NULL,
      scoring_version TEXT,
      subject_type TEXT NOT NULL CHECK (subject_type IN ('CLAIM','SOURCE')),
      subject_id TEXT NOT NULL,
      state TEXT NOT NULL CHECK (state IN ('AUTO_ACCEPTED','REJECTED','NEEDS_REVIEW','SOURCE_BLOCKED')),
      score REAL,
      claim_class TEXT,
      independent_groups INTEGER,
      components_json TEXT,
      reasons_json TEXT NOT NULL,
      evidence_json TEXT,
      acquisition_sha256 TEXT NOT NULL,
      analysis_sha256 TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(job_id, run_id, subject_type, subject_id))""",
    "CREATE INDEX IF NOT EXISTS ix_policy_dec_job ON evidence_policy_decisions(job_id, decision_id)",
    """CREATE TRIGGER IF NOT EXISTS evidence_policy_decisions_no_update
       BEFORE UPDATE ON evidence_policy_decisions
       BEGIN SELECT RAISE(ABORT, 'evidence_policy_decisions is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS evidence_policy_decisions_no_delete
       BEFORE DELETE ON evidence_policy_decisions
       BEGIN SELECT RAISE(ABORT, 'evidence_policy_decisions is append-only'); END""",
)


def ensure(connection) -> None:
    for statement in DDL:
        connection.execute(statement)


def latest_run(connection, job_id: str, acquisition_sha256: str, analysis_sha256: str) -> list[dict]:
    """Rows of the most recent run that evaluated EXACTLY these artifacts; [] when stale/absent."""
    ensure(connection)
    row = connection.execute(
        "SELECT run_id FROM evidence_policy_decisions WHERE job_id=? AND acquisition_sha256=? AND analysis_sha256=? "
        "ORDER BY decision_id DESC LIMIT 1", (job_id, acquisition_sha256, analysis_sha256)).fetchone()
    if row is None:
        return []
    out = []
    for r in connection.execute(
            "SELECT * FROM evidence_policy_decisions WHERE job_id=? AND run_id=? ORDER BY decision_id",
            (job_id, row[0])):
        d = dict(r)
        d["reasons"] = json.loads(d.pop("reasons_json"))
        d["components"] = json.loads(d.pop("components_json") or "null")
        d["evidence"] = json.loads(d.pop("evidence_json") or "null")
        out.append(d)
    return out


def insert_run(connection, job_id: str, run_id: str, policy_version: str, scoring_version: str,
               acquisition_sha256: str, analysis_sha256: str, rows: list[dict], created_at: str) -> bool:
    """Append a run once. Returns False when this exact run already exists (idempotent replay)."""
    ensure(connection)
    if connection.execute("SELECT 1 FROM evidence_policy_decisions WHERE job_id=? AND run_id=? LIMIT 1",
                          (job_id, run_id)).fetchone():
        return False
    for r in rows:
        connection.execute(
            "INSERT INTO evidence_policy_decisions(job_id,run_id,policy_version,scoring_version,subject_type,"
            "subject_id,state,score,claim_class,independent_groups,components_json,reasons_json,evidence_json,"
            "acquisition_sha256,analysis_sha256,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, run_id, policy_version, scoring_version, r["subject_type"], r["subject_id"], r["state"],
             r.get("score"), r.get("claim_class"), r.get("independent_groups"),
             json.dumps(r.get("components"), sort_keys=True), json.dumps(r.get("reasons", [])),
             json.dumps(r.get("evidence"), sort_keys=True), acquisition_sha256, analysis_sha256, created_at))
    return True
