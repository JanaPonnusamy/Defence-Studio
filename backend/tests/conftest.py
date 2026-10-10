import base64
import hashlib
import hmac
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app

SECRET = "phase-one-test-signing-secret-32-bytes-minimum"
os.environ.setdefault("DEFENCE_STUDIO_AUTH_SECRET", SECRET)


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
