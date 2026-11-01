"""Storage lifecycle: artifact registry, retention, dry-run and confirmed cleanup.

Safety model (see docs/STORAGE_AUDIT_20261101.md):

* Only files under the single *managed* root (the core storage root) can ever be
  deleted. Module stores (``standalone/*/storage``) are *observed*: measured,
  never touched.
* Only a short list of temporary retention classes can become deletable. Every
  other class, including ``UNCLASSIFIED``, is locked to "preserve" and cannot be
  unlocked through configuration.
* A file named anywhere in the database (any text column) or inside a stored
  JSON/text artifact is protected, whatever its class. Unknown means keep.
* Files of a job that is not in a terminal state, or that has running stages,
  worker claims or pending renders, are protected.
* The retention clock starts at the latest of file mtime, first registry
  sighting, and (for rejected images) the rejection time.
* Deletion happens only for items of a recent dry run, after the exact phrase
  ``DELETE <n> FILES``. Each item is re-classified, re-resolved under the root,
  checked for symlinks/reparse points and re-hashed before ``unlink``.
* Ownerless files (orphans) are reported only; the API never deletes them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat as stat_mod
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

STORAGE_DDL = """
CREATE TABLE IF NOT EXISTS storage_artifacts (
    artifact_id TEXT PRIMARY KEY,
    root_key TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    job_id TEXT,
    job_kind TEXT,
    module TEXT NOT NULL,
    category TEXT NOT NULL,
    retention_class TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    preserved INTEGER NOT NULL,
    size_bytes INTEGER,
    mtime_ns INTEGER,
    sha256 TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_accessed_at TEXT,
    expires_at TEXT,
    deleted_at TEXT,
    UNIQUE (root_key, rel_path)
);
CREATE INDEX IF NOT EXISTS ix_storage_artifacts_job ON storage_artifacts(job_id);
CREATE TABLE IF NOT EXISTS storage_retention_policy (
    retention_class TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    days INTEGER,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS storage_cleanup_runs (
    run_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    dry_run_id TEXT UNIQUE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    candidate_bytes INTEGER NOT NULL DEFAULT 0,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    reclaimed_bytes INTEGER NOT NULL DEFAULT 0,
    confirm_text TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS storage_cleanup_items (
    run_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    root_key TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    size_bytes INTEGER,
    sha256 TEXT,
    retention_class TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (run_id, artifact_id)
);
"""

MANAGED_ROOT_KEY = "core"
DRY_RUN_TTL = timedelta(minutes=30)
AFTER_VERIFIED_RENDER = "AFTER_VERIFIED_RENDER"

# class -> (default mode, default days, locked)
RETENTION_DEFAULTS: dict[str, tuple[str, int | None, bool]] = {
    "TEMP_SEARCH_RESPONSE": ("DAYS", 3, False),
    "RAW_ARTICLE_HTML": ("DAYS", 7, False),
    "TEMP_THUMBNAIL": ("DAYS", 7, False),
    "REJECTED_IMAGE_TEMP": ("DAYS", 7, False),
    "FAILED_RENDER_INTERMEDIATE": ("DAYS", 3, False),
    "TEMP_LOG": ("DAYS", 30, False),
    "RENDER_INTERMEDIATE": (AFTER_VERIFIED_RENDER, None, False),
    "TEMP_AUDIO_CHUNK": (AFTER_VERIFIED_RENDER, None, False),
    "RESEARCH_EVIDENCE": ("PRESERVE", None, True),
    "RESEARCH_ARTIFACT": ("PRESERVE", None, True),
    "SCRIPT": ("PRESERVE", None, True),
    "ACCEPTED_IMAGE": ("PRESERVE", None, True),
    "IMAGE_CANDIDATE_PENDING": ("PRESERVE", None, True),
    "FINAL_VIDEO": ("PRESERVE", None, True),
    "RENDER_OUTPUT": ("PRESERVE", None, True),
    "AUDIO": ("PRESERVE", None, True),
    "ASSET": ("PRESERVE", None, True),
    "UNCLASSIFIED": ("PRESERVE", None, True),
    "ORPHAN": ("PRESERVE", None, True),
}
TEMPORARY_CLASSES = frozenset(k for k, (_, _, locked) in RETENTION_DEFAULTS.items() if not locked)

WORKFLOW_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
IMAGE_JOB_TERMINAL = frozenset({"COMPLETED", "COMPLETED_WITH_SHORTFALL", "FAILED", "CANCELLED"})
ACTIVE_STAGE = frozenset({"RUNNING", "QUEUED", "CLAIMED", "IN_PROGRESS"})
ACTIVE_INTEGRATION = frozenset({"QUEUED", "RUNNING", "PENDING", "IN_PROGRESS", "INTERRUPTED"})

_CATEGORY_CLASS = {
    "evidence": "RESEARCH_EVIDENCE",
    "research": "RESEARCH_ARTIFACT",
    "discovery": "RESEARCH_ARTIFACT",
    "scripts": "SCRIPT",
    "package": "FINAL_VIDEO",
    "packages": "FINAL_VIDEO",
    "approvals": "FINAL_VIDEO",
    "render": "RENDER_OUTPUT",
    "renders": "RENDER_OUTPUT",
    "qc": "RENDER_OUTPUT",
    "visual": "RENDER_OUTPUT",
    "scenes": "RENDER_OUTPUT",
    "audio": "AUDIO",
    "audio-sets": "AUDIO",
    "assets": "ASSET",
}
_TMP_SEGMENTS = frozenset({"tmp", "temp", "work", "intermediate", "intermediates"})
_PATH_TOKEN = re.compile(r"(?:jobs|image-research)/[^\s\"'<>\\|?*]+")
_NAME_TOKEN = re.compile(
    r"[A-Za-z0-9._-]{4,}\.(?:json|html?|txt|md|mp4|mkv|mov|webm|wav|mp3|m4a|aac|flac|ogg|"
    r"jpe?g|png|webp|gif|svg|srt|vtt|ass|bin|part|tmp|log)\b", re.IGNORECASE)
_REFERENCE_FILE_EXT = frozenset({".json", ".md", ".txt", ".srt", ".vtt", ".ass"})
_MAX_REFERENCE_READ = 20 * 1024 * 1024
# Columns that *classify* a file rather than protect it.
_CLASSIFYING_COLUMNS = {("image_research_candidates", "stored_relative_path")}


class StorageError(Exception):
    def __init__(self, status_code: int, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass
class _Entry:
    rel_path: str
    size: int
    mtime_ns: int
    job_id: str | None = None
    job_kind: str | None = None
    module: str = "core"
    category: str = ""
    retention_class: str = "UNCLASSIFIED"
    owner_id: str | None = None
    clock_start: datetime | None = None
    blocked: list[str] = field(default_factory=list)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat_mod, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def default_observed_roots(project_root: Path) -> dict[str, Path]:
    base = project_root / "standalone"
    if not base.is_dir():
        return {}
    return {d.name: d / "storage" for d in sorted(base.iterdir()) if (d / "storage").is_dir()}


class StorageLifecycleService:
    def __init__(self, database_file, managed_root, *, observed_roots: dict[str, Path] | None = None,
                 now: Callable[[], datetime] | None = None, min_free_bytes: int | None = None):
        self.database_file = Path(database_file)
        self.root = Path(managed_root).resolve()
        self.observed_roots = {k: Path(v) for k, v in (observed_roots or {}).items()}
        self._now = now or (lambda: datetime.now(timezone.utc))
        env_floor = os.environ.get("DEFENCE_STUDIO_MIN_FREE_DISK_MB")
        self.min_free_bytes = (min_free_bytes if min_free_bytes is not None
                               else int(env_floor) * 1024 * 1024 if env_floor and env_floor.isdigit()
                               else 2 * 1024 ** 3)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ db
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_file, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _tables(connection) -> set[str]:
        return {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # ------------------------------------------------------------ retention
    def retention(self) -> dict:
        with closing(self._connect()) as c:
            overrides = {r["retention_class"]: dict(r) for r in c.execute("SELECT * FROM storage_retention_policy")}
        categories = []
        for name, (mode, days, locked) in RETENTION_DEFAULTS.items():
            row = overrides.get(name)
            if row and not locked:
                mode, days = row["mode"], row["days"]
            categories.append({
                "name": name, "mode": mode, "days": days, "preserve": mode == "PRESERVE",
                "locked": locked, "temporary": name in TEMPORARY_CLASSES,
                "updated_at": row["updated_at"] if row and not locked else None,
                "updated_by": row["updated_by"] if row and not locked else None,
            })
        return {"categories": categories}

    def _policy(self) -> dict[str, tuple[str, int | None]]:
        return {c["name"]: (c["mode"], c["days"]) for c in self.retention()["categories"]}

    def update_retention(self, owner_id: str, categories: dict) -> dict:
        if not isinstance(categories, dict) or not categories:
            raise StorageError(422, "categories must be a non-empty object")
        current = self._policy()
        updates = []
        for name, value in categories.items():
            if name not in RETENTION_DEFAULTS:
                raise StorageError(422, f"unknown retention class: {name}")
            mode_now, days_now = current[name]
            if isinstance(value, str) and value.strip().lower() == "after_verified_render":
                mode, days = AFTER_VERIFIED_RENDER, None
            elif value is None or (isinstance(value, str) and value.strip().lower() == "preserve"):
                mode, days = "PRESERVE", None
            elif isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3650:
                mode, days = "DAYS", value
            elif isinstance(value, float) and value.is_integer() and 1 <= value <= 3650:
                mode, days = "DAYS", int(value)
            else:
                raise StorageError(422, f"{name}: use 1..3650 days, null/'preserve' or 'after_verified_render'")
            if RETENTION_DEFAULTS[name][2]:
                if (mode, days) != (mode_now, days_now):
                    raise StorageError(422, f"{name} is a protected class and always preserved")
                continue
            if mode == AFTER_VERIFIED_RENDER and RETENTION_DEFAULTS[name][0] != AFTER_VERIFIED_RENDER:
                raise StorageError(422, f"{name}: 'after_verified_render' applies to render intermediates only")
            if (mode, days) != (mode_now, days_now):
                updates.append((name, mode, days))
        if updates:
            stamp = _iso(self._now())
            with closing(self._connect()) as c, c:
                for name, mode, days in updates:
                    c.execute(
                        "INSERT INTO storage_retention_policy(retention_class, mode, days, updated_at, updated_by) "
                        "VALUES(?,?,?,?,?) ON CONFLICT(retention_class) DO UPDATE SET mode=excluded.mode, "
                        "days=excluded.days, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                        (name, mode, days, stamp, owner_id))
        return self.retention()

    # ------------------------------------------------------------- context
    def _context(self, connection) -> dict:
        tables = self._tables(connection)
        ctx = {"workflow": {}, "image_jobs": {}, "image_candidates": {}, "verified_render": set(),
               "failed_render": set(), "busy": set(), "refs": set(), "names": set()}
        if "workflow_jobs" in tables:
            for r in connection.execute("SELECT job_id, owner_id, status FROM workflow_jobs"):
                ctx["workflow"][r["job_id"]] = (r["owner_id"], r["status"])
        if "workflow_stages" in tables:
            marks = ",".join("?" * len(ACTIVE_STAGE))
            for r in connection.execute(f"SELECT DISTINCT job_id FROM workflow_stages WHERE status IN ({marks})",
                                        tuple(ACTIVE_STAGE)):
                ctx["busy"].add(r[0])
        if "worker_claims" in tables:
            cols = {r[1] for r in connection.execute("PRAGMA table_info(worker_claims)")}
            if "job_id" in cols:
                ctx["busy"].update(r[0] for r in connection.execute("SELECT DISTINCT job_id FROM worker_claims"))
        if "integration_artifacts" in tables:
            qc_ok = {r[0] for r in connection.execute(
                "SELECT DISTINCT job_id FROM integration_artifacts WHERE kind='qc_report' AND status='SUCCEEDED'")}
            for r in connection.execute("SELECT job_id, kind, status, uri, sha256 FROM integration_artifacts"):
                if r["status"] in ACTIVE_INTEGRATION:
                    ctx["busy"].add(r["job_id"])
                if r["kind"] == "render" and r["status"] == "FAILED":
                    ctx["failed_render"].add(r["job_id"])
                if (r["kind"] == "render" and r["status"] == "SUCCEEDED" and r["job_id"] in qc_ok
                        and r["uri"] and r["sha256"] and self._verify_file(r["uri"], r["sha256"])):
                    ctx["verified_render"].add(r["job_id"])
        if "image_research_jobs" in tables:
            for r in connection.execute("SELECT job_id, owner_id, status FROM image_research_jobs"):
                ctx["image_jobs"][r["job_id"]] = (r["owner_id"], r["status"])
        if "image_research_candidates" in tables:
            reviewed = {}
            if "image_research_reviews" in tables:
                cols = {r[1] for r in connection.execute("PRAGMA table_info(image_research_reviews)")}
                stamp_col = next((c for c in ("created_at", "reviewed_at", "decided_at") if c in cols), None)
                if stamp_col and "image_id" in cols:
                    for r in connection.execute(
                            f"SELECT image_id, MAX({stamp_col}) AS ts FROM image_research_reviews GROUP BY image_id"):
                        reviewed[r["image_id"]] = r["ts"]
            for r in connection.execute(
                    "SELECT image_id, job_id, review_status, stored_relative_path FROM image_research_candidates "
                    "WHERE stored_relative_path IS NOT NULL"):
                ctx["image_candidates"][r["stored_relative_path"].replace("\\", "/")] = (
                    r["review_status"], r["job_id"], reviewed.get(r["image_id"]))
        # Generic reference scan of every text value in the database.
        for table in sorted(tables):
            if table.startswith("storage_") or table == "sqlite_sequence":
                continue
            cols = [r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')
                    if (table, r[1]) not in _CLASSIFYING_COLUMNS]
            if not cols:
                continue
            select = ", ".join(f'"{c}"' for c in cols)
            for row in connection.execute(f'SELECT {select} FROM "{table}"'):
                for value in row:
                    if isinstance(value, str) and len(value) >= 5:
                        self._collect_refs(value, ctx)
        return ctx

    @staticmethod
    def _collect_refs(text: str, ctx: dict) -> None:
        for token in _PATH_TOKEN.findall(text.replace("\\", "/")):
            ctx["refs"].add(token.rstrip(".,;)]}"))
        for token in _NAME_TOKEN.findall(text):
            ctx["names"].add(token.lower())

    def _verify_file(self, uri: str, sha256: str) -> bool:
        try:
            path = self._safe_path(uri)
            return path.is_file() and _sha256(path) == sha256
        except (StorageError, OSError):
            return False

    def _safe_path(self, rel_path: str) -> Path:
        rel = rel_path.replace("\\", "/")
        if not rel or rel.startswith("/") or ":" in rel or any(p in ("..", "") for p in rel.split("/")):
            raise StorageError(400, f"unsafe path: {rel_path!r}")
        candidate = self.root.joinpath(*rel.split("/"))
        resolved = candidate.resolve()
        if resolved != candidate.absolute() and os.path.normcase(str(resolved)) != os.path.normcase(str(candidate.absolute())):
            raise StorageError(400, f"path resolves elsewhere (link): {rel_path!r}")
        if not resolved.is_relative_to(self.root):
            raise StorageError(400, f"path escapes the managed root: {rel_path!r}")
        return resolved

    # ---------------------------------------------------------------- scan
    def _walk(self) -> list[_Entry]:
        entries = []
        if not self.root.is_dir():
            return entries
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            base = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not (base / d).is_symlink()]
            for name in filenames:
                path = base / name
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if not stat_mod.S_ISREG(st.st_mode) or _is_reparse(st):
                    continue
                rel = path.relative_to(self.root).as_posix()
                entries.append(_Entry(rel_path=rel, size=st.st_size, mtime_ns=st.st_mtime_ns))
        return entries

    def _classify(self, entry: _Entry, ctx: dict, references: set[str]) -> None:
        parts = entry.rel_path.split("/")
        name = parts[-1].lower()
        suffix = Path(name).suffix
        mtime = datetime.fromtimestamp(entry.mtime_ns / 1e9, tz=timezone.utc)
        entry.clock_start = mtime
        if parts[0] == "jobs" and len(parts) >= 3:
            entry.job_id, entry.job_kind = parts[1], "workflow"
            entry.category = parts[2] if len(parts) > 3 else "(job root)"
            entry.module = "core:" + (parts[2] if len(parts) > 3 else "job")
            info = ctx["workflow"].get(entry.job_id)
            if info is None:
                entry.retention_class = "ORPHAN"
                entry.blocked.append("ORPHAN_REPORT_ONLY")
                return
            entry.owner_id, status = info
            segments = {p.lower() for p in parts[2:-1]}
            if suffix == ".log" or "logs" in segments:
                cls = "TEMP_LOG"
            elif "search-cache" in segments:
                cls = "TEMP_SEARCH_RESPONSE"
            elif "frames" in segments or (segments & _TMP_SEGMENTS) or suffix in (".part", ".tmp"):
                cls = ("FAILED_RENDER_INTERMEDIATE" if entry.job_id in ctx["failed_render"] or status in ("FAILED", "CANCELLED")
                       else "RENDER_INTERMEDIATE")
            elif "chunks" in segments and entry.category == "audio":
                cls = "TEMP_AUDIO_CHUNK"
            elif "thumbnails" in segments:
                cls = "TEMP_THUMBNAIL"
            elif entry.category == "research" and suffix in (".html", ".htm"):
                cls = "RAW_ARTICLE_HTML"
            else:
                cls = _CATEGORY_CLASS.get(entry.category, "UNCLASSIFIED")
            entry.retention_class = cls
            if status not in WORKFLOW_TERMINAL:
                entry.blocked.append(f"JOB_NOT_TERMINAL:{status}")
            if entry.job_id in ctx["busy"]:
                entry.blocked.append("JOB_HAS_ACTIVE_WORK")
            if cls in ("RENDER_INTERMEDIATE", "TEMP_AUDIO_CHUNK") and entry.job_id not in ctx["verified_render"]:
                entry.blocked.append("NO_VERIFIED_RENDER")
        elif parts[0] == "image-research" and len(parts) >= 3:
            entry.job_id, entry.job_kind = parts[1], "image_research"
            entry.category, entry.module = "image-research", "image-research"
            job = ctx["image_jobs"].get(entry.job_id)
            candidate = ctx["image_candidates"].get(entry.rel_path)
            if job is None or candidate is None:
                entry.retention_class = "ORPHAN"
                entry.blocked.append("ORPHAN_REPORT_ONLY")
                return
            entry.owner_id, status = job
            review_status, _, reviewed_at = candidate
            if review_status == "ACCEPTED":
                entry.retention_class = "ACCEPTED_IMAGE"
            elif review_status == "REJECTED":
                entry.retention_class = "REJECTED_IMAGE_TEMP"
                stamp = _parse(reviewed_at)
                if stamp is None:
                    entry.blocked.append("REJECTION_TIME_UNKNOWN")
                else:
                    entry.clock_start = max(entry.clock_start, stamp)
            else:
                entry.retention_class = "IMAGE_CANDIDATE_PENDING"
            if status not in IMAGE_JOB_TERMINAL:
                entry.blocked.append(f"JOB_NOT_TERMINAL:{status}")
        else:
            entry.module, entry.category = "core:unmanaged", parts[0] if len(parts) > 1 else "(root)"
            entry.retention_class = "UNCLASSIFIED"
        if entry.rel_path in references or name in ctx["names"]:
            entry.blocked.append("REFERENCED")

    def _scan(self, connection) -> tuple[list[_Entry], dict]:
        ctx = self._context(connection)
        entries = self._walk()
        # Reference scan of stored JSON/text artifacts (the referrer never protects itself).
        file_refs: dict[str, set[str]] = {}
        for entry in entries:
            if Path(entry.rel_path).suffix.lower() in _REFERENCE_FILE_EXT and entry.size <= _MAX_REFERENCE_READ:
                try:
                    text = (self.root / entry.rel_path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                local = {"refs": set(), "names": set()}
                self._collect_refs(text, local)
                own = Path(entry.rel_path).name.lower()
                local["names"].discard(own)
                local["refs"].discard(entry.rel_path)
                ctx["names"].update(local["names"])
                file_refs[entry.rel_path] = local["refs"]
        references = set(ctx["refs"])
        for refs in file_refs.values():
            references.update(refs)
        for entry in entries:
            self._classify(entry, ctx, references)
        return entries, ctx

    def _sync(self, connection, entries: list[_Entry]) -> dict[str, dict]:
        """Upsert registry rows; hash only new or changed files. Returns rows by rel_path."""
        now = self._now()
        stamp = _iso(now)
        policy = self._policy()
        existing = {r["rel_path"]: dict(r) for r in connection.execute(
            "SELECT * FROM storage_artifacts WHERE root_key=?", (MANAGED_ROOT_KEY,))}
        seen = set()
        rows = {}
        for e in entries:
            seen.add(e.rel_path)
            old = existing.get(e.rel_path)
            sha = old["sha256"] if old and old["size_bytes"] == e.size and old["mtime_ns"] == e.mtime_ns else None
            if sha is None:
                try:
                    sha = _sha256(self.root / e.rel_path)
                except OSError:
                    sha = None  # locked by another process; recorded as unknown, never deletable
                    e.blocked.append("HASH_UNAVAILABLE")
            first_seen = old["first_seen_at"] if old and old["lifecycle_state"] not in ("DELETED",) else stamp
            start = max(e.clock_start or now, _parse(first_seen) or now)
            mode, days = policy[e.retention_class]
            expires = _iso(start + timedelta(days=days)) if mode == "DAYS" and days else None
            preserved = e.retention_class not in TEMPORARY_CLASSES or mode == "PRESERVE"
            if preserved:
                state = "PRESERVED"
            elif mode == AFTER_VERIFIED_RENDER:
                state = "EXPIRED" if "NO_VERIFIED_RENDER" not in e.blocked else "TEMPORARY"
            else:
                state = "EXPIRED" if expires and _parse(expires) <= now else "TEMPORARY"
            row = {
                "artifact_id": old["artifact_id"] if old else hashlib.sha256(
                    f"{MANAGED_ROOT_KEY}:{e.rel_path}".encode()).hexdigest()[:32],
                "root_key": MANAGED_ROOT_KEY, "rel_path": e.rel_path, "job_id": e.job_id, "job_kind": e.job_kind,
                "module": e.module, "category": e.category, "retention_class": e.retention_class,
                "lifecycle_state": state, "preserved": int(preserved), "size_bytes": e.size, "mtime_ns": e.mtime_ns,
                "sha256": sha, "first_seen_at": first_seen, "last_seen_at": stamp,
                "last_accessed_at": old["last_accessed_at"] if old else None, "expires_at": expires, "deleted_at": None,
            }
            connection.execute(
                "INSERT INTO storage_artifacts VALUES(:artifact_id,:root_key,:rel_path,:job_id,:job_kind,:module,"
                ":category,:retention_class,:lifecycle_state,:preserved,:size_bytes,:mtime_ns,:sha256,:first_seen_at,"
                ":last_seen_at,:last_accessed_at,:expires_at,:deleted_at) ON CONFLICT(root_key, rel_path) DO UPDATE SET "
                "job_id=excluded.job_id, job_kind=excluded.job_kind, module=excluded.module, category=excluded.category, "
                "retention_class=excluded.retention_class, lifecycle_state=excluded.lifecycle_state, "
                "preserved=excluded.preserved, size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns, "
                "sha256=excluded.sha256, first_seen_at=excluded.first_seen_at, last_seen_at=excluded.last_seen_at, "
                "expires_at=excluded.expires_at, deleted_at=NULL", row)
            rows[e.rel_path] = row
        for rel, old in existing.items():
            if rel not in seen and old["lifecycle_state"] not in ("DELETED", "MISSING"):
                connection.execute("UPDATE storage_artifacts SET lifecycle_state='MISSING', last_seen_at=? "
                                   "WHERE artifact_id=?", (stamp, old["artifact_id"]))
        return rows

    def _eligibility(self, entry: _Entry, row: dict, owner_id: str | None) -> str | None:
        """None if deletable for this owner, otherwise the first blocking reason."""
        if entry.retention_class == "ORPHAN":
            return "ORPHAN_REPORT_ONLY"
        if entry.retention_class not in TEMPORARY_CLASSES:
            return "PROTECTED_CLASS"
        if entry.blocked:
            return entry.blocked[0]
        if row["lifecycle_state"] != "EXPIRED":
            return "RETENTION_NOT_ELAPSED" if row["lifecycle_state"] == "TEMPORARY" else "PRESERVED_BY_POLICY"
        if row["sha256"] is None:
            return "HASH_UNAVAILABLE"
        if owner_id is not None and entry.owner_id != owner_id:
            return "NOT_OWNER"
        return None

    # --------------------------------------------------------------- views
    def summary(self) -> dict:
        with self._lock, closing(self._connect()) as c:
            with c:
                entries, _ = self._scan(c)
                rows = self._sync(c, entries)
            runs = [dict(r) for r in c.execute("SELECT * FROM storage_cleanup_runs ORDER BY started_at DESC")]
        by_module: dict[str, dict] = {}
        by_class: dict[str, dict] = {}
        permanent = temporary = orphan = unclassified = total = 0
        for e in entries:
            row = rows[e.rel_path]
            total += e.size
            m = by_module.setdefault(e.module, {"module": e.module, "bytes": 0, "count": 0})
            m["bytes"] += e.size; m["count"] += 1
            k = by_class.setdefault(e.retention_class, {"bytes": 0, "count": 0})
            k["bytes"] += e.size; k["count"] += 1
            if e.retention_class == "ORPHAN":
                orphan += e.size
            elif e.retention_class == "UNCLASSIFIED":
                unclassified += e.size
            elif row["preserved"]:
                permanent += e.size
            else:
                temporary += e.size
        roots = [self._root_report("core (managed)", self.root, total, len(entries))]
        for key, path in self.observed_roots.items():
            size, count = self._measure(path)
            by_module.setdefault(f"module:{key}", {"module": f"module:{key} (observed, never cleaned)",
                                                   "bytes": size, "count": count})
            roots.append(self._root_report(f"{key} (observed)", path, size, count))
        largest = sorted(entries, key=lambda e: -e.size)[:15]
        last = runs[0] if runs else None
        return {
            "measured_at": _iso(self._now()),
            "total_bytes": total, "file_count": len(entries),
            "permanent_bytes": permanent, "temporary_bytes": temporary,
            "orphan_bytes": orphan, "unclassified_bytes": unclassified,
            "observed_module_bytes": sum(r["bytes"] for r in roots[1:]),
            "by_module": sorted(by_module.values(), key=lambda m: -m["bytes"]),
            "by_retention_class": by_class,
            "roots": roots,
            "largest": [{"artifact_id": rows[e.rel_path]["artifact_id"], "path_relative": e.rel_path,
                         "module": e.module, "bytes": e.size, "lifecycle_state": rows[e.rel_path]["lifecycle_state"]}
                        for e in largest],
            "last_cleanup": self._history_row(last) if last else None,
            "reclaimed_bytes_total": sum(r["reclaimed_bytes"] for r in runs if r["kind"] == "EXECUTE"),
            "cleanup_errors": [self._history_row(r) for r in runs if r["failed_count"] or r["error"]][:10],
            "notes": ["Observed module stores are measured only; their files are never registered or deleted.",
                      "last_accessed_at is not tracked (filesystem access times are unreliable)."],
        }

    def _root_report(self, label: str, path: Path, size: int, count: int) -> dict:
        try:
            usage = shutil.disk_usage(path if path.exists() else path.parent)
            free, disk_total = usage.free, usage.total
        except OSError:
            free = disk_total = None
        return {"root": label, "path": str(path), "bytes": size, "files": count,
                "disk_free_bytes": free, "disk_total_bytes": disk_total,
                "min_free_bytes": self.min_free_bytes,
                "below_floor": None if free is None else free < self.min_free_bytes}

    @staticmethod
    def _measure(path: Path) -> tuple[int, int]:
        size = count = 0
        for dirpath, _, files in os.walk(path, followlinks=False):
            for f in files:
                try:
                    size += os.lstat(os.path.join(dirpath, f)).st_size; count += 1
                except OSError:
                    pass
        return size, count

    def capacity(self, required_bytes: int = 0) -> dict:
        usage = shutil.disk_usage(self.root if self.root.exists() else self.root.parent)
        ok = usage.free - required_bytes >= self.min_free_bytes
        return {"disk_free_bytes": usage.free, "required_bytes": required_bytes,
                "min_free_bytes": self.min_free_bytes, "sufficient": ok}

    def job(self, owner_id: str, job_id: str) -> dict:
        with self._lock, closing(self._connect()) as c:
            with c:
                entries, ctx = self._scan(c)
                rows = self._sync(c, entries)
        owner = (ctx["workflow"].get(job_id) or ctx["image_jobs"].get(job_id) or (None, None))[0]
        if owner != owner_id:
            raise StorageError(404, "job not found")
        items = []
        for e in entries:
            if e.job_id != job_id:
                continue
            row = rows[e.rel_path]
            reason = self._eligibility(e, row, owner_id)
            items.append({"artifact_id": row["artifact_id"], "path_relative": e.rel_path, "module": e.module,
                          "category": e.category, "retention_class": e.retention_class,
                          "lifecycle_state": row["lifecycle_state"], "bytes": e.size, "sha256": row["sha256"],
                          "first_seen_at": row["first_seen_at"], "expires_at": row["expires_at"],
                          "preserved": bool(row["preserved"]), "deletable": reason is None,
                          "blocked_by": [reason] if reason else []})
        return {"job_id": job_id, "bytes": sum(i["bytes"] for i in items), "count": len(items), "artifacts": items}

    # ------------------------------------------------------------- cleanup
    def dry_run(self, owner_id: str) -> dict:
        started = self._now()
        run_id = str(uuid.uuid4())
        with self._lock, closing(self._connect()) as c:
            with c:
                entries, _ = self._scan(c)
                rows = self._sync(c, entries)
                candidates, skipped, preserved_counts = [], [], {}
                for e in entries:
                    row = rows[e.rel_path]
                    reason = self._eligibility(e, row, owner_id)
                    if reason is None:
                        candidates.append((e, row))
                    elif e.retention_class in TEMPORARY_CLASSES or e.retention_class == "ORPHAN":
                        skipped.append({"artifact_id": row["artifact_id"], "path_relative": e.rel_path,
                                        "module": e.module, "bytes": e.size, "retention_class": e.retention_class,
                                        "reason": reason})
                    else:
                        preserved_counts[e.retention_class] = preserved_counts.get(e.retention_class, 0) + 1
                cand_bytes = sum(e.size for e, _ in candidates)
                c.execute("INSERT INTO storage_cleanup_runs(run_id, owner_id, kind, started_at, finished_at, status, "
                          "candidate_count, candidate_bytes, skipped_count) VALUES(?,?,?,?,?,?,?,?,?)",
                          (run_id, owner_id, "DRY_RUN", _iso(started), _iso(self._now()), "COMPLETED",
                           len(candidates), cand_bytes, len(skipped)))
                for e, row in candidates:
                    c.execute("INSERT INTO storage_cleanup_items VALUES(?,?,?,?,?,?,?,?,?)",
                              (run_id, row["artifact_id"], MANAGED_ROOT_KEY, e.rel_path, e.size, row["sha256"],
                               e.retention_class, "CANDIDATE", f"expired {row['expires_at'] or 'after verified render'}"))
        return {
            "run_id": run_id, "dry_run": True, "created_at": _iso(started),
            "expires_at": _iso(started + DRY_RUN_TTL),
            "candidates": [{"artifact_id": r["artifact_id"], "path_relative": e.rel_path, "module": e.module,
                            "bytes": e.size, "sha256": r["sha256"], "retention_class": e.retention_class,
                            "lifecycle_state": r["lifecycle_state"], "expires_at": r["expires_at"]}
                           for e, r in candidates],
            "would_reclaim_bytes": cand_bytes,
            "confirm_phrase": f"DELETE {len(candidates)} FILES",
            "skipped": skipped[:500], "skipped_count": len(skipped),
            "preserved_counts": preserved_counts, "errors": [],
        }

    def execute(self, owner_id: str, dry_run_id: str, confirm: str) -> dict:
        with self._lock, closing(self._connect()) as c:
            dry = c.execute("SELECT * FROM storage_cleanup_runs WHERE run_id=? AND kind='DRY_RUN'",
                            (dry_run_id,)).fetchone()
            if dry is None or dry["owner_id"] != owner_id:
                raise StorageError(404, "dry run not found")
            previous = c.execute("SELECT * FROM storage_cleanup_runs WHERE dry_run_id=?", (dry_run_id,)).fetchone()
            if previous is not None:
                return self._execute_result(c, dict(previous), replayed=True)
            expected = f"DELETE {dry['candidate_count']} FILES"
            if dry["candidate_count"] == 0:
                raise StorageError(409, "the dry run found nothing to delete")
            if confirm != expected:
                raise StorageError(422, f"confirmation must be exactly '{expected}'")
            if self._now() - _parse(dry["started_at"]) > DRY_RUN_TTL:
                raise StorageError(409, "dry run is older than 30 minutes; run a new dry run")
            planned = [dict(r) for r in c.execute(
                "SELECT * FROM storage_cleanup_items WHERE run_id=? AND decision='CANDIDATE'", (dry_run_id,))]
            run_id = str(uuid.uuid4())
            with c:
                c.execute("INSERT INTO storage_cleanup_runs(run_id, owner_id, kind, dry_run_id, started_at, status, "
                          "candidate_count, candidate_bytes, confirm_text) VALUES(?,?,?,?,?,?,?,?,?)",
                          (run_id, owner_id, "EXECUTE", dry_run_id, _iso(self._now()), "RUNNING",
                           len(planned), sum(p["size_bytes"] or 0 for p in planned), confirm))
            # Fresh classification: job state, references and retention are re-checked now.
            with c:
                entries, _ = self._scan(c)
                rows = self._sync(c, entries)
            current = {e.rel_path: e for e in entries}
            deleted = failed = skipped = reclaimed = 0
            for item in planned:
                decision, reason = self._delete_one(item, current.get(item["rel_path"]),
                                                    rows.get(item["rel_path"]), owner_id)
                if decision == "DELETED":
                    deleted += 1; reclaimed += item["size_bytes"] or 0
                elif decision == "FAILED":
                    failed += 1
                else:
                    skipped += 1
                with c:
                    c.execute("INSERT INTO storage_cleanup_items VALUES(?,?,?,?,?,?,?,?,?)",
                              (run_id, item["artifact_id"], item["root_key"], item["rel_path"], item["size_bytes"],
                               item["sha256"], item["retention_class"], decision, reason))
                    if decision in ("DELETED", "ALREADY_GONE"):
                        c.execute("UPDATE storage_artifacts SET lifecycle_state='DELETED', deleted_at=? "
                                  "WHERE artifact_id=?", (_iso(self._now()), item["artifact_id"]))
            with c:
                c.execute("UPDATE storage_cleanup_runs SET finished_at=?, status=?, deleted_count=?, failed_count=?, "
                          "skipped_count=?, reclaimed_bytes=? WHERE run_id=?",
                          (_iso(self._now()), "COMPLETED_WITH_ERRORS" if failed else "COMPLETED",
                           deleted, failed, skipped, reclaimed, run_id))
            run = dict(c.execute("SELECT * FROM storage_cleanup_runs WHERE run_id=?", (run_id,)).fetchone())
            return self._execute_result(c, run, replayed=False)

    def _delete_one(self, item: dict, entry: _Entry | None, row: dict | None, owner_id: str) -> tuple[str, str]:
        try:
            path = self._safe_path(item["rel_path"])
        except StorageError as exc:
            return "BLOCKED", str(exc.detail)
        if entry is None:
            return ("ALREADY_GONE", "file no longer present") if not path.exists() else ("BLOCKED", "not a regular file")
        reason = self._eligibility(entry, row, owner_id)
        if reason is not None:
            return "BLOCKED", f"no longer eligible: {reason}"
        try:
            st = path.lstat()
            if not stat_mod.S_ISREG(st.st_mode) or _is_reparse(st):
                return "BLOCKED", "not a regular file"
            if st.st_size != item["size_bytes"] or _sha256(path) != item["sha256"]:
                return "BLOCKED", "content changed since the dry run"
            path.unlink()
        except FileNotFoundError:
            return "ALREADY_GONE", "file disappeared before deletion"
        except OSError as exc:
            return "FAILED", f"{exc.__class__.__name__}: {exc}"
        return "DELETED", "verified registry, job state, retention, location and sha256"

    def _execute_result(self, c, run: dict, *, replayed: bool) -> dict:
        items = [dict(r) for r in c.execute("SELECT * FROM storage_cleanup_items WHERE run_id=?", (run["run_id"],))]
        def rows(decision):
            return [{"artifact_id": i["artifact_id"], "path_relative": i["rel_path"], "bytes": i["size_bytes"],
                     "retention_class": i["retention_class"], "lifecycle_state": i["decision"], "reason": i["reason"]}
                    for i in items if i["decision"] == decision]
        failed = rows("FAILED")
        return {"run_id": run["run_id"], "dry_run": False, "dry_run_id": run["dry_run_id"], "replayed": replayed,
                "status": run["status"], "deleted": rows("DELETED"), "already_gone": rows("ALREADY_GONE"),
                "blocked": rows("BLOCKED"), "failed": failed, "reclaimed_bytes": run["reclaimed_bytes"],
                "errors": [f"{f['path_relative']}: {f['reason']}" for f in failed]}

    def history(self, owner_id: str, limit: int = 50) -> dict:
        with closing(self._connect()) as c:
            runs = [dict(r) for r in c.execute(
                "SELECT * FROM storage_cleanup_runs WHERE owner_id=? ORDER BY started_at DESC LIMIT ?",
                (owner_id, limit))]
        return {"cleanups": [self._history_row(r) for r in runs]}

    @staticmethod
    def _history_row(r: dict) -> dict:
        return {"run_id": r["run_id"], "kind": r["kind"], "dry_run": r["kind"] == "DRY_RUN",
                "dry_run_id": r["dry_run_id"], "started_at": r["started_at"], "finished_at": r["finished_at"],
                "status": r["status"], "candidate_count": r["candidate_count"],
                "would_reclaim_bytes": r["candidate_bytes"], "deleted_count": r["deleted_count"],
                "failed_count": r["failed_count"], "skipped_count": r["skipped_count"],
                "reclaimed_bytes": r["reclaimed_bytes"], "error": r["error"]}

    def render_controls(self) -> dict:
        cap = self.capacity()
        return {
            "note": "Only the disk floor is enforced by the storage service. Render concurrency and reuse switches "
                    "are not implemented as configurable controls yet (workstream S4).",
            "disk_floor_bytes": self.min_free_bytes, "disk_free_bytes": cap["disk_free_bytes"],
            "max_simultaneous_renders": None, "stream_inputs": None, "thumbnail_cache": None,
            "reuse_assets": None, "reuse_tts": None, "reuse_ffmpeg_preprocess": None,
        }
