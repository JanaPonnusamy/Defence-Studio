"""Defence Studio database safety tool for the two-PC Git workflow.

Subcommands (all operate on the project's default DB unless --db is given):

  validate    Integrity-check the DB and print its schema version, job count,
              the target job's status, and the persisted review-decision count.
              Exits non-zero if integrity fails.

  snapshot    Produce a WAL-safe, single-file snapshot of the DB using SQLite's
              backup API (safe even if the DB is in WAL mode) to a destination
              path. Use this to prepare the DB for a Git commit AFTER stopping
              the app. Verifies the snapshot's integrity before finishing.

  fingerprint Print a stable SHA-256 of the DB's logical content (via the
              backup API into memory) so you can tell whether your local DB
              differs from the one about to arrive from Git -- BEFORE you pull.
              Two DBs with the same fingerprint are byte-identical snapshots.

This tool never writes review decisions and never mutates the source DB.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "defence_studio.sqlite3"
TARGET_JOB = "41714114-a85e-4ccf-a591-b254110c52d5"


def _open_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _decision_count(con: sqlite3.Connection) -> int:
    total = 0
    for table in ("source_reviews", "claim_reviews"):
        try:
            total += con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            pass
    return total


def cmd_validate(db: Path) -> int:
    if not db.exists():
        print(f"error: database not found: {db}", file=sys.stderr)
        return 2
    con = _open_ro(db)
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    version = con.execute("SELECT version FROM schema_info WHERE singleton = 1").fetchone()[0]
    jobs = con.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0]
    target = con.execute(
        "SELECT status FROM workflow_jobs WHERE job_id = ?", (TARGET_JOB,)
    ).fetchone()
    decisions = _decision_count(con)
    con.close()
    print(f"database:          {db}")
    print(f"integrity_check:   {integrity}")
    print(f"schema version:    {version}")
    print(f"jobs:              {jobs}")
    print(f"target job status: {target['status'] if target else '(absent)'}")
    print(f"review decisions:  {decisions}")
    return 0 if integrity == "ok" else 1


def cmd_fingerprint(db: Path) -> int:
    if not db.exists():
        print(f"error: database not found: {db}", file=sys.stderr)
        return 2
    # Back up into an in-memory DB, then serialize to bytes for a content hash
    # that ignores incidental file-level differences (WAL framing, page order).
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    mem = sqlite3.connect(":memory:")
    with mem:
        src.backup(mem)
    data = mem.serialize() if hasattr(mem, "serialize") else None
    src.close()
    if data is None:  # very old Python without Connection.serialize
        data = db.read_bytes()
    print(hashlib.sha256(data).hexdigest())
    mem.close()
    return 0


def cmd_snapshot(db: Path, dest: Path, force: bool) -> int:
    if not db.exists():
        print(f"error: source database not found: {db}", file=sys.stderr)
        return 2
    if dest.exists() and not force:
        print(
            f"error: destination exists: {dest}\n"
            f"       refusing to overwrite without --force (never blindly clobber a DB).",
            file=sys.stderr,
        )
        return 3
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    with out:
        src.backup(out)
    out.execute("PRAGMA journal_mode = DELETE")
    out.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    integrity = out.execute("PRAGMA integrity_check").fetchone()[0]
    out.close()
    src.close()
    print(f"snapshot: {db} -> {dest}  (integrity: {integrity})")
    return 0 if integrity == "ok" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Defence Studio DB safety tool")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="DB path (default: in-repo development DB)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("fingerprint")
    snap = sub.add_parser("snapshot")
    snap.add_argument("dest", type=Path)
    snap.add_argument("--force", action="store_true", help="overwrite an existing destination")
    args = parser.parse_args(argv)

    if args.command == "validate":
        return cmd_validate(args.db)
    if args.command == "fingerprint":
        return cmd_fingerprint(args.db)
    if args.command == "snapshot":
        return cmd_snapshot(args.db, args.dest, args.force)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
