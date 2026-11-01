"""Tamil speech-reference library on the existing workflow queue and asset store.

Metadata may come from the YouTube Data API. Caption bytes are stored only when
text retention is authorized, from an uploaded SRT/VTT file or the existing
subtitle-only provider. Audio is stored only from an uploaded recording when
acoustic analysis is authorized. YouTube audio is never downloaded. Nothing here
clones a voice or trains a model.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from defence_studio.database import connect
from defence_studio.services.jobs import (
    IdempotencyConflict, InvalidTransition, JobError, JobNotFound, JobValidationError,
)
from defence_studio.services.speech_reference_text import (
    NORMALIZATION_NOTE, CueParseError, measure_wav, normalize_tamil, parse_cues, proportional_spans,
    rate_band, segment_energy_contour, sentence_position, speaking_rate, tokenize, validate_ipa,
)
from defence_studio.services.speech_reference_youtube import (
    SpeechAcquireError, build_metadata_client,
)
from defence_studio.storage.artifacts import ArtifactStore

REQUEST_SCHEMA = "defence-studio.speech-reference.v1"
STAGE = "speech_reference"
MAX_VIDEOS_CAP = 25
DEFAULT_MAX_VIDEOS = 5
SUBTITLE_PREFS = {"official_captions", "user_file", "none"}
AUDIO_METHODS = {"none", "user_recording", "youtube_audio"}
LANGUAGES = {"ta", "ta-IN"}
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{12,80}$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{20,32}$")
_REVIEW_KINDS = {"occurrence", "segment", "pronunciation", "word", "sample", "meaning"}
_OCCURRENCE_FIELDS = {"delivery_label", "start_ms", "end_ms", "observed_text", "speaker_profile_id", "sentence_position"}


class SpeechPermissionError(JobError):
    status_code = 403


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def parse_youtube_reference(url: str, kind: str) -> dict:
    if kind not in {"channel", "video", "playlist"}:
        raise JobValidationError("source kind must be channel, video, or playlist")
    raw = (url or "").strip()
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or parts.username or parts.password or host not in _HOSTS:
        raise JobValidationError("Source URL must be an https YouTube channel, video, or playlist URL")
    query = parse_qs(parts.query)
    video_id = None
    if host == "youtu.be":
        video_id = parts.path.strip("/").split("/")[0] or None
    elif parts.path.startswith(("/shorts/", "/embed/", "/live/")):
        segments = [segment for segment in parts.path.split("/") if segment]
        video_id = segments[1] if len(segments) > 1 else None
    elif parts.path in {"/watch", "/watch/"}:
        video_id = (query.get("v") or [None])[0]
    playlist_id = (query.get("list") or [None])[0]
    channel_id = handle = legacy = None
    path_bits = [bit for bit in parts.path.split("/") if bit]
    if path_bits[:1] == ["channel"] and len(path_bits) >= 2:
        channel_id = path_bits[1]
    elif path_bits and path_bits[0].startswith("@"):
        handle = path_bits[0][1:]
    elif path_bits[:1] == ["c"] and len(path_bits) >= 2:
        legacy = path_bits[1]
    elif path_bits[:1] == ["user"] and len(path_bits) >= 2:
        legacy = path_bits[1]
    if kind == "video":
        if not video_id or not _VIDEO_ID.fullmatch(video_id):
            raise JobValidationError("URL is not a YouTube video URL")
        canonical = f"video:{video_id}"
    elif kind == "playlist":
        if not playlist_id or not _PLAYLIST_ID.fullmatch(playlist_id):
            raise JobValidationError("URL is not a YouTube playlist URL")
        video_id = None
        canonical = f"playlist:{playlist_id}"
    else:
        video_id = playlist_id = None
        if channel_id and _CHANNEL_ID.fullmatch(channel_id):
            canonical = f"channel:{channel_id}"
        elif handle:
            canonical = "channel-handle:" + handle.casefold()
        elif legacy:
            canonical = "channel-name:" + legacy.casefold()
        else:
            raise JobValidationError("URL is not a YouTube channel URL")
    return {
        "kind": kind, "url": raw, "video_id": video_id, "playlist_id": playlist_id,
        "channel_id": channel_id, "handle": handle, "legacy_name": legacy, "canonical_key": canonical,
    }


def _default_subtitle_fetcher(url, languages, max_bytes, cancel):
    from defence_studio.acquisition.provider import ProviderError
    from defence_studio.acquisition.providers.youtube import _require_ytdlp_backend
    try:
        result = _require_ytdlp_backend(url, languages, max_bytes, cancel)
    except ProviderError as exc:
        raise SpeechAcquireError(exc.code, exc.message, exc.retryable) from exc
    return {
        "video_id": result.video_id,
        "title": result.title,
        "tracks": [
            {"language": track.language, "is_auto": track.is_auto, "ext": track.ext, "content": track.content}
            for track in result.tracks
        ],
    }


class SpeechReferenceService:
    def __init__(self, database_file, storage_root, *, metadata_client=None, subtitle_fetcher=None):
        self.database_file = database_file
        self.store = ArtifactStore(Path(storage_root))
        self.metadata_client = metadata_client or build_metadata_client()
        self.subtitle_fetcher = subtitle_fetcher or _default_subtitle_fetcher

    def handlers(self):
        return {STAGE: self.handler}

    def handler(self, context, cancel):
        if self._stopped(context.job_id, cancel):
            raise RuntimeError("cancelled")
        summary = self._acquire(context.job_id, context.owner_id, cancel)
        self._save_result(context.job_id, summary)
        return []

    def register(self, owner_id: str, idempotency_key: str, *, kind: str, url: str, language: str,
                 subtitle_preference: str, audio_source_method: str, permission: dict, notes: str = "",
                 intended_use: str = "", max_videos: int = DEFAULT_MAX_VIDEOS) -> dict:
        parsed = parse_youtube_reference(url, kind)
        language = (language or "ta").strip()
        if language not in LANGUAGES:
            raise JobValidationError("This module currently accepts Tamil language codes ta and ta-IN")
        if subtitle_preference not in SUBTITLE_PREFS:
            raise JobValidationError("subtitle_preference must be official_captions, user_file, or none")
        if audio_source_method not in AUDIO_METHODS:
            raise JobValidationError("audio_source_method must be none, user_recording, or youtube_audio")
        if not isinstance(max_videos, int) or not 1 <= max_videos <= MAX_VIDEOS_CAP:
            raise JobValidationError(f"max_videos must be an integer from 1 to {MAX_VIDEOS_CAP}")
        permission = self._permission(permission or {})
        notes, intended_use = (notes or "")[:2000], (intended_use or "")[:2000]
        payload = {
            "kind": kind, "url": parsed["url"], "canonical_key": parsed["canonical_key"],
            "language": language, "subtitle_preference": subtitle_preference,
            "audio_source_method": audio_source_method, "permission": permission,
            "notes": notes, "intended_use": intended_use, "max_videos": max_videos,
        }
        digest = hashlib.sha256(_canon(payload).encode()).hexdigest()
        now = _now()
        source_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
        limitation = ""
        audio_origin = "none"
        if audio_source_method == "youtube_audio":
            audio_origin = "blocked"
            limitation = (
                "Public visibility is not permission to download or retain audio. "
                "YouTube audio download is not available. Provide an authorized recording, or leave audio unset."
            )
        elif audio_source_method == "user_recording":
            audio_origin = "user_recording_expected"
        caption_origin = {"official_captions": "official_caption_requested", "user_file": "user_file_expected",
                          "none": "none"}[subtitle_preference]
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_job = connection.execute(
                    "SELECT job_id, request_hash FROM workflow_jobs WHERE owner_id=? AND idempotency_key=?",
                    (owner_id, idempotency_key)).fetchone()
                if existing_job:
                    if existing_job["request_hash"] != digest:
                        raise IdempotencyConflict("Idempotency key was already used with a different request")
                    connection.commit()
                    return self.get_job(existing_job["job_id"], owner_id)
                duplicate = connection.execute(
                    "SELECT source_id FROM speech_reference_sources WHERE owner_id=? AND canonical_key=?",
                    (owner_id, parsed["canonical_key"])).fetchone()
                if duplicate:
                    raise InvalidTransition(
                        f"This YouTube source is already registered (source_id={duplicate['source_id']})")
                connection.execute(
                    "INSERT INTO speech_reference_sources(source_id, owner_id, source_kind, source_url, canonical_key, "
                    "video_id, channel_id, playlist_id, title, published_at, language_preference, subtitle_preference, "
                    "audio_source_method, notes, intended_use, acquisition_status, analysis_status, last_error, "
                    "limitation, max_videos, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?, "
                    "'registered','not_started','',?,?,?,?)",
                    (source_id, owner_id, kind, parsed["url"], parsed["canonical_key"], parsed["video_id"],
                     parsed["channel_id"], parsed["playlist_id"], language, subtitle_preference, audio_source_method,
                     notes, intended_use, limitation, max_videos, now, now))
                connection.execute(
                    "INSERT INTO speech_source_provenance(provenance_id, source_id, source_owner, acquisition_method, "
                    "acquisition_outcome, imported_at, caption_origin, audio_origin, permission_status, "
                    "licence_reference, retain_text, acoustic_analysis_allowed, dataset_export_allowed, "
                    "model_training_allowed, voice_cloning_authorized, review_status, notes) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unreviewed',?)",
                    (str(uuid.uuid4()), source_id, permission["source_owner"], "pending", "registered", now,
                     caption_origin, audio_origin, permission["permission_status"], permission["licence_reference"],
                     int(permission["retain_text"]), int(permission["acoustic_analysis_allowed"]),
                     int(permission["dataset_export_allowed"]), int(permission["model_training_allowed"]),
                     int(permission["voice_cloning_authorized"]), permission["notes"]))
                if kind == "video":
                    self._insert_video(connection, source_id, {
                        "video_id": parsed["video_id"], "title": None, "published_at": None,
                        "webpage_url": parsed["url"], "caption_availability": "unknown", "caption_language": None,
                    }, audio_source_method, now)
                topic = f"Speech reference {kind}: {parsed['canonical_key']}"[:200]
                connection.execute(
                    "INSERT INTO workflow_jobs(job_id, owner_id, idempotency_key, request_schema, request_hash, topic, "
                    "language, status, created_at, updated_at, queued_at) VALUES(?,?,?,?,?,?,?,'QUEUED',?,?,?)",
                    (job_id, owner_id, idempotency_key, REQUEST_SCHEMA, digest, topic, language, now, now, now))
                connection.execute(
                    "INSERT INTO workflow_stages(job_id, stage_key, ordinal, status, version, created_at, updated_at) "
                    "VALUES(?,?,0,'PENDING',1,?,?)", (job_id, STAGE, now, now))
                connection.execute(
                    "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                    "VALUES(?,?,'','PENDING',1,?)", (job_id, STAGE, now))
                connection.execute(
                    "INSERT INTO speech_reference_jobs(job_id, owner_id, source_id, kind, payload_json, payload_sha256, "
                    "created_at, updated_at) VALUES(?,?,?,'inspect',?,?,?,?)",
                    (job_id, owner_id, source_id, _canon({"source_id": source_id, "action": "inspect"}), digest, now, now))
                self._audit(connection, owner_id, "source", source_id, "register", owner_id, "Source registered",
                            {"job_id": job_id, "audio_origin": audio_origin})
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return self.get_job(job_id, owner_id)

    def list_sources(self, owner_id: str, *, query: str = "", acquisition_status: str = "",
                     permission_status: str = "", limit: int = 20, offset: int = 0) -> dict:
        limit = min(max(int(limit), 1), 100)
        offset = max(int(offset), 0)
        where = ["s.owner_id=?"]
        args: list = [owner_id]
        if query.strip():
            like = _like(query.strip())
            where.append("(IFNULL(s.title,'') LIKE ? ESCAPE '\\' OR s.source_url LIKE ? ESCAPE '\\' "
                         "OR IFNULL(s.video_id,'') LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM speech_reference_videos v "
                         "WHERE v.source_id=s.source_id AND (IFNULL(v.title,'') LIKE ? ESCAPE '\\' OR v.video_id LIKE ? ESCAPE '\\')))")
            args.extend([like, like, like, like, like])
        if acquisition_status:
            where.append("s.acquisition_status=?")
            args.append(acquisition_status)
        if permission_status:
            where.append("p.permission_status=?")
            args.append(permission_status)
        sql_where = " AND ".join(where)
        with closing(connect(self.database_file)) as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM speech_reference_sources s JOIN speech_source_provenance p "
                "ON p.provenance_id=(SELECT provenance_id FROM speech_source_provenance WHERE source_id=s.source_id "
                "ORDER BY imported_at DESC LIMIT 1) WHERE " + sql_where, args).fetchone()[0]
            rows = connection.execute(
                "SELECT s.*, p.permission_status, p.caption_origin, p.audio_origin, p.retain_text, "
                "p.acoustic_analysis_allowed, p.dataset_export_allowed, p.model_training_allowed, "
                "p.voice_cloning_authorized, p.licence_reference, "
                "(SELECT COUNT(*) FROM speech_reference_videos v WHERE v.source_id=s.source_id) AS video_count, "
                "(SELECT COALESCE(SUM(sentence_count),0) FROM speech_reference_videos v WHERE v.source_id=s.source_id) AS sentence_count, "
                "(SELECT COALESCE(SUM(word_occurrence_count),0) FROM speech_reference_videos v WHERE v.source_id=s.source_id) AS word_occurrence_count, "
                "(SELECT COALESCE(SUM(reviewed_count),0) FROM speech_reference_videos v WHERE v.source_id=s.source_id) AS reviewed_count "
                "FROM speech_reference_sources s JOIN speech_source_provenance p ON p.provenance_id="
                "(SELECT provenance_id FROM speech_source_provenance WHERE source_id=s.source_id ORDER BY imported_at DESC LIMIT 1) "
                f"WHERE {sql_where} ORDER BY s.updated_at DESC LIMIT ? OFFSET ?",
                (*args, limit, offset)).fetchall()
        return {"total": total, "limit": limit, "offset": offset,
                "sources": [self._source_row(row) for row in rows]}

    def get_source(self, owner_id: str, source_id: str) -> dict:
        with closing(connect(self.database_file)) as connection:
            row = self._source_query(connection, owner_id, source_id)
            videos = connection.execute(
                "SELECT * FROM speech_reference_videos WHERE source_id=? ORDER BY published_at, video_id",
                (source_id,)).fetchall()
        body = self._source_row(row)
        body["videos"] = [dict(video) for video in videos]
        return body

    def run_job(self, owner_id: str, job_id: str) -> dict:
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM workflow_jobs WHERE job_id=? AND owner_id=?", (job_id, owner_id)).fetchone()
            if row is None or connection.execute(
                    "SELECT 1 FROM speech_reference_jobs WHERE job_id=?", (job_id,)).fetchone() is None:
                connection.rollback()
                raise JobNotFound("Speech-reference job not found")
            if row["status"] != "QUEUED":
                connection.rollback()
                raise InvalidTransition(f"Job in {row['status']} is not queued")
            connection.execute("UPDATE workflow_jobs SET status='RUNNING', updated_at=? WHERE job_id=? AND status='QUEUED'",
                               (now, job_id))
            connection.execute(
                "UPDATE workflow_stages SET status='RUNNING', attempt_count=attempt_count+1, updated_at=?, error='' "
                "WHERE job_id=?", (now, job_id))
            connection.execute(
                "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                "VALUES(?,?,'QUEUED','RUNNING',1,?)", (job_id, STAGE, now))
            connection.commit()
        cancel = threading.Event()
        try:
            summary = self._acquire(job_id, owner_id, cancel)
        except RuntimeError as exc:
            if str(exc) == "cancelled":
                self._finish_job(job_id, "CANCELLED", "Cancelled by user")
                return self.get_job(job_id, owner_id)
            self._finish_job(job_id, "FAILED", str(exc))
            return self.get_job(job_id, owner_id)
        except SpeechAcquireError as exc:
            self._finish_job(job_id, "FAILED", f"{exc.code}: {exc}")
            return self.get_job(job_id, owner_id)
        self._save_result(job_id, summary)
        self._finish_job(job_id, "COMPLETED", "")
        return self.get_job(job_id, owner_id)

    def cancel(self, owner_id: str, job_id: str) -> dict:
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT j.status FROM workflow_jobs j JOIN speech_reference_jobs s USING(job_id) "
                    "WHERE j.job_id=? AND j.owner_id=?", (job_id, owner_id)).fetchone()
                if row is None:
                    raise JobNotFound("Speech-reference job not found")
                connection.execute("UPDATE speech_reference_jobs SET cancel_requested=1, updated_at=? WHERE job_id=?",
                                   (now, job_id))
                if row["status"] == "CANCELLED":
                    connection.commit()
                    return self.get_job(job_id, owner_id)
                if row["status"] not in {"QUEUED", "RUNNING"}:
                    raise InvalidTransition(f"Job in {row['status']} cannot be cancelled")
                connection.execute(
                    "UPDATE worker_claims SET status='FAILED', finished_at=?, failure_detail='CANCELLED' "
                    "WHERE job_id=? AND status='ACTIVE'", (now, job_id))
                connection.execute(
                    "UPDATE workflow_stages SET status='FAILED', updated_at=?, error='Cancelled by user' WHERE job_id=?",
                    (now, job_id))
                connection.execute("UPDATE workflow_jobs SET status='CANCELLED', updated_at=? WHERE job_id=?", (now, job_id))
                connection.execute(
                    "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at, error) "
                    "VALUES(?,?,?,'CANCELLED',1,?,'Cancelled by user')", (job_id, STAGE, row["status"], now))
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return self.get_job(job_id, owner_id)

    def retry(self, owner_id: str, job_id: str, idempotency_key: str) -> dict:
        old = self.get_job(job_id, owner_id)
        if old["status"] not in {"FAILED", "CANCELLED"}:
            raise InvalidTransition(f"Job in {old['status']} cannot be retried")
        with closing(connect(self.database_file)) as connection:
            row = connection.execute("SELECT payload_json, source_id FROM speech_reference_jobs WHERE job_id=?",
                                     (job_id,)).fetchone()
            source = connection.execute(
                "SELECT * FROM speech_reference_sources WHERE source_id=? AND owner_id=?",
                (row["source_id"], owner_id)).fetchone()
        payload = json.loads(row["payload_json"])
        return self._queue_existing(owner_id, idempotency_key, source, payload, retry_of=job_id, topic=old["topic"])

    def list_jobs(self, owner_id: str, limit: int = 50) -> dict:
        with closing(connect(self.database_file)) as connection:
            rows = connection.execute(
                "SELECT j.*, s.kind AS speech_kind, s.progress_note, s.result_json, s.error_detail, s.source_id, s.retry_of "
                "FROM workflow_jobs j JOIN speech_reference_jobs s USING(job_id) WHERE j.owner_id=? "
                "ORDER BY j.created_at DESC LIMIT ?", (owner_id, min(limit, 200))).fetchall()
        return {"jobs": [self._job_shape(row) for row in rows]}

    def get_job(self, job_id: str, owner_id: str) -> dict:
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT j.*, s.kind AS speech_kind, s.progress_note, s.result_json, s.error_detail, s.source_id, s.retry_of "
                "FROM workflow_jobs j JOIN speech_reference_jobs s USING(job_id) WHERE j.job_id=? AND j.owner_id=?",
                (job_id, owner_id)).fetchone()
            if row is None:
                raise JobNotFound("Speech-reference job not found")
            stage = connection.execute(
                "SELECT status, attempt_count, max_attempts, error FROM workflow_stages WHERE job_id=?",
                (job_id,)).fetchone()
        body = self._job_shape(row)
        body["stage"] = dict(stage) if stage else None
        if stage and stage["error"] and not body["error"]:
            body["error"] = stage["error"]
        return body

    def upload_subtitles(self, owner_id: str, video_row_id: str, filename: str, data: bytes) -> dict:
        if not data:
            raise JobValidationError("Subtitle file is empty")
        if len(data) > 2_000_000:
            raise JobValidationError("Subtitle file exceeds 2 MB")
        try:
            raw = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise JobValidationError(f"Subtitle file is not UTF-8: {exc}") from None
        cues, warnings = parse_cues(raw)
        if not cues:
            raise JobValidationError("No subtitle cues could be parsed. No transcript text was invented.")
        with closing(connect(self.database_file)) as connection:
            video, source, provenance = self._video_bundle(connection, owner_id, video_row_id)
            if not provenance["retain_text"]:
                raise SpeechPermissionError(
                    "Text retention is not authorized for this source. Record permission before storing a transcript.")
            digest = hashlib.sha256(data).hexdigest()
            existing = connection.execute(
                "SELECT transcript_id FROM speech_reference_transcripts WHERE video_row_id=? AND source_sha256=?",
                (video_row_id, digest)).fetchone()
            if existing:
                return {"transcript_id": existing["transcript_id"], "unchanged": True, "warnings": warnings}
            stored = self._write_asset(source["source_id"], f"captions-{digest[:16]}.vtt", data, "text/vtt")
            transcript_id = self._store_transcript(
                connection, video, source, raw, cues, digest, stored.uri, "user_subtitle_file", owner_id, warnings)
            connection.execute(
                "UPDATE speech_reference_videos SET caption_availability='stored', caption_language=?, last_error='', "
                "last_processed_at=? WHERE video_row_id=?",
                (source["language_preference"], _now(), video_row_id))
            connection.commit()
        return {"transcript_id": transcript_id, "unchanged": False, "cue_count": len(cues), "warnings": warnings}

    def upload_audio(self, owner_id: str, video_row_id: str, filename: str, data: bytes) -> dict:
        if not data:
            raise JobValidationError("Audio file is empty")
        if len(data) > 50_000_000:
            raise JobValidationError("Audio file exceeds 50 MB")
        ext = Path(filename or "").suffix.lower()
        if ext not in {".wav", ".mp3", ".m4a", ".flac"}:
            raise JobValidationError("Audio must be a wav, mp3, m4a, or flac file you are authorized to store")
        with closing(connect(self.database_file)) as connection:
            video, source, provenance = self._video_bundle(connection, owner_id, video_row_id)
            if not provenance["acoustic_analysis_allowed"]:
                raise SpeechPermissionError(
                    "Acoustic analysis is not authorized. The recording was not stored. "
                    "Watching a video does not authorize retaining its audio.")
            digest = hashlib.sha256(data).hexdigest()
            existing = connection.execute(
                "SELECT sample_id, asset_uri FROM speech_samples WHERE video_row_id=? AND asset_sha256=?",
                (video_row_id, digest)).fetchone()
            if existing:
                connection.commit()
                return {"sample_id": existing["sample_id"], "unchanged": True, "asset_uri": existing["asset_uri"]}
            stored = self._write_asset(source["source_id"], f"audio-{digest[:16]}{ext}", data, "audio/wav" if ext == ".wav" else "application/octet-stream")
            measured = {"duration_ms": None, "sample_rate_hz": None, "channels": None, "energy_mean": None,
                        "method": None, "limitation": "Compressed audio was stored. Duration, energy, and pitch were not measured."}
            contour_ready = None
            if ext == ".wav":
                measured = measure_wav(data)
                contour_ready = data
            sample_id = str(uuid.uuid4())
            now = _now()
            connection.execute(
                "INSERT INTO speech_samples(sample_id, owner_id, asset_uri, asset_sha256, video_row_id, source_type, "
                "duration_ms, sample_rate_hz, channels, audio_quality_status, permission_status, review_status) "
                "VALUES(?,?,?,?,?,'shared_recording_offset',?,?,?,?, 'stored',?,'needs_review')",
                (sample_id, owner_id, stored.uri, digest, video_row_id, measured.get("duration_ms"),
                 measured.get("sample_rate_hz"), measured.get("channels"), provenance["permission_status"]))
            if measured.get("energy_mean") is not None:
                self._feature(connection, owner_id, "sample", sample_id, "energy_mean", measured["energy_mean"],
                              measured["method"], is_estimate=0)
            if contour_ready is not None:
                for segment in connection.execute(
                        "SELECT segment_id, start_ms, end_ms FROM speech_segments WHERE video_row_id=? "
                        "AND review_status!='superseded'", (video_row_id,)):
                    contour = segment_energy_contour(contour_ready, segment["start_ms"], segment["end_ms"])
                    if contour:
                        connection.execute(
                            "UPDATE speech_segments SET energy_contour_json=?, measurement_method=? WHERE segment_id=?",
                            (_canon(contour), "pcm_rms_v1", segment["segment_id"]))
                        self._feature(connection, owner_id, "segment", segment["segment_id"], "energy_contour",
                                      contour, "pcm_rms_v1", is_estimate=0)
            connection.execute(
                "UPDATE speech_reference_videos SET audio_availability='user_provided', audio_limitation=?, "
                "last_processed_at=? WHERE video_row_id=?",
                (measured.get("limitation") or "", now, video_row_id))
            connection.commit()
        return {"sample_id": sample_id, "unchanged": False, "measurement": measured, "pitch_measured": False,
                "note": "One recording is stored for this video. Word rows keep time offsets; they do not copy the file."}

    def correct_transcript(self, owner_id: str, transcript_id: str, cues: list, reviewer: str, note: str = "") -> dict:
        if not isinstance(cues, list) or not cues:
            raise JobValidationError("A corrected cue list is required")
        cleaned = []
        for cue in cues:
            try:
                start, end = int(cue["start_ms"]), int(cue["end_ms"])
            except (KeyError, TypeError, ValueError):
                raise JobValidationError("Each cue needs integer start_ms and end_ms") from None
            text = normalize_tamil(str(cue.get("text") or ""))
            if end <= start or not text:
                raise JobValidationError("Each corrected cue needs text and an end after its start")
            cleaned.append({"start_ms": start, "end_ms": end, "original_text": str(cue.get("text")), "text": text})
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            transcript = connection.execute(
                "SELECT t.*, v.source_id, v.video_row_id FROM speech_reference_transcripts t "
                "JOIN speech_reference_videos v USING(video_row_id) JOIN speech_reference_sources s "
                "ON s.source_id=v.source_id WHERE t.transcript_id=? AND s.owner_id=?",
                (transcript_id, owner_id)).fetchone()
            if transcript is None:
                connection.rollback()
                raise JobNotFound("Transcript not found")
            original = transcript["original_text"]
            version = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM speech_transcript_versions WHERE transcript_id=?",
                (transcript_id,)).fetchone()[0]
            version_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO speech_transcript_versions(version_id, transcript_id, version, cues_json, kind, created_at, created_by) "
                "VALUES(?,?,?,?,'corrected',?,?)",
                (version_id, transcript_id, version, _canon(cleaned), now, reviewer))
            connection.execute(
                "UPDATE speech_segments SET review_status='superseded' WHERE video_row_id=? AND review_status!='superseded'",
                (transcript["video_row_id"],))
            connection.execute(
                "UPDATE speech_occurrences SET review_status='superseded' WHERE video_row_id=? AND review_status!='superseded'",
                (transcript["video_row_id"],))
            self._segment_cues(connection, owner_id, transcript["source_id"], transcript["video_row_id"], version_id,
                               cleaned, transcript["language"] if "language" in transcript.keys() else "ta")
            connection.execute(
                "INSERT INTO speech_corrections(correction_id, owner_id, target_kind, target_id, field_name, previous_json, "
                "new_json, reviewer_id, occurred_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), owner_id, "transcript", transcript_id, "cues",
                 transcript["original_cues_json"], _canon(cleaned), reviewer, now))
            self._audit(connection, owner_id, "transcript", transcript_id, "correct", reviewer, note or "Cue correction",
                        {"version_id": version_id})
            connection.commit()
            untouched = connection.execute(
                "SELECT original_text FROM speech_reference_transcripts WHERE transcript_id=?",
                (transcript_id,)).fetchone()[0]
        if untouched != original:
            raise RuntimeError("Original transcript was modified")
        return {"transcript_id": transcript_id, "version_id": version_id, "version": version, "original_preserved": True}

    def link_occurrence(self, owner_id: str, occurrence_id: str, *, word_id: str | None = None,
                        canonical_text: str | None = None, surface_text: str | None = None,
                        word_type: str | None = None, domain: str | None = None) -> dict:
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            occurrence = self._owned_occurrence(connection, owner_id, occurrence_id)
            surface = normalize_tamil(surface_text or occurrence["observed_text"])
            if word_id:
                word = connection.execute(
                    "SELECT * FROM tamil_words WHERE word_id=? AND owner_id=?", (word_id, owner_id)).fetchone()
                if word is None:
                    connection.rollback()
                    raise JobNotFound("Tamil word not found")
            else:
                canonical = normalize_tamil(canonical_text or surface)
                if not canonical:
                    connection.rollback()
                    raise JobValidationError("canonical_text is required")
                word = connection.execute(
                    "SELECT * FROM tamil_words WHERE owner_id=? AND normalized_text=?",
                    (owner_id, canonical)).fetchone()
                if word is None:
                    word_id = str(uuid.uuid4())
                    now = _now()
                    connection.execute(
                        "INSERT INTO tamil_words(word_id, owner_id, tamil_text, normalized_text, word_type, domain, "
                        "is_technical_term, review_status, created_at, updated_at) VALUES(?,?,?,?,?,?,0,'unreviewed',?,?)",
                        (word_id, owner_id, canonical, canonical, word_type, domain, now, now))
                    word = connection.execute("SELECT * FROM tamil_words WHERE word_id=?", (word_id,)).fetchone()
            form_id = None
            if surface and surface != word["normalized_text"]:
                form = connection.execute(
                    "SELECT word_form_id FROM tamil_word_forms WHERE word_id=? AND normalized_surface_text=?",
                    (word["word_id"], surface)).fetchone()
                if form is None:
                    form_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO tamil_word_forms(word_form_id, word_id, surface_text, normalized_surface_text, review_status) "
                        "VALUES(?,?,?,?,'unreviewed')", (form_id, word["word_id"], surface, surface))
                else:
                    form_id = form["word_form_id"]
            connection.execute(
                "UPDATE speech_occurrences SET word_id=?, word_form_id=? WHERE occurrence_id=?",
                (word["word_id"], form_id, occurrence_id))
            connection.commit()
        return self.get_occurrence(owner_id, occurrence_id)

    def add_meaning(self, owner_id: str, word_id: str, *, meaning_ta: str, meaning_en: str | None = None,
                    context_domain: str | None = None, example_sentence_ta: str | None = None) -> dict:
        meaning_ta = normalize_tamil(meaning_ta)
        if not meaning_ta:
            raise JobValidationError("meaning_ta is required")
        meaning_id = str(uuid.uuid4())
        with closing(connect(self.database_file)) as connection:
            word = connection.execute(
                "SELECT 1 FROM tamil_words WHERE word_id=? AND owner_id=?", (word_id, owner_id)).fetchone()
            if word is None:
                raise JobNotFound("Tamil word not found")
            connection.execute(
                "INSERT INTO tamil_word_meanings(meaning_id, word_id, meaning_ta, meaning_en, context_domain, "
                "example_sentence_ta, review_status) VALUES(?,?,?,?,?,?,'unreviewed')",
                (meaning_id, word_id, meaning_ta, meaning_en, context_domain, example_sentence_ta))
        return {"meaning_id": meaning_id, "review_status": "unreviewed"}

    def add_pronunciation(self, owner_id: str, word_id: str, *, pronunciation_text: str,
                          phonetic_representation: str | None = None, phonetic_scheme: str | None = None,
                          word_form_id: str | None = None, meaning_id: str | None = None, locale: str = "ta-IN",
                          dialect: str | None = None, context_rule: str | None = None, provider_id: str | None = None,
                          confidence: float | None = None) -> dict:
        pronunciation_text = (pronunciation_text or "").strip()
        if not pronunciation_text:
            raise JobValidationError("pronunciation_text is required")
        if phonetic_scheme == "ipa":
            if not phonetic_representation or not validate_ipa(phonetic_representation):
                raise JobValidationError("IPA phonetic_representation is missing or contains characters this checker rejects")
        elif phonetic_representation and phonetic_scheme not in {None, "provider_hint", "ipa"}:
            raise JobValidationError("phonetic_scheme must be ipa or provider_hint")
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise JobValidationError("confidence must be between 0 and 1, or omitted")
        pronunciation_id = str(uuid.uuid4())
        with closing(connect(self.database_file)) as connection:
            if connection.execute("SELECT 1 FROM tamil_words WHERE word_id=? AND owner_id=?", (word_id, owner_id)).fetchone() is None:
                raise JobNotFound("Tamil word not found")
            connection.execute(
                "INSERT INTO tamil_pronunciations(pronunciation_id, word_id, word_form_id, meaning_id, pronunciation_text, "
                "phonetic_representation, phonetic_scheme, locale, dialect, context_rule, provider_id, confidence, "
                "confidence_origin, review_status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'unreviewed',?)",
                (pronunciation_id, word_id, word_form_id, meaning_id, pronunciation_text, phonetic_representation,
                 phonetic_scheme, locale or "ta-IN", dialect, context_rule, provider_id, confidence,
                 "reviewer_supplied" if confidence is not None else None, _now()))
        return {"pronunciation_id": pronunciation_id, "review_status": "unreviewed",
                "approval_means": None, "note": "Stored as an unreviewed variant. It is not labeled verified."}

    def correct_occurrence(self, owner_id: str, occurrence_id: str, reviewer: str, fields: dict, note: str = "") -> dict:
        if not isinstance(fields, dict) or not fields:
            raise JobValidationError("fields are required")
        unknown = set(fields) - _OCCURRENCE_FIELDS
        if unknown:
            raise JobValidationError(f"Unsupported occurrence fields: {', '.join(sorted(unknown))}")
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._owned_occurrence(connection, owner_id, occurrence_id)
            updates = dict(row)
            for key, value in fields.items():
                connection.execute(
                    "INSERT INTO speech_corrections(correction_id, owner_id, target_kind, target_id, field_name, "
                    "previous_json, new_json, reviewer_id, occurred_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), owner_id, "occurrence", occurrence_id, key, _canon(row[key]), _canon(value),
                     reviewer, now))
                updates[key] = value
            if "start_ms" in fields or "end_ms" in fields:
                start, end = int(updates["start_ms"]), int(updates["end_ms"])
                if end <= start:
                    connection.rollback()
                    raise JobValidationError("end_ms must be greater than start_ms")
                updates["duration_ms"] = end - start
                updates["duration_basis"] = "manual_boundary"
                updates["timestamp_kind"] = "manual"
                updates["alignment_method"] = "manual"
                updates["alignment_confidence"] = None
                updates["timestamps_are_exact"] = 0
            if "observed_text" in fields:
                updates["observed_text"] = normalize_tamil(str(updates["observed_text"]))
            updates["review_status"] = "needs_review"
            connection.execute(
                "UPDATE speech_occurrences SET observed_text=?, start_ms=?, end_ms=?, duration_ms=?, duration_basis=?, "
                "speaker_profile_id=?, sentence_position=?, delivery_label=?, timestamp_kind=?, alignment_method=?, "
                "alignment_confidence=?, timestamps_are_exact=0, review_status='needs_review' WHERE occurrence_id=?",
                (updates["observed_text"], int(updates["start_ms"]), int(updates["end_ms"]), int(updates["duration_ms"]),
                 updates["duration_basis"], updates["speaker_profile_id"], updates["sentence_position"],
                 updates["delivery_label"], updates["timestamp_kind"], updates["alignment_method"],
                 updates["alignment_confidence"], occurrence_id))
            self._audit(connection, owner_id, "occurrence", occurrence_id, "correct", reviewer, note, {})
            connection.commit()
        return self.get_occurrence(owner_id, occurrence_id)

    def review(self, owner_id: str, kind: str, target_id: str, action: str, reviewer: str, note: str = "") -> dict:
        if kind not in _REVIEW_KINDS or action not in {"approve", "reject"}:
            raise JobValidationError("Unsupported review target or action")
        status = "approved" if action == "approve" else "rejected"
        table = {"occurrence": "speech_occurrences", "segment": "speech_segments", "pronunciation": "tamil_pronunciations",
                 "word": "tamil_words", "sample": "speech_samples", "meaning": "tamil_word_meanings"}[kind]
        id_column = {"occurrence": "occurrence_id", "segment": "segment_id", "pronunciation": "pronunciation_id",
                     "word": "word_id", "sample": "sample_id", "meaning": "meaning_id"}[kind]
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if kind == "word":
                owned = connection.execute(
                    f"SELECT review_status, alignment_confidence FROM {table} WHERE {id_column}=? AND owner_id=?",
                    (target_id, owner_id)).fetchone() if kind == "occurrence" else None
            if kind == "occurrence":
                row = self._owned_occurrence(connection, owner_id, target_id)
            elif kind == "segment":
                row = connection.execute(
                    "SELECT g.review_status, g.segment_id FROM speech_segments g JOIN speech_reference_videos v USING(video_row_id) "
                    "JOIN speech_reference_sources s ON s.source_id=v.source_id WHERE g.segment_id=? AND s.owner_id=?",
                    (target_id, owner_id)).fetchone()
            elif kind == "sample":
                row = connection.execute(
                    "SELECT review_status FROM speech_samples WHERE sample_id=? AND owner_id=?",
                    (target_id, owner_id)).fetchone()
            elif kind in {"word", "pronunciation", "meaning"}:
                owner_sql = {
                    "word": "SELECT review_status FROM tamil_words WHERE word_id=? AND owner_id=?",
                    "pronunciation": "SELECT p.review_status FROM tamil_pronunciations p JOIN tamil_words w USING(word_id) "
                                     "WHERE p.pronunciation_id=? AND w.owner_id=?",
                    "meaning": "SELECT m.review_status FROM tamil_word_meanings m JOIN tamil_words w USING(word_id) "
                               "WHERE m.meaning_id=? AND w.owner_id=?",
                }[kind]
                row = connection.execute(owner_sql, (target_id, owner_id)).fetchone()
            else:
                row = None
            if row is None:
                connection.rollback()
                raise JobNotFound("Review target not found")
            if kind == "occurrence":
                connection.execute("UPDATE speech_occurrences SET review_status=? WHERE occurrence_id=?", (status, target_id))
                self._refresh_video(connection, row["video_row_id"])
            else:
                connection.execute(f"UPDATE {table} SET review_status=? WHERE {id_column}=?", (status, target_id))
            detail = {}
            if kind == "occurrence" and action == "approve" and row["alignment_confidence"] is not None and row["alignment_confidence"] < 0.5:
                detail["low_confidence"] = True
            self._audit(connection, owner_id, kind, target_id, action, reviewer, note, detail)
            connection.commit()
        body = {"kind": kind, "target_id": target_id, "review_status": status, "approval_means": "human_review"}
        body.update(detail)
        return body

    def list_occurrences(self, owner_id: str, *, query: str = "", delivery: str = "", rate_band_name: str = "",
                         sentence_position_name: str = "", review_status: str = "", speaker_profile_id: str = "",
                         include_superseded: bool = False, limit: int = 50, offset: int = 0) -> dict:
        limit, offset = min(max(int(limit), 1), 100), max(int(offset), 0)
        where = ["o.owner_id=?"]
        args: list = [owner_id]
        if not include_superseded:
            where.append("o.review_status!='superseded'")
        if query.strip():
            like = _like(normalize_tamil(query))
            where.append("(o.observed_text LIKE ? ESCAPE '\\' OR IFNULL(w.tamil_text,'') LIKE ? ESCAPE '\\' "
                         "OR IFNULL(w.normalized_text,'') LIKE ? ESCAPE '\\' OR IFNULL(f.surface_text,'') LIKE ? ESCAPE '\\')")
            args.extend([like, like, like, like])
        if delivery:
            where.append("o.delivery_label=?")
            args.append(delivery)
        if sentence_position_name:
            where.append("o.sentence_position=?")
            args.append(sentence_position_name)
        if review_status:
            where.append("o.review_status=?")
            args.append(review_status)
        if speaker_profile_id:
            where.append("o.speaker_profile_id=?")
            args.append(speaker_profile_id)
        if rate_band_name == "slow":
            where.append("o.speaking_rate < 90")
        elif rate_band_name == "normal":
            where.append("o.speaking_rate >= 90 AND o.speaking_rate <= 150")
        elif rate_band_name == "fast":
            where.append("o.speaking_rate > 150")
        elif rate_band_name:
            raise JobValidationError("rate_band must be slow, normal, or fast")
        sql = (" FROM speech_occurrences o LEFT JOIN tamil_words w ON w.word_id=o.word_id "
               "LEFT JOIN tamil_word_forms f ON f.word_form_id=o.word_form_id WHERE " + " AND ".join(where))
        with closing(connect(self.database_file)) as connection:
            total = connection.execute("SELECT COUNT(*)" + sql, args).fetchone()[0]
            rows = connection.execute(
                "SELECT o.*, w.tamil_text AS canonical_text, f.surface_text" + sql +
                " ORDER BY o.created_at, o.occurrence_id LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return {"total": total, "limit": limit, "offset": offset,
                "rate_band_note": "slow/normal/fast group caption-timing speaking-rate estimates. They are not universal speech categories.",
                "occurrences": [self._occurrence_body(row) for row in rows]}

    def get_occurrence(self, owner_id: str, occurrence_id: str) -> dict:
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT o.*, w.tamil_text AS canonical_text, f.surface_text FROM speech_occurrences o "
                "LEFT JOIN tamil_words w ON w.word_id=o.word_id LEFT JOIN tamil_word_forms f ON f.word_form_id=o.word_form_id "
                "WHERE o.occurrence_id=? AND o.owner_id=?", (occurrence_id, owner_id)).fetchone()
            if row is None:
                raise JobNotFound("Occurrence not found")
            segment = connection.execute(
                "SELECT energy_contour_json, measurement_method, segment_text, speaking_rate_method FROM speech_segments WHERE segment_id=?",
                (row["segment_id"],)).fetchone()
            video = connection.execute(
                "SELECT video_id, title, webpage_url, audio_availability FROM speech_reference_videos WHERE video_row_id=?",
                (row["video_row_id"],)).fetchone()
        body = self._occurrence_body(row)
        body["segment_text"] = segment["segment_text"] if segment else None
        body["energy_contour"] = json.loads(segment["energy_contour_json"]) if segment and segment["energy_contour_json"] else None
        body["energy_contour_scope"] = "segment" if body["energy_contour"] else None
        body["video"] = dict(video) if video else None
        body["pitch_measured"] = False
        return body

    def compare(self, owner_id: str, occurrence_ids: list[str]) -> dict:
        if not isinstance(occurrence_ids, list) or not 2 <= len(occurrence_ids) <= 6:
            raise JobValidationError("Select between 2 and 6 occurrences")
        rows = [self.get_occurrence(owner_id, occurrence_id) for occurrence_id in occurrence_ids]
        return {"occurrences": rows, "count": len(rows),
                "note": "Each row is one observed use. Durations are not averaged or copied onto the canonical word."}

    def search_words(self, owner_id: str, query: str) -> dict:
        like = _like(normalize_tamil(query))
        with closing(connect(self.database_file)) as connection:
            words = connection.execute(
                "SELECT * FROM tamil_words WHERE owner_id=? AND (normalized_text LIKE ? ESCAPE '\\' OR tamil_text LIKE ? ESCAPE '\\') "
                "ORDER BY tamil_text LIMIT 50", (owner_id, like, like)).fetchall()
            body = []
            for word in words:
                forms = connection.execute("SELECT * FROM tamil_word_forms WHERE word_id=?", (word["word_id"],)).fetchall()
                meanings = connection.execute("SELECT * FROM tamil_word_meanings WHERE word_id=?", (word["word_id"],)).fetchall()
                pronunciations = connection.execute(
                    "SELECT * FROM tamil_pronunciations WHERE word_id=?", (word["word_id"],)).fetchall()
                body.append({"word": dict(word), "forms": [dict(item) for item in forms],
                             "meanings": [dict(item) for item in meanings],
                             "pronunciations": [dict(item) for item in pronunciations]})
        return {"words": body}

    def video_detail(self, owner_id: str, video_row_id: str) -> dict:
        with closing(connect(self.database_file)) as connection:
            video, _source, provenance = self._video_bundle(connection, owner_id, video_row_id)
            transcript = connection.execute(
                "SELECT transcript_id, language, source_sha256, acquisition_method, imported_at, normalization_note, "
                "original_text FROM speech_reference_transcripts WHERE video_row_id=? ORDER BY imported_at DESC LIMIT 1",
                (video_row_id,)).fetchone()
            segments = connection.execute(
                "SELECT * FROM speech_segments WHERE video_row_id=? AND review_status!='superseded' ORDER BY ordinal",
                (video_row_id,)).fetchall()
            sample = connection.execute(
                "SELECT sample_id, duration_ms, sample_rate_hz, channels, audio_quality_status, permission_status, review_status "
                "FROM speech_samples WHERE video_row_id=? ORDER BY rowid DESC LIMIT 1", (video_row_id,)).fetchone()
        return {"video": dict(video), "permission_status": provenance["permission_status"],
                "acoustic_analysis_allowed": bool(provenance["acoustic_analysis_allowed"]),
                "transcript": dict(transcript) if transcript else None,
                "segments": [dict(segment) for segment in segments],
                "sample": dict(sample) if sample else None}

    def audio_file(self, owner_id: str, video_row_id: str) -> Path:
        with closing(connect(self.database_file)) as connection:
            _video, source, provenance = self._video_bundle(connection, owner_id, video_row_id)
            if not provenance["acoustic_analysis_allowed"]:
                raise SpeechPermissionError("Playback of retained audio is blocked until acoustic analysis is authorized.")
            sample = connection.execute(
                "SELECT asset_uri FROM speech_samples WHERE video_row_id=? AND asset_uri IS NOT NULL ORDER BY rowid DESC LIMIT 1",
                (video_row_id,)).fetchone()
        if sample is None:
            raise JobNotFound("No authorized recording is stored for this video")
        _uri, path = self.store._resolve(source["source_id"], sample["asset_uri"])
        if not path.is_file():
            raise JobNotFound("Stored recording file is missing")
        return path

    def export_manifest(self, owner_id: str, purpose: str) -> dict:
        if purpose == "voice_clone":
            raise SpeechPermissionError(
                "Voice cloning is not performed. An authorization flag does not imitate a speaker or train a voice.")
        if purpose not in {"experiment", "training"}:
            raise JobValidationError("purpose must be experiment or training")
        with closing(connect(self.database_file)) as connection:
            rows = connection.execute(
                "SELECT o.*, w.tamil_text, f.surface_text, v.video_id, v.webpage_url, s.source_url, "
                "p.permission_status, p.dataset_export_allowed, p.model_training_allowed, p.acoustic_analysis_allowed, "
                "p.licence_reference, p.voice_cloning_authorized, t.original_text IS NOT NULL AS has_transcript "
                "FROM speech_occurrences o JOIN speech_reference_videos v ON v.video_row_id=o.video_row_id "
                "JOIN speech_reference_sources s ON s.source_id=o.source_id "
                "JOIN speech_source_provenance p ON p.provenance_id=(SELECT provenance_id FROM speech_source_provenance "
                "WHERE source_id=s.source_id ORDER BY imported_at DESC LIMIT 1) "
                "LEFT JOIN tamil_words w ON w.word_id=o.word_id LEFT JOIN tamil_word_forms f ON f.word_form_id=o.word_form_id "
                "LEFT JOIN speech_reference_transcripts t ON t.video_row_id=v.video_row_id "
                "WHERE o.owner_id=? AND o.review_status='approved'", (owner_id,)).fetchall()
            items, blocked = [], []
            for row in rows:
                if row["permission_status"] != "recorded" or not row["dataset_export_allowed"]:
                    blocked.append({"occurrence_id": row["occurrence_id"],
                                    "reason": "Dataset export is not authorized for this source."})
                    continue
                if purpose == "training" and not row["model_training_allowed"]:
                    blocked.append({"occurrence_id": row["occurrence_id"],
                                    "reason": "Model training is not authorized for this source."})
                    continue
                items.append({
                    "occurrence_id": row["occurrence_id"], "observed_text": row["observed_text"],
                    "canonical_text": row["tamil_text"], "surface_text": row["surface_text"],
                    "source_url": row["source_url"], "video_id": row["video_id"], "webpage_url": row["webpage_url"],
                    "start_ms": row["start_ms"], "end_ms": row["end_ms"], "duration_ms": row["duration_ms"],
                    "duration_basis": row["duration_basis"], "timestamp_kind": row["timestamp_kind"],
                    "timestamps_are_exact": False, "speaking_rate": row["speaking_rate"],
                    "speaking_rate_method": row["speaking_rate_method"], "alignment_method": row["alignment_method"],
                    "alignment_confidence": row["alignment_confidence"], "delivery_label": row["delivery_label"],
                    "licence_reference": row["licence_reference"],
                    "audio_included": False,
                    "permitted_use": {"dataset_export": True, "model_training": bool(row["model_training_allowed"]),
                                      "voice_cloning": False},
                })
            if not items:
                reasons = sorted({item["reason"] for item in blocked}) or ["No approved occurrences are available to export."]
                raise SpeechPermissionError("Export blocked. " + " ".join(reasons))
            manifest = {
                "schema_version": "defence-studio.speech-training-manifest.v1",
                "purpose": purpose, "training_performed": False,
                "note": "This manifest lists authorized, approved observations. It does not train a model.",
                "items": items, "blocked": blocked,
            }
            encoded = _canon(manifest).encode()
            manifest_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO speech_training_manifests(manifest_id, owner_id, purpose, created_at, item_count, "
                "manifest_json, sha256, training_performed) VALUES(?,?,?,?,?,?,?,0)",
                (manifest_id, owner_id, purpose, _now(), len(items), encoded.decode(), hashlib.sha256(encoded).hexdigest()))
            self._audit(connection, owner_id, "manifest", manifest_id, "export", owner_id, purpose,
                        {"item_count": len(items), "blocked": len(blocked)})
            connection.commit()
        manifest["manifest_id"] = manifest_id
        return manifest

    def tts_hints(self, owner_id: str, text: str) -> dict:
        """Approved pronunciation text only. Pitch is not returned. Conflicts are not auto-resolved."""
        normalized = normalize_tamil(text)
        with closing(connect(self.database_file)) as connection:
            rows = connection.execute(
                "SELECT p.pronunciation_id, p.pronunciation_text, p.provider_id, p.word_form_id, w.tamil_text, "
                "f.surface_text FROM tamil_pronunciations p JOIN tamil_words w USING(word_id) "
                "LEFT JOIN tamil_word_forms f ON f.word_form_id=p.word_form_id "
                "WHERE w.owner_id=? AND p.review_status='approved'", (owner_id,)).fetchall()
        grouped: dict[str, list] = {}
        for row in rows:
            key = row["surface_text"] or row["tamil_text"]
            grouped.setdefault(key, []).append(row)
        terms, warnings, applied = {}, [], []
        for key, variants in grouped.items():
            if key and key in normalized:
                spoken = {item["pronunciation_text"] for item in variants}
                if len(spoken) == 1:
                    chosen = variants[0]
                    terms[key] = {"spoken": chosen["pronunciation_text"], "pronunciation_id": chosen["pronunciation_id"],
                                  "provider_id": chosen["provider_id"]}
                    applied.append(chosen["pronunciation_id"])
                else:
                    warnings.append(
                        f"Approved pronunciation variants disagree for {key}; none was applied automatically.")
        return {"terms": terms, "warnings": warnings, "applied_ids": applied,
                "note": "Dictionary hints are text substitutions. Reference pitch is not applied, and no model was trained."}

    def _acquire(self, job_id: str, owner_id: str, cancel) -> dict:
        with closing(connect(self.database_file)) as connection:
            job = connection.execute("SELECT source_id FROM speech_reference_jobs WHERE job_id=? AND owner_id=?",
                                     (job_id, owner_id)).fetchone()
            source = connection.execute(
                "SELECT * FROM speech_reference_sources WHERE source_id=? AND owner_id=?",
                (job["source_id"], owner_id)).fetchone()
            provenance = connection.execute(
                "SELECT * FROM speech_source_provenance WHERE source_id=? ORDER BY imported_at DESC LIMIT 1",
                (source["source_id"],)).fetchone()
        parsed = parse_youtube_reference(source["source_url"], source["source_kind"])
        self._progress(job_id, "Requesting metadata")
        if self._stopped(job_id, cancel):
            raise RuntimeError("cancelled")
        meta = self.metadata_client(parsed, source["max_videos"], cancel)
        videos = list(meta.get("videos") or [])[: source["max_videos"]]
        now = _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute(
                "UPDATE speech_reference_sources SET title=COALESCE(?, title), published_at=COALESCE(?, published_at), "
                "channel_id=COALESCE(?, channel_id), acquisition_status=?, limitation=?, last_error='', updated_at=? "
                "WHERE source_id=?",
                (meta.get("title"), meta.get("published_at"), meta.get("channel_id"),
                 "metadata_ready" if videos or source["source_kind"] == "video" else "metadata_partial",
                 meta.get("limitation") or "", now, source["source_id"]))
            connection.execute(
                "UPDATE speech_source_provenance SET acquisition_method=?, acquisition_outcome=? WHERE provenance_id=?",
                (meta.get("method") or "unknown", meta.get("outcome") or "unknown", provenance["provenance_id"]))
            for item in videos:
                self._insert_video(connection, source["source_id"], item, source["audio_source_method"], now)
            connection.commit()
        outcomes = []
        for index, item in enumerate(videos):
            if self._stopped(job_id, cancel):
                self._progress(job_id, f"Stopped after {index} of {len(videos)} videos. Partial results were kept.")
                raise RuntimeError("cancelled")
            self._progress(job_id, f"Video {index + 1}/{len(videos)} ({item.get('video_id')})")
            outcomes.append(self._ingest_captions(owner_id, source, provenance, item, cancel))
        if not videos:
            outcomes.append({"video_id": None, "outcome": meta.get("outcome"), "limitation": meta.get("limitation")})
        return {"method": meta.get("method"), "outcome": meta.get("outcome"), "limitation": meta.get("limitation"),
                "video_count": len(videos), "truncated": bool(meta.get("truncated")), "videos": outcomes}

    def _ingest_captions(self, owner_id, source, provenance, item, cancel) -> dict:
        video_id = item["video_id"]
        with closing(connect(self.database_file)) as connection:
            video = connection.execute(
                "SELECT * FROM speech_reference_videos WHERE source_id=? AND video_id=?",
                (source["source_id"], video_id)).fetchone()
        if source["subtitle_preference"] != "official_captions":
            return {"video_id": video_id, "outcome": "metadata_only",
                    "limitation": "Captions were not requested for this source."}
        if not provenance["retain_text"]:
            message = "Caption download was not run because text retention is not authorized."
            with closing(connect(self.database_file)) as connection:
                connection.execute("UPDATE speech_reference_videos SET last_error=?, last_processed_at=? WHERE video_row_id=?",
                                   (message, _now(), video["video_row_id"]))
                connection.commit()
            return {"video_id": video_id, "outcome": "captions_not_stored", "limitation": message}
        page = item.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        try:
            fetched = self.subtitle_fetcher(page, [source["language_preference"], "ta"], 2_000_000, cancel)
        except SpeechAcquireError as exc:
            with closing(connect(self.database_file)) as connection:
                connection.execute(
                    "UPDATE speech_reference_videos SET caption_availability=?, last_error=?, last_processed_at=? "
                    "WHERE video_row_id=?",
                    ("missing" if exc.code == "no_subtitles" else "error", f"{exc.code}: {exc}", _now(), video["video_row_id"]))
                connection.commit()
            return {"video_id": video_id, "outcome": exc.code, "limitation": str(exc), "retryable": exc.retryable}
        tracks = [track for track in fetched.get("tracks") or [] if track.get("content")]
        if not tracks:
            message = "No subtitle track was returned. No transcript text was invented."
            with closing(connect(self.database_file)) as connection:
                connection.execute(
                    "UPDATE speech_reference_videos SET caption_availability='missing', last_error=?, last_processed_at=? "
                    "WHERE video_row_id=?", (message, _now(), video["video_row_id"]))
                connection.commit()
            return {"video_id": video_id, "outcome": "no_subtitles", "limitation": message}
        track = tracks[0]
        data = track["content"] if isinstance(track["content"], bytes) else str(track["content"]).encode()
        try:
            raw = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            message = "Subtitle bytes were not valid UTF-8. They were not decoded with a fallback encoding."
            with closing(connect(self.database_file)) as connection:
                connection.execute("UPDATE speech_reference_videos SET last_error=? WHERE video_row_id=?",
                                   (message, video["video_row_id"]))
                connection.commit()
            return {"video_id": video_id, "outcome": "undecodable_subtitles", "limitation": message}
        cues, warnings = parse_cues(raw)
        if not cues:
            message = "Subtitle file contained no usable cues. No transcript text was invented."
            with closing(connect(self.database_file)) as connection:
                connection.execute(
                    "UPDATE speech_reference_videos SET caption_availability='missing', last_error=? WHERE video_row_id=?",
                    (message, video["video_row_id"]))
                connection.commit()
            return {"video_id": video_id, "outcome": "no_cues", "limitation": message, "warnings": warnings}
        digest = hashlib.sha256(data).hexdigest()
        with closing(connect(self.database_file)) as connection:
            existing = connection.execute(
                "SELECT transcript_id FROM speech_reference_transcripts WHERE video_row_id=? AND source_sha256=?",
                (video["video_row_id"], digest)).fetchone()
            if existing:
                connection.commit()
                return {"video_id": video_id, "outcome": "unchanged", "transcript_id": existing["transcript_id"]}
            stored = self._write_asset(source["source_id"], f"captions-{video_id}-{digest[:12]}.{track.get('ext') or 'vtt'}", data, "text/vtt")
            transcript_id = self._store_transcript(
                connection, video, source, raw, cues, digest, stored.uri,
                "youtube_subtitle_provider" + ("_auto" if track.get("is_auto") else ""), owner_id, warnings)
            connection.execute(
                "UPDATE speech_reference_videos SET caption_availability='stored', caption_language=?, title=COALESCE(title, ?), "
                "last_error='', last_processed_at=? WHERE video_row_id=?",
                (track.get("language"), fetched.get("title"), _now(), video["video_row_id"]))
            connection.commit()
        return {"video_id": video_id, "outcome": "captions_stored", "transcript_id": transcript_id, "warnings": warnings}

    def _store_transcript(self, connection, video, source, raw, cues, digest, uri, method, owner_id, warnings) -> str:
        now = _now()
        transcript_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            "INSERT INTO speech_reference_transcripts(transcript_id, video_row_id, language, original_text, original_cues_json, "
            "source_asset_uri, source_sha256, acquisition_method, imported_at, normalization_note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (transcript_id, video["video_row_id"], source["language_preference"], raw, _canon(cues), uri, digest, method, now,
             NORMALIZATION_NOTE + ((" Warnings: " + "; ".join(warnings)) if warnings else "")))
        normalized = [{**cue} for cue in cues]
        connection.execute(
            "INSERT INTO speech_transcript_versions(version_id, transcript_id, version, cues_json, kind, created_at, created_by) "
            "VALUES(?,?,1,?,'normalized',?,?)",
            (version_id, transcript_id, _canon(normalized), now, "import"))
        self._segment_cues(connection, owner_id, source["source_id"], video["video_row_id"], version_id, normalized,
                           source["language_preference"])
        provenance = connection.execute(
            "SELECT provenance_id FROM speech_source_provenance WHERE source_id=? ORDER BY imported_at DESC LIMIT 1",
            (source["source_id"],)).fetchone()
        connection.execute("UPDATE speech_occurrences SET provenance_id=? WHERE version_id=?",
                           (provenance["provenance_id"], version_id))
        return transcript_id

    def _segment_cues(self, connection, owner_id, source_id, video_row_id, version_id, cues, language) -> None:
        now = _now()
        previous_end = None
        for ordinal, cue in enumerate(cues):
            tokens = tokenize(cue["text"])
            duration = cue["end_ms"] - cue["start_ms"]
            rate = speaking_rate(len(tokens), duration)
            pause_before = None
            if previous_end is not None and cue["start_ms"] >= previous_end:
                pause_before = cue["start_ms"] - previous_end
            segment_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO speech_segments(segment_id, video_row_id, version_id, source_id, ordinal, start_ms, end_ms, "
                "segment_text, segment_type, speaking_rate, speaking_rate_method, pause_structure_json, delivery_label, "
                "measurement_method, confidence, timestamp_kind, review_status) VALUES(?,?,?,?,?,?,?,?, 'cue', ?, ?, ?, NULL, "
                "'estimated_from_caption_timing', NULL, 'source_caption', 'needs_review')",
                (segment_id, video_row_id, version_id, source_id, ordinal, cue["start_ms"], cue["end_ms"], cue["text"],
                 rate, "estimated_from_caption_timing" if rate is not None else None,
                 _canon({"pause_before_ms": pause_before, "pause_basis": "caption_gap" if pause_before is not None else None})))
            spans = [(cue["start_ms"], cue["end_ms"])] if len(tokens) == 1 else proportional_spans(
                cue["start_ms"], cue["end_ms"], tokens)
            if len(tokens) == 1:
                kind, method, confidence, basis = "source_caption", "source_caption_cue", 0.5, "caption_span"
            elif spans:
                kind, method, confidence, basis = "estimate", "proportional_within_caption_cue", 0.2, "proportional_estimate"
            else:
                spans = []
            for index, token in enumerate(tokens):
                if index >= len(spans):
                    break
                start, end = spans[index]
                connection.execute(
                    "INSERT INTO speech_occurrences(occurrence_id, owner_id, source_id, video_row_id, version_id, segment_id, "
                    "observed_text, start_ms, end_ms, duration_ms, duration_basis, context_before, context_after, "
                    "sentence_position, speaking_rate, speaking_rate_method, pause_before_ms, alignment_method, "
                    "alignment_confidence, timestamp_kind, timestamps_are_exact, measurement_method, review_status, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?, 'needs_review', ?)",
                    (str(uuid.uuid4()), owner_id, source_id, video_row_id, version_id, segment_id, token, start, end,
                     end - start, basis, tokens[index - 1] if index else None, tokens[index + 1] if index + 1 < len(tokens) else None,
                     sentence_position(index, len(tokens)), rate,
                     "estimated_from_caption_timing" if rate is not None else None,
                     pause_before if index == 0 else None, method, confidence, kind,
                     "estimated_from_caption_timing" if kind == "estimate" else "source_caption_span", now))
            previous_end = cue["end_ms"]
        self._refresh_video(connection, video_row_id)
        connection.execute(
            "UPDATE speech_reference_videos SET analysis_status='ready_for_review' WHERE video_row_id=?", (video_row_id,))
        connection.execute(
            "UPDATE speech_reference_sources SET analysis_status='ready_for_review', last_processed_at=? WHERE source_id=?",
            (now, source_id))

    def _insert_video(self, connection, source_id: str, item: dict, audio_method: str, now: str) -> None:
        video_id = item.get("video_id")
        if not video_id:
            return
        audio_availability = "blocked" if audio_method == "youtube_audio" else "none"
        limitation = ""
        if audio_method == "youtube_audio":
            limitation = "YouTube audio was not downloaded. Provide an authorized recording to analyse sound."
        connection.execute(
            "INSERT INTO speech_reference_videos(video_row_id, source_id, video_id, title, published_at, webpage_url, "
            "caption_language, caption_availability, audio_availability, audio_limitation, last_processed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id, video_id) DO UPDATE SET "
            "title=COALESCE(excluded.title, speech_reference_videos.title), "
            "published_at=COALESCE(excluded.published_at, speech_reference_videos.published_at), "
            "caption_language=COALESCE(excluded.caption_language, speech_reference_videos.caption_language), "
            "caption_availability=CASE WHEN speech_reference_videos.caption_availability='stored' THEN 'stored' "
            "ELSE excluded.caption_availability END, last_processed_at=excluded.last_processed_at",
            (str(uuid.uuid4()), source_id, video_id, item.get("title"), item.get("published_at"), item.get("webpage_url"),
             item.get("caption_language"), item.get("caption_availability") or "unknown", audio_availability, limitation, now))

    def _refresh_video(self, connection, video_row_id: str) -> None:
        connection.execute(
            "UPDATE speech_reference_videos SET sentence_count=(SELECT COUNT(*) FROM speech_segments WHERE video_row_id=? "
            "AND review_status!='superseded'), word_occurrence_count=(SELECT COUNT(*) FROM speech_occurrences "
            "WHERE video_row_id=? AND review_status!='superseded'), reviewed_count=(SELECT COUNT(*) FROM speech_occurrences "
            "WHERE video_row_id=? AND review_status='approved') WHERE video_row_id=?",
            (video_row_id, video_row_id, video_row_id, video_row_id))

    def _video_bundle(self, connection, owner_id: str, video_row_id: str):
        video = connection.execute("SELECT * FROM speech_reference_videos WHERE video_row_id=?", (video_row_id,)).fetchone()
        if video is None:
            raise JobNotFound("Video not found")
        source = connection.execute(
            "SELECT * FROM speech_reference_sources WHERE source_id=? AND owner_id=?",
            (video["source_id"], owner_id)).fetchone()
        if source is None:
            raise JobNotFound("Video not found")
        provenance = connection.execute(
            "SELECT * FROM speech_source_provenance WHERE source_id=? ORDER BY imported_at DESC LIMIT 1",
            (source["source_id"],)).fetchone()
        return video, source, provenance

    def _owned_occurrence(self, connection, owner_id: str, occurrence_id: str):
        row = connection.execute(
            "SELECT * FROM speech_occurrences WHERE occurrence_id=? AND owner_id=?",
            (occurrence_id, owner_id)).fetchone()
        if row is None:
            raise JobNotFound("Occurrence not found")
        return row

    def _write_asset(self, source_id: str, name: str, data: bytes, media_type: str):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "asset.bin"
        return self.store.write_bytes(source_id, f"jobs/{source_id}/speech/{safe}", data, media_type)

    def _feature(self, connection, owner_id, kind, target_id, name, value, method, *, is_estimate: int) -> None:
        connection.execute(
            "INSERT INTO speech_feature_measurements(measurement_id, owner_id, target_kind, target_id, feature_name, "
            "value_json, method, is_estimate, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), owner_id, kind, target_id, name, _canon(value), method, is_estimate, _now()))

    def _permission(self, permission: dict) -> dict:
        status = permission.get("permission_status") or "unknown"
        if status not in {"unknown", "recorded"}:
            raise JobValidationError("permission_status must be unknown or recorded")
        flags = {
            "retain_text": bool(permission.get("retain_text")),
            "acoustic_analysis_allowed": bool(permission.get("acoustic_analysis_allowed")),
            "dataset_export_allowed": bool(permission.get("dataset_export_allowed")),
            "model_training_allowed": bool(permission.get("model_training_allowed")),
            "voice_cloning_authorized": bool(permission.get("voice_cloning_authorized")),
        }
        if status != "recorded" and any(flags.values()):
            raise JobValidationError(
                "Restricted uses require permission_status 'recorded'. Unknown permission cannot enable retention, "
                "acoustic analysis, export, training, or voice cloning.")
        if flags["model_training_allowed"] and not flags["dataset_export_allowed"]:
            raise JobValidationError("Model training requires dataset export to be authorized as well")
        return {"permission_status": status, "source_owner": str(permission.get("source_owner") or "")[:300],
                "licence_reference": str(permission.get("licence_reference") or "")[:500],
                "notes": str(permission.get("notes") or "")[:2000], **flags}

    def _source_query(self, connection, owner_id: str, source_id: str):
        row = connection.execute(
            "SELECT s.*, p.permission_status, p.caption_origin, p.audio_origin, p.retain_text, "
            "p.acoustic_analysis_allowed, p.dataset_export_allowed, p.model_training_allowed, "
            "p.voice_cloning_authorized, p.licence_reference, p.acquisition_method, p.acquisition_outcome, "
            "(SELECT COUNT(*) FROM speech_reference_videos v WHERE v.source_id=s.source_id) AS video_count, "
            "(SELECT COALESCE(SUM(sentence_count),0) FROM speech_reference_videos v WHERE v.source_id=s.source_id) AS sentence_count, "
            "(SELECT COALESCE(SUM(word_occurrence_count),0) FROM speech_reference_videos v WHERE v.source_id=s.source_id) AS word_occurrence_count, "
            "(SELECT COALESCE(SUM(reviewed_count),0) FROM speech_reference_videos v WHERE v.source_id=s.source_id) AS reviewed_count "
            "FROM speech_reference_sources s JOIN speech_source_provenance p ON p.provenance_id="
            "(SELECT provenance_id FROM speech_source_provenance WHERE source_id=s.source_id ORDER BY imported_at DESC LIMIT 1) "
            "WHERE s.source_id=? AND s.owner_id=?", (source_id, owner_id)).fetchone()
        if row is None:
            raise JobNotFound("Speech-reference source not found")
        return row

    def _source_row(self, row) -> dict:
        body = dict(row)
        body["review_progress"] = (
            {"reviewed": body.get("reviewed_count") or 0, "occurrences": body.get("word_occurrence_count") or 0})
        body["voice_cloning_performed"] = False
        return body

    def _occurrence_body(self, row) -> dict:
        body = dict(row)
        body["timestamps_are_exact"] = False
        body["rate_band"] = rate_band(body.get("speaking_rate"))
        body["rate_band_basis"] = body.get("speaking_rate_method")
        body["pitch_median_hz"] = body.get("pitch_median_hz")
        return body

    def _job_shape(self, row) -> dict:
        return {"job_id": row["job_id"], "source_id": row["source_id"], "kind": row["speech_kind"],
                "status": row["status"], "topic": row["topic"], "language": row["language"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "progress_note": row["progress_note"], "error": row["error_detail"] or None,
                "retry_of": row["retry_of"], "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "can_cancel": row["status"] in {"QUEUED", "RUNNING"},
                "can_retry": row["status"] in {"FAILED", "CANCELLED"}}

    def _queue_existing(self, owner_id, idempotency_key, source, payload, *, retry_of, topic) -> dict:
        digest = hashlib.sha256(_canon(payload).encode()).hexdigest()
        job_id, now = str(uuid.uuid4()), _now()
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT job_id, request_hash FROM workflow_jobs WHERE owner_id=? AND idempotency_key=?",
                    (owner_id, idempotency_key)).fetchone()
                if existing:
                    if existing["request_hash"] != digest:
                        raise IdempotencyConflict("Idempotency key was already used with a different request")
                    connection.commit()
                    return self.get_job(existing["job_id"], owner_id)
                connection.execute(
                    "INSERT INTO workflow_jobs(job_id, owner_id, idempotency_key, request_schema, request_hash, topic, "
                    "language, status, created_at, updated_at, queued_at) VALUES(?,?,?,?,?,?,?,'QUEUED',?,?,?)",
                    (job_id, owner_id, idempotency_key, REQUEST_SCHEMA, digest, topic, source["language_preference"], now, now, now))
                connection.execute(
                    "INSERT INTO workflow_stages(job_id, stage_key, ordinal, status, version, created_at, updated_at) "
                    "VALUES(?,?,0,'PENDING',1,?,?)", (job_id, STAGE, now, now))
                connection.execute(
                    "INSERT INTO workflow_reference_placeholder(job_id) SELECT ? WHERE 0", (job_id,)) if False else None
                connection.execute(
                    "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at) "
                    "VALUES(?,?,'','PENDING',1,?)", (job_id, STAGE, now))
                connection.execute(
                    "INSERT INTO speech_reference_jobs(job_id, owner_id, source_id, kind, payload_json, payload_sha256, "
                    "retry_of, created_at, updated_at) VALUES(?,?,?,'inspect',?,?,?,?,?)",
                    (job_id, owner_id, source["source_id"], _canon(payload), digest, retry_of, now, now))
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return self.get_job(job_id, owner_id)

    def _progress(self, job_id: str, note: str) -> None:
        with closing(connect(self.database_file)) as connection:
            connection.execute("UPDATE speech_reference_jobs SET progress_note=?, updated_at=? WHERE job_id=?",
                               (note[:500], _now(), job_id))

    def _save_result(self, job_id: str, summary: dict) -> None:
        with closing(connect(self.database_file)) as connection:
            connection.execute("UPDATE speech_reference_jobs SET result_json=?, progress_note='Completed', updated_at=? WHERE job_id=?",
                               (_canon(summary), _now(), job_id))

    def _finish_job(self, job_id: str, status: str, error: str) -> None:
        now = _now()
        stage = "COMPLETED" if status == "COMPLETED" else "FAILED"
        with closing(connect(self.database_file)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT status FROM workflow_jobs WHERE job_id=?", (job_id,)).fetchone()
            if current and current["status"] == "CANCELLED" and status != "CANCELLED":
                connection.commit()
                return
            connection.execute("UPDATE workflow_jobs SET status=?, updated_at=? WHERE job_id=?", (status, now, job_id))
            connection.execute("UPDATE workflow_stages SET status=?, error=?, updated_at=? WHERE job_id=?",
                               (stage, error[:4000], now, job_id))
            connection.execute("UPDATE speech_reference_jobs SET error_detail=?, updated_at=? WHERE job_id=?",
                               (error[:4000], now, job_id))
            connection.execute(
                "INSERT INTO workflow_transitions(job_id, stage_key, from_status, to_status, stage_version, occurred_at, error) "
                "VALUES(?,?,'RUNNING',?,?,?)", (job_id, STAGE, status, now, error[:4000]))
            connection.commit()

    def _stopped(self, job_id: str, cancel) -> bool:
        if cancel is not None and cancel.is_set():
            return True
        with closing(connect(self.database_file)) as connection:
            row = connection.execute(
                "SELECT j.status, s.cancel_requested FROM workflow_jobs j JOIN speech_reference_jobs s USING(job_id) "
                "WHERE j.job_id=?", (job_id,)).fetchone()
        return row is None or row["status"] == "CANCELLED" or bool(row["cancel_requested"])

    def _audit(self, connection, owner_id, kind, target_id, action, reviewer, note, detail) -> None:
        connection.execute(
            "INSERT INTO speech_reviews(review_id, owner_id, target_kind, target_id, action, reviewer_id, note, "
            "detail_json, occurred_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), owner_id, kind, target_id, action, reviewer, note or "", _canon(detail or {}), _now()))
