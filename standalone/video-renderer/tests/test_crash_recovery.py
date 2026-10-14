"""Kill the real server process mid-render, restart it, and check what is left behind."""
from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from renderer.media import process_cmdline
from renderer.synthetic import small_package
from tests.conftest import tools_or_skip

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.ffmpeg


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(storage: Path, port: int) -> subprocess.Popen:
    env = {**os.environ, "RENDERER_STORAGE_ROOT": str(storage)}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "renderer.main:app", "--host", "127.0.0.1", "--port",
                          str(port), "--log-level", "warning"], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 60
    while time.time() < end:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return p
        except httpx.HTTPError:
            time.sleep(0.2)
    p.kill()
    raise AssertionError("server did not start")


def test_killing_the_server_mid_render_yields_job_interrupted_after_restart(tmp_path):
    tools_or_skip()
    storage = tmp_path / "storage"
    pkg = small_package(tmp_path / "pkg", profile={"width": 640, "height": 360, "fps": 25})
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = start_server(storage, port)
    try:
        jid = httpx.post(f"{base}/renders", json={"package_path": str(pkg)}, timeout=10).json()["job_id"]
        end = time.time() + 120
        while time.time() < end:
            j = httpx.get(f"{base}/renders/{jid}", timeout=10).json()
            if j["status"] == "RUNNING" and j["frames_done"] >= 50:
                break
            time.sleep(0.1)
        else:
            pytest.fail("render never reached 50 frames")
        with sqlite3.connect(storage / "renderer.db") as db:
            ff_pid = db.execute("select ffmpeg_pid from render_jobs where job_id=?", (jid,)).fetchone()[0]
        assert ff_pid and "ffmpeg" in (process_cmdline(ff_pid) or "").lower()  # the encoder is really running
        server.kill()  # hard crash: no cleanup code runs
        server.wait(timeout=30)
    finally:
        if server.poll() is None:
            server.kill()
    job_dir = storage / "renders" / jid
    assert not (job_dir / "output.mp4").exists()  # nothing was ever marked complete

    server = start_server(storage, port)  # restart -> recovery
    try:
        j = httpx.get(f"{base}/renders/{jid}", timeout=10).json()
        assert j["status"] == "FAILED" and j["error_code"] == "JOB_INTERRUPTED", j
        assert httpx.get(f"{base}/renders/{jid}/output", timeout=10).status_code == 409
        assert httpx.get(f"{base}/health", timeout=10).json()["recovery"]["interrupted"] == [jid]
        end = time.time() + 30  # orphaned ffmpeg is gone (exits on the broken pipe, or recovery terminates it)
        while time.time() < end and process_cmdline(ff_pid):
            time.sleep(0.2)
        assert process_cmdline(ff_pid) is None
        assert not (job_dir / "output.partial.mp4").exists() and not (job_dir / "output.mp4").exists()
        retry = httpx.post(f"{base}/renders/{jid}/retry", timeout=10).json()
        assert retry["retry_of"] == jid and retry["job_id"] != jid  # retried as a NEW job, new output path
        httpx.post(f"{base}/renders/{retry['job_id']}/cancel", timeout=10)
        end = time.time() + 60
        while time.time() < end and httpx.get(f"{base}/renders/{retry['job_id']}", timeout=10).json()["status"] in (
                "QUEUED", "RUNNING"):
            time.sleep(0.2)
    finally:
        server.kill()
        server.wait(timeout=30)
