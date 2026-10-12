from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Isolated temp storage + DB for the whole session, set BEFORE any app import.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="vvm_test_"))
os.environ["VIDEO_MODULE_STORAGE_ROOT"] = str(_TEST_ROOT)
os.environ.pop("VIDEO_MODULE_DATABASE_URL", None)
os.environ["VIDEO_MODULE_API_KEY"] = ""
os.environ["VIDEO_MODULE_LOG_LEVEL"] = "INFO"
os.environ["VIDEO_MODULE_GENERATION_RETRY_BACKOFF_S"] = "0.01"
os.environ.pop("VIDEO_MODULE_ALLOW_PLACEHOLDER_FALLBACK", None)

# ---- NO PAID CALLS: real-provider credentials are stripped for the normal suite. Only the opt-in
# test module (tests/test_provider_live_optin.py) may use them, and only when
# VVM_ENABLE_PAID_PROVIDER_TESTS=1 AND a key is present.
PAID_TESTS_ENABLED = os.environ.get("VVM_ENABLE_PAID_PROVIDER_TESTS") == "1"
_SAVED_OPENAI_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("VIDEO_MODULE_OPENAI_API_KEY")
for _k in ("OPENAI_API_KEY", "VIDEO_MODULE_OPENAI_API_KEY"):
    # Audit fix: set to EMPTY rather than removing. A removed variable lets pydantic-settings fall back to a key
    # in the module .env file; an empty process variable takes precedence over .env and means "no key".
    os.environ[_k] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings as _get_settings  # noqa: E402
from app.main import app  # noqa: E402

_get_settings().openai_api_key = None  # belt and braces: the normal suite never holds a provider key


def _install_network_guard():
    """Any real HTTP request from the OpenAI adapter during the normal suite is a test failure."""
    import httpx

    from app.generation.openai_images import OpenAIImagesProvider

    def _refuse(request):
        raise AssertionError(f"Real network call attempted in tests: {request.method} {request.url}")

    OpenAIImagesProvider._transport = httpx.MockTransport(_refuse)


_install_network_guard()


@pytest.fixture(scope="session")
def test_root() -> Path:
    return _TEST_ROOT


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)


@pytest.fixture()
def project(client) -> str:
    r = client.post("/projects", json={"name": f"test-{time.time_ns()}", "ai_image_budget_usd": 5.0})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


@pytest.fixture()
def db():
    from app.db.base import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def wait_job(client, job_id: str, timeout: float = 60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/jobs/{job_id}").json()
        if j["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def import_png(client, project_id: str, *, seed: int = 1, w: int = 640, h: int = 360, asset_type: str = "SOURCE_PHOTO",
               source_type: str = "EXTERNAL_SOURCE", rights_status: str = "LICENSED", licence: str | None = "Test licence",
               title: str = "photo", meta: dict | None = None, filename: str = "img.png"):
    import json

    from fixtures.synthetic import png_bytes

    data = {"project_id": project_id, "asset_type": asset_type, "source_type": source_type,
            "rights_status": rights_status, "title": title}
    if licence:
        data["licence"] = licence
    if meta is not None:
        data["extra_metadata"] = json.dumps(meta)
    return client.post("/assets/import", data=data, files={"file": (filename, png_bytes(w, h, seed), "image/png")})
