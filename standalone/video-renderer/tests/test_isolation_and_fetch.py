"""Isolation (no imports from sibling projects) and the VVM fetch script against a local fake VVM (GET only)."""
from __future__ import annotations

import ast
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from renderer.package import load_package
from renderer.synthetic import small_package

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {"app", "backend", "qvault", "defencestudio", "defence_studio", "audio_module", "video_visual_module",
             "whisper", "whisper_studio", "faster_whisper", "openai", "anthropic", "requests"}
ALLOWED_THIRD_PARTY = {"fastapi", "starlette", "pydantic", "sqlalchemy", "PIL", "uvicorn", "multipart"}


def _imports(py: Path):
    for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0]


def test_no_imports_from_other_projects_and_only_expected_dependencies():
    stdlib = set(sys.stdlib_module_names)
    local = {"renderer", "scripts", "tests"}
    for py in [*ROOT.joinpath("renderer").rglob("*.py"), *ROOT.joinpath("scripts").rglob("*.py")]:
        for mod in _imports(py):
            assert mod.lower() not in FORBIDDEN, f"{py.name} imports forbidden module {mod}"
            assert mod in stdlib | local | ALLOWED_THIRD_PARTY, f"{py.name} imports unexpected module {mod}"


def test_code_does_not_reference_sibling_project_directories():
    for py in [*ROOT.joinpath("renderer").rglob("*.py"), *ROOT.joinpath("scripts").rglob("*.py")]:
        text = py.read_text(encoding="utf-8")
        for needle in ("QVault", "defenceStudio", "audio-module"):
            assert needle not in text, f"{py.name} references {needle}"
        for line in text.splitlines():  # the only allowed path hack: scripts putting THIS project's root on sys.path
            if "sys.path" in line:
                assert "Path(__file__).resolve().parent.parent" in line, f"{py.name}: unexpected sys.path use: {line}"


# ---- fetch script against a fake VVM ---------------------------------------------------------------------------
class FakeVVM:
    def __init__(self, pkg: Path, corrupt: bool = False):
        self.pkg, self.corrupt, self.methods, self.paths = pkg, corrupt, [], []
        tl = json.loads((pkg / "timeline.json").read_text())
        self.assets = {aid: (pkg / m["path"]) for aid, m in json.loads((pkg / "package.json").read_text())["assets"].items()}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def do_GET(self):
                outer.methods.append("GET")
                outer.paths.append(self.path)
                if self.path == f"/visual-plans/{tl['visual_plan_id']}/timeline":
                    body = (pkg / "timeline.json").read_bytes()
                elif self.path.startswith("/assets/") and self.path.endswith("/file"):
                    aid = self.path.split("/")[2]
                    if aid not in outer.assets:
                        self.send_error(404)
                        return
                    body = outer.assets[aid].read_bytes()
                    if outer.corrupt:
                        body = body[:-1] + bytes([body[-1] ^ 1])
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                outer.methods.append("POST")
                self.send_error(405)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.plan_id = tl["visual_plan_id"]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def fetch_fn():
    sys.path.insert(0, str(ROOT))
    from scripts.fetch_from_vvm import fetch
    return fetch


def test_fetch_builds_a_valid_package_using_only_get(tmp_path, fetch_fn):
    src = small_package(tmp_path / "src", profile=None)
    vvm = FakeVVM(src)
    try:
        out = fetch_fn(vvm.url, vvm.plan_id, src / "narration.wav", tmp_path / "fetched", {"width": 640, "height": 360, "fps": 25})
    finally:
        vvm.close()
    assert set(vvm.methods) == {"GET"}
    assert all(p.startswith(("/visual-plans/", "/assets/")) for p in vvm.paths)
    assert (out / "timeline.json").read_bytes() == (src / "timeline.json").read_bytes()  # byte exact
    pkg = load_package(out)
    assert pkg.profile.as_dict()["width"] == 640 and len(pkg.assets) == 3  # only assets READY shots use
    assert pkg.manifest["source"]["visual_plan_id"] == vvm.plan_id


def test_fetch_rejects_corrupted_asset_and_cleans_up(tmp_path, fetch_fn):
    src = small_package(tmp_path / "src", profile=None)
    vvm = FakeVVM(src, corrupt=True)
    try:
        with pytest.raises(SystemExit) as e:
            fetch_fn(vvm.url, vvm.plan_id, src / "narration.wav", tmp_path / "fetched", None)
    finally:
        vvm.close()
    assert "checksum_sha256" in str(e.value) and not (tmp_path / "fetched").exists()


def test_fetch_refuses_remote_hosts_and_nonempty_output(tmp_path, fetch_fn):
    wav = tmp_path / "n.wav"
    wav.write_bytes(b"x")
    with pytest.raises(SystemExit) as e:
        fetch_fn("http://example.com", "p", wav, tmp_path / "o", None)
    assert "non-localhost" in str(e.value)
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    with pytest.raises(SystemExit) as e:
        fetch_fn("http://127.0.0.1:1", "p", wav, tmp_path / "full", None)
    assert "not empty" in str(e.value)
    with pytest.raises(SystemExit):
        fetch_fn("http://127.0.0.1:1", "p", tmp_path / "missing.wav", tmp_path / "o2", None)


def test_fetch_unreachable_vvm_fails_cleanly(tmp_path, fetch_fn):
    wav = tmp_path / "n.wav"
    wav.write_bytes(b"x")
    with pytest.raises(SystemExit) as e:
        fetch_fn("http://127.0.0.1:9", "p", wav, tmp_path / "o", None)
    assert "fetch failed" in str(e.value) and not (tmp_path / "o").exists()
