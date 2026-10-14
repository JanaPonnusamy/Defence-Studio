from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from renderer.config import Settings
from renderer.db import RenderJob, make_session_factory
from renderer.jobs import JobService, new_job_id
from renderer.media import locate_tools
from renderer.package import sha256_file
from renderer.synthetic import small_package

TINY = {"width": 320, "height": 180, "fps": 12}


def tools_or_skip():
    t = locate_tools(Settings())
    if t is None:
        pytest.skip("ffmpeg/ffprobe not available on PATH")
    return t


@pytest.fixture()
def settings(tmp_path):
    s = Settings(storage_root=tmp_path / "storage")
    s.ensure_dirs()
    return s


@pytest.fixture()
def tools():
    return tools_or_skip()


@pytest.fixture()
def pkg_dir(tmp_path):
    """A valid 20 s / 4 shot package at a tiny output size (fast)."""
    return small_package(tmp_path / "pkg", profile=TINY)


@pytest.fixture()
def service(settings):
    svc = JobService(settings, make_session_factory(settings.db_path))
    yield svc
    svc.shutdown(wait=False)


def rewrite_manifest(root: Path, mutate) -> dict:
    """Load package.json, apply mutate(manifest), write it back (no re-hashing)."""
    p = root / "package.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    mutate(m)
    p.write_text(json.dumps(m), encoding="utf-8")
    return m


def rewrite_timeline(root: Path, mutate) -> None:
    """Edit timeline.json and refresh its sha256 in the manifest."""
    p = root / "timeline.json"
    t = json.loads(p.read_text(encoding="utf-8"))
    mutate(t)
    p.write_text(json.dumps(t), encoding="utf-8")
    rewrite_manifest(root, lambda m: m.__setitem__("timeline_sha256", sha256_file(p)))


def insert_job(svc: JobService, status: str, **kw) -> str:
    jid = new_job_id()
    with svc.sf() as db:
        db.add(RenderJob(job_id=jid, status=status, package_path=kw.pop("package_path", "x"),
                         stage=status.lower(), **kw))
        db.commit()
    return jid


def wait_for(svc: JobService, job_id: str, statuses, timeout: float = 240.0) -> RenderJob:
    end = time.time() + timeout
    while time.time() < end:
        j = svc.get(job_id)
        if j.status in statuses:
            return j
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} stayed {svc.get(job_id).status} (wanted {statuses})")
