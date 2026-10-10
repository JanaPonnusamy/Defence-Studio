"""YouTube subtitle/caption acquisition provider.

Acquires only subtitles/captions for a YouTube video — never the full video —
in line with the core design's preference for transcripts when the spoken text
is sufficient. It is host-restricted to YouTube domains (so it cannot be pointed
at an arbitrary internal host) and delegates the actual yt-dlp call to an
injectable backend, which is the adapter boundary the tests exercise with a
deterministic double. The real backend sends no cookies/credentials by default
and does not bypass access controls: a video requiring sign-in simply fails
honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from defence_studio.acquisition.provider import (
    AcquisitionProvider,
    ProviderContext,
    ProviderError,
    ProviderResult,
    StoredOutput,
)
from defence_studio.acquisition.url_guard import ValidatedUrl
from defence_studio.contracts.acquisition import DeclaredMetadata, MetadataStatus
from defence_studio.storage.artifacts import safe_filename


_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be",
}


@dataclass(frozen=True)
class SubtitleTrack:
    language: str
    is_auto: bool
    ext: str
    content: bytes


@dataclass(frozen=True)
class YoutubeSubtitles:
    video_id: str
    title: str | None
    channel: str | None
    upload_date: str | None  # yt-dlp style YYYYMMDD, or None
    webpage_url: str | None
    tracks: tuple[SubtitleTrack, ...] = field(default_factory=tuple)


# Backend signature: (url, languages, max_bytes, cancel) -> YoutubeSubtitles
def _require_ytdlp_backend(url, languages, max_bytes, cancel) -> YoutubeSubtitles:
    """Default real backend. Downloads subtitle tracks only via yt-dlp.

    Imported lazily so the module loads without yt-dlp installed; a deployment
    that wants live YouTube acquisition installs the ``youtube`` extra.
    """
    try:
        import tempfile

        import yt_dlp
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ProviderError(
            "backend_unavailable",
            "yt-dlp is not installed; install the 'youtube' extra to acquire captions",
            retryable=False,
        ) from exc

    langs = list(languages) if languages else ["en"]
    with tempfile.TemporaryDirectory() as tmp:
        opts = {
            "skip_download": True,       # never fetch the video stream
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": "vtt/srt/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many error types
            raise ProviderError("youtube_error", f"yt-dlp failed: {exc}", retryable=True) from exc

        tracks: list[SubtitleTrack] = []
        for sub_file in sorted(Path(tmp).glob("*")):
            if sub_file.suffix.lower() not in (".vtt", ".srt"):
                continue
            data = sub_file.read_bytes()
            if len(data) > max_bytes:
                raise ProviderError("too_large", "Subtitle track exceeds size limit", retryable=False)
            parts = sub_file.name.split(".")
            lang = parts[-2] if len(parts) >= 3 else (langs[0] if langs else "und")
            tracks.append(SubtitleTrack(language=lang, is_auto=False, ext=sub_file.suffix.lstrip("."), content=data))
        return YoutubeSubtitles(
            video_id=str(info.get("id") or ""),
            title=info.get("title") or None,
            channel=info.get("channel") or info.get("uploader") or None,
            upload_date=info.get("upload_date") or None,
            webpage_url=info.get("webpage_url") or url,
            tracks=tuple(tracks),
        )


class YoutubeSubtitleProvider(AcquisitionProvider):
    name = "youtube_subtitles"
    version = "0.1.0"
    source_component = "defence_studio.acquisition.providers.youtube@0.1.0"

    def __init__(self, backend=_require_ytdlp_backend):
        self._backend = backend

    def supports(self, validated_url: ValidatedUrl) -> bool:
        return validated_url.host in _YOUTUBE_HOSTS and self._video_id(validated_url.url) is not None

    def acquire(self, context: ProviderContext) -> ProviderResult:
        url = context.validated_url.url
        if self._video_id(url) is None:
            raise ProviderError("not_a_video", "URL is not a YouTube video URL", retryable=False)

        result = self._backend(
            url, context.target.language_preferences,
            context.limits.max_bytes_per_source, context.cancel,
        )
        if not result.tracks:
            raise ProviderError(
                "no_subtitles",
                "No subtitles/captions are available for this video",
                retryable=False,
            )

        outputs: list[StoredOutput] = []
        languages: list[str] = []
        for index, track in enumerate(result.tracks):
            languages.append(track.language)
            kind = "auto" if track.is_auto else "sub"
            filename = safe_filename(f"{kind}-{track.language}-{index}", url, f".{track.ext}")
            uri = f"jobs/{context.job_id}/research/sources/{context.source_id}/{filename}"
            stored = context.store.write_bytes(context.job_id, uri, track.content, f"text/{track.ext}")
            outputs.append(StoredOutput(
                artifact_type="source_subtitles",
                uri=stored.uri,
                sha256=stored.sha256,
                bytes=stored.bytes,
                media_type=stored.media_type,
            ))

        published = self._to_iso(result.upload_date)
        declared = DeclaredMetadata(
            title=result.title,
            title_status=MetadataStatus.COLLECTED if result.title else MetadataStatus.UNAVAILABLE,
            publisher=result.channel,
            publisher_status=MetadataStatus.COLLECTED if result.channel else MetadataStatus.UNAVAILABLE,
            published_at=published,
            published_at_status=MetadataStatus.COLLECTED if published else MetadataStatus.UNAVAILABLE,
            language=languages[0] if languages else None,
            language_status=MetadataStatus.COLLECTED if languages else MetadataStatus.UNAVAILABLE,
        )
        return ProviderResult(
            source_type="youtube_video",
            artifacts=tuple(outputs),
            declared_metadata=declared,
            original_metadata={
                "video_id": result.video_id,
                "subtitle_languages": languages,
                "has_video_download": False,
            },
            final_url=result.webpage_url or url,
        )

    @staticmethod
    def _video_id(url: str) -> str | None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host == "youtu.be":
            candidate = parts.path.lstrip("/").split("/")[0]
            return candidate or None
        if "/watch" in parts.path:
            values = parse_qs(parts.query).get("v")
            return values[0] if values else None
        if parts.path.startswith(("/shorts/", "/embed/", "/live/")):
            segments = [segment for segment in parts.path.split("/") if segment]
            return segments[1] if len(segments) >= 2 else None
        return None

    @staticmethod
    def _to_iso(upload_date: str | None) -> str | None:
        if not upload_date or len(upload_date) != 8 or not upload_date.isdigit():
            return None
        return f"{upload_date[0:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
