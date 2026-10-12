"""Phase 2 development-authentication regression tests.

Covers: safe bypass activation, disabled-by-default behaviour, unsafe-config
rejection (non-loopback origin, proxy headers, missing opt-in), normal
production bearer validation still working, and owner isolation under bypass.
"""

import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.dev_auth import DevAuthConfig, load_dev_auth_config, request_is_loopback

SECRET = "phase-two-dev-auth-signing-secret-32bytes-minimum"
DEV_OWNER = "e2e-operator"


def _token(subject, secret=SECRET, exp_delta=600):
    enc = lambda v: base64.urlsafe_b64encode(v).rstrip(b"=").decode()
    header = enc(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = enc(json.dumps({"sub": subject, "exp": int(time.time()) + exp_delta}).encode())
    si = f"{header}.{payload}"
    sig = enc(hmac.new(secret.encode(), si.encode(), hashlib.sha256).digest())
    return f"{si}.{sig}"


def _app(tmp_path, dev_config):
    return create_app(tmp_path / "wf.sqlite3", SECRET, dev_auth=dev_config)


def _loopback_client(app):
    # Starlette's TestClient lets us pretend the request came from loopback.
    return TestClient(app, client=("127.0.0.1", 11111))


def _remote_client(app):
    return TestClient(app, client=("203.0.113.9", 22222))


# --- Pure config / loopback helper behaviour ---------------------------------

def test_config_disabled_by_default(monkeypatch):
    for v in ("DEFENCE_STUDIO_DEV_MODE", "DEFENCE_STUDIO_DEV_AUTH_BYPASS", "DEFENCE_STUDIO_DEV_AUTH_OWNER"):
        monkeypatch.delenv(v, raising=False)
    cfg = load_dev_auth_config()
    assert cfg.enabled is False and cfg.safe is False


def test_config_requires_both_flags(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_DEV_AUTH_BYPASS", "1")
    monkeypatch.delenv("DEFENCE_STUDIO_DEV_MODE", raising=False)
    assert load_dev_auth_config().safe is False
    monkeypatch.setenv("DEFENCE_STUDIO_DEV_MODE", "1")
    monkeypatch.setenv("DEFENCE_STUDIO_DEV_AUTH_OWNER", "e2e-operator")
    assert load_dev_auth_config().safe is True


def test_loopback_helper_rejects_proxy_headers():
    assert request_is_loopback("127.0.0.1", {}) is True
    assert request_is_loopback("127.0.0.1", {"x-forwarded-for": "1.2.3.4"}) is False
    assert request_is_loopback("::1", {"forwarded": "for=1.2.3.4"}) is False
    assert request_is_loopback("203.0.113.9", {}) is False
    assert request_is_loopback(None, {}) is False


# --- Disabled by default: production behaviour is unchanged -------------------

def test_disabled_bypass_requires_token_even_on_loopback(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=False, owner_id=""))
    with _loopback_client(app) as c:
        assert c.get("/api/jobs").status_code == 401


def test_production_token_still_works_when_bypass_disabled(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=False, owner_id=""))
    with _loopback_client(app) as c:
        r = c.get("/api/jobs", headers={"Authorization": f"Bearer {_token('user-1')}"})
        assert r.status_code == 200
        assert r.json() == {"jobs": []}


# --- Safe bypass activation (opt-in + loopback) ------------------------------

def test_safe_bypass_allows_loopback_without_token(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=True, owner_id=DEV_OWNER))
    with _loopback_client(app) as c:
        r = c.get("/api/jobs")
        assert r.status_code == 200
        assert "jobs" in r.json()


def test_bypass_rejected_for_non_loopback_origin(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=True, owner_id=DEV_OWNER))
    with _remote_client(app) as c:
        assert c.get("/api/jobs").status_code == 401


def test_bypass_rejected_when_proxy_header_present(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=True, owner_id=DEV_OWNER))
    with _loopback_client(app) as c:
        r = c.get("/api/jobs", headers={"X-Forwarded-For": "203.0.113.9"})
        assert r.status_code == 401


def test_invalid_token_still_rejected_under_bypass(tmp_path):
    # A supplied-but-invalid token must never silently fall through to bypass.
    app = _app(tmp_path, DevAuthConfig(enabled=True, owner_id=DEV_OWNER))
    with _loopback_client(app) as c:
        r = c.get("/api/jobs", headers={"Authorization": "Bearer not.a.token"})
        assert r.status_code == 401


def test_real_token_subject_wins_over_bypass_owner(tmp_path):
    # With a valid token for a different owner, that owner is used (not the dev owner).
    app = _app(tmp_path, DevAuthConfig(enabled=True, owner_id=DEV_OWNER))
    with _loopback_client(app) as c:
        # create a job as user-1 via a real token
        r = c.post("/api/jobs", json={"topic": "t", "language": "en"},
                   headers={"Authorization": f"Bearer {_token('user-1')}", "Idempotency-Key": "k1"})
        assert r.status_code == 201
        # bypass session (no token) is the dev owner, which must NOT see user-1's job
        assert c.get("/api/jobs").json() == {"jobs": []}


# --- Owner isolation under bypass --------------------------------------------

def test_bypass_owner_isolation(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=True, owner_id=DEV_OWNER))
    with _loopback_client(app) as c:
        # dev owner creates a job (no token needed)
        r = c.post("/api/jobs", json={"topic": "t", "language": "en"},
                   headers={"Idempotency-Key": "k1"})
        assert r.status_code == 201
        job_id = r.json()["job_id"]
        # a different real owner cannot see it
        r2 = c.get(f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {_token('other')}"})
        assert r2.status_code == 404


# --- Dev-session endpoint -----------------------------------------------------

def test_dev_session_returns_token_only_on_safe_loopback(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=True, owner_id=DEV_OWNER))
    with _loopback_client(app) as c:
        r = c.get("/api/dev/session")
        assert r.status_code == 200
        body = r.json()
        assert body["owner_id"] == DEV_OWNER and body["token_type"] == "Bearer"
        # the minted token actually authenticates
        r2 = c.get("/api/jobs", headers={"Authorization": f"Bearer {body['token']}"})
        assert r2.status_code == 200


def test_dev_session_404_when_disabled(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=False, owner_id=""))
    with _loopback_client(app) as c:
        assert c.get("/api/dev/session").status_code == 404


def test_dev_session_404_for_non_loopback(tmp_path):
    app = _app(tmp_path, DevAuthConfig(enabled=True, owner_id=DEV_OWNER))
    with _remote_client(app) as c:
        assert c.get("/api/dev/session").status_code == 404
