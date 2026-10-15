"""Job state machine, cancel, retry, idempotency and restart recovery (no ffmpeg needed: ffmpeg is pointed at a
non-existent binary where a job would otherwise start rendering)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from renderer.config import Settings
from renderer.db import ALLOWED, TERMINAL, make_session_factory
from renderer.errors import Cancelled, RendererError
from renderer.jobs import JobService, normalise_options
from tests.conftest import insert_job, wait_for


@pytest.fixture()
def svc(tmp_path):
    s = Settings(storage_root=tmp_path / "storage", ffmpeg=str(tmp_path / "no-such-ffmpeg.exe"))
    s.ensure_dirs()
    service = JobService(s, make_session_factory(s.db_path))
    yield service
    service.shutdown(wait=False)


def test_state_machine_table():
    assert set(ALLOWED) == {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}
    assert all(not ALLOWED[s] for s in TERMINAL)
    assert ALLOWED["QUEUED"] == {"RUNNING", "CANCELLED", "FAILED"}
    assert ALLOWED["RUNNING"] == {"SUCCEEDED", "FAILED", "CANCELLED"}


@pytest.mark.parametrize("frm,to,ok", [("QUEUED", "RUNNING", True), ("QUEUED", "SUCCEEDED", False),
                                       ("RUNNING", "SUCCEEDED", True), ("RUNNING", "QUEUED", False),
                                       ("SUCCEEDED", "FAILED", False), ("FAILED", "RUNNING", False),
                                       ("CANCELLED", "RUNNING", False), ("QUEUED", "CANCELLED", True)])
def test_transitions_enforced(svc, frm, to, ok):
    jid = insert_job(svc, frm)
    assert svc._move(jid, to) is ok
    assert svc.get(jid).status == (to if ok else frm)


def test_terminal_state_is_never_overwritten(svc):
    jid = insert_job(svc, "CANCELLED")
    assert not svc._move(jid, "SUCCEEDED") and not svc._move(jid, "FAILED") and not svc._move(jid, "RUNNING")
    assert svc.get(jid).status == "CANCELLED"


def test_cancel_queued_is_immediate(svc):
    jid = insert_job(svc, "QUEUED")
    j = svc.cancel(jid)
    assert j.status == "CANCELLED" and j.error_code == "JOB_CANCELLED" and j.completed_at
    with pytest.raises(RendererError) as e:
        svc.cancel(jid)
    assert e.value.code == "CONFLICT"


def test_cancel_running_is_cooperative(svc):
    jid = insert_job(svc, "RUNNING")
    j = svc.cancel(jid)
    assert j.status == "RUNNING" and j.cancel_requested  # the worker loop turns this into CANCELLED
    assert svc._should_cancel(jid) is True


def test_late_worker_callbacks_cannot_report_progress_after_cancel(monkeypatch, svc, pkg_dir):
    """A stale render callback must not make a terminal cancellation look complete."""
    started = threading.Event()
    release = threading.Event()
    callbacks = {}

    monkeypatch.setattr("renderer.jobs.require_tools", lambda _settings: object())
    monkeypatch.setattr("renderer.jobs.load_package", lambda _path: SimpleNamespace(manifest_sha256="test-manifest"))

    def fake_render(_pkg, _tools, _settings, _out_dir, hooks, *, meta):
        callbacks["hooks"] = hooks
        started.set()
        assert release.wait(10)
        raise Cancelled()

    monkeypatch.setattr("renderer.jobs.render_package", fake_render)
    job, created = svc.create(str(pkg_dir))
    assert created and started.wait(10)
    wait_for(svc, job.job_id, ("RUNNING",), 10)
    svc.cancel(job.job_id)
    release.set()
    assert wait_for(svc, job.job_id, ("CANCELLED",), 10).status == "CANCELLED"

    # Simulate a callback already queued by the worker after the terminal write.
    callbacks["hooks"].on_progress(100, 100)
    callbacks["hooks"].on_pid(12345)
    final = svc.get(job.job_id)
    assert final.status == "CANCELLED"
    assert final.progress == 0.0
    assert final.ffmpeg_pid is None


def test_cancel_unknown_job(svc):
    with pytest.raises(RendererError) as e:
        svc.cancel("r_nope")
    assert e.value.code == "NOT_FOUND"


def test_ffmpeg_missing_fails_explicitly_and_makes_no_output(svc, pkg_dir):
    job, created = svc.create(str(pkg_dir))
    assert created
    j = wait_for(svc, job.job_id, TERMINAL, 30)
    assert j.status == "FAILED" and j.error_code == "FFMPEG_NOT_AVAILABLE" and "Nothing was rendered" in j.error_message
    assert j.output_path is None and not list(svc.job_dir(j.job_id).glob("*.mp4"))


def test_retry_creates_a_new_job_and_only_for_failed_or_cancelled(svc, pkg_dir):
    job, _ = svc.create(str(pkg_dir))
    failed = wait_for(svc, job.job_id, TERMINAL, 30)
    new = svc.retry(failed.job_id)
    assert new.job_id != failed.job_id and new.retry_of == failed.job_id and new.idempotency_key is None
    assert svc.get(failed.job_id).status == "FAILED"  # the original is untouched
    queued = insert_job(svc, "QUEUED")
    running = insert_job(svc, "RUNNING")
    done = insert_job(svc, "SUCCEEDED")
    for jid in (queued, running, done):
        with pytest.raises(RendererError) as e:
            svc.retry(jid)
        assert e.value.code == "CONFLICT"
    cancelled = svc.cancel(insert_job(svc, "QUEUED", package_path=str(pkg_dir)))
    assert svc.retry(cancelled.job_id).retry_of == cancelled.job_id


def test_idempotency_key(svc, pkg_dir, tmp_path):
    a, created_a = svc.create(str(pkg_dir), idempotency_key="episode-1")
    b, created_b = svc.create(str(pkg_dir), idempotency_key="episode-1")
    assert created_a and not created_b and a.job_id == b.job_id
    with pytest.raises(RendererError) as e:  # same key, different options
        svc.create(str(pkg_dir), {"transition_seconds": 1.0}, "episode-1")
    assert e.value.code == "CONFLICT"
    other = tmp_path / "other"
    other.mkdir()
    (other / "package.json").write_text("{}")
    with pytest.raises(RendererError) as e:  # same key, different package
        svc.create(str(other), idempotency_key="episode-1")
    assert e.value.code == "CONFLICT"
    c, _ = svc.create(str(pkg_dir))
    d, _ = svc.create(str(pkg_dir))
    assert c.job_id != d.job_id  # no key -> no dedup


def test_create_rejects_bad_input(svc, tmp_path):
    with pytest.raises(RendererError) as e:
        svc.create(str(tmp_path / "missing"))
    assert e.value.code == "INVALID_PACKAGE"
    for bad in ({"nope": 1}, {"transition_seconds": -1}, {"transition_seconds": "x"}, {"transition_seconds": 9}):
        with pytest.raises(RendererError):
            normalise_options(bad)
    assert normalise_options({"transition_seconds": 0.25}) == {"transition_seconds": 0.25}


def test_recovery_marks_running_failed_and_deletes_partial_output(svc):
    running = insert_job(svc, "RUNNING", ffmpeg_pid=None, frames_done=40, frames_total=500, progress=0.08)
    d = svc.job_dir(running)
    d.mkdir(parents=True)
    (d / "output.partial.mp4").write_bytes(b"half a file")
    (d / "output.mp4").write_bytes(b"unverified")  # died between rename and DB update: still not trusted
    done = insert_job(svc, "SUCCEEDED")
    dd = svc.job_dir(done)
    dd.mkdir(parents=True)
    (dd / "output.mp4").write_bytes(b"verified earlier")
    summary = svc.recover()
    j = svc.get(running)
    assert summary["interrupted"] == [running]
    assert j.status == "FAILED" and j.error_code == "JOB_INTERRUPTED" and j.output_path is None and j.completed_at
    assert not (d / "output.partial.mp4").exists() and not (d / "output.mp4").exists()
    assert svc.get(done).status == "SUCCEEDED" and (dd / "output.mp4").read_bytes() == b"verified earlier"


def test_recovery_requeues_queued_jobs(svc, pkg_dir):
    queued = insert_job(svc, "QUEUED", package_path=str(pkg_dir))
    assert svc.recover()["requeued"] == [queued]
    assert wait_for(svc, queued, TERMINAL, 30).status == "FAILED"  # ffmpeg is absent in this fixture


def test_recovery_does_not_kill_unrelated_processes(svc):
    import os
    jid = insert_job(svc, "RUNNING", ffmpeg_pid=os.getpid())  # a live pid that is NOT an ffmpeg of this job
    svc.recover()
    assert svc.get(jid).error_code == "JOB_INTERRUPTED"
    assert os.getpid() > 0  # still here
