"""Isolation (spec §2, §33) + security (spec §32) + migrations."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_IMPORT_ROOTS = {"backend", "frontend", "defence_studio", "defencestudio", "audio_module", "audio-module",
                          "ndf", "standalone"}


def _py_files():
    for sub in ("app", "tests", "scripts", "fixtures", "alembic"):
        yield from (ROOT / sub).rglob("*.py")


def test_no_imports_from_parent_app_or_audio_module():
    offenders = []
    for f in _py_files():
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    offenders.append(f"{f}: relative import beyond package")  # we only use absolute imports
                names = [node.module or ""]
            for n in names:
                top = n.split(".")[0].lower()
                if top in FORBIDDEN_IMPORT_ROOTS:
                    offenders.append(f"{f}: import {n}")
    assert offenders == []


def test_no_path_references_outside_module():
    needles = ("audio-module", "audio_module", "..\\..\\", "../../", "defenceStudio\\backend", "sys.path.insert(0, str(ROOT.parent")
    offenders = []
    for f in _py_files():
        if f.parent.name == "tests":  # tests deliberately contain traversal strings as attack inputs
            continue
        text = f.read_text(encoding="utf-8")
        offenders += [f"{f}: {n}" for n in needles if n in text]
    assert offenders == []


def test_module_imports_and_runs_with_only_its_own_tree(tmp_path):
    """Run in a fresh interpreter whose sys.path contains only this module + site-packages."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    env["VIDEO_MODULE_STORAGE_ROOT"] = str(tmp_path / "iso_storage")
    code = ("import sys; sys.path=[p for p in sys.path if 'defenceStudio' not in p or 'video-visual-module' in p];"
            "from fastapi.testclient import TestClient; from app.main import app;"
            "c=TestClient(app).__enter__(); print(c.get('/health').json()['status'])")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "ok"
    loaded = subprocess.run([sys.executable, "-c", "import app.main, sys; print('\\n'.join(sorted(m.__file__ or '' for m in sys.modules.values() if getattr(m,'__file__',None))))"],
                            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    for line in loaded.stdout.splitlines():
        if "defenceStudio" in line:
            assert "video-visual-module" in line, f"module loaded from outside: {line}"


def test_no_shell_execution_or_raw_sql_in_app():
    for f in (ROOT / "app").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for needle in ("subprocess", "os.system", "shell=True", "eval(", "exec(", "pickle"):
            assert needle not in text, f"{f.name} uses {needle}"
        for line in text.splitlines():
            if "text(" in line and "SELECT 1" not in line and "sqlalchemy import" not in line:
                assert "execute" not in line, f"{f.name}: raw SQL execution"


def test_log_redaction():
    from app.logging_conf import redact

    s = redact("calling with api_key=sk-abc123456789 and Authorization: Bearer abc.def.ghi password=hunter2")
    assert "sk-abc123456789" not in s and "hunter2" not in s and "abc.def.ghi" not in s


def test_api_key_never_logged_or_returned(client, monkeypatch, project):
    from app.config import get_settings
    from app.logging_conf import get_logger, tail_log

    monkeypatch.setattr(get_settings(), "api_key", "TOPSECRET-123456")
    get_logger("test").warning("debug dump api_key=TOPSECRET-123456")
    r = client.get("/diagnostics", headers={"X-API-Key": "TOPSECRET-123456"})
    assert r.status_code == 200 and "TOPSECRET" not in r.text and r.json()["api_key_required"] is True
    logs = client.get("/diagnostics/logs", headers={"X-API-Key": "TOPSECRET-123456"}).json()["lines"]
    assert logs and not any("TOPSECRET" in line for line in logs)
    assert not any("TOPSECRET" in line for line in tail_log(500))


def test_file_endpoint_refuses_tampered_path(client, project, db):
    from app.db.models import Asset

    a = Asset(project_id=project, asset_type="IMAGE", source_type="USER_PROVIDED", local_path="../../../Windows/win.ini",
              mime_type="text/plain")
    db.add(a)
    db.commit()
    r = client.get(f"/assets/{a.asset_id}/file")
    assert r.status_code == 400 and r.json()["error_code"] == "PATH_TRAVERSAL_REJECTED"


def test_ui_escapes_server_data():
    js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    assert "const esc" in js and "innerHTML = a.title" not in js


def test_alembic_migration_matches_models(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from app.db.base import Base

    env = {**os.environ, "VIDEO_MODULE_STORAGE_ROOT": str(tmp_path / "mig")}
    r = subprocess.run([sys.executable, "-c", "from app.db.init_db import init_db; init_db(); init_db(); print('ok')"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    eng = create_engine(f"sqlite:///{(tmp_path / 'mig' / 'video_visual_module.db').as_posix()}")
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff
