"""YouTube Data API v3 / Google OAuth 2.0 web-server-flow client.

Every Google call in the system lives here and is reachable only from backend
services; secrets and tokens never reach the frontend, URLs we return, or
logs (``redact`` is applied to every error string). All HTTP goes through an
injectable ``httpx`` client factory so contract tests can run offline against
a fake Google -- those tests are *mocked* and are labelled as such; they do
not verify the real Google service.

Scopes are the minimum for the features used: ``youtube.upload`` (videos,
thumbnails) and ``youtube.force-ssl`` (captions, channel read, videos.list).
Quota unit costs are the documented defaults and configurable; they are
estimates used by the guard, not measurements.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl")
CHUNK = 8 * 1024 * 1024  # multiple of 256 KiB as the resumable protocol requires

QUOTA_COST = {"videos.insert": 1600, "thumbnails.set": 50, "captions.insert": 400, "videos.list": 1,
              "channels.list": 1, "videos.update": 50}


class YouTubeError(Exception):
    """Structured API failure. ``kind`` drives retry policy in the service."""

    def __init__(self, kind: str, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.message, self.status, self.retry_after = kind, message, status, retry_after


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @staticmethod
    def from_env() -> "OAuthConfig":
        return OAuthConfig(os.environ.get("DEFENCE_STUDIO_YT_CLIENT_ID", ""),
                           os.environ.get("DEFENCE_STUDIO_YT_CLIENT_SECRET", ""),
                           os.environ.get("DEFENCE_STUDIO_YT_REDIRECT_URI", ""))

    def missing(self) -> list[str]:
        out = []
        if not self.client_id:
            out.append("DEFENCE_STUDIO_YT_CLIENT_ID")
        if not self.client_secret:
            out.append("DEFENCE_STUDIO_YT_CLIENT_SECRET")
        if not self.redirect_uri:
            out.append("DEFENCE_STUDIO_YT_REDIRECT_URI")
        return out


def redact(text: str, *secrets_: str) -> str:
    for s in secrets_:
        if s:
            text = text.replace(s, "***")
    return text


def classify(response: httpx.Response) -> YouTubeError:
    """Map a Google error response to a retry class. No token ever included."""
    try:
        err = (response.json().get("error") or {})
    except ValueError:
        err = {}
    if isinstance(err, str):  # OAuth style {"error": "invalid_grant"}
        err = {"errors": [{"reason": err}], "message": response.text[:200]}
    reasons = [e.get("reason", "") for e in err.get("errors", []) if isinstance(e, dict)]
    msg = str(err.get("message") or response.text[:200])[:300]
    ra = response.headers.get("retry-after")
    retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
    status = response.status_code
    if "invalid_grant" in reasons or status == 401 or "authError" in reasons:
        return YouTubeError("auth", f"Authorization failed ({', '.join(reasons) or status}): {msg}", status)
    if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
        return YouTubeError("quota", "YouTube API quota exhausted for today", status, retry_after)
    if status == 429 or "rateLimitExceeded" in reasons or "userRateLimitExceeded" in reasons:
        return YouTubeError("rate_limit", f"Rate limited: {msg}", status, retry_after)
    if status in (500, 502, 503, 504):
        return YouTubeError("transient", f"YouTube server error {status}", status, retry_after)
    if status == 403:
        return YouTubeError("forbidden", f"Forbidden ({', '.join(reasons) or 'no reason'}): {msg}", status)
    if status == 404:
        return YouTubeError("not_found", f"Not found: {msg}", status)
    if status == 400:
        return YouTubeError("invalid", f"Invalid request ({', '.join(reasons) or 'badRequest'}): {msg}", status)
    return YouTubeError("unknown", f"HTTP {status}: {msg}", status)


class YouTubeApi:
    def __init__(self, oauth: OAuthConfig | None = None, *, client_factory=None, timeout: float = 60.0):
        self.oauth = oauth or OAuthConfig.from_env()
        self._factory = client_factory or (lambda **kw: httpx.Client(**kw))
        self.timeout = timeout

    # -- plumbing ------------------------------------------------------------

    def _send(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            with self._factory(timeout=self.timeout) as client:
                return client.request(method, url, **kw)
        except httpx.TimeoutException as exc:
            raise YouTubeError("transient", "Request to Google timed out") from exc
        except httpx.HTTPError as exc:
            raise YouTubeError("transient", f"Network error talking to Google: {type(exc).__name__}") from exc

    @staticmethod
    def _bearer(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def _json(self, response: httpx.Response, ok=(200, 201)) -> dict:
        if response.status_code not in ok:
            raise classify(response)
        try:
            return response.json()
        except ValueError as exc:
            raise YouTubeError("unknown", "Google returned a non-JSON response") from exc

    # -- OAuth ---------------------------------------------------------------

    @staticmethod
    def new_state() -> str:
        return secrets.token_urlsafe(32)

    def consent_url(self, state: str, scopes: tuple[str, ...] = SCOPES) -> str:
        if self.oauth.missing():
            raise YouTubeError("not_configured", "Missing: " + ", ".join(self.oauth.missing()))
        return AUTH_URL + "?" + urlencode({
            "client_id": self.oauth.client_id, "redirect_uri": self.oauth.redirect_uri, "response_type": "code",
            "scope": " ".join(scopes), "state": state, "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "false"})

    def exchange_code(self, code: str) -> dict:
        r = self._send("POST", TOKEN_URL, data={
            "code": code, "client_id": self.oauth.client_id, "client_secret": self.oauth.client_secret,
            "redirect_uri": self.oauth.redirect_uri, "grant_type": "authorization_code"})
        return self._json(r)

    def refresh(self, refresh_token: str) -> dict:
        r = self._send("POST", TOKEN_URL, data={
            "refresh_token": refresh_token, "client_id": self.oauth.client_id,
            "client_secret": self.oauth.client_secret, "grant_type": "refresh_token"})
        return self._json(r)

    def revoke(self, token: str) -> bool:
        r = self._send("POST", REVOKE_URL, data={"token": token})
        return r.status_code == 200

    # -- read ----------------------------------------------------------------

    def my_channel(self, access_token: str) -> dict:
        r = self._send("GET", f"{API}/channels", params={"part": "snippet", "mine": "true"},
                       headers=self._bearer(access_token))
        items = self._json(r).get("items", [])
        if not items:
            raise YouTubeError("no_channel", "The authorized Google account has no YouTube channel")
        it = items[0]
        sn = it.get("snippet", {})
        return {"channel_id": it["id"], "title": sn.get("title", ""), "handle": sn.get("customUrl"),
                "thumbnail_url": ((sn.get("thumbnails") or {}).get("default") or {}).get("url")}

    def video_status(self, access_token: str, video_id: str) -> dict | None:
        r = self._send("GET", f"{API}/videos", params={"part": "status,snippet,processingDetails", "id": video_id},
                       headers=self._bearer(access_token))
        items = self._json(r).get("items", [])
        return items[0] if items else None

    # -- upload --------------------------------------------------------------

    def start_resumable(self, access_token: str, body: dict, size: int, mime: str = "video/mp4") -> str:
        r = self._send("POST", f"{UPLOAD_API}/videos", params={"uploadType": "resumable", "part": "snippet,status"},
                       headers={**self._bearer(access_token), "X-Upload-Content-Length": str(size),
                                "X-Upload-Content-Type": mime, "Content-Type": "application/json; charset=UTF-8"},
                       content=json.dumps(body).encode("utf-8"))
        if r.status_code != 200 or not r.headers.get("location"):
            raise classify(r)
        return r.headers["location"]

    def upload_chunk(self, session_url: str, data: bytes, offset: int, total: int) -> dict:
        """PUT one chunk. Returns ``{"done": bool, "next_offset": int, "video": dict|None}``."""
        end = offset + len(data) - 1
        r = self._send("PUT", session_url, content=data,
                       headers={"Content-Length": str(len(data)), "Content-Range": f"bytes {offset}-{end}/{total}"})
        return self._chunk_result(r, total)

    def query_session(self, session_url: str, total: int) -> dict:
        r = self._send("PUT", session_url, content=b"",
                       headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"})
        return self._chunk_result(r, total)

    def _chunk_result(self, r: httpx.Response, total: int) -> dict:
        if r.status_code in (200, 201):
            return {"done": True, "next_offset": total, "video": r.json()}
        if r.status_code == 308:
            rng = r.headers.get("range")  # "bytes=0-1048575"
            nxt = int(rng.split("-")[-1]) + 1 if rng and "-" in rng else 0
            return {"done": False, "next_offset": nxt, "video": None}
        if r.status_code == 404:
            raise YouTubeError("session_expired", "Resumable upload session no longer exists", 404)
        raise classify(r)

    def set_thumbnail(self, access_token: str, video_id: str, data: bytes, mime: str) -> dict:
        r = self._send("POST", f"{UPLOAD_API}/thumbnails/set", params={"videoId": video_id},
                       headers={**self._bearer(access_token), "Content-Type": mime}, content=data)
        return self._json(r)

    def insert_caption(self, access_token: str, video_id: str, language: str, name: str, data: bytes) -> dict:
        boundary = "ds" + secrets.token_hex(8)
        meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}})
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
                f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8") + data + \
               f"\r\n--{boundary}--\r\n".encode("utf-8")
        r = self._send("POST", f"{UPLOAD_API}/captions", params={"uploadType": "multipart", "part": "snippet"},
                       headers={**self._bearer(access_token),
                                "Content-Type": f"multipart/related; boundary={boundary}"}, content=body)
        return self._json(r)

    def update_status(self, access_token: str, video_id: str, status: dict) -> dict:
        r = self._send("PUT", f"{API}/videos", params={"part": "status"},
                       headers={**self._bearer(access_token), "Content-Type": "application/json"},
                       content=json.dumps({"id": video_id, "status": status}).encode("utf-8"))
        return self._json(r)
