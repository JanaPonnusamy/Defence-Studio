import base64
import hashlib
import hmac
import json
import os
import time

SECRET = "phase-one-test-signing-secret-32-bytes-minimum"
# Must run before `defence_studio.api.app` is imported below: that module
# constructs a module-level FastAPI app (`app = create_app()`, the
# conventional `uvicorn defence_studio.api.app:app` entrypoint documented in
# README.md/OPERATIONS.md) as an import-time side effect, and create_app()
# raises RuntimeError if DEFENCE_STUDIO_AUTH_SECRET is unset/too short. A
# fresh test process has no secret exported yet, so this test-only default
# must be set before that import executes, not after.
os.environ.setdefault("DEFENCE_STUDIO_AUTH_SECRET", SECRET)

import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app


def access_token(subject: str, secret: str = SECRET) -> str:
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = encode(json.dumps({"sub": subject, "exp": int(time.time()) + 600}).encode())
    signing_input = f"{header}.{payload}"
    signature = encode(hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "workflow.sqlite3", SECRET)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def token():
    return access_token


@pytest.fixture
def auth_headers(token):
    return {"Authorization": f"Bearer {token('user-1')}"}
