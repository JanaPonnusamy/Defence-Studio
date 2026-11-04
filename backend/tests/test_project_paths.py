"""Root containment: project paths stay inside the repository."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from defence_studio.config import database_path, storage_root
from defence_studio.project_paths import (
    ProjectPathError,
    configure_project_logging,
    release_task_directory,
    require_inside_root,
    runtime_temp_root,
    task_directory,
    temporary_directory,
)


def test_default_database_and_storage_stay_inside_root():
    assert require_inside_root(database_path()) == database_path().resolve()
    assert require_inside_root(storage_root()) == storage_root().resolve()


def test_database_path_rejects_a_user_profile_override(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_DB_PATH", r"C:\Users\Public\not-defence-era.sqlite3")
    with pytest.raises(ProjectPathError):
        database_path()


def test_storage_path_rejects_an_outside_override(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_STORAGE_PATH", r"C:\dsv\storage")
    with pytest.raises(ProjectPathError):
        storage_root()


def test_absolute_path_inside_the_root_is_accepted(monkeypatch):
    from defence_studio.config import PROJECT_ROOT

    target = PROJECT_ROOT / "data" / "containment check" / "studio.sqlite3"
    monkeypatch.setenv("DEFENCE_STUDIO_DB_PATH", str(target))
    resolved = database_path()
    assert resolved == target.resolve()
    assert not resolved.exists()


def test_relative_parent_segment_cannot_leave_the_root(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_STORAGE_PATH", r"data\..\..\outside-storage")
    with pytest.raises(ProjectPathError):
        storage_root()


def test_missing_directory_inside_the_root_is_accepted_and_not_created(monkeypatch):
    from defence_studio.config import PROJECT_ROOT

    target = PROJECT_ROOT / "tmp" / "pytest" / "missing-containment-dir" / "app.sqlite3"
    monkeypatch.setenv("DEFENCE_STUDIO_DB_PATH", str(target))
    resolved = database_path()
    assert resolved == target.resolve()
    assert not target.parent.exists()


def test_unc_path_is_rejected(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_DB_PATH", r"\\server\share\defence.sqlite3")
    with pytest.raises(ProjectPathError):
        database_path()


def test_unusable_path_fails_closed_without_a_fallback(monkeypatch):
    def boom(self, *args, **kwargs):
        raise OSError("unusable path")

    monkeypatch.setattr(Path, "resolve", boom)
    monkeypatch.setenv("DEFENCE_STUDIO_DB_PATH", r"C:\not-a-volume\db.sqlite3")
    with pytest.raises(ProjectPathError):
        database_path()


def test_symlink_to_an_outside_directory_is_rejected(monkeypatch, tmp_path):
    link = tmp_path / "escape-link"
    try:
        link.symlink_to(Path(r"C:\Users\Public"), target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink not permitted: {exc}")
    monkeypatch.setenv("DEFENCE_STUDIO_STORAGE_PATH", str(link))
    with pytest.raises(ProjectPathError):
        storage_root()


def test_junction_to_an_outside_directory_is_rejected(monkeypatch, tmp_path):
    import subprocess

    link = tmp_path / "escape-junction"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), r"C:\Users\Public"],
        capture_output=True, text=True,
    )
    if created.returncode != 0 or not link.exists():
        pytest.skip(created.stderr.strip() or "junction was not created")
    try:
        monkeypatch.setenv("DEFENCE_STUDIO_STORAGE_PATH", str(link))
        with pytest.raises(ProjectPathError):
            storage_root()
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


def test_startup_refuses_an_outside_database_and_does_not_create_it():
    import os
    import subprocess
    import sys

    outside = Path(r"C:\dsv\containment-must-not-create.sqlite3")
    env = dict(os.environ)
    env["DEFENCE_STUDIO_AUTH_SECRET"] = "x" * 40
    env["DEFENCE_STUDIO_DB_PATH"] = str(outside)
    env["DEFENCE_STUDIO_STORAGE_PATH"] = str(
        Path(__file__).resolve().parents[2] / "tmp" / "pytest" / "startup-storage"
    )
    env.pop("DEFENCE_STUDIO_STARTUP_DIAGNOSTICS", None)
    result = subprocess.run(
        [sys.executable, "-c", "from defence_studio.api.app import create_app; create_app()"],
        cwd=Path(__file__).resolve().parents[1],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert "project root" in result.stderr
    assert not outside.exists()


def test_temporary_directory_cleanup_does_not_delete_a_sibling():
    sibling = task_directory("sibling")
    try:
        with temporary_directory("worker") as folder:
            assert folder.is_dir()
            assert folder.parent == runtime_temp_root().resolve()
            (folder / "owned.txt").write_text("ok", encoding="utf-8")
        assert not folder.exists()
        assert sibling.is_dir()
    finally:
        release_task_directory(sibling)


def test_release_refuses_the_temp_root_and_a_nested_path():
    with pytest.raises(ProjectPathError):
        release_task_directory(runtime_temp_root())
    folder = task_directory("nested")
    try:
        nested = folder / "child"
        nested.mkdir()
        with pytest.raises(ProjectPathError):
            release_task_directory(nested)
    finally:
        release_task_directory(folder)


def test_rotating_log_stays_inside_a_task_directory():
    logger_name = "containment-test"
    logger = logging.getLogger(logger_name)
    logger.propagate = False
    with temporary_directory("logtest") as folder:
        path = configure_project_logging(
            logger_name, directory=folder, max_bytes=180, backup_count=2
        )
        for _ in range(40):
            logger.info("x" * 40)
        for handler in list(logger.handlers):
            handler.flush()
            handler.close()
            logger.removeHandler(handler)
        written = list(Path(folder).glob("containment-test.log*"))
        assert path.is_file()
        assert require_inside_root(path)
        assert len(written) >= 2
    assert not folder.exists()
