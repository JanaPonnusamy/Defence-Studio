"""Pipeline trace: raw -> clean -> claims -> passages, and fact-preserving normalization."""
import pytest
from fastapi.testclient import TestClient

from defence_studio.analysis.normalize import normalize_bytes
from defence_studio.api.app import create_app
from defence_studio.services.pipeline_trace import PipelineTraceService
from defence_studio.services.review import ReviewNotFound
from test_review_f5 import _basic_job, OWNER
from conftest import SECRET, access_token


def test_trace_links_raw_clean_claims_and_verifies_passages(tmp_path):
    db, root, jobs, store, job, *_ = _basic_job(tmp_path)
    svc = PipelineTraceService(db, root)
    t = svc.pipeline_trace(job, OWNER)
    assert t["available"] and t["sources"] and t["hashes"]["analysis_result_sha256"]
    src = t["sources"][0]
    assert src["raw"]["sha256_verified"] is True and src["raw"]["text"]
    assert src["clean"]["text"] and src["clean"]["sha256"]
    assert src["claims"] and all(c["matches_normalized_text"] for c in src["passage_checks"])
    assert src["claims"][0]["passage"]["source_id"] == src["source_id"]
    with pytest.raises(ReviewNotFound):
        svc.pipeline_trace(job, "someone-else")


def test_trace_api_requires_owner(tmp_path):
    db, root, jobs, store, job, *_ = _basic_job(tmp_path)
    with TestClient(create_app(db, SECRET, root)) as client:
        url = f"/api/jobs/{job}/research/pipeline-trace"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer " + access_token("other")}).status_code == 404
        r = client.get(url, headers={"Authorization": "Bearer " + access_token(OWNER)})
        assert r.status_code == 200 and r.json()["available"] is True


def test_normalization_keeps_figures_units_dates_quotes_and_drops_boilerplate():
    html = (b"<html><head><style>.x{color:red}</style><script>var n=999777;</script></head><body>"
            b"<nav>Home | Subscribe | Cookie settings</nav><article>"
            b"<p>The S-400 Triumf has a range of 400 km and engages targets at 30,000 m on 12 March 2024.</p>"
            b"<p>\"We will deliver on schedule,\" the minister said.</p></article>"
            b"<footer>Copyright 2024 All rights reserved</footer></body></html>")
    out = normalize_bytes(html, "text/html").text
    for fact in ("S-400", "400 km", "30,000 m", "12 March 2024", "\"We will deliver on schedule,\"", "minister"):
        assert fact in out
    assert "999777" not in out and "color:red" not in out
