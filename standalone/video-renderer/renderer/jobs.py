"""Persisted, in-process render jobs: state machine, progress, cooperative cancel, restart recovery.

* Every job is a DB row; the only legal transitions are in db.ALLOWED and every status change is a
  conditional UPDATE (`WHERE status IN (...)`), so a cancel and a worker pick-up cannot both win.
* A job is SUCCEEDED only after render_package() returned (ffprobe + full decode passed and the
  final output.mp4 name exists). Output paths are versioned per job and never overwritten.
* Cancel: QUEUED -> CANCELLED immediately; RUNNING -> cancel_requested, honoured within ~8 frames;
  the render loop kills only the ffmpeg process it started.
* Restart recovery (`recover`): RUNNING -> FAILED / JOB_INTERRUPTED, partial output deleted, any
  orphaned ffmpeg still belonging to that job is terminated; QUEUED jobs are re-submitted.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from renderer.config import Settings
from renderer.db import ALLOWED, TERMINAL, RenderJob, utcnow
from renderer.errors import Cancelled, RendererError
from renderer.logging_conf import get_logger
from renderer.media import kill_pid, process_cmdline, require_tools
from renderer.package import load_package
from renderer.render import LOG_NAME, OUTPUT_NAME, PARTIAL_NAME, QC_NAME, Hooks, render_package

logger = get_logger("jobs")
OPTION_KEYS = {"transition_seconds"}


def new_job_id() -> str:
    return "r_" + uuid.uuid4().hex[:14]


def normalise_options(options: dict | None) -> dict:
    options = dict(options or {})
    extra = set(options) - OPTION_KEYS
    if extra:
        raise RendererError("INVALID_REQUEST", f"unknown option(s) {sorted(extra)}; allowed: {sorted(OPTION_KEYS)}")
    ts = options.get("transition_seconds")
    if ts is not None and (isinstance(ts, bool) or not isinstance(ts, (int, float)) or not (0 <= ts <= 3)):
        raise RendererError("INVALID_REQUEST", "transition_seconds must be a number between 0 and 3")
    return {k: v for k, v in options.items() if v is not None}


def fingerprint(package_path: str, options: dict) -> str:
    return hashlib.sha256(json.dumps({"p": package_path, "o": options}, sort_keys=True).encode()).hexdigest()


class JobService:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]):
        self.settings, self.sf = settings, session_factory
        self._pool = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="render")
        self._lock = threading.Lock()
        self._inflight: set[str] = set()

    # ---- helpers ---------------------------------------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        return self.settings.renders_dir / job_id

    def get(self, job_id: str) -> RenderJob:
        with self.sf() as db:
            job = db.get(RenderJob, job_id)
        if job is None:
            raise RendererError("NOT_FOUND", f"render job {job_id} not found")
        return job

    def by_key(self, key: str) -> RenderJob | None:
        with self.sf() as db:
            return db.query(RenderJob).filter_by(idempotency_key=key).first()

    def list(self, limit: int = 50, status: str | None = None) -> list[RenderJob]:
        with self.sf() as db:
            q = db.query(RenderJob).order_by(RenderJob.created_at.desc())
            if status:
                q = q.filter(RenderJob.status == status)
            return q.limit(limit).all()

    def _move(self, job_id: str, new: str, only_from: str | None = None, **fields) -> bool:
        """Conditional status change. Returns False when the job was not in a state allowing `new`."""
        sources = [s for s, tos in ALLOWED.items() if new in tos and (only_from is None or s == only_from)]
        with self.sf() as db:
            res = db.execute(update(RenderJob).where(RenderJob.job_id == job_id, RenderJob.status.in_(sources))
                             .values(status=new, **fields))
            db.commit()
            return res.rowcount == 1

    # ---- creation --------------------------------------------------------------------------------------
    def create(self, package_path: str, options: dict | None = None, idempotency_key: str | None = None,
               retry_of: str | None = None) -> tuple[RenderJob, bool]:
        """Create (or idempotently return) a job and queue it. Returns (job, created)."""
        options = normalise_options(options)
        p = Path(package_path)
        if not p.is_dir() or not (p / "package.json").is_file():
            raise RendererError("INVALID_PACKAGE", f"package folder '{package_path}' does not exist or has no package.json")
        resolved = str(p.resolve())
        fp = fingerprint(resolved, options)
        if idempotency_key is not None and not (1 <= len(idempotency_key) <= 200):
            raise RendererError("INVALID_REQUEST", "idempotency_key must be 1..200 characters")
        with self.sf() as db:
            if idempotency_key:
                prior = db.query(RenderJob).filter_by(idempotency_key=idempotency_key).first()
                if prior is not None:
                    if prior.request_fingerprint != fp:
                        raise RendererError("CONFLICT", "idempotency_key already used with a different request")
                    return prior, False
            job = RenderJob(job_id=new_job_id(), package_path=resolved, options_json=json.dumps(options),
                            idempotency_key=idempotency_key, request_fingerprint=fp, retry_of=retry_of,
                            status="QUEUED", stage="queued")
            db.add(job)
            db.commit()
        self.submit(job.job_id)
        return job, True

    def submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._inflight:
                return
            self._inflight.add(job_id)
        self._pool.submit(self._run, job_id)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)

    # ---- cancel / retry --------------------------------------------------------------------------------
    def cancel(self, job_id: str) -> RenderJob:
        job = self.get(job_id)
        if job.status in TERMINAL:
            raise RendererError("CONFLICT", f"job is already {job.status}")
        if self._move(job_id, "CANCELLED", only_from="QUEUED", completed_at=utcnow(), stage="cancelled",
                      error_code="JOB_CANCELLED", error_message="Cancelled before it started."):
            pass  # was QUEUED
        else:
            with self.sf() as db:  # RUNNING: cooperative
                db.execute(update(RenderJob).where(RenderJob.job_id == job_id, RenderJob.status == "RUNNING")
                           .values(cancel_requested=True, stage="cancelling"))
                db.commit()
        return self.get(job_id)

    def retry(self, job_id: str) -> RenderJob:
        job = self.get(job_id)
        if job.status not in ("FAILED", "CANCELLED"):
            raise RendererError("CONFLICT", f"only FAILED or CANCELLED jobs can be retried (job is {job.status})")
        new, _ = self.create(job.package_path, json.loads(job.options_json or "{}"), None, retry_of=job_id)
        return new

    # ---- execution -------------------------------------------------------------------------------------
    def _run(self, job_id: str) -> None:
        try:
            self._run_inner(job_id)
        finally:
            with self._lock:
                self._inflight.discard(job_id)

    def _should_cancel(self, job_id: str) -> bool:
        with self.sf() as db:
            row = db.get(RenderJob, job_id)
            return bool(row and row.cancel_requested)

    def _run_inner(self, job_id: str) -> None:
        if not self._move(job_id, "RUNNING", started_at=utcnow(), stage="validating package"):
            return  # cancelled (or already handled) before a worker picked it up
        job = self.get(job_id)
        out_dir = self.job_dir(job_id)

        def progress(done: int, total: int) -> None:
            with self.sf() as db:
                db.execute(update(RenderJob).where(RenderJob.job_id == job_id, RenderJob.status == "RUNNING")
                           .values(frames_done=done, frames_total=total, progress=round(done / total, 4),
                                   stage="rendering"))
                db.commit()

        def on_pid(pid: int | None) -> None:
            with self.sf() as db:
                db.execute(update(RenderJob).where(RenderJob.job_id == job_id, RenderJob.status == "RUNNING")
                           .values(ffmpeg_pid=pid))
                db.commit()

        try:
            settings = self.settings
            opts = json.loads(job.options_json or "{}")
            if "transition_seconds" in opts:
                settings = dataclasses.replace(settings, transition_seconds=float(opts["transition_seconds"]))
            tools = require_tools(settings)
            pkg = load_package(Path(job.package_path))
            if job.package_manifest_sha256 and job.package_manifest_sha256 != pkg.manifest_sha256:
                raise RendererError("INVALID_PACKAGE", "package manifest changed since the job was created")
            with self.sf() as db:
                db.execute(update(RenderJob).where(RenderJob.job_id == job_id)
                           .values(package_manifest_sha256=pkg.manifest_sha256))
                db.commit()
            report = render_package(pkg, tools, settings, out_dir,
                                    Hooks(progress, lambda: self._should_cancel(job_id), on_pid),
                                    meta={"job_id": job_id})
        except Cancelled:
            self._move(job_id, "CANCELLED", completed_at=utcnow(), stage="cancelled", error_code="JOB_CANCELLED",
                       error_message="Cancelled by request.", ffmpeg_pid=None)
            logger.info("job %s cancelled", job_id)
        except RendererError as exc:
            self._move(job_id, "FAILED", completed_at=utcnow(), stage="failed", error_code=exc.code,
                       error_message=exc.message[:2000], ffmpeg_pid=None)
            logger.warning("job %s failed: %s %s", job_id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001
            logger.error("job %s crashed: %s\n%s", job_id, exc, traceback.format_exc())
            self._move(job_id, "FAILED", completed_at=utcnow(), stage="failed", error_code="INTERNAL_ERROR",
                       error_message=f"Internal error ({type(exc).__name__}); see log for job {job_id}.",
                       ffmpeg_pid=None)
        else:
            ok = self._move(job_id, "SUCCEEDED", completed_at=utcnow(), stage="done", progress=1.0,
                            output_path=str(out_dir / OUTPUT_NAME), qc_path=str(out_dir / QC_NAME),
                            output_sha256=report["output"]["sha256"], ffmpeg_pid=None)
            logger.info("job %s succeeded (%s)", job_id, "recorded" if ok else "state changed meanwhile")

    # ---- restart recovery ------------------------------------------------------------------------------
    def recover(self) -> dict:
        """Run once at startup, before accepting work. Returns a summary."""
        interrupted, requeued = [], []
        with self.sf() as db:
            running = db.query(RenderJob).filter(RenderJob.status == "RUNNING").all()
            queued = [j.job_id for j in db.query(RenderJob).filter(RenderJob.status == "QUEUED").all()]
        for job in running:
            d = self.job_dir(job.job_id)
            self._kill_orphan(job, d)
            for name in (PARTIAL_NAME, OUTPUT_NAME):  # an unverified output is never kept as "complete"
                _delete(d / name)
            self._move(job.job_id, "FAILED", completed_at=utcnow(), stage="failed", error_code="JOB_INTERRUPTED",
                       error_message="The renderer stopped while this job was running; partial output was deleted. "
                                     "Retry it as a new job.", ffmpeg_pid=None, output_path=None)
            interrupted.append(job.job_id)
        for jid in queued:
            self.submit(jid)
            requeued.append(jid)
        if interrupted or requeued:
            logger.warning("recovery: interrupted=%s requeued=%s", interrupted, requeued)
        return {"interrupted": interrupted, "requeued": requeued}

    @staticmethod
    def _kill_orphan(job: RenderJob, job_dir: Path) -> None:
        """Terminate an ffmpeg that outlived a crashed renderer, only if its command line proves it is ours."""
        if not job.ffmpeg_pid:
            return
        cmd = process_cmdline(job.ffmpeg_pid)
        if cmd and "ffmpeg" in cmd.lower() and PARTIAL_NAME in cmd and job.job_id in cmd:
            logger.warning("terminating orphaned ffmpeg pid %s of job %s", job.ffmpeg_pid, job.job_id)
            kill_pid(job.ffmpeg_pid)
            time.sleep(0.5)


def _delete(path: Path) -> None:
    for _ in range(20):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:  # the orphan may still be closing the file
            time.sleep(0.25)
    logger.error("could not delete %s", path)
