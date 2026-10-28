"""YouTube Data API metadata for speech-reference sources.

This client requests snippet and caption-availability metadata only. It does not
request streams, files, or captions, and it does not use cookies. When no API
key is configured, callers receive identifiers parsed from the URL and an
explicit limitation — never a guessed title, date, or video list.
"""
from __future__ import annotations

import os

API_ROOT = "https://www.googleapis.com/youtube/v3"


class SpeechAcquireError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _reason(body: dict) -> str:
    errors = ((body.get("error") or {}).get("errors") or []) if isinstance(body, dict) else []
    if errors and isinstance(errors[0], dict):
        return str(errors[0].get("reason") or "")
    return ""


def _raise_for_status(status: int, body: dict) -> None:
    if status == 200:
        return
    reason = _reason(body)
    message = ((body.get("error") or {}).get("message") if isinstance(body, dict) else None) or f"HTTP {status}"
    if status == 429 or reason in {"quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded"}:
        raise SpeechAcquireError(reason or "rate_limited", f"YouTube Data API rate limit: {message}", True)
    if status in {500, 503}:
        raise SpeechAcquireError("youtube_unavailable", f"YouTube Data API error: {message}", True)
    raise SpeechAcquireError(reason or "youtube_api_error", f"YouTube Data API rejected the request: {message}", False)


def url_only_metadata(parsed: dict) -> dict:
    limitation = (
        "DEFENCE_STUDIO_YOUTUBE_API_KEY is not configured. Only identifiers present in the URL "
        "were recorded. Titles, publication dates, caption language, and channel or playlist "
        "video lists were not retrieved."
    )
    videos = []
    if parsed["kind"] == "video" and parsed.get("video_id"):
        videos.append({
            "video_id": parsed["video_id"],
            "title": None,
            "published_at": None,
            "caption_availability": "unknown",
            "caption_language": None,
            "webpage_url": parsed["url"],
        })
    elif parsed["kind"] in {"channel", "playlist"}:
        limitation += " No videos were enumerated."
    return {
        "method": "url_parse_only",
        "outcome": "youtube_data_api_unconfigured",
        "limitation": limitation,
        "title": None,
        "published_at": None,
        "channel_id": parsed.get("channel_id"),
        "videos": videos,
        "truncated": False,
        "omitted_count": 0,
    }


def _video_item(item: dict) -> dict:
    snippet = item.get("snippet") or {}
    details = item.get("contentDetails") or {}
    video_id = item.get("id") if isinstance(item.get("id"), str) else (details.get("videoId") or snippet.get("resourceId", {}).get("videoId"))
    caption = details.get("caption")
    if caption == "true":
        availability = "available"
    elif caption == "false":
        availability = "missing"
    else:
        availability = "unknown"
    return {
        "video_id": video_id,
        "title": snippet.get("title") or None,
        "published_at": snippet.get("publishedAt") or None,
        "caption_availability": availability,
        "caption_language": (snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or None),
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
    }


def inspect_with_api(parsed: dict, max_videos: int, api_key: str, getter) -> dict:
    """`getter(path, params) -> (status, json)` must be the official Data API."""
    params_key = {"key": api_key}
    title = None
    published = None
    channel_id = parsed.get("channel_id")
    videos: list[dict] = []
    truncated = False
    omitted = 0

    if parsed["kind"] == "video":
        status, body = getter("/videos", {**params_key, "part": "snippet,contentDetails", "id": parsed["video_id"]})
        _raise_for_status(status, body)
        items = body.get("items") or []
        if not items:
            raise SpeechAcquireError("video_not_found", "YouTube Data API returned no video for this id", False)
        item = _video_item(items[0])
        title = item["title"]
        published = item["published_at"]
        videos = [item]
    elif parsed["kind"] == "playlist":
        status, body = getter("/playlists", {**params_key, "part": "snippet", "id": parsed["playlist_id"]})
        _raise_for_status(status, body)
        items = body.get("items") or []
        if items:
            snippet = items[0].get("snippet") or {}
            title = snippet.get("title") or None
            published = snippet.get("publishedAt") or None
        status, body = getter("/playlistItems", {
            **params_key, "part": "snippet,contentDetails", "playlistId": parsed["playlist_id"],
            "maxResults": min(max_videos, 50),
        })
        _raise_for_status(status, body)
        rows = body.get("items") or []
        if body.get("nextPageToken") and len(rows) >= max_videos:
            truncated = True
        videos = [_video_item(row) for row in rows[:max_videos]]
        omitted = max(0, len(rows) - max_videos)
    else:
        query = {"part": "snippet,contentDetails"}
        if channel_id:
            query["id"] = channel_id
        elif parsed.get("handle"):
            query["forHandle"] = parsed["handle"]
        elif parsed.get("legacy_name"):
            query["forUsername"] = parsed["legacy_name"]
        else:
            return {
                "method": "youtube_data_api_v3",
                "outcome": "channel_identity_unresolved",
                "limitation": "This channel URL has no channel id, handle, or legacy username that the Data API accepts.",
                "title": None, "published_at": None, "channel_id": None, "videos": [],
                "truncated": False, "omitted_count": 0,
            }
        status, body = getter("/channels", {**params_key, **query})
        _raise_for_status(status, body)
        items = body.get("items") or []
        if not items:
            return {
                "method": "youtube_data_api_v3",
                "outcome": "channel_not_found",
                "limitation": "YouTube Data API returned no channel for this URL. No videos were enumerated.",
                "title": None, "published_at": None, "channel_id": channel_id, "videos": [],
                "truncated": False, "omitted_count": 0,
            }
        snippet = items[0].get("snippet") or {}
        title = snippet.get("title") or None
        published = snippet.get("publishedAt") or None
        channel_id = items[0].get("id") or channel_id
        uploads = ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not uploads:
            return {
                "method": "youtube_data_api_v3",
                "outcome": "uploads_playlist_unavailable",
                "limitation": "The channel record has no uploads playlist. No videos were enumerated.",
                "title": title, "published_at": published, "channel_id": channel_id, "videos": [],
                "truncated": False, "omitted_count": 0,
            }
        status, body = getter("/playlistItems", {
            **params_key, "part": "snippet,contentDetails", "playlistId": uploads,
            "maxResults": min(max_videos, 50),
        })
        _raise_for_status(status, body)
        rows = body.get("items") or []
        truncated = bool(body.get("nextPageToken"))
        videos = [_video_item(row) for row in rows[:max_videos]]
        omitted = max(0, len(rows) - len(videos))

    videos = [video for video in videos if video.get("video_id")]
    return {
        "method": "youtube_data_api_v3",
        "outcome": "metadata_retrieved",
        "limitation": (
            f"Enumerated {len(videos)} of the requested limit ({max_videos}). Further pages were not requested."
            if truncated else None
        ),
        "title": title,
        "published_at": published,
        "channel_id": channel_id,
        "videos": videos,
        "truncated": truncated,
        "omitted_count": omitted,
    }


def httpx_getter(path: str, params: dict):
    import httpx
    response = httpx.get(API_ROOT + path, params=params, timeout=20.0)
    try:
        body = response.json()
    except Exception:
        body = {"error": {"message": response.text[:300]}}
    return response.status_code, body


def build_metadata_client(api_key: str | None = None, getter=None):
    key = os.environ.get("DEFENCE_STUDIO_YOUTUBE_API_KEY", "") if api_key is None else api_key

    def inspect(parsed: dict, max_videos: int, _cancel=None) -> dict:
        if not key:
            return url_only_metadata(parsed)
        return inspect_with_api(parsed, max_videos, key, getter or httpx_getter)

    return inspect
