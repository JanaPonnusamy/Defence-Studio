"""Post-implementation verification gate: secret handling and isolation (offline, no network)."""
import json
import logging
import pathlib

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.providers.openai_text import OpenAiTextProvider, ProviderError, scrub_secrets
from defence_studio.services.youtube import YouTubeService
from test_scene_service_m1 import OWNER, approved_script  # noqa: F401
from conftest import SECRET, access_token

KEY = "sk-proj-GATEKEY1234567890abcd"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_SECRET_KEY", Fernet.generate_key().decode())


def _provider(body: str, status: int = 401):
    return OpenAiTextProvider(api_key=KEY, client_factory=lambda **kw: httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, text=body)), **kw))


def test_provider_rejection_never_echoes_key_material():
    body = json.dumps({"error": {"message": f"Incorrect API key provided: {KEY}. Also sk-other-ZZZZ9999 Bearer {KEY}"}})
    with pytest.raises(ProviderError) as ei:
        _provider(body).generate(model="gpt-4o-mini", system_prompt="s", user_prompt="u")
    msg = str(ei.value)
    assert KEY not in msg and "GATEKEY" not in msg and "ZZZZ9999" not in msg and "HTTP 401" in msg
    assert scrub_secrets("plain text") == "plain text"


def test_credential_flow_leaks_nothing_to_logs_or_responses(approved_script, caplog):
    s = approved_script
    caplog.set_level(logging.DEBUG)
    app = create_app(s["db"], SECRET, s["root"])
    mine = {"Authorization": f"Bearer {access_token(OWNER)}"}
    with TestClient(app) as client:
        r1 = client.put("/api/settings/credentials/openai", json={"value": KEY}, headers=mine)
        r2 = client.get("/api/settings/credentials", headers=mine)
        r3 = client.get(f"/api/jobs/{s['job']}/usage", headers=mine)
        r4 = client.put("/api/settings/credentials/openai", json={"value": KEY[:3]}, headers=mine)
    blob = "".join(r.text for r in (r1, r2, r3, r4)) + caplog.text
    assert KEY not in blob and "GATEKEY" not in blob
    assert r2.json()["credentials"][0]["last4"] == KEY[-4:]


def test_stored_database_and_artifacts_hold_no_plaintext_key(approved_script):
    s = approved_script
    YouTubeService(s["db"], s["root"]).set_credential(OWNER, "openai", KEY)
    needle = KEY.encode()
    assert needle not in pathlib.Path(s["db"]).read_bytes()
    wal = pathlib.Path(str(s["db"]) + "-wal")
    if wal.exists():
        assert needle not in wal.read_bytes()
    for p in pathlib.Path(s["root"]).rglob("*"):
        if p.is_file() and p.stat().st_size < 5_000_000:
            assert needle not in p.read_bytes(), p


def test_frontend_sources_embed_no_secret_shaped_strings():
    root = pathlib.Path(__file__).resolve().parents[2] / "frontend"
    import re
    pat = re.compile(r"sk-[A-Za-z0-9]{16,}|GOCSPX-[A-Za-z0-9_-]{10,}|AIza[0-9A-Za-z_-]{30,}|ya29\.[0-9A-Za-z_-]{20,}")
    for p in list(root.rglob("*.js")) + list(root.rglob("*.html")) + list(root.rglob("*.css")):
        if "node_modules" in p.parts or "tests" in p.parts:
            continue
        assert not pat.search(p.read_text(encoding="utf-8", errors="ignore")), p
