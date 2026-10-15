"""Fresh-process entry point checks; production secret validation stays strict."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_voice_numpy_is_a_runtime_dependency():
    import tomllib
    project = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())['project']
    assert any(item.startswith('numpy') for item in project['dependencies'])


@pytest.mark.parametrize("secret", [None, "short", "x" * 32])
def test_entrypoint_secret_validation_in_fresh_process(tmp_path, secret):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("DEFENCE_STUDIO_AUTH_SECRET", None)
    env["DEFENCE_STUDIO_DB_PATH"] = str(tmp_path / "app.sqlite3")
    env["DEFENCE_STUDIO_STORAGE_PATH"] = str(tmp_path / "storage")
    if secret is not None:
        env["DEFENCE_STUDIO_AUTH_SECRET"] = secret
    result = subprocess.run([sys.executable, "-c",
                             "from defence_studio.api.app import app; print(app.title)"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    if secret is None or len(secret) < 32:
        assert result.returncode != 0
        assert "at least 32 bytes" in result.stderr
        assert not (tmp_path / "app.sqlite3").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert "Defence Studio API" in result.stdout
