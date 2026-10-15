"""Job-scoped temporary workspace inside the repository root, cleaned up at terminal state.

Layout: ``<project root>/tmp/jobs/<job_id>/`` (git-ignored). Only *intermediate* files live here
(scratch manifests, policy inputs). Durable outputs — sources, analysis, evidence packages,
scripts, provenance — stay in the artifact store and database and are never touched by cleanup.

Safety properties:
  * ``job_id`` must be a UUID: no separators, no ``..``, nothing that can name another directory.
  * every path is resolved and must stay inside that job's directory (symlinks included);
  * cleanup removes exactly one job directory, is idempotent, and only acts on terminal jobs
    (``sweep``) or on an explicit ``cleanup`` call for a terminal job;
  * a failed removal is recorded (``cleanup_error``) and retried by the next sweep; success is
    only recorded after the directory is verified gone.
State is kept in its own table (created on demand) so no schema migration is required.
"""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.database import connect

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CLEANUP_LOCK = threading.Lock()      # serialises removals within one process; removal itself is idempotent
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

_DDL = """CREATE TABLE IF NOT EXISTS job_workspaces (
  job_id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  cleaned_at TEXT,
  cleanup_attempts INTEGER NOT NULL DEFAULT 0,
  cleanup_error TEXT
)"""


class WorkspaceError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def default_workspace_root() -> Path:
    configured = os.environ.get("DEFENCE_STUDIO_WORKSPACE_ROOT")
    return Path(configured) if configured else PROJECT_ROOT / "tmp" / "jobs"


class JobWorkspaceService:
    def __init__(self, database_file: Path, root: Path | None = None):
        self.database_file = Path(database_file)
        self.root = Path(root) if root else default_workspace_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        with closing(connect(self.database_file)) as c:
            c.execute(_DDL)
            c.commit()

    # -- path handling -------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        try:
            canonical = str(uuid.UUID(str(job_id)))
        except ValueError:
            raise WorkspaceError("job_id must be a UUID") from None
        path = (self.root / canonical).resolve()
        if path.parent != self.root:
            raise WorkspaceError("workspace path escapes the workspace root")
        return path

    def create(self, job_id: str) -> Path:
        path = self.job_dir(job_id)
        path.mkdir(parents=True, exist_ok=True)
        with closing(connect(self.database_file)) as c:
            c.execute("INSERT INTO job_workspaces(job_id,path,created_at) VALUES(?,?,?) "
                      "ON CONFLICT(job_id) DO UPDATE SET cleaned_at=NULL, cleanup_error=NULL",
                      (path.name, str(path), _now()))
            c.commit()
        return path

    def resolve(self, job_id: str, relative: str) -> Path:
        base = self.job_dir(job_id)
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or not relative:
            raise WorkspaceError("workspace-relative path required (no absolute paths or '..')")
        target = (base / rel).resolve()
        if base != target and base not in target.parents:
            raise WorkspaceError("path escapes the job workspace")
        return target

    def write_text(self, job_id: str, relative: str, text: str) -> Path:
        self.create(job_id)
        target = self.resolve(job_id, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    # -- cleanup -------------------------------------------------------------
    def _job_status(self, job_id: str) -> str | None:
        with closing(connect(self.database_file)) as c:
            row = c.execute("SELECT status FROM workflow_jobs WHERE job_id=?", (job_id,)).fetchone()
        return row[0] if row else None

    def cleanup(self, job_id: str, *, force: bool = False) -> dict:
        """Remove one job's workspace. Refuses non-terminal jobs unless ``force`` (not used by sweep)."""
        path = self.job_dir(job_id)
        status = self._job_status(path.name)
        if not force and status is not None and status not in TERMINAL_STATUSES:
            raise WorkspaceError(f"job is {status}; workspace is only removed at a terminal state")
        error = None
        try:
            with _CLEANUP_LOCK:
                if path.exists() or path.is_symlink():
                    if path.is_symlink():
                        path.unlink()
                    else:
                        try:
                            shutil.rmtree(path)
                        except FileNotFoundError:
                            pass                      # another process removed it first: fine
                if path.exists():
                    raise OSError("workspace still present after removal")
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
        with closing(connect(self.database_file)) as c:
            c.execute("INSERT INTO job_workspaces(job_id,path,created_at,cleaned_at,cleanup_attempts,cleanup_error) "
                      "VALUES(?,?,?,?,1,?) ON CONFLICT(job_id) DO UPDATE SET "
                      "cleaned_at=excluded.cleaned_at, cleanup_attempts=cleanup_attempts+1, "
                      "cleanup_error=excluded.cleanup_error",
                      (path.name, str(path), _now(), None if error else _now(), error))
            c.commit()
        return {"job_id": path.name, "cleaned": error is None, "error": error}

    def sweep(self) -> list[dict]:
        """Clean every workspace whose job is terminal, plus previously failed cleanups.

        Safe to call repeatedly and after a crash/restart: it works from the job table and the
        directory listing only. Directories whose job is unknown are left alone (reported)."""
        results = []
        with closing(connect(self.database_file)) as c:
            known = {r[0]: r[1] for r in c.execute("SELECT job_id,status FROM workflow_jobs")}
        for child in sorted(self.root.iterdir()) if self.root.exists() else []:
            if not child.is_dir() and not child.is_symlink():
                continue
            try:
                job_id = str(uuid.UUID(child.name))
            except ValueError:
                continue                       # not ours: never touch
            status = known.get(job_id)
            if status in TERMINAL_STATUSES:
                results.append(self.cleanup(job_id))
            elif status is None:
                results.append({"job_id": job_id, "cleaned": False, "error": "unknown job; left in place"})
        return results

    def status(self, job_id: str) -> dict:
        path = self.job_dir(job_id)
        with closing(connect(self.database_file)) as c:
            row = c.execute("SELECT * FROM job_workspaces WHERE job_id=?", (path.name,)).fetchone()
        return {"job_id": path.name, "exists": path.exists(),
                "cleaned_at": row["cleaned_at"] if row else None,
                "cleanup_attempts": row["cleanup_attempts"] if row else 0,
                "cleanup_error": row["cleanup_error"] if row else None}
