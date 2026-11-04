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
