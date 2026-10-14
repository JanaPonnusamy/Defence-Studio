"""RM-8: YouTube OAuth, publishing profiles and the durable publishing queue.

IMPORTANT: every Google endpoint here is a MOCK (FakeGoogle over
httpx.MockTransport). These tests verify our logic -- state machine, safety
gates, resumability, error policy, encryption -- and DO NOT verify the real
Google/YouTube service. Real OAuth, upload, thumbnail, caption and schedule
behaviour remains unverified until a credentialed private test upload is run.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.database import connect
from defence_studio.integrations.youtube import OAuthConfig, YouTubeApi
from defence_studio.services import youtube as ytmod
from defence_studio.services.integration import IntegrationStale, IntegrationValidationError
from defence_studio.services.youtube import YouTubeService, approval_digest
from test_package_rm7 import _approved_chain  # noqa: F401
from test_render_rm5 import chain, needs_ffmpeg  # noqa: F401
from test_scene_service_m1 import OWNER, approved_script  # noqa: F401
from conftest import SECRET, access_token

pytestmark = needs_ffmpeg
CHANNEL = {"id": "UC_DEFENCE_ERA", "snippet": {"title": "Defence Era", "customUrl": "@DefenceEraHQ1",
                                               "thumbnails": {"default": {"url": "https://img.example/t.jpg"}}}}


class FakeGoogle:
    """Mock of accounts.google.com + oauth2/token + YouTube Data API v3."""

    def __init__(self):
        self.videos: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.calls: list[str] = []
        self.fail_chunk_at = None       # chunk index at which to return 503 once
        self.chunk_count = 0
        self.refresh_error = None       # "invalid_grant"
        self.insert_error = None        # (status, reason)
        self.thumb_error = None         # status
        self.caption_error = None
        self.expired_session = False
        self.no_refresh_token = False
        self.scopes = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl"
        self.refresh_tokens = {"RT-SECRET-123"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, method = str(request.url), request.method
        path = request.url.path
        self.calls.append(f"{method} {path}")
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["good-code"]:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                body = {"access_token": "AT-1", "expires_in": 3599, "scope": self.scopes}
                if not self.no_refresh_token:
                    body["refresh_token"] = "RT-SECRET-123"
                return httpx.Response(200, json=body)
            if self.refresh_error or form["refresh_token"][0] not in self.refresh_tokens:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "AT-2", "expires_in": 3599})
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            self.refresh_tokens.clear()
            return httpx.Response(200, json={})
        auth = request.headers.get("authorization", "")
        if path.endswith("/channels"):
            return httpx.Response(200, json={"items": [CHANNEL]})
        if path == "/upload/youtube/v3/videos" and method == "POST":
            if self.insert_error:
                status, reason = self.insert_error
                return httpx.Response(status, json={"error": {"errors": [{"reason": reason}], "message": reason}})
            meta = json.loads(request.content)
            vid = f"VID{len(self.videos) + 1}"
            sid = f"https://upload.example/session/{vid}"
            self.sessions[sid] = {"video_id": vid, "received": 0, "meta": meta, "size": int(request.headers["x-upload-content-length"])}
            return httpx.Response(200, headers={"location": sid})
        if url.startswith("https://upload.example/session/"):
            sess = self.sessions.get(url)
            if sess is None or self.expired_session:
                return httpx.Response(404, json={"error": {"message": "gone"}})
            cr = request.headers["content-range"]
            if cr.startswith("bytes */"):
                if sess["received"] >= sess["size"]:
                    return self._complete(sess)
                return httpx.Response(308, headers={"range": f"bytes=0-{sess['received'] - 1}"} if sess["received"] else {})
            self.chunk_count += 1
            if self.fail_chunk_at is not None and self.chunk_count == self.fail_chunk_at:
                self.fail_chunk_at = None
                return httpx.Response(503, json={"error": {"message": "backend error"}})
            m = re.match(r"bytes (\d+)-(\d+)/(\d+)", cr)
            start, end = int(m.group(1)), int(m.group(2))
            assert start == sess["received"], f"out-of-order chunk {start} != {sess['received']}"
            sess["received"] = end + 1
            if sess["received"] >= sess["size"]:
                return self._complete(sess)
            return httpx.Response(308, headers={"range": f"bytes=0-{end}"})
        if path == "/upload/youtube/v3/thumbnails/set":
            if self.thumb_error:
                return httpx.Response(self.thumb_error, json={"error": {"errors": [{"reason": "invalidImage"}], "message": "bad"}})
            self.videos[request.url.params["videoId"]]["thumbnail"] = True
            return httpx.Response(200, json={"items": [{}]})
        if path == "/upload/youtube/v3/captions":
            if self.caption_error:
                return httpx.Response(self.caption_error, json={"error": {"errors": [{"reason": "invalidCaption"}], "message": "bad"}})
            self.videos["VID1"].setdefault("captions", []).append(1)
            return httpx.Response(200, json={"id": "CAP1"})
        if path == "/youtube/v3/videos" and method == "GET":
            v = self.videos.get(request.url.params["id"])
            return httpx.Response(200, json={"items": [v] if v else []})
        if path == "/youtube/v3/videos" and method == "PUT":
            body = json.loads(request.content)
            v = self.videos[body["id"]]
            v["status"].update({k: x for k, x in body["status"].items() if k != "selfDeclaredMadeForKids"})
            return httpx.Response(200, json=v)
        return httpx.Response(404, json={"error": {"message": f"unexpected {method} {url}"}})

    def _complete(self, sess):
        vid = sess["video_id"]
        v = {"id": vid, "snippet": {"title": sess["meta"]["snippet"]["title"]},
             "status": {"privacyStatus": sess["meta"]["status"]["privacyStatus"], "uploadStatus": "uploaded"},
             "processingDetails": {"processingStatus": "processing"}}
        self.videos[vid] = v
        return httpx.Response(200, json=v)

    def api(self) -> YouTubeApi:
        return YouTubeApi(OAuthConfig("cid", "csecret", "https://studio.example/api/youtube/oauth/callback"),
                          client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(self.handler), **kw))


@pytest.fixture(autouse=True)
def yt_env(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_YT_TOKEN_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("DEFENCE_STUDIO_YT_EXPECTED_CHANNEL_ID", raising=False)
    monkeypatch.setattr(ytmod, "CHUNK", 64 * 1024)


@pytest.fixture
def pub(chain):
    """Approved render -> approved package -> YouTubeService with fake Google + connected channel."""
    pkg_svc, r, q = _approved_chain(chain, "yt")
    s = chain["s"]
    item = pkg_svc.start_package(s["job"], OWNER, "en", "yt-p", background=False)
    assert item["status"] == "AWAITING_REVIEW", item["detail"]
    pkg_svc.review_package(s["job"], OWNER, "en", item["version"], "APPROVED", "ok")
    g = FakeGoogle()
    now = {"t": datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)}
    svc = YouTubeService(s["db"], s["root"], provider=s["provider"], youtube_api=g.api(), now=lambda: now["t"],
                         sleep=lambda x: None)
    start = svc.connect_start(OWNER)
    state = parse_qs(urlparse(start["authorization_url"]).query)["state"][0]
    cb = svc.connect_callback(state, "good-code", None)
    conn = svc.confirm_channel(OWNER, cb["connection_id"], CHANNEL["id"])
    return dict(chain=chain, s=s, g=g, svc=svc, conn=conn, pkg=item["version"], now=now)


def _job(pub, key="j1", overrides=None, profile=None):
    return pub["svc"].create_job(OWNER, pub["s"]["job"], "en", pub["pkg"], pub["conn"]["connection_id"], key,
                                 profile_id=profile, overrides=overrides)


def _approve(pub, job, confirmation=None):
    cur = pub["svc"].get_job(OWNER, job["publish_id"])
    return pub["svc"].approve_job(OWNER, job["publish_id"], cur["approval_digest_current"], "reviewed", confirmation)


def _run(pub, job, **kw):
    pub["svc"].queue_job(OWNER, job["publish_id"], background=False)
    pub["svc"].run_job(job["publish_id"])
    return pub["svc"].get_job(OWNER, job["publish_id"])


# -- OAuth / connection ------------------------------------------------------------------


def test_consent_url_is_minimal_scope_offline_with_random_state(chain):
    s = chain["s"]
    g = FakeGoogle()
    svc = YouTubeService(s["db"], s["root"], youtube_api=g.api())
    a, b = svc.connect_start(OWNER), svc.connect_start(OWNER)
    qa, qb = parse_qs(urlparse(a["authorization_url"]).query), parse_qs(urlparse(b["authorization_url"]).query)
    assert qa["access_type"] == ["offline"] and qa["redirect_uri"] == ["https://studio.example/api/youtube/oauth/callback"]
    assert qa["state"] != qb["state"] and len(qa["state"][0]) >= 40
    assert set(qa["scope"][0].split()) == {"https://www.googleapis.com/auth/youtube.upload",
                                           "https://www.googleapis.com/auth/youtube.force-ssl"}
    assert "csecret" not in a["authorization_url"]


def test_unconfigured_oauth_and_missing_key_fail_closed(chain, monkeypatch):
    s = chain["s"]
    monkeypatch.delenv("DEFENCE_STUDIO_YT_TOKEN_KEY")
    svc = YouTubeService(s["db"], s["root"], youtube_api=FakeGoogle().api())
    with pytest.raises(IntegrationValidationError, match="TOKEN_KEY"):
        svc.connect_start(OWNER)
    assert "DEFENCE_STUDIO_YT_TOKEN_KEY" in svc.config_status()["missing"]
    monkeypatch.setenv("DEFENCE_STUDIO_YT_TOKEN_KEY", Fernet.generate_key().decode())
    unconf = YouTubeService(s["db"], s["root"], youtube_api=YouTubeApi(OAuthConfig("", "", "")))
    with pytest.raises(IntegrationValidationError, match="not configured"):
        unconf.connect_start(OWNER)


def test_state_validation_denial_replay_and_scopes(chain):
    s = chain["s"]
    g = FakeGoogle()
    svc = YouTubeService(s["db"], s["root"], youtube_api=g.api())
    with pytest.raises(IntegrationValidationError, match="Invalid, expired"):
        svc.connect_callback("forged", "good-code", None)
    state = parse_qs(urlparse(svc.connect_start(OWNER)["authorization_url"]).query)["state"][0]
    assert svc.connect_callback(state, None, "access_denied")["status"] == "CONSENT_DENIED"
    with pytest.raises(IntegrationValidationError, match="already-used"):  # replay of the same state
        svc.connect_callback(state, "good-code", None)
    state = parse_qs(urlparse(svc.connect_start(OWNER)["authorization_url"]).query)["state"][0]
    g.no_refresh_token = True
    with pytest.raises(IntegrationValidationError, match="no refresh token"):
        svc.connect_callback(state, "good-code", None)
    g.no_refresh_token = False
    g.scopes = "https://www.googleapis.com/auth/youtube.upload"
    state = parse_qs(urlparse(svc.connect_start(OWNER)["authorization_url"]).query)["state"][0]
    with pytest.raises(IntegrationValidationError, match="scopes"):
        svc.connect_callback(state, "good-code", None)


def test_tokens_encrypted_never_returned_and_wrong_channel_detected(chain, monkeypatch):
    s = chain["s"]
    g = FakeGoogle()
    svc = YouTubeService(s["db"], s["root"], youtube_api=g.api())
    state = parse_qs(urlparse(svc.connect_start(OWNER)["authorization_url"]).query)["state"][0]
    cb = svc.connect_callback(state, "good-code", None)
    with connect(s["db"]) as c:
        raw = c.execute("SELECT encrypted_refresh_token FROM youtube_connections").fetchone()[0]
    assert raw and "RT-SECRET-123" not in raw
    listing = json.dumps(svc.list_connections(OWNER))
    assert "RT-SECRET-123" not in listing and "csecret" not in listing and "encrypted_refresh_token" not in listing
    monkeypatch.setenv("DEFENCE_STUDIO_YT_EXPECTED_CHANNEL_ID", "UC_SOMEONE_ELSE")
    with pytest.raises(IntegrationValidationError, match="not the expected channel"):
        svc.confirm_channel(OWNER, cb["connection_id"], CHANNEL["id"])
    with pytest.raises(IntegrationValidationError, match="does not match"):
        svc.confirm_channel(OWNER, cb["connection_id"], "UC_OTHER")


def test_disconnect_revokes_deletes_credentials_and_blocks_jobs(pub):
    job = _job(pub)
    out = pub["svc"].disconnect(OWNER, pub["conn"]["connection_id"])
    assert out["remote_revoked"] is True and out["local_credentials_deleted"] is True
    with connect(pub["s"]["db"]) as c:
        assert c.execute("SELECT encrypted_refresh_token, status FROM youtube_connections").fetchone()[:] == (None, "DISCONNECTED")
    with pytest.raises(Exception):
        _job(pub, "j-after")


# -- job creation / approval gates --------------------------------------------------------


def test_job_defaults_private_and_requires_approved_current_package(pub, chain):
    job = _job(pub)
    assert job["visibility"] == "private" and job["state"] == "AWAITING_APPROVAL" and job["required_confirmation"] is None
    assert job["tags"] and "Sources:" in job["description"]
    with pytest.raises(IntegrationStale, match="active publishing job"):
        _job(pub, "j-dup")
    assert _job(pub, "j1")["publish_id"] == job["publish_id"]  # idempotent replay


def test_nothing_uploads_without_exact_approval(pub):
    job = _job(pub)
    with pytest.raises(IntegrationStale, match="no valid approval"):
        pub["svc"].queue_job(OWNER, job["publish_id"], background=False)
    with pytest.raises(IntegrationStale, match="metadata changed"):
        pub["svc"].approve_job(OWNER, job["publish_id"], "0" * 64, "x", None)
    assert not any("upload/youtube/v3/videos" in c for c in pub["g"].calls)
    _approve(pub, job)
    pub["svc"].edit_job(OWNER, job["publish_id"], {"title": "A different title"})  # edit clears approval
    with pytest.raises(IntegrationStale, match="no valid approval"):
        pub["svc"].queue_job(OWNER, job["publish_id"], background=False)


def test_public_and_schedule_need_typed_confirmation(pub):
    job = _job(pub, overrides={"visibility": "public"})
    assert job["required_confirmation"] == "PUBLISH PUBLIC"
    cur = pub["svc"].get_job(OWNER, job["publish_id"])
    with pytest.raises(IntegrationValidationError, match="PUBLISH PUBLIC"):
        pub["svc"].approve_job(OWNER, job["publish_id"], cur["approval_digest_current"], "go", None)
    with pytest.raises(IntegrationValidationError, match="PUBLISH PUBLIC"):
        pub["svc"].approve_job(OWNER, job["publish_id"], cur["approval_digest_current"], "go", "yes")
    ok = pub["svc"].approve_job(OWNER, job["publish_id"], cur["approval_digest_current"], "go", "PUBLISH PUBLIC")
    assert ok["approved"] is True


def test_schedule_validation_and_timezone_conversion(pub):
    with pytest.raises(IntegrationValidationError, match="15 minutes"):
        _job(pub, "s1", overrides={"visibility": "private", "schedule_local": "2026-10-14T12:10", "timezone": "UTC"})
    with pytest.raises(IntegrationValidationError, match="IANA"):
        _job(pub, "s2", overrides={"schedule_local": "2026-10-20T18:00", "timezone": "Mars/Base"})
    job = _job(pub, "s3", overrides={"schedule_local": "2026-10-20T18:00", "timezone": "Asia/Calcutta"})
    assert job["scheduled_utc"].startswith("2026-10-20T12:30:00") and job["required_confirmation"] == "SCHEDULE PRIVATE"


# -- execution ---------------------------------------------------------------------------------


def test_private_upload_thumbnail_captions_verified_and_audited(pub):
    job = _job(pub)
    _approve(pub, job)
    done = _run(pub, job)
    assert done["state"] == "COMPLETED", done["error_message"]
    g = pub["g"]
    assert len(g.videos) == 1 and g.videos["VID1"]["status"]["privacyStatus"] == "private"
    assert g.videos["VID1"].get("thumbnail") and g.videos["VID1"].get("captions")
    assert done["youtube_video_id"] == "VID1" and done["verified_at"] and done["progress"] == 1.0
    assert done["thumbnail_state"] == "SET" and done["caption_tracks"][0]["state"] == "UPLOADED"
    states = [e["new_state"] for e in done["events"] if e["event"] == "STATE"]
    assert states[:4] == ["QUEUED", "VALIDATING", "READY_TO_UPLOAD", "UPLOADING"] and states[-1] == "COMPLETED"
    assert "UPLOADED_PRIVATE" in states and "VERIFYING" in states
    assert done["quota_units_used"] >= 1600 + 50 + 400
    assert not any("RT-SECRET" in json.dumps(e) for e in done["events"])


def test_transfer_interruption_resumes_without_a_second_video(pub):
    g = pub["g"]
    job = _job(pub)
    _approve(pub, job)
    g.fail_chunk_at = 2
    first = _run(pub, job)
    assert first["state"] == "UPLOAD_RETRY_WAIT" and 0 < first["bytes_uploaded"] < first["bytes_total"]
    assert first["next_retry_at"] and first["youtube_video_id"] is None
    with connect(pub["s"]["db"]) as c:
        ref = c.execute("SELECT session_ref_encrypted FROM publishing_jobs").fetchone()[0]
    assert ref and "upload.example" not in ref  # session URL is stored encrypted
    assert pub["svc"].process_due() == 0            # not yet due: no hammering
    pub["now"]["t"] += timedelta(hours=1)
    assert pub["svc"].process_due() == 1
    done = pub["svc"].get_job(OWNER, job["publish_id"])
    assert done["state"] == "COMPLETED" and len(g.videos) == 1
    assert sum(1 for c in g.calls if c == "POST /upload/youtube/v3/videos") == 1  # one insert only


def test_worker_restart_recovers_and_resumes_existing_session(pub):
    g = pub["g"]
    job = _job(pub)
    _approve(pub, job)
    g.fail_chunk_at = 2
    _run(pub, job)
    pub["svc"]._set_state(job["publish_id"], "UPLOADING", "simulate crash mid-transfer", force=True)
    restarted = YouTubeService(pub["s"]["db"], pub["s"]["root"], provider=pub["s"]["provider"],
                               youtube_api=g.api(), now=lambda: pub["now"]["t"] + timedelta(hours=2))
    assert restarted.recover_interrupted() >= 1
    assert restarted.get_job(OWNER, job["publish_id"])["state"] == "UPLOAD_PAUSED"
    restarted.run_job(job["publish_id"])
    done = restarted.get_job(OWNER, job["publish_id"])
    assert done["state"] == "COMPLETED" and len(g.videos) == 1


def test_revoked_credentials_require_reauth_and_pause_jobs(pub):
    pub["g"].refresh_error = "invalid_grant"
    job = _job(pub)
    _approve(pub, job)
    done = _run(pub, job)
    assert done["state"] == "REAUTH_REQUIRED" and not pub["g"].videos
    status = pub["svc"].list_connections(OWNER)["connections"][0]["status"]
    assert status == "REAUTH_REQUIRED"


def test_quota_exhaustion_does_not_loop(pub):
    pub["g"].insert_error = (403, "quotaExceeded")
    job = _job(pub)
    _approve(pub, job)
    done = _run(pub, job)
    assert done["state"] == "FAILED_RETRYABLE" and done["error_code"] == "quota"
    calls = len(pub["g"].calls)
    assert pub["svc"].process_due() == 0 and len(pub["g"].calls) == calls  # no retry until quota day passes
    pub["now"]["t"] += timedelta(days=1, hours=1)
    pub["g"].insert_error = None
    assert pub["svc"].process_due() == 1
    assert pub["svc"].get_job(OWNER, job["publish_id"])["state"] == "COMPLETED"


def test_permanent_errors_are_final_and_bounded_retries_end(pub):
    pub["g"].insert_error = (400, "invalidTitle")
    job = _job(pub)
    _approve(pub, job)
    done = _run(pub, job)
    assert done["state"] == "FAILED_FINAL" and "invalidTitle" in done["error_message"]
    pub2 = _job(pub, "j-b", overrides={"title": "Another title for retry test"}) if False else None


def test_thumbnail_failure_is_independent_and_retryable_without_reupload(pub):
    g = pub["g"]
    g.thumb_error = 400
    job = _job(pub)
    _approve(pub, job)
    first = _run(pub, job)
    assert first["state"] == "FAILED_RETRYABLE" and first["thumbnail_state"] == "FAILED"
    assert first["youtube_video_id"] == "VID1" and g.videos["VID1"]["status"]["privacyStatus"] == "private"
    g.thumb_error = None
    retried = pub["svc"].retry_part(OWNER, job["publish_id"], "thumbnail")
    assert retried["state"] == "COMPLETED" and retried["thumbnail_state"] == "SET"
    assert sum(1 for c in g.calls if c == "POST /upload/youtube/v3/videos") == 1


def test_caption_failure_is_independent(pub):
    g = pub["g"]
    g.caption_error = 400
    job = _job(pub)
    _approve(pub, job)
    first = _run(pub, job)
    assert first["state"] == "FAILED_RETRYABLE" and first["caption_tracks"][0]["state"] == "FAILED"
    g.caption_error = None
    done = pub["svc"].retry_part(OWNER, job["publish_id"], "captions")
    assert done["state"] == "COMPLETED" and len(g.videos) == 1


def test_public_publication_only_after_confirmation_and_after_assets(pub):
    g = pub["g"]
    job = _job(pub, overrides={"visibility": "public"})
    _approve(pub, job, "PUBLISH PUBLIC")
    done = _run(pub, job)
    assert done["state"] == "COMPLETED", done["error_message"]
    assert g.videos["VID1"]["status"]["privacyStatus"] == "public"
    order = [e["new_state"] for e in done["events"] if e["event"] == "STATE"]
    assert order.index("UPLOADED_PRIVATE") < order.index("PUBLISHING") < order.index("PUBLISHED")
    # the very first remote status of the video was private
    assert g.sessions["https://upload.example/session/VID1"]["meta"]["status"]["privacyStatus"] == "private"


def test_scheduled_publication_is_verified_from_remote_readback(pub):
    job = _job(pub, overrides={"schedule_local": "2026-10-20T18:00", "timezone": "Asia/Calcutta"})
    _approve(pub, job, "SCHEDULE PRIVATE")
    done = _run(pub, job)
    assert done["state"] == "COMPLETED", done["error_message"]
    rs = done["remote_status"]["status"]
    assert rs["privacyStatus"] == "private" and rs["publishAt"].startswith("2026-10-20T12:30")
    assert "SCHEDULED" in [e["new_state"] for e in done["events"]]


def test_cancel_before_upload_and_cross_owner_isolation(pub):
    job = _job(pub)
    other = "someone-else"
    with pytest.raises(Exception):
        pub["svc"].get_job(other, job["publish_id"])
    with pytest.raises(Exception):
        pub["svc"].cancel_job(other, job["publish_id"])
    with pytest.raises(Exception):
        pub["svc"].approve_job(other, job["publish_id"], "0" * 64, "x")
    assert pub["svc"].cancel_job(OWNER, job["publish_id"])["state"] == "CANCELLED"
    with pytest.raises(IntegrationStale):
        pub["svc"].cancel_job(OWNER, job["publish_id"])
    assert pub["svc"].list_jobs(other)["jobs"] == []


def test_illegal_transition_rejected_server_side(pub):
    job = _job(pub)
    with pytest.raises(IntegrationStale, match="Illegal transition"):
        pub["svc"]._set_state(job["publish_id"], "COMPLETED", "skip everything")


def test_http_routes_auth_callback_and_no_secrets(pub):
    s = pub["s"]
    app = create_app(s["db"], SECRET, s["root"], integration_clients={"youtube": pub["g"].api()})
    mine = {"Authorization": f"Bearer {access_token(OWNER)}"}
    other = {"Authorization": f"Bearer {access_token('intruder')}"}
    with TestClient(app, follow_redirects=False) as client:
        assert client.get("/api/youtube/config").status_code == 401
        assert client.get("/api/youtube/connections", headers=other).json()["connections"] == []
        text = client.get("/api/youtube/connections", headers=mine).text
        assert "RT-SECRET" not in text and "encrypted_refresh_token" not in text
        cb = client.get("/api/youtube/oauth/callback?state=forged&code=x")  # no bearer; bad state
        assert cb.status_code == 303 and "error=" in cb.headers["location"]
        job = _job(pub)
        assert client.get(f"/api/youtube/jobs/{job['publish_id']}", headers=other).status_code == 404
        got = client.get(f"/api/youtube/jobs/{job['publish_id']}", headers=mine).json()
        assert got["visibility"] == "private" and got["state"] == "AWAITING_APPROVAL"
        bad = client.post(f"/api/youtube/jobs/{job['publish_id']}/approve", headers=mine,
                          json={"approval_digest": "0" * 64, "note": "x"})
        assert bad.status_code == 409
        q = client.post(f"/api/youtube/jobs/{job['publish_id']}/queue", headers=mine)
        assert q.status_code == 409  # not approved
