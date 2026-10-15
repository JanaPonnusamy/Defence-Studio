"""Gate audit: stale script bindings, no provider calls on read paths, no paid request without credentials."""
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, access_token
from defence_studio.api.app import create_app
from defence_studio.database import connect
from defence_studio.providers.openai_text import OpenAiTextProvider, ProviderError
from defence_studio.services.factcheck import FactcheckService, factcheck_staleness
from defence_studio.services.jobs import JobService
from test_factcheck_engine import OWNER, STATEMENT, SUPPORT_A, SUPPORT_B, _claim, _job, _source


def _approved_package(tmp_path):
    db = _job(tmp_path)
    subject = JobService(db).list_for_owner(OWNER)[0]["job_id"]
    service = FactcheckService(db, tmp_path / "store")
    service.enqueue(OWNER, "c", subject, "claims", {
        "claims": [_claim(STATEMENT, claim_id="keep")],
        "sources": [_source("origin", STATEMENT), _source("a", SUPPORT_A, publisher="A"),
                    _source("b", SUPPORT_B, publisher="B")],
        "collection": {"attempted": True, "errors": []}})
    service.run_pending()
    service.enqueue(OWNER, "p1", subject, "package", {"topic": "Northwind drill"})
    service.run_pending()
    package = service.latest_package(subject, OWNER)["package"]
    service.approve(subject, OWNER, package["package_id"], "Reviewed.")
    return db, subject, service, package


def test_script_revision_without_binding_or_after_new_package_is_stale(tmp_path):
    db, subject, service, package = _approved_package(tmp_path)
    with closing(connect(db)) as c:
        # no binding recorded for this revision -> cannot claim to match the approved package
        assert "no matching approval" in factcheck_staleness(c, subject, "ta", 1)
        c.execute("INSERT INTO verification_script_bindings(job_id, language, version, package_id, package_sha256) "
                  "VALUES(?,?,?,?,?)", (subject, "ta", 1, package["package_id"], package["content_sha256"]))
        c.commit()
        assert factcheck_staleness(c, subject, "ta", 1) is None             # bound to the current approved package
    service.enqueue(OWNER, "p2", subject, "package", {"topic": "Northwind drill revised"})
    service.run_pending()
    with closing(connect(db)) as c:
        assert "changed after this script revision" in factcheck_staleness(c, subject, "ta", 1)


def test_no_fact_check_package_means_no_staleness_claim(tmp_path):
    db = _job(tmp_path)
    subject = JobService(db).list_for_owner(OWNER)[0]["job_id"]
    with closing(connect(db)) as c:
        assert factcheck_staleness(c, subject, "ta", 1) is None


def test_reading_provider_status_and_package_makes_no_provider_call(tmp_path, monkeypatch):
    """Opening the Fact Check tab = GET requests only. Any outbound HTTP would fail this test."""
    def no_network(*a, **k):
        raise AssertionError("outbound HTTP during a read path")
    import socket
    import urllib.request
    # TestClient is itself an httpx.Client (in-process transport), so block the REAL transports only.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setenv("DEFENCE_STUDIO_REVERSE_IMAGE_URL", "https://gateway.example/r")
    monkeypatch.setenv("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL", "https://search.example/q")
    monkeypatch.setenv("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY", "k" * 12)
    db = _job(tmp_path)
    subject = JobService(db).list_for_owner(OWNER)[0]["job_id"]
    app = create_app(db, SECRET, storage_root_path=tmp_path / "store")
    headers = {"Authorization": f"Bearer {access_token(OWNER)}"}
    with TestClient(app) as client:
        routes = [r.path for r in app.routes if "factcheck" in getattr(r, "path", "")
                  and "GET" in getattr(r, "methods", set()) and "{" not in r.path.replace("{subject_job_id}", "")]
        assert routes, "expected Fact Check GET routes"
        for path in routes:
            res = client.get(path.replace("{subject_job_id}", subject), headers=headers)
            assert res.status_code < 500, path


def test_paid_provider_without_key_never_sends_a_request(monkeypatch):
    sent = []
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda *a, **k: sent.append(1))
    monkeypatch.delenv("DEFENCE_STUDIO_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAiTextProvider(api_key="")
    with pytest.raises(ProviderError) as exc:
        provider.generate(model="gpt-4o-mini", system_prompt="s", user_prompt="u")
    assert exc.value.code == "missing_api_key" and sent == []
