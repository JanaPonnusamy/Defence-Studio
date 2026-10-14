from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from renderer.config import Settings
from renderer.main import create_app
from renderer.package import sha256_file
from tests.conftest import rewrite_manifest


@pytest.fixture()
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def poll(client, job_id, statuses=("SUCCEEDED", "FAILED", "CANCELLED"), timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/renders/{job_id}").json()
        if j["status"] in statuses:
            return j
        time.sleep(0.2)
    raise AssertionError("timeout")


def test_health_reports_ffmpeg(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["ffmpeg"]["status"] in ("AVAILABLE", "NOT_AVAILABLE")
    if h["ffmpeg"]["status"] == "AVAILABLE":
        assert h["ffmpeg"]["version"] and "license_flags" in h["ffmpeg"] and h["ffmpeg"]["ffmpeg"]


def test_health_not_available_and_job_fails_explicitly(tmp_path, pkg_dir):
    s = Settings(storage_root=tmp_path / "s", ffmpeg=str(tmp_path / "missing.exe"))
    with TestClient(create_app(s)) as c:
        h = c.get("/health").json()
        assert h["ffmpeg"] == {"status": "NOT_AVAILABLE", "path": None, "version": "NOT_AVAILABLE"}
        r = c.post("/renders", json={"package_path": str(pkg_dir)})
        j = poll(c, r.json()["job_id"])
        assert j["status"] == "FAILED" and j["error_code"] == "FFMPEG_NOT_AVAILABLE"
        assert c.get(f"/renders/{j['job_id']}/output").status_code == 409


@pytest.mark.ffmpeg
def test_render_lifecycle_output_and_qc(client, pkg_dir, tools):
    r = client.post("/renders", json={"package_path": str(pkg_dir), "idempotency_key": "k1"})
    assert r.status_code == 202 and r.json()["created"] is True
    jid = r.json()["job_id"]
    again = client.post("/renders", json={"package_path": str(pkg_dir), "idempotency_key": "k1"})
    assert again.status_code == 200 and again.json()["job_id"] == jid and again.json()["created"] is False
    j = poll(client, jid)
    assert j["status"] == "SUCCEEDED" and j["progress"] == 1.0 and j["output_url"] == f"/renders/{jid}/output"
    out = client.get(f"/renders/{jid}/output")
    assert out.status_code == 200 and out.headers["content-type"] == "video/mp4" and out.content[4:8] == b"ftyp"
    qc = client.get(f"/renders/{jid}/qc").json()
    assert qc["passed"] and qc["output"]["sha256"] == j["output_sha256"]
    assert qc["ffmpeg"]["license_flags"]["summary"] and qc["ffmpeg"]["version"]
    assert client.post(f"/renders/{jid}/cancel").status_code == 409  # terminal
    assert client.post(f"/renders/{jid}/retry").status_code == 409  # only FAILED / CANCELLED
    assert [x["job_id"] for x in client.get("/renders").json()["renders"]] == [jid]


@pytest.mark.ffmpeg
def test_invalid_package_fails_the_job_with_details(client, pkg_dir, tools):
    rewrite_manifest(pkg_dir, lambda m: m["narration"].update(sha256="0" * 64))
    jid = client.post("/renders", json={"package_path": str(pkg_dir)}).json()["job_id"]
    j = poll(client, jid)
    assert j["status"] == "FAILED" and j["error_code"] == "INVALID_PACKAGE" and "sha256 mismatch" in j["error_message"]
    retry = client.post(f"/renders/{jid}/retry")
    assert retry.status_code == 202 and retry.json()["retry_of"] == jid
    assert poll(client, retry.json()["job_id"])["status"] == "FAILED"  # still the same bad package: honest failure


def test_request_validation_errors(client, tmp_path):
    assert client.post("/renders", json={"package_path": 5}).status_code == 422
    assert client.post("/renders", json={"package_path": "x", "bogus": 1}).json()["error"]["code"] == "INVALID_REQUEST"
    r = client.post("/renders", json={"package_path": str(tmp_path / "nothing-here")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_PACKAGE"
    r = client.post("/renders", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    for path in ("/renders/r_missing", "/renders/r_missing/qc", "/renders/r_missing/output"):
        assert client.get(path).status_code == 404
    assert client.post("/renders/r_missing/cancel").status_code == 404
    assert client.post("/renders/r_missing/retry").status_code == 404


def _zip_dir(root: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(root).as_posix())
    return buf.getvalue()


@pytest.mark.ffmpeg
def test_upload_zip_renders(client, pkg_dir, tools):
    r = client.post("/renders", files={"file": ("pkg.zip", _zip_dir(pkg_dir), "application/zip")},
                    data={"idempotency_key": "up-1"})
    assert r.status_code == 202, r.text
    j = poll(client, r.json()["job_id"])
    assert j["status"] == "SUCCEEDED"
    again = client.post("/renders", files={"file": ("pkg.zip", _zip_dir(pkg_dir), "application/zip")},
                        data={"idempotency_key": "up-1"})
    assert again.status_code == 200 and again.json()["job_id"] == j["job_id"]


def test_upload_rejects_zip_slip_and_garbage(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.txt", "x")
    r = client.post("/renders", files={"file": ("e.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 422 and "traversal" in r.json()["error"]["message"]
    r = client.post("/renders", files={"file": ("e.zip", b"definitely not a zip", "application/zip")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_PACKAGE"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "no manifest")
    assert client.post("/renders", files={"file": ("e.zip", buf.getvalue(), "application/zip")}).status_code == 422
    assert client.post("/renders", data={"x": "1"}, files={"other": ("a", b"1")}).status_code == 422
