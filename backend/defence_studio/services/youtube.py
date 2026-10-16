"""RM-8: YouTube connection, publishing profiles and the durable publishing queue.

Safety model (all enforced server-side):
* Nothing is uploaded until the owner explicitly approves *that* job; the
  approval is bound to a digest of the exact package + metadata + visibility.
* Every upload is created PRIVATE. A different final visibility (unlisted /
  public / scheduled) is applied only in a later, separately recorded step,
  after thumbnail/captions, and only if the approval carried the exact typed
  confirmation for that visibility.
* A video id is persisted the moment YouTube returns it; any retry reconciles
  against YouTube before acting, so a retry never creates a second video.
* Tokens: refresh token only as Fernet ciphertext; access tokens live in
  process memory only; nothing secret is returned, logged or stored in events.
* State changes are validated against a transition table and audited.

Mocked-Google tests exercise this logic; they do NOT verify the real service.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from defence_studio.contracts.evidence import canonical
from defence_studio.database import connect
from defence_studio.integrations.youtube import (
    CHUNK, QUOTA_COST, SCOPES, YouTubeApi, YouTubeError, redact,
)
from defence_studio.services.package import PACKAGE_KIND, PackageService, validate_metadata
from defence_studio.services.reports import ReportsMixin
from defence_studio.services.integration import (
    IntegrationNotFound, IntegrationStale, IntegrationValidationError,
)

TRANSITIONS = {
    "DRAFT": {"AWAITING_APPROVAL", "CANCELLED"},
    "AWAITING_APPROVAL": {"QUEUED", "DRAFT", "CANCELLED"},
    "QUEUED": {"VALIDATING", "FAILED_FINAL", "CANCELLED", "CANCEL_REQUESTED"},
    "VALIDATING": {"READY_TO_UPLOAD", "FAILED_FINAL", "FAILED_RETRYABLE", "REAUTH_REQUIRED", "CANCELLED"},
    "READY_TO_UPLOAD": {"UPLOADING", "CANCELLED", "FAILED_RETRYABLE", "FAILED_FINAL"},
    "UPLOADING": {"UPLOAD_PAUSED", "UPLOAD_RETRY_WAIT", "UPLOADED_PRIVATE", "FAILED_RETRYABLE", "FAILED_FINAL",
                  "REAUTH_REQUIRED", "CANCEL_REQUESTED", "CANCELLED"},
    "UPLOAD_PAUSED": {"UPLOADING", "FAILED_FINAL", "CANCELLED"},
    "UPLOAD_RETRY_WAIT": {"UPLOADING", "FAILED_FINAL", "CANCELLED"},
    "UPLOADED_PRIVATE": {"THUMBNAIL_PENDING", "CAPTIONS_PENDING", "PUBLISHING", "VERIFYING", "FAILED_RETRYABLE", "FAILED_FINAL",
                         "REAUTH_REQUIRED", "CANCELLED"},
    "THUMBNAIL_PENDING": {"THUMBNAIL_UPLOADING", "CAPTIONS_PENDING", "PUBLISHING", "VERIFYING", "FAILED_FINAL", "CANCELLED"},
    "THUMBNAIL_UPLOADING": {"CAPTIONS_PENDING", "PUBLISHING", "VERIFYING", "FAILED_RETRYABLE", "FAILED_FINAL",
                            "REAUTH_REQUIRED"},
    "CAPTIONS_PENDING": {"CAPTIONS_UPLOADING", "PUBLISHING", "VERIFYING", "FAILED_FINAL", "CANCELLED"},
    "CAPTIONS_UPLOADING": {"PUBLISHING", "VERIFYING", "FAILED_RETRYABLE", "FAILED_FINAL", "REAUTH_REQUIRED"},
    "PUBLISHING": {"SCHEDULED", "PUBLISHED", "VERIFYING", "FAILED_RETRYABLE", "FAILED_FINAL", "REAUTH_REQUIRED"},
    "SCHEDULED": {"VERIFYING", "FAILED_FINAL"},
    "PUBLISHED": {"VERIFYING", "FAILED_FINAL"},
    "VERIFYING": {"COMPLETED", "FAILED_RETRYABLE", "FAILED_FINAL", "REAUTH_REQUIRED"},
    "FAILED_RETRYABLE": {"QUEUED", "UPLOADING", "THUMBNAIL_PENDING", "CAPTIONS_PENDING", "PUBLISHING",
                         "VERIFYING", "CANCELLED", "FAILED_FINAL"},
    "REAUTH_REQUIRED": {"QUEUED", "CANCELLED"},
    "CANCEL_REQUESTED": {"CANCELLED"},
    "COMPLETED": set(), "FAILED_FINAL": set(), "CANCELLED": set(),
}
TERMINAL = {"COMPLETED", "FAILED_FINAL", "CANCELLED"}
ACTIVE_DUP_BLOCK = set(TRANSITIONS) - TERMINAL - {"DRAFT", "AWAITING_APPROVAL"}
VISIBILITIES = ("private", "unlisted", "public")
YT_CATEGORIES_NUMERIC = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class YtNotFound(IntegrationNotFound):
    pass


class YtValidation(IntegrationValidationError):
    pass


class YtConflict(IntegrationStale):
    pass


def required_confirmation(visibility: str, scheduled: bool) -> str | None:
    """The exact phrase the owner must type to approve a non-private outcome."""
    if visibility == "private" and not scheduled:
        return None
    return ("SCHEDULE " if scheduled else "PUBLISH ") + visibility.upper()


def approval_digest(job: dict) -> str:
    return hashlib.sha256(canonical({
        "package_sha256": job["package_sha256"], "channel": job["target_channel_id"], "title": job["title"],
        "description": job["description"], "tags": json.loads(job["tags_json"]), "category_id": job["category_id"],
        "language": job["default_language"], "visibility": job["visibility"], "scheduled_utc": job["scheduled_utc"],
        "made_for_kids": bool(job["made_for_kids"])})).hexdigest()


class YouTubeService(ReportsMixin, PackageService):
    def __init__(self, database_file, storage_root, *, provider=None, youtube_api: YouTubeApi | None = None,
                 sleep=time.sleep, now=None):
        super().__init__(database_file, storage_root, provider=provider)
        self._api = youtube_api
        self._sleep = sleep
        self._now_fn = now or (lambda: datetime.now(timezone.utc))
        self._token_cache: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    # -- configuration ----------------------------------------------------------

    def api(self) -> YouTubeApi:
        return self._api or YouTubeApi()

    def _fernet(self):
        key = os.environ.get("DEFENCE_STUDIO_YT_TOKEN_KEY", "")
        if not key:
            raise YtValidation("DEFENCE_STUDIO_YT_TOKEN_KEY is not set; refusing to store OAuth tokens unencrypted")
        from cryptography.fernet import Fernet
        try:
            return Fernet(key.encode())
        except Exception as exc:
            raise YtValidation("DEFENCE_STUDIO_YT_TOKEN_KEY is not a valid Fernet key") from exc

    def config_status(self) -> dict:
        api = self.api()
        missing = api.oauth.missing()
        if not os.environ.get("DEFENCE_STUDIO_YT_TOKEN_KEY"):
            missing.append("DEFENCE_STUDIO_YT_TOKEN_KEY")
        return {"configured": not missing, "missing": missing, "scopes": list(SCOPES),
                "redirect_uri": api.oauth.redirect_uri or None,
                "expected_channel_id": os.environ.get("DEFENCE_STUDIO_YT_EXPECTED_CHANNEL_ID") or None,
                "daily_quota_limit": self._daily_limit(),
                "note": "Real OAuth/upload/caption/thumbnail/schedule behaviour is unverified until a connected "
                        "channel completes a private test upload."}

    @staticmethod
    def _daily_limit() -> int:
        try:
            return int(os.environ.get("DEFENCE_STUDIO_YT_DAILY_QUOTA", "10000"))
        except ValueError:
            return 10000

    # -- audit ----------------------------------------------------------------------

    @staticmethod
    def _audit(connection, publish_id, event, prev, new, detail, actor) -> None:
        connection.execute("INSERT INTO publishing_job_events VALUES(?,?,?,?,?,?,?,?)",
                           (str(uuid.uuid4()), publish_id, event, prev, new, detail[:1000], actor, _now()))

    # -- OAuth connection ----------------------------------------------------------------

    def connect_start(self, owner_id: str) -> dict:
        self._fernet()  # fail before sending the user to Google if we could not store the token
        api = self.api()
        state = api.new_state()
        now = self._now_fn()
        with closing(connect(self.database_file)) as c:
            c.execute("INSERT INTO youtube_oauth_states VALUES(?,?,?,?,?,0)",
                      (state, owner_id, api.oauth.redirect_uri, now.isoformat(),
                       (now + timedelta(minutes=10)).isoformat()))
        try:
            return {"authorization_url": api.consent_url(state), "state_expires_in_seconds": 600}
        except YouTubeError as exc:
            raise YtValidation(f"YouTube OAuth is not configured: {exc.message}") from None

    def connect_callback(self, state: str, code: str | None, error: str | None) -> dict:
        """Called from the browser redirect. The state row (single-use, 10 min,
        bound to the initiating owner) is the CSRF/replay protection."""
        with closing(connect(self.database_file)) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM youtube_oauth_states WHERE state=?", (state or "",)).fetchone()
            if row is None or row["used"] or row["expires_at"] < self._now_fn().isoformat():
                c.commit()
                raise YtValidation("Invalid, expired or already-used OAuth state")
            c.execute("UPDATE youtube_oauth_states SET used=1 WHERE state=?", (state,))
            c.commit()
        owner = row["owner_id"]
        if error or not code:
            return {"status": "CONSENT_DENIED", "error": error or "no authorization code returned"}
        api = self.api()
        tokens = api.exchange_code(code)
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise YtValidation("Google returned no refresh token; revoke the app's access in the Google account "
                               "and connect again (offline access is required for background uploads)")
        granted = tokens.get("scope", "")
        missing = [s for s in SCOPES if s not in granted.split()]
        if missing:
            raise YtValidation("Required scopes were not granted: " + ", ".join(missing))
        channel = api.my_channel(tokens["access_token"])
        cid = str(uuid.uuid4())
        enc = self._fernet().encrypt(refresh.encode()).decode()
        now = _now()
        with closing(connect(self.database_file)) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE youtube_connections SET status='DISCONNECTED', encrypted_refresh_token=NULL, updated_at=? "
                      "WHERE owner_id=? AND status IN ('PENDING_CONFIRMATION','CONNECTED','REAUTH_REQUIRED','ERROR')",
                      (now, owner))
            c.execute("INSERT INTO youtube_connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, owner, channel["channel_id"], channel["title"], channel["handle"],
                       channel["thumbnail_url"], enc, granted, "PENDING_CONFIRMATION", None, now, now, None, now))
            c.commit()
        return {"status": "PENDING_CONFIRMATION", "connection_id": cid, "channel": channel,
                "expected_channel_id": os.environ.get("DEFENCE_STUDIO_YT_EXPECTED_CHANNEL_ID") or None}

    def confirm_channel(self, owner_id: str, connection_id: str, channel_id: str) -> dict:
        conn = self._connection(owner_id, connection_id)
        if conn["status"] != "PENDING_CONFIRMATION":
            raise YtConflict(f"Connection is {conn['status']}, not PENDING_CONFIRMATION")
        if channel_id != conn["channel_id"]:
            raise YtValidation("The confirmed channel id does not match the authorized channel")
        expected = os.environ.get("DEFENCE_STUDIO_YT_EXPECTED_CHANNEL_ID")
        if expected and expected != conn["channel_id"]:
            raise YtValidation(f"Authorized channel {conn['channel_id']} is not the expected channel; "
                               "disconnect and authorize with the correct account")
        with closing(connect(self.database_file)) as c:
            c.execute("UPDATE youtube_connections SET status='CONNECTED', confirmed_at=?, updated_at=?, "
                      "last_checked_at=? WHERE connection_id=?", (_now(), _now(), _now(), connection_id))
        return self.connection_view(owner_id, connection_id)

    def disconnect(self, owner_id: str, connection_id: str) -> dict:
        conn = self._connection(owner_id, connection_id)
        revoked = False
        if conn["encrypted_refresh_token"]:
            try:
                token = self._fernet().decrypt(conn["encrypted_refresh_token"].encode()).decode()
                revoked = self.api().revoke(token)
            except Exception:
                revoked = False
        with closing(connect(self.database_file)) as c:
            c.execute("UPDATE youtube_connections SET status='DISCONNECTED', encrypted_refresh_token=NULL, "
                      "updated_at=? WHERE connection_id=?", (_now(), connection_id))
            # Dependent unfinished jobs cannot proceed without credentials.
            c.execute("UPDATE publishing_jobs SET state='REAUTH_REQUIRED', updated_at=? WHERE connection_id=? "
                      "AND state NOT IN ('COMPLETED','FAILED_FINAL','CANCELLED','DRAFT','AWAITING_APPROVAL')",
                      (_now(), connection_id))
        self._token_cache.pop(connection_id, None)
        return {"status": "DISCONNECTED", "remote_revoked": revoked, "local_credentials_deleted": True}

    def _connection(self, owner_id: str, connection_id: str) -> dict:
        with closing(connect(self.database_file)) as c:
            row = c.execute("SELECT * FROM youtube_connections WHERE connection_id=? AND owner_id=?",
                            (connection_id, owner_id)).fetchone()
        if row is None:
            raise YtNotFound("Unknown YouTube connection")
        return dict(row)

    @staticmethod
    def _public_connection(row: dict) -> dict:
        hide = {"encrypted_refresh_token"}
        view = {k: v for k, v in row.items() if k not in hide}
        view["granted_scopes"] = row["granted_scopes"].split()
        view["has_refresh_token"] = bool(row.get("encrypted_refresh_token"))
        return view

    def connection_view(self, owner_id: str, connection_id: str) -> dict:
        return self._public_connection(self._connection(owner_id, connection_id))

    def list_connections(self, owner_id: str) -> dict:
        with closing(connect(self.database_file)) as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM youtube_connections WHERE owner_id=? ORDER BY connected_at DESC", (owner_id,))]
        expected = os.environ.get("DEFENCE_STUDIO_YT_EXPECTED_CHANNEL_ID")
        views = []
        for r in rows:
            v = self._public_connection(r)
            v["channel_mismatch"] = bool(expected and r["channel_id"] != expected)
            views.append(v)
        return {"connections": views, "config": self.config_status()}

    def _access_token(self, connection_id: str) -> str:
        cached = self._token_cache.get(connection_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        with closing(connect(self.database_file)) as c:
            row = c.execute("SELECT * FROM youtube_connections WHERE connection_id=?", (connection_id,)).fetchone()
        if row is None or row["status"] not in ("CONNECTED",) or not row["encrypted_refresh_token"]:
            raise YouTubeError("auth", "YouTube connection is not active")
        refresh = self._fernet().decrypt(row["encrypted_refresh_token"].encode()).decode()
        try:
            tokens = self.api().refresh(refresh)
        except YouTubeError as exc:
            if exc.kind == "auth":
                with closing(connect(self.database_file)) as c:
                    c.execute("UPDATE youtube_connections SET status='REAUTH_REQUIRED', last_error_code='invalid_grant',"
                              " updated_at=? WHERE connection_id=?", (_now(), connection_id))
            raise
        token = tokens["access_token"]
        self._token_cache[connection_id] = (token, time.time() + int(tokens.get("expires_in", 3000)))
        return token

    # -- profiles --------------------------------------------------------------------------

    def save_profile(self, owner_id: str, connection_id: str, data: dict, profile_id: str | None = None) -> dict:
        self._connection(owner_id, connection_id)
        allowed = {"name", "title_prefix", "description_template", "tags", "category_id", "language",
                   "default_visibility", "timezone", "caption_languages", "made_for_kids"}
        unknown = set(data) - allowed
        if unknown:
            raise YtValidation(f"Unknown profile field(s): {sorted(unknown)}")
        vis = data.get("default_visibility", "private")
        if vis not in VISIBILITIES:
            raise YtValidation("default_visibility must be private, unlisted or public")
        tz = data.get("timezone", "Asia/Calcutta")
        try:
            ZoneInfo(tz)
        except Exception:
            raise YtValidation(f"Unknown time zone {tz!r}") from None
        if not str(data.get("category_id", "25")).isdigit():
            raise YtValidation("category_id must be numeric")
        now = _now()
        row = (data.get("name", "Default"), data.get("title_prefix", ""), data.get("description_template", ""),
               json.dumps(data.get("tags", [])), str(data.get("category_id", "25")), data.get("language", "en"),
               vis, tz, json.dumps(data.get("caption_languages", [])), int(bool(data.get("made_for_kids", False))))
        with closing(connect(self.database_file)) as c:
            if profile_id:
                if c.execute("SELECT 1 FROM publishing_profiles WHERE profile_id=? AND owner_id=?",
                             (profile_id, owner_id)).fetchone() is None:
                    raise YtNotFound("Unknown publishing profile")
                c.execute("UPDATE publishing_profiles SET name=?,title_prefix=?,description_template=?,tags_json=?,"
                          "category_id=?,language=?,default_visibility=?,timezone=?,caption_languages_json=?,"
                          "made_for_kids=?,updated_at=? WHERE profile_id=?", (*row, now, profile_id))
            else:
                profile_id = str(uuid.uuid4())
                c.execute("INSERT INTO publishing_profiles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (profile_id, owner_id, connection_id, *row, now, now))
            return dict(c.execute("SELECT * FROM publishing_profiles WHERE profile_id=?", (profile_id,)).fetchone())

    def list_profiles(self, owner_id: str) -> list[dict]:
        with closing(connect(self.database_file)) as c:
            return [dict(r) for r in c.execute("SELECT * FROM publishing_profiles WHERE owner_id=? ORDER BY name",
                                               (owner_id,))]

    # -- job creation / approval ------------------------------------------------------------

    def _job(self, owner_id: str, publish_id: str) -> dict:
        with closing(connect(self.database_file)) as c:
            row = c.execute("SELECT * FROM publishing_jobs WHERE publish_id=? AND owner_id=?",
                            (publish_id, owner_id)).fetchone()
        if row is None:
            raise YtNotFound("Unknown publishing job")
        return dict(row)

    def _view(self, row: dict) -> dict:
        v = {k: x for k, x in row.items() if k not in ("session_ref_encrypted",)}
        v["tags"] = json.loads(v.pop("tags_json"))
        v["remote_status"] = json.loads(v.pop("remote_status_json") or "null")
        v["required_confirmation"] = required_confirmation(row["visibility"], bool(row["scheduled_utc"]))
        v["approved"] = bool(row["approved_at"]) and row["approval_digest"] == approval_digest(row)
        v["is_terminal"] = row["state"] in TERMINAL
        return v

    def create_job(self, owner_id: str, job_id: str, language: str, package_version: int, connection_id: str,
                   idempotency_key: str, *, profile_id: str | None = None, overrides: dict | None = None) -> dict:
        self._check_ownership(job_id, owner_id)
        conn = self._connection(owner_id, connection_id)
        if conn["status"] != "CONNECTED":
            raise YtConflict(f"YouTube connection is {conn['status']}; connect and confirm the channel first")
        key = self._validate_idempotency_key(idempotency_key)
        with closing(connect(self.database_file)) as c:
            replay = c.execute("SELECT * FROM publishing_jobs WHERE owner_id=? AND idempotency_key=?",
                               (owner_id, key)).fetchone()
        if replay:
            return self._view(dict(replay))
        pkg_row = self.get_integration(job_id, owner_id, language, PACKAGE_KIND, package_version)
        if pkg_row["status"] != "APPROVED":
            raise YtConflict(f"Package v{package_version} is {pkg_row['status']}; approve the package first")
        stale = [i for i in self.package_status(job_id, owner_id, language)["items"]
                 if i["version"] == package_version][0]["stale"]
        if stale:
            raise YtConflict("The package no longer matches the current QC-approved render; rebuild it")
        manifest = self.read_integration_body(job_id, owner_id, language, PACKAGE_KIND, package_version)
        meta = manifest["metadata"]
        profile = None
        if profile_id:
            profile = next((p for p in self.list_profiles(owner_id) if p["profile_id"] == profile_id), None)
            if profile is None or profile["connection_id"] != connection_id:
                raise YtNotFound("Unknown publishing profile for this connection")
        ov = overrides or {}
        unknown = set(ov) - {"title", "description", "tags", "category_id", "visibility", "schedule_local", "timezone",
                             "made_for_kids"}
        if unknown:
            raise YtValidation(f"Unknown override(s): {sorted(unknown)}")
        title = (profile["title_prefix"] if profile else "") + ov.get("title", meta["title"])
        description = ov.get("description", meta["description"])
        if profile and profile["description_template"] and "description" not in ov:
            description = profile["description_template"].replace("{description}", description) \
                if "{description}" in profile["description_template"] else description + "\n\n" + profile["description_template"]
        tags = ov.get("tags", list(dict.fromkeys(meta["tags"] + (json.loads(profile["tags_json"]) if profile else []))))
        visibility = ov.get("visibility", profile["default_visibility"] if profile else "private")
        if visibility not in VISIBILITIES:
            raise YtValidation("visibility must be private, unlisted or public")
        tz = ov.get("timezone", profile["timezone"] if profile else "Asia/Calcutta")
        scheduled_utc = None
        if ov.get("schedule_local"):
            try:
                local = datetime.fromisoformat(ov["schedule_local"]).replace(tzinfo=ZoneInfo(tz))
            except Exception:
                raise YtValidation("schedule_local must be an ISO local date-time and timezone a valid IANA zone") from None
            if local.astimezone(timezone.utc) <= self._now_fn() + timedelta(minutes=15):
                raise YtValidation("The scheduled time must be at least 15 minutes in the future")
            scheduled_utc = local.astimezone(timezone.utc).isoformat()
        probs = validate_metadata({"title": title, "description": description, "tags": tags,
                                   "hashtags": meta.get("hashtags", []),
                                   "category_id": ov.get("category_id", profile["category_id"] if profile else meta["category_id"])})
        if "{" in description and "}" in description and any(p in description for p in ("{title}", "{url}", "{TODO}")):
            probs.append("description contains unresolved placeholders")
        if probs:
            raise YtValidation("Metadata invalid: " + "; ".join(probs))
        video = manifest["files"]["video_branded"]
        publish_id = str(uuid.uuid4())
        now = _now()
        with closing(connect(self.database_file)) as c:
            c.execute("BEGIN IMMEDIATE")
            dup = c.execute("SELECT publish_id, state FROM publishing_jobs WHERE job_id=? AND language=? AND "
                            "package_sha256=? AND state NOT IN ('CANCELLED','FAILED_FINAL')",
                            (job_id, language, pkg_row["sha256"])).fetchone()
            if dup:
                c.rollback()
                raise YtConflict(f"This package already has an active publishing job ({dup['publish_id']}, {dup['state']})")
            c.execute("INSERT INTO publishing_jobs(publish_id,owner_id,job_id,language,package_version,package_sha256,"
                      "connection_id,target_channel_id,title,description,tags_json,category_id,default_language,"
                      "visibility,scheduled_utc,timezone,made_for_kids,state,bytes_total,idempotency_key,created_at,"
                      "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (publish_id, owner_id, job_id, language, package_version, pkg_row["sha256"], connection_id,
                       conn["channel_id"], title, description, json.dumps(tags),
                       ov.get("category_id", profile["category_id"] if profile else meta["category_id"]),
                       meta["default_language"], visibility, scheduled_utc, tz,
                       int(bool(ov.get("made_for_kids", profile["made_for_kids"] if profile else 0))),
                       "AWAITING_APPROVAL", video["size_bytes"], key, now, now))
            if manifest["files"].get("subtitles_srt"):
                c.execute("INSERT INTO publishing_caption_tracks VALUES(?,?,?,?,?,?,?,?,?)",
                          (str(uuid.uuid4()), publish_id, language, f"{language} subtitles",
                           manifest["files"]["subtitles_srt"]["sha256"], None, "PENDING", None, now))
            self._audit(c, publish_id, "CREATED", "DRAFT", "AWAITING_APPROVAL",
                        f"package v{package_version}, visibility {visibility}", owner_id)
            c.commit()
        return self._view(self._job(owner_id, publish_id))

    def approve_job(self, owner_id: str, publish_id: str, digest: str, note: str,
                    confirmation: str | None = None, *, background: bool = True) -> dict:
        note = (note or "").strip()
        if not note:
            raise YtValidation("An approval note is required")
        job = self._job(owner_id, publish_id)
        if job["state"] != "AWAITING_APPROVAL":
            raise YtConflict(f"Job is {job['state']}, not AWAITING_APPROVAL")
        if digest != approval_digest(job):
            raise YtConflict("The metadata changed since you reviewed it; reload and review again")
        need = required_confirmation(job["visibility"], bool(job["scheduled_utc"]))
        if need and confirmation != need:
            raise YtValidation(f"Typed confirmation required for a non-private outcome: {need!r}")
        with closing(connect(self.database_file)) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE publishing_jobs SET approved_by=?, approved_at=?, approval_digest=?, approval_note=?, "
                      "updated_at=? WHERE publish_id=?", (owner_id, _now(), digest, note[:500], _now(), publish_id))
            self._audit(c, publish_id, "APPROVED", "AWAITING_APPROVAL", "AWAITING_APPROVAL",
                        f"visibility={job['visibility']} schedule={job['scheduled_utc']}", owner_id)
            c.commit()
        return self._view(self._job(owner_id, publish_id))

    def queue_job(self, owner_id: str, publish_id: str, *, background: bool = True) -> dict:
        job = self._job(owner_id, publish_id)
        if job["state"] not in ("AWAITING_APPROVAL", "FAILED_RETRYABLE", "REAUTH_REQUIRED", "UPLOAD_PAUSED"):
            raise YtConflict(f"Job is {job['state']}; it cannot be queued")
        if not job["approved_at"] or job["approval_digest"] != approval_digest(job):
            raise YtConflict("The job has no valid approval; approve the exact metadata first")
        target = "QUEUED" if job["state"] in ("AWAITING_APPROVAL", "REAUTH_REQUIRED") else None
        if target:
            self._set_state(publish_id, target, "Queued by owner", owner_id)
        if background:
            threading.Thread(target=self.run_job, args=(publish_id,), daemon=True, name="yt-worker").start()
        return self._view(self._job(owner_id, publish_id))

    def edit_job(self, owner_id: str, publish_id: str, changes: dict) -> dict:
        job = self._job(owner_id, publish_id)
        if job["state"] not in ("AWAITING_APPROVAL",):
            raise YtConflict("Only a job awaiting approval can be edited")
        fields = {"title": "title", "description": "description", "visibility": "visibility",
                  "category_id": "category_id"}
        bad = set(changes) - set(fields) - {"tags"}
        if bad:
            raise YtValidation(f"Unknown field(s): {sorted(bad)}")
        if "visibility" in changes and changes["visibility"] not in VISIBILITIES:
            raise YtValidation("visibility must be private, unlisted or public")
        sets, params = [], []
        for k, col in fields.items():
            if k in changes:
                sets.append(f"{col}=?"); params.append(changes[k])
        if "tags" in changes:
            sets.append("tags_json=?"); params.append(json.dumps(changes["tags"]))
        merged = {**job, **{fields.get(k, "tags_json"): (json.dumps(v) if k == "tags" else v) for k, v in changes.items()}}
        probs = validate_metadata({"title": merged["title"], "description": merged["description"],
                                   "tags": json.loads(merged["tags_json"]), "hashtags": [],
                                   "category_id": merged["category_id"]})
        if probs:
            raise YtValidation("Metadata invalid: " + "; ".join(probs))
        with closing(connect(self.database_file)) as c:
            c.execute(f"UPDATE publishing_jobs SET {', '.join(sets)}, approved_at=NULL, approval_digest=NULL, "
                      "approved_by=NULL, updated_at=? WHERE publish_id=?", (*params, _now(), publish_id))
            self._audit(c, publish_id, "EDITED", job["state"], job["state"], "metadata edited; approval cleared",
                        owner_id)
        return self._view(self._job(owner_id, publish_id))

    def cancel_job(self, owner_id: str, publish_id: str) -> dict:
        job = self._job(owner_id, publish_id)
        if job["state"] in TERMINAL:
            raise YtConflict(f"Job is already {job['state']}")
        target = "CANCEL_REQUESTED" if job["state"] in ("UPLOADING", "THUMBNAIL_UPLOADING", "CAPTIONS_UPLOADING",
                                                        "PUBLISHING", "VERIFYING") else "CANCELLED"
        self._set_state(publish_id, target, "Cancelled by owner", owner_id, force=True)
        return self._view(self._job(owner_id, publish_id))

    # -- state machine --------------------------------------------------------------------------

    def _set_state(self, publish_id: str, new: str, detail: str, actor: str = "system", *, force=False,
                   **cols) -> None:
        with closing(connect(self.database_file)) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT state FROM publishing_jobs WHERE publish_id=?", (publish_id,)).fetchone()
            prev = row["state"]
            if prev != new and not force and new not in TRANSITIONS.get(prev, set()):
                c.rollback()
                raise YtConflict(f"Illegal transition {prev} -> {new}")
            sets, params = ["state=?", "updated_at=?"], [new, _now()]
            for k, v in cols.items():
                sets.append(f"{k}=?"); params.append(v)
            c.execute(f"UPDATE publishing_jobs SET {', '.join(sets)} WHERE publish_id=?", (*params, publish_id))
            self._audit(c, publish_id, "STATE", prev, new, detail, actor)
            c.commit()

    def _state(self, publish_id: str) -> str:
        with closing(connect(self.database_file)) as c:
            return c.execute("SELECT state FROM publishing_jobs WHERE publish_id=?", (publish_id,)).fetchone()["state"]

    def _cols(self, publish_id: str, **cols) -> None:
        sets = ", ".join(f"{k}=?" for k in cols) + ", updated_at=?"
        with closing(connect(self.database_file)) as c:
            c.execute(f"UPDATE publishing_jobs SET {sets} WHERE publish_id=?", (*cols.values(), _now(), publish_id))

    def _spend(self, publish_id: str, op: str) -> None:
        units = QUOTA_COST[op]
        with closing(connect(self.database_file)) as c:
            c.execute("UPDATE publishing_jobs SET quota_units_used = quota_units_used + ? WHERE publish_id=?",
                      (units, publish_id))

    def _quota_used_today(self) -> int:
        day = self._now_fn().strftime("%Y-%m-%d")
        with closing(connect(self.database_file)) as c:
            r = c.execute("SELECT COALESCE(SUM(quota_units_used),0) FROM publishing_jobs WHERE substr(updated_at,1,10)=?",
                          (day,)).fetchone()
        return int(r[0])

    # -- the worker --------------------------------------------------------------------------------

    def run_job(self, publish_id: str) -> None:
        """Advance one job as far as it safely can. Idempotent and resumable:
        every step re-reads persisted state; nothing is assumed from memory."""
        if not self._lock.acquire(blocking=False):
            return  # one runner per process; others pick the job up via process_due
        try:
            self._advance(publish_id)
        finally:
            self._lock.release()

    def _advance(self, publish_id: str) -> None:
        try:
            job = self._load(publish_id)
            if job["state"] in TERMINAL or job["state"] in ("AWAITING_APPROVAL", "DRAFT", "REAUTH_REQUIRED",
                                                            "UPLOAD_RETRY_WAIT"):
                return
            if job["state"] == "CANCEL_REQUESTED":
                self._set_state(publish_id, "CANCELLED", "Cancelled", "system")
                return
            if not job["approved_at"] or job["approval_digest"] != approval_digest(job):
                self._set_state(publish_id, "FAILED_FINAL", "Approval missing or no longer matches the metadata")
                return
            if job["state"] in ("QUEUED", "FAILED_RETRYABLE") and not job["youtube_video_id"]:
                if job["state"] == "FAILED_RETRYABLE":
                    self._set_state(publish_id, "QUEUED", "Retrying")
                self._validate(publish_id)
            if self._state(publish_id) in ("READY_TO_UPLOAD", "UPLOAD_PAUSED", "UPLOADING") \
                    and not self._load(publish_id)["youtube_video_id"]:
                self._upload(publish_id)
            job = self._load(publish_id)
            if job["youtube_video_id"] and job["state"] not in TERMINAL:
                self._post_upload(publish_id)
        except YouTubeError as exc:
            self._handle_error(publish_id, exc)
        except IntegrationStale as exc:
            # A rebuilt/reviewed package is required; retries cannot repair rights.
            try:
                state = self._state(publish_id)
                if state == "CANCEL_REQUESTED":
                    self._set_state(publish_id, "CANCELLED", "Cancellation won the precondition failure")
                elif state not in TERMINAL:
                    self._set_state(publish_id, "FAILED_FINAL", f"Precondition failed: {exc}",
                                    error_code="precondition", error_message=str(exc)[:500])
            except YtConflict:
                if self._state(publish_id) not in TERMINAL:
                    raise  # Otherwise cancellation/completion won the transition race.
        except Exception as exc:  # never leave a job silently RUNNING
            self._safe_fail(publish_id, f"internal: {type(exc).__name__}: {exc}")

    def _load(self, publish_id: str) -> dict:
        with closing(connect(self.database_file)) as c:
            return dict(c.execute("SELECT * FROM publishing_jobs WHERE publish_id=?", (publish_id,)).fetchone())

    def _safe_fail(self, publish_id: str, detail: str) -> None:
        try:
            state = self._state(publish_id)
            if state not in TERMINAL:
                self._set_state(publish_id, "FAILED_RETRYABLE" if "FAILED_RETRYABLE" in TRANSITIONS[state]
                                else "FAILED_FINAL", detail, error_code="internal", error_message=detail[:500])
        except Exception:
            pass

    def _handle_error(self, publish_id: str, exc: YouTubeError) -> None:
        job = self._load(publish_id)
        state = job["state"]
        attempts = job["attempts"] + 1
        msg = redact(exc.message)[:500]
        cols = dict(attempts=attempts, error_code=exc.kind, error_message=msg)
        if exc.kind == "auth":
            self._cols(publish_id, **cols)
            if "REAUTH_REQUIRED" in TRANSITIONS[state]:
                self._set_state(publish_id, "REAUTH_REQUIRED", msg)
            return
        if exc.kind == "quota":
            tomorrow = (self._now_fn() + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
            self._cols(publish_id, **cols, next_retry_at=tomorrow.isoformat())
            if "FAILED_RETRYABLE" in TRANSITIONS[state]:
                self._set_state(publish_id, "FAILED_RETRYABLE", "Quota exhausted; will not retry before the quota resets")
            return
        if exc.kind == "part_failed":  # thumbnail/captions: the video stays private; retry them alone
            self._cols(publish_id, **cols)
            if "FAILED_RETRYABLE" in TRANSITIONS[state]:
                self._set_state(publish_id, "FAILED_RETRYABLE", msg)
            return
        if exc.kind in ("rate_limit", "transient", "session_expired") and attempts < job["max_attempts"]:
            delay = exc.retry_after if exc.retry_after else min(2 ** attempts * 5 + random.uniform(0, 3), 900)
            nxt = (self._now_fn() + timedelta(seconds=delay)).isoformat()
            if exc.kind == "session_expired":
                self._cols(publish_id, **cols, next_retry_at=nxt, session_ref_encrypted=None, bytes_uploaded=0,
                           progress=0)
            else:
                self._cols(publish_id, **cols, next_retry_at=nxt)
            target = "UPLOAD_RETRY_WAIT" if state == "UPLOADING" else "FAILED_RETRYABLE"
            if target in TRANSITIONS[state]:
                self._set_state(publish_id, target, f"{exc.kind}: retry at {nxt}")
            return
        self._cols(publish_id, **cols)
        final = exc.kind in ("invalid", "forbidden", "not_found", "no_channel", "unknown") or attempts >= job["max_attempts"]
        target = "FAILED_FINAL" if final else "FAILED_RETRYABLE"
        if target in TRANSITIONS[state]:
            self._set_state(publish_id, target, msg)

    def process_due(self) -> int:
        """Scheduler tick: resume jobs whose retry time has arrived (and
        reconcile jobs interrupted by a restart). Returns how many were run."""
        now = self._now_fn().isoformat()
        with closing(connect(self.database_file)) as c:
            ids = [r["publish_id"] for r in c.execute(
                "SELECT publish_id FROM publishing_jobs WHERE state IN ('UPLOAD_RETRY_WAIT','FAILED_RETRYABLE',"
                "'UPLOAD_PAUSED','QUEUED') AND (next_retry_at IS NULL OR next_retry_at<=?) "
                "AND approved_at IS NOT NULL", (now,))]
        n = 0
        for pid in ids:
            state = self._state(pid)
            if state == "UPLOAD_RETRY_WAIT":
                self._set_state(pid, "UPLOADING", "Retry time reached")
            self.run_job(pid)
            n += 1
        return n

    def recover_interrupted(self) -> int:
        """Startup: jobs left in an in-flight state by a dead process become
        UPLOAD_PAUSED / FAILED_RETRYABLE (explicit, audited). Parent sweep also
        covers the integration artifacts."""
        n = super().recover_interrupted()
        with closing(connect(self.database_file)) as c:
            rows = c.execute("SELECT publish_id, state FROM publishing_jobs WHERE state IN "
                             "('UPLOADING','VALIDATING','THUMBNAIL_UPLOADING','CAPTIONS_UPLOADING','PUBLISHING',"
                             "'VERIFYING')").fetchall()
        for r in rows:
            if r["state"] == "UPLOADING":
                self._set_state(r["publish_id"], "UPLOAD_PAUSED", "Process restarted during upload")
            else:
                self._set_state(r["publish_id"], "FAILED_RETRYABLE", "Process restarted mid-step", force=True)
            n += 1
        return n

    # -- stages -----------------------------------------------------------------------------------------

    def _validate(self, publish_id: str) -> None:
        self._set_state(publish_id, "VALIDATING", "Preflight")
        job = self._load(publish_id)
        conn = self._connection(job["owner_id"], job["connection_id"])
        problems = []
        if conn["status"] != "CONNECTED":
            raise YouTubeError("auth", f"YouTube connection is {conn['status']}")
        if conn["channel_id"] != job["target_channel_id"]:
            problems.append("connected channel differs from the job's target channel")
        expected = os.environ.get("DEFENCE_STUDIO_YT_EXPECTED_CHANNEL_ID")
        if expected and expected != conn["channel_id"]:
            problems.append("connected channel is not the expected channel")
        manifest = None
        try:
            manifest = self.read_integration_body(job["job_id"], job["owner_id"], job["language"], PACKAGE_KIND,
                                                  job["package_version"])
            self.verify_package_asset_rights(job["job_id"], job["owner_id"], job["language"], manifest)
            pkg_row = self.get_integration(job["job_id"], job["owner_id"], job["language"], PACKAGE_KIND,
                                           job["package_version"])
            if pkg_row["sha256"] != job["package_sha256"] or pkg_row["status"] != "APPROVED":
                problems.append("package changed or is no longer approved")
            v = manifest["files"]["video_branded"]
            if self.store.sha256_of(job["job_id"], v["uri"]) != v["sha256"]:
                problems.append("video file failed its integrity check")
        except IntegrationStale as exc:
            problems.append(f"package rights or state changed: {exc}")
        except Exception as exc:
            problems.append(f"package unreadable: {type(exc).__name__}")
        if job["scheduled_utc"] and job["scheduled_utc"] <= self._now_fn().isoformat():
            problems.append("the scheduled time is in the past")
        probs = validate_metadata({"title": job["title"], "description": job["description"],
                                   "tags": json.loads(job["tags_json"]), "hashtags": [], "category_id": job["category_id"]})
        problems += probs
        if self._quota_used_today() + QUOTA_COST["videos.insert"] > self._daily_limit():
            self._cols(job["publish_id"], error_code="quota", error_message="Daily quota budget would be exceeded",
                       next_retry_at=(self._now_fn() + timedelta(days=1)).isoformat())
            self._set_state(publish_id, "FAILED_RETRYABLE", "Local quota guard: not enough daily quota left")
            return
        if problems:
            self._cols(publish_id, error_code="preflight", error_message="; ".join(problems)[:500])
            self._set_state(publish_id, "FAILED_FINAL", "Preflight failed: " + "; ".join(problems))
            return
        self._set_state(publish_id, "READY_TO_UPLOAD", "Preflight passed")

    def _video_path(self, job: dict) -> tuple[Path, dict]:
        manifest = self.read_integration_body(job["job_id"], job["owner_id"], job["language"], PACKAGE_KIND,
                                              job["package_version"])
        self.verify_package_asset_rights(job["job_id"], job["owner_id"], job["language"], manifest)
        ref = manifest["files"]["video_branded"]
        return self.store.job_dir(job["job_id"]) / ref["uri"].split(f"jobs/{job['job_id']}/", 1)[1], manifest

    def _upload(self, publish_id: str) -> None:
        job = self._load(publish_id)
        if job["youtube_video_id"]:
            return
        if job["state"] != "UPLOADING":
            self._set_state(publish_id, "UPLOADING", "Starting/resuming transfer")
        path, _ = self._video_path(job)
        token = self._access_token(job["connection_id"])
        total = path.stat().st_size
        api = self.api()
        session = None
        offset = job["bytes_uploaded"]
        if job["session_ref_encrypted"]:
            session = self._fernet().decrypt(job["session_ref_encrypted"].encode()).decode()
            try:
                state = api.query_session(session, total)
                offset = state["next_offset"]
                if state["done"]:
                    self._finish_upload(publish_id, state["video"])
                    return
            except YouTubeError as exc:
                if exc.kind != "session_expired":
                    raise
                session, offset = None, 0
        if session is None:
            body = {"snippet": {"title": job["title"], "description": job["description"],
                                "tags": json.loads(job["tags_json"]), "categoryId": job["category_id"],
                                "defaultLanguage": job["default_language"],
                                "defaultAudioLanguage": job["default_language"]},
                    # Always private at creation; final visibility is a later, audited step.
                    "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": bool(job["made_for_kids"])}}
            session = api.start_resumable(token, body, total)
            self._spend(publish_id, "videos.insert")
            enc = self._fernet().encrypt(session.encode()).decode()
            self._cols(publish_id, session_ref_encrypted=enc, bytes_uploaded=0, bytes_total=total)
            offset = 0
        with path.open("rb") as fh:
            while offset < total:
                if self._state(publish_id) == "CANCEL_REQUESTED":
                    self._set_state(publish_id, "CANCELLED", "Cancelled during transfer")
                    return
                fh.seek(offset)
                data = fh.read(min(CHUNK, total - offset))
                result = api.upload_chunk(session, data, offset, total)
                offset = result["next_offset"] if not result["done"] else total
                self._cols(publish_id, bytes_uploaded=offset, progress=round(offset / total * 0.8, 4))
                if result["done"]:
                    self._finish_upload(publish_id, result["video"])
                    return
        # Zero-length remainder: ask the session for its final state.
        state = api.query_session(session, total)
        if state["done"]:
            self._finish_upload(publish_id, state["video"])
        else:
            raise YouTubeError("transient", "Transfer ended without a completion response")

    def _finish_upload(self, publish_id: str, video: dict) -> None:
        vid = video.get("id")
        if not vid:
            raise YouTubeError("unknown", "Upload completed but Google returned no video id")
        self._cols(publish_id, youtube_video_id=vid, session_ref_encrypted=None, progress=0.8, error_code=None,
                   error_message=None)
        self._set_state(publish_id, "UPLOADED_PRIVATE", f"Uploaded as private video {vid}")

    def _post_upload(self, publish_id: str) -> None:
        job = self._load(publish_id)
        vid = job["youtube_video_id"]
        self._video_path(job)  # Recheck rights before any post-upload provider call.
        token = self._access_token(job["connection_id"])
        api = self.api()
        # Reconcile: the video must still exist before any further call.
        remote = api.video_status(token, vid)
        self._spend(publish_id, "videos.list")
        if remote is None:
            raise YouTubeError("not_found", f"Video {vid} no longer exists on YouTube")
        state = self._state(publish_id)
        # thumbnail (independent)
        if job["thumbnail_state"] != "SET":
            self._thumbnail(publish_id, token)
        # captions (independent, per track)
        self._captions(publish_id, token)
        # final visibility (separate, audited, only when approved for non-private)
        job = self._load(publish_id)
        if job["thumbnail_state"] == "FAILED" or self._captions_failed(publish_id):
            raise YouTubeError("part_failed", "Thumbnail or captions failed; retry them independently. "
                                              "The video remains private.")
        want_public = job["visibility"] != "private" or job["scheduled_utc"]
        if want_public and (remote.get("status", {}).get("privacyStatus") == "private" and
                            not remote.get("status", {}).get("publishAt")):
            need = required_confirmation(job["visibility"], bool(job["scheduled_utc"]))
            if need is None or job["approved_at"] is None:
                raise YtConflict("Non-private visibility without approval")
            if self._state(publish_id) not in ("PUBLISHING",):
                self._set_state(publish_id, "PUBLISHING", f"Applying visibility {job['visibility']}")
            if job["scheduled_utc"]:
                status = {"privacyStatus": "private", "publishAt": job["scheduled_utc"],
                          "selfDeclaredMadeForKids": bool(job["made_for_kids"])}
            else:
                status = {"privacyStatus": job["visibility"], "selfDeclaredMadeForKids": bool(job["made_for_kids"])}
            self._video_path(job)  # A revocation during thumbnail/caption work blocks publication.
            api.update_status(token, vid, status)
            self._spend(publish_id, "videos.update")
            self._set_state(publish_id, "SCHEDULED" if job["scheduled_utc"] else "PUBLISHED",
                            "Visibility change accepted by API")
        self._verify(publish_id, token)

    def _thumbnail(self, publish_id: str, token: str) -> None:
        job = self._load(publish_id)
        manifest = self.read_integration_body(job["job_id"], job["owner_id"], job["language"], PACKAGE_KIND,
                                              job["package_version"])
        ref = manifest["files"]["thumbnail"]
        data = self.store.read_bytes(job["job_id"], ref["uri"])
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            self._cols(publish_id, thumbnail_state="FAILED", error_code="thumbnail", error_message="thumbnail hash mismatch")
            return
        st = self._state(publish_id)
        if st != "THUMBNAIL_UPLOADING":
            if st != "THUMBNAIL_PENDING" and "THUMBNAIL_PENDING" in TRANSITIONS[st]:
                self._set_state(publish_id, "THUMBNAIL_PENDING", "Thumbnail queued")
            self._set_state(publish_id, "THUMBNAIL_UPLOADING", "Uploading thumbnail")
        try:
            self.api().set_thumbnail(token, job["youtube_video_id"], data, "image/jpeg")
            self._spend(publish_id, "thumbnails.set")
            self._cols(publish_id, thumbnail_state="SET")
        except YouTubeError as exc:
            if exc.kind in ("auth", "quota", "transient", "rate_limit"):
                raise
            self._cols(publish_id, thumbnail_state="FAILED", error_code="thumbnail", error_message=redact(exc.message)[:300])

    def _captions(self, publish_id: str, token: str) -> None:
        job = self._load(publish_id)
        with closing(connect(self.database_file)) as c:
            tracks = [dict(r) for r in c.execute(
                "SELECT * FROM publishing_caption_tracks WHERE publish_id=? AND state IN ('PENDING','FAILED')",
                (publish_id,))]
        if not tracks:
            return
        manifest = self.read_integration_body(job["job_id"], job["owner_id"], job["language"], PACKAGE_KIND,
                                              job["package_version"])
        ref = manifest["files"]["subtitles_srt"]
        data = self.store.read_bytes(job["job_id"], ref["uri"])
        st = self._state(publish_id)
        if st != "CAPTIONS_UPLOADING":
            if st != "CAPTIONS_PENDING" and "CAPTIONS_PENDING" in TRANSITIONS[st]:
                self._set_state(publish_id, "CAPTIONS_PENDING", "Captions queued")
            self._set_state(publish_id, "CAPTIONS_UPLOADING", "Uploading captions")
        for t in tracks:
            if hashlib.sha256(data).hexdigest() != t["source_sha256"]:
                self._track(t["track_id"], "FAILED", None, "caption_source_changed")
                continue
            try:
                resp = self.api().insert_caption(token, job["youtube_video_id"], t["language"], t["name"], data)
                self._spend(publish_id, "captions.insert")
                self._track(t["track_id"], "UPLOADED", resp.get("id"), None)
            except YouTubeError as exc:
                if exc.kind in ("auth", "quota", "transient", "rate_limit"):
                    raise
                self._track(t["track_id"], "FAILED", None, exc.kind)

    def _track(self, track_id, state, caption_id, err) -> None:
        with closing(connect(self.database_file)) as c:
            c.execute("UPDATE publishing_caption_tracks SET state=?, youtube_caption_id=?, error_code=?, updated_at=? "
                      "WHERE track_id=?", (state, caption_id, err, _now(), track_id))

    def _captions_failed(self, publish_id: str) -> bool:
        with closing(connect(self.database_file)) as c:
            return c.execute("SELECT 1 FROM publishing_caption_tracks WHERE publish_id=? AND state='FAILED'",
                             (publish_id,)).fetchone() is not None

    def _verify(self, publish_id: str, token: str) -> None:
        job = self._load(publish_id)
        st = self._state(publish_id)
        if "VERIFYING" in TRANSITIONS[st]:
            self._set_state(publish_id, "VERIFYING", "Reading the video back from YouTube")
        remote = self.api().video_status(token, job["youtube_video_id"])
        self._spend(publish_id, "videos.list")
        if remote is None:
            raise YouTubeError("not_found", "Video not found when verifying")
        rs = remote.get("status", {})
        problems = []
        if job["scheduled_utc"]:
            if rs.get("privacyStatus") != "private" or not rs.get("publishAt"):
                problems.append("schedule was not accepted by YouTube")
        elif rs.get("privacyStatus") != job["visibility"]:
            problems.append(f"remote visibility is {rs.get('privacyStatus')!r}, expected {job['visibility']!r}")
        if remote.get("snippet", {}).get("title") != job["title"]:
            problems.append("remote title differs from the approved title")
        self._cols(publish_id, remote_status_json=json.dumps({"status": rs, "title": remote.get("snippet", {}).get("title"),
                                                              "processing": remote.get("processingDetails", {})}),
                   verified_at=_now())
        if problems:
            self._cols(publish_id, error_code="verify", error_message="; ".join(problems))
            self._set_state(publish_id, "FAILED_RETRYABLE", "Verification failed: " + "; ".join(problems))
            return
        self._cols(publish_id, progress=1.0, error_code=None, error_message=None)
        self._set_state(publish_id, "COMPLETED", "Verified against YouTube")

    # -- independent retries --------------------------------------------------------------------------

    def retry_part(self, owner_id: str, publish_id: str, part: str) -> dict:
        job = self._job(owner_id, publish_id)
        if part not in ("thumbnail", "captions"):
            raise YtValidation("part must be 'thumbnail' or 'captions'")
        if not job["youtube_video_id"]:
            raise YtConflict("The video has not been uploaded yet")
        if job["state"] in ("CANCELLED", "FAILED_FINAL"):
            raise YtConflict(f"Job is {job['state']}")
        if part == "thumbnail":
            self._cols(publish_id, thumbnail_state="NONE", error_code=None, error_message=None)
        else:
            with closing(connect(self.database_file)) as c:
                c.execute("UPDATE publishing_caption_tracks SET state='PENDING', error_code=NULL WHERE publish_id=? "
                          "AND state='FAILED'", (publish_id,))
        if job["state"] not in ("FAILED_RETRYABLE", "UPLOADED_PRIVATE"):
            raise YtConflict(f"Job is {job['state']}; wait for it to settle before retrying {part}")
        if job["state"] == "FAILED_RETRYABLE":
            self._set_state(publish_id, "THUMBNAIL_PENDING" if part == "thumbnail" else "CAPTIONS_PENDING",
                            f"Retry {part}", owner_id)
        self.run_job(publish_id)
        return self._view(self._job(owner_id, publish_id))

    # -- reads -------------------------------------------------------------------------------------------

    def list_jobs(self, owner_id: str) -> dict:
        with closing(connect(self.database_file)) as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM publishing_jobs WHERE owner_id=? ORDER BY created_at DESC", (owner_id,))]
        return {"jobs": [self._view(r) for r in rows], "quota_used_today_estimate": self._quota_used_today(),
                "daily_quota_limit": self._daily_limit()}

    def get_job(self, owner_id: str, publish_id: str) -> dict:
        job = self._job(owner_id, publish_id)
        with closing(connect(self.database_file)) as c:
            events = [dict(r) for r in c.execute(
                "SELECT event,prev_state,new_state,detail,actor,occurred_at FROM publishing_job_events "
                "WHERE publish_id=? ORDER BY occurred_at, rowid", (publish_id,))]
            tracks = [dict(r) for r in c.execute("SELECT * FROM publishing_caption_tracks WHERE publish_id=?",
                                                 (publish_id,))]
        v = self._view(job)
        v["events"], v["caption_tracks"] = events, tracks
        v["approval_digest_current"] = approval_digest(job)
        return v
