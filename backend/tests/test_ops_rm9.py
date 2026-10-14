"""RM-9: pipeline overview, usage report, per-owner credentials, path guard, migration."""
import json
import os
import sqlite3

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.database import SCHEMA_VERSION, connect, initialize_database
from defence_studio.providers.openai_text import OpenAiTextProvider
from defence_studio.services.script import ScriptService
from defence_studio.services.youtube import YouTubeService
from defence_studio.storage.artifacts import ArtifactStore, StoragePathError
from test_scene_service_m1 import OWNER, SCENE_CONFIG, _generate_plan, _plan_sha, approved_script  # noqa: F401
from conftest import SECRET, access_token


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_SECRET_KEY", Fernet.generate_key().decode())


def test_credentials_encrypted_at_rest_never_returned_and_owner_scoped(approved_script):
    s = approved_script
    svc = YouTubeService(s["db"], s["root"])
    out = svc.set_credential(OWNER, "openai", "sk-test-ABCDEFGH1234")
    cred = out["credentials"][0]
    assert cred["configured"] and cred["last4"] == "1234" and cred["source"] == "owner"
    assert "sk-test" not in json.dumps(out)
    with connect(s["db"]) as c:
        raw = c.execute("SELECT encrypted_value FROM provider_credentials").fetchone()[0]
    assert "sk-test" not in raw
    assert svc.credential_status("someone-else")["credentials"][0]["configured"] is False
    # the script service builds its provider with the OWNER's key (and not for others)
    scripts = ScriptService(s["db"], s["root"])
    provider = scripts._resolve_provider(OWNER)
    assert isinstance(provider, OpenAiTextProvider) and provider.api_key == "sk-test-ABCDEFGH1234"
    assert scripts._resolve_provider("someone-else").api_key != "sk-test-ABCDEFGH1234"
    assert svc.delete_credential(OWNER, "openai")["credentials"][0]["configured"] is False


def test_credential_validation_and_missing_key_fails_closed(approved_script, monkeypatch):
    s = approved_script
    svc = YouTubeService(s["db"], s["root"])
    from defence_studio.services.integration import IntegrationValidationError
    with pytest.raises(IntegrationValidationError, match="unknown|Unknown"):
        svc.set_credential(OWNER, "elevenlabs", "xxxxxxxxxxxx")
    with pytest.raises(IntegrationValidationError, match="invalid"):
        svc.set_credential(OWNER, "openai", "short")
    monkeypatch.delenv("DEFENCE_STUDIO_SECRET_KEY")
    monkeypatch.delenv("DEFENCE_STUDIO_YT_TOKEN_KEY", raising=False)
    with pytest.raises(IntegrationValidationError, match="SECRET_KEY"):
        svc.set_credential(OWNER, "openai", "sk-test-ABCDEFGH1234")


def test_usage_report_keeps_categories_separate_and_never_sums_unmeasured(approved_script):
    s = approved_script
    svc = YouTubeService(s["db"], s["root"])
    first = svc.usage_report(s["job"], OWNER)["text_generation"]  # the fixture already generated one revision
    assert first["total_inr"] == 0.05 and len(first["calls"]) == 1
    s["script"].generate(s["job"], OWNER, __import__("test_script_phase3").GOOD_CONFIG, "u-1")
    report = svc.usage_report(s["job"], OWNER)
    t = report["text_generation"]
    assert t["total_inr"] == 0.1 and t["within_target"] is True and t["target_inr"] == 10.0
    assert report["image_generation"]["enabled"] is False and report["tts"]["total_inr"] is None
    assert "guarantee" in t["note"]
    with pytest.raises(Exception):
        svc.usage_report(s["job"], "someone-else")


def test_pipeline_overview_is_derived_from_persisted_state(approved_script):
    s = approved_script
    svc = YouTubeService(s["db"], s["root"])
    before = {x["stage"]: x for x in svc.pipeline_overview(s["job"], OWNER, "ta")["stages"]}
    assert before["scene_plan"]["status"] == "NONE" and before["script"]["approved_version"] == 1
    plan = _generate_plan(s)
    s["scene"].approve(s["job"], OWNER, "ta", plan["version"], _plan_sha(s["scene"], s["job"], plan["version"]), "ok", "ov-ap")
    fresh = YouTubeService(s["db"], s["root"])  # a "new device": nothing cached
    after = {x["stage"]: x for x in fresh.pipeline_overview(s["job"], OWNER, "ta")["stages"]}
    assert after["scene_plan"]["status"] == "APPROVED" and after["audio_set"]["status"] == "NONE"
    assert after["publishing"]["status"] == "NONE"


def test_windows_deep_path_is_a_clear_error_not_a_crash(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows MAX_PATH guard")
    deep = tmp_path
    for _ in range(12):
        deep = deep / ("d" * 20)
    store = ArtifactStore(deep)
    with pytest.raises(StoragePathError, match="Windows limit"):
        store.write_bytes("job-1", "jobs/job-1/x/" + "f" * 80 + ".json", b"{}", "application/json")
    ok = ArtifactStore(tmp_path / "s")
    assert ok.write_bytes("job-1", "jobs/job-1/x/y.json", b"{}", "application/json").sha256


def test_v9_database_upgrades_to_current_preserving_data(approved_script):
    s = approved_script
    with sqlite3.connect(s["db"]) as c:
        c.execute("DROP TABLE provider_credentials")
        c.execute("UPDATE schema_info SET version = 9")
    before = sqlite3.connect(s["db"]).execute("SELECT COUNT(*) FROM script_revisions").fetchone()[0]
    initialize_database(s["db"])
    with sqlite3.connect(s["db"]) as c:
        assert c.execute("SELECT version FROM schema_info").fetchone()[0] == SCHEMA_VERSION == 10
        assert c.execute("SELECT COUNT(*) FROM script_revisions").fetchone()[0] == before >= 1
        assert c.execute("SELECT COUNT(*) FROM provider_credentials").fetchone()[0] == 0


def test_http_settings_and_reports_auth(approved_script):
    s = approved_script
    app = create_app(s["db"], SECRET, s["root"])
    mine = {"Authorization": f"Bearer {access_token(OWNER)}"}
    other = {"Authorization": f"Bearer {access_token('intruder')}"}
    with TestClient(app) as client:
        assert client.get("/api/settings/credentials").status_code == 401
        put = client.put("/api/settings/credentials/openai", json={"value": "sk-live-WXYZ9876"}, headers=mine)
        assert put.status_code == 200 and "sk-live" not in put.text and put.json()["credentials"][0]["last4"] == "9876"
        assert client.get("/api/settings/credentials", headers=other).json()["credentials"][0]["configured"] is False
        assert client.put("/api/settings/credentials/openai", json={"value": "x" * 3}, headers=mine).status_code == 422
        assert client.get(f"/api/jobs/{s['job']}/usage", headers=other).status_code == 404
        assert client.get(f"/api/jobs/{s['job']}/pipeline/ta", headers=other).status_code == 404
        usage = client.get(f"/api/jobs/{s['job']}/usage", headers=mine)
        assert usage.status_code == 200 and usage.json()["currency"] == "INR"
        assert client.get(f"/api/jobs/{s['job']}/pipeline/ta", headers=mine).status_code == 200
