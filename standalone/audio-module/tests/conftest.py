from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPO = PROJECT_ROOT.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from tools.project_paths import session_workspace

sys.path.insert(0, str(PROJECT_ROOT))

# Point this test session at an isolated temp storage root + sqlite db
# BEFORE any app module is imported, so app.config.get_settings() picks it
# up. This keeps tests from polluting the real storage/ directory.
_TEST_ROOT = session_workspace("audio-module")
os.environ["AUDIO_MODULE_STORAGE_ROOT"] = str(_TEST_ROOT)
os.environ["AUDIO_MODULE_DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'test.db').as_posix()}"


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_root():
    yield
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


@pytest.fixture(scope="session")
def fixture_wav_path() -> Path:
    path = PROJECT_ROOT / "fixtures" / "test_fixture_speech_like.wav"
    if not path.exists():
        from fixtures.generate_fixture import main
        main()
    return path


@pytest.fixture(scope="session")
def fixture_transcript_text() -> str:
    path = PROJECT_ROOT / "fixtures" / "test_fixture_transcript.txt"
    if not path.exists():
        from fixtures.generate_fixture import main
        main()
    return path.read_text(encoding="utf-8")
