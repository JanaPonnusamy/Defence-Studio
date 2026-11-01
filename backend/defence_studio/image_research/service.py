"""Image Research jobs: search, acquisition, dedup, review, export.

The module runs without a content-research job. It does not invent search
hits, licenses, or a full candidate count when sources fall short.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from defence_studio.database import connect
from defence_studio.image_research.dedup import assign_duplicates, canonical_url, host_of
from defence_studio.image_research.images import image_size, perceptual_hash, sha256_bytes, sniff_format
from defence_studio.image_research.providers import (
    GuardedImageDownloader,
    GuardedJsonClient,
    OpenverseProvider,
    ProviderError,
    SocialMediaSource,
    WikimediaCommonsProvider,
)
from defence_studio.image_research.schema import ensure_schema


_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_STOP = {"the", "a", "an", "of", "and", "for", "to", "in", "on", "with", "from", "by"}
_ALLOWED_SOURCES = {"wikimedia_commons", "openverse", "social_media"}
_EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "jpg": ".jpg", "gif": ".gif", "webp": ".webp"}
_DISK_FLOOR = 100 * 1024 * 1024
_REVIEW_STATUSES = {"PENDING_REVIEW", "ACCEPTED", "REJECTED", "NEEDS_VERIFICATION"}


class ImageResearchError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def default_providers(client=None) -> list:
    json_client = client or GuardedJsonClient()
    return [WikimediaCommonsProvider(json_client), OpenverseProvider(json_client), SocialMediaSource()]


class ImageResearchService:
    def __init__(self, database_file, storage_root, *, providers=None, downloader=None,
                 disk_free=None, now=None):
        self.database_file = Path(database_file)
        self.storage_root = Path(storage_root)
        self.providers = providers if providers is not None else default_providers()
        self.downloader = downloader or GuardedImageDownloader()
        self._disk_free = disk_free or (lambda: shutil.disk_usage(self.storage_root).free)
        self._now = now or (lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        self.storage_root.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            ensure_schema(connection)

    def providers_status(self) -> dict:
        return {
            "providers": [provider.describe() for provider in self.providers],
            "live_search": False,
            "note": "This is configuration status. Counts come only from a completed job.",
        }

    def create_job(self, owner_id: str, idempotency_key: str, body: dict) -> dict:
        request = _normalize_request(body)
        with closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT job_id FROM image_research_jobs WHERE owner_id=? AND idempotency_key=?",
                (owner_id, idempotency_key),
            ).fetchone()
            if existing:
                return self.get_job(existing["job_id"], owner_id)
            job_id = str(uuid.uuid4())
            now = self._now()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO image_research_jobs
                   (job_id, owner_id, idempotency_key, topic, request_json, status, error, summary_json,
                    research_topic_id, script_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'QUEUED', NULL, NULL, ?, ?, ?, ?)""",
                (job_id, owner_id, idempotency_key, request["topic"], json.dumps(request),
                 request.get("research_topic_id"), request.get("script_id"), now, now),
            )
            connection.execute("COMMIT")
        return self.get_job(job_id, owner_id)

    def list_jobs(self, owner_id: str) -> dict:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT job_id, topic, status, error, summary_json, created_at, updated_at
                   FROM image_research_jobs WHERE owner_id=? ORDER BY created_at DESC""",
                (owner_id,),
            ).fetchall()
        return {"jobs": [_job_summary(row) for row in rows]}

    def get_job(self, job_id: str, owner_id: str) -> dict:
        with closing(self._connect()) as connection:
            row = self._job_row(connection, job_id, owner_id)
        return _job_detail(row)

    def run(self, job_id: str, owner_id: str) -> dict:
        job = self.get_job(job_id, owner_id)
        if job["status"] in {"COMPLETED", "COMPLETED_WITH_SHORTFALL"}:
            return job
        self._set_status(job_id, "RUNNING", None)
        try:
            self._execute(job_id, owner_id, job["request"])
        except ImageResearchError as exc:
            self._set_status(job_id, "FAILED", exc.detail)
            raise
        except Exception as exc:
            self._set_status(job_id, "FAILED", f"Image research failed: {exc}")
            raise ImageResearchError(500, "Image research failed") from exc
        return self.get_job(job_id, owner_id)

    def candidates(self, job_id: str, owner_id: str, filters: dict | None = None) -> dict:
        self.get_job(job_id, owner_id)
        clauses = ["job_id=?"]
        params: list = [job_id]
        filters = filters or {}
        for column, key in (
            ("provider", "source"),
            ("review_status", "review_status"),
            ("rights_status", "rights_status"),
            ("duplicate_role", "duplicate_role"),
            ("license_name", "license"),
            ("category", "category"),
        ):
            value = filters.get(key)
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""SELECT * FROM image_research_candidates
                    WHERE {' AND '.join(clauses)} ORDER BY eligible DESC, relevance_score DESC, title""",
                params,
            ).fetchall()
        return {"candidates": [_candidate(row) for row in rows]}

    def duplicates(self, job_id: str, owner_id: str) -> dict:
        payload = self.candidates(job_id, owner_id)
        groups: dict[str, list] = {}
        for candidate in payload["candidates"]:
            if not candidate.get("similarity_group_id"):
                continue
            groups.setdefault(candidate["similarity_group_id"], []).append(candidate)
        visible = []
        for members in groups.values():
            if len(members) < 2:
                continue
            preferred = next((item for item in members if item["duplicate_role"] == "PREFERRED"), members[0])
            visible.append({
                "similarity_group_id": preferred["similarity_group_id"],
                "preferred": preferred,
                "alternatives": [item for item in members if item["image_id"] != preferred["image_id"]],
            })
        return {"groups": visible, "crop_detection": "NOT_IMPLEMENTED"}

    def review(self, image_id: str, owner_id: str, action: str, reason: str) -> dict:
        if action not in {"ACCEPT", "REJECT", "NEEDS_VERIFICATION"}:
            raise ImageResearchError(422, "Review action is not supported")
        if action == "REJECT" and not (reason or "").strip():
            raise ImageResearchError(422, "A rejection reason is required")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT c.*, j.owner_id FROM image_research_candidates c
                   JOIN image_research_jobs j ON j.job_id=c.job_id
                   WHERE c.image_id=?""",
                (image_id,),
            ).fetchone()
            if row is None or row["owner_id"] != owner_id:
                raise ImageResearchError(404, "Image candidate not found")
            skip = _accept_block(row, action)
            if skip:
                raise ImageResearchError(409, skip)
            now = self._now()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO image_research_reviews
                   (review_id, image_id, job_id, owner_id, action, from_status, to_status, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), image_id, row["job_id"], owner_id, action, row["review_status"],
                 _status_for(action), reason.strip() or None, now),
            )
            connection.execute(
                "UPDATE image_research_candidates SET review_status=? WHERE image_id=?",
                (_status_for(action), image_id),
            )
            connection.execute("COMMIT")
            self._refresh_summary(connection, row["job_id"])
            updated = connection.execute(
                "SELECT * FROM image_research_candidates WHERE image_id=?", (image_id,)
            ).fetchone()
        return _candidate(updated)

    def bulk_review(self, job_id: str, owner_id: str, action: str, image_ids: list[str], reason: str) -> dict:
        if action not in {"ACCEPT", "REJECT"}:
            raise ImageResearchError(422, "Bulk review supports ACCEPT or REJECT")
        applied, skipped = [], []
        for image_id in image_ids:
            try:
                applied.append(self.review(image_id, owner_id, action, reason))
            except ImageResearchError as exc:
                skipped.append({"image_id": image_id, "reason": exc.detail})
        self.get_job(job_id, owner_id)
        return {"applied": applied, "skipped": skipped}

    def export_manifest(self, job_id: str, owner_id: str) -> dict:
        job = self.get_job(job_id, owner_id)
        accepted = [item for item in self.candidates(job_id, owner_id)["candidates"]
                    if item["review_status"] == "ACCEPTED"]
        return {
            "job_id": job_id,
            "topic": job["topic"],
            "exported_at": self._now(),
            "accepted_count": len(accepted),
            "images": accepted,
            "note": "Accepted images are not inserted into a video. Publishing still requires the existing approval workflow.",
        }

    def preview_path(self, image_id: str, owner_id: str) -> Path:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT c.stored_relative_path, j.owner_id
                   FROM image_research_candidates c
                   JOIN image_research_jobs j ON j.job_id=c.job_id
                   WHERE c.image_id=?""",
                (image_id,),
            ).fetchone()
        if row is None or row["owner_id"] != owner_id or not row["stored_relative_path"]:
            raise ImageResearchError(404, "No stored preview for this candidate")
        return self._resolve_stored(row["stored_relative_path"])

    def _execute(self, job_id: str, owner_id: str, request: dict) -> None:
        free = int(self._disk_free())
        if free < _DISK_FLOOR:
            raise ImageResearchError(409, f"INSUFFICIENT_DISK: {free} bytes free; image acquisition needs at least {_DISK_FLOOR}")
        selected = [item for item in self.providers if item.name in request["sources"]]
        reports = []
        drafts = []
        limit = min(120, max(request["minimum_unique"] * 2, request["minimum_unique"] + 15))
        query = " ".join([request["topic"], *request["keywords"], *request["categories"]]).strip()
        for provider in selected:
            described = provider.describe()
            if described.get("status") == "UNSUPPORTED":
                reports.append({**described, "result_count": 0, "runtime_status": "UNSUPPORTED"})
                continue
            try:
                found = provider.search(query, limit)
            except ProviderError as exc:
                reports.append({**described, "result_count": 0, "runtime_status": "ERROR", "error": exc.message})
                continue
            except Exception as exc:
                reports.append({**described, "result_count": 0, "runtime_status": "ERROR", "error": str(exc)})
                continue
            reports.append({**described, "result_count": len(found), "runtime_status": "OK"})
            drafts.extend(found)
        if selected and all(report.get("runtime_status") == "ERROR" for report in reports):
            detail = "Every selected image provider failed. No candidates were invented. " + " | ".join(
                f"{report.get('name')}: {report.get('error') or report.get('runtime_status')}" for report in reports
            )
            self._write_failure_summary(job_id, request, reports, detail)
            raise ImageResearchError(502, detail)
        records = self._prepare_records(job_id, request, drafts)
        self._save_records(job_id, records, reports, request)

    def _prepare_records(self, job_id: str, request: dict, drafts: list[dict]) -> list[dict]:
        collapsed: dict[str, dict] = {}
        order: list[str] = []
        for draft in drafts:
            canonical = canonical_url(draft.get("original_image_url"))
            if not canonical:
                continue
            current = collapsed.get(canonical)
            if current is None:
                collapsed[canonical] = dict(draft)
                collapsed[canonical]["canonical_image_url"] = canonical
                order.append(canonical)
                continue
            if _pixels(draft) > _pixels(current):
                draft = dict(draft)
                draft["canonical_image_url"] = canonical
                collapsed[canonical] = draft
        records = []
        for canonical in order:
            draft = collapsed[canonical]
            record = self._acquire_one(job_id, request, draft)
            records.append(record)
        assign_duplicates(records)
        self._release_duplicate_files(records)
        for record in records:
            record["eligible"] = 1 if _is_eligible(record) else 0
        return records

    def _acquire_one(self, job_id: str, request: dict, draft: dict) -> dict:
        now = self._now()
        image_id = str(uuid.uuid4())
        rights = draft.get("rights") or {}
        status, data, error = self.downloader.fetch(draft["original_image_url"])
        digest = perceptual = None
        hash_status = "NOT_FETCHED"
        width = _as_int(draft.get("width"))
        height = _as_int(draft.get("height"))
        file_format = draft.get("file_format")
        relative = None
        if status == "OK" and data:
            sniffed = sniff_format(data)
            if sniffed is None:
                status, data, error = "FAILED", None, "Downloaded bytes are not a recognized image"
            else:
                file_format = sniffed
                measured_w, measured_h = image_size(data)
                width = measured_w or width
                height = measured_h or height
                digest = sha256_bytes(data)
                perceptual, hash_status = perceptual_hash(data)
                relative = self._write_image(job_id, image_id, file_format, data)
        page = canonical_url(draft.get("page_url")) if draft.get("page_url") else None
        relevance_score, relevance_status = _relevance(request, draft)
        date_status = _date_status(request, draft.get("publication_date"))
        return {
            "image_id": image_id,
            "job_id": job_id,
            "provider": draft.get("provider") or "unknown",
            "original_image_url": draft["original_image_url"],
            "canonical_image_url": draft["canonical_image_url"],
            "thumbnail_url": draft.get("thumbnail_url"),
            "source_website": draft.get("source_website") or host_of(draft["canonical_image_url"]),
            "page_url": draft.get("page_url"),
            "canonical_page_url": page,
            "creator": draft.get("creator") or "",
            "title": draft.get("title") or "",
            "caption": draft.get("caption") or "",
            "publication_date": draft.get("publication_date"),
            "acquired_at": now,
            "license_name": draft.get("license_name"),
            "license_url": draft.get("license_url"),
            "attribution": draft.get("attribution") or "",
            "commercial_use": rights.get("commercial_use") or "UNKNOWN",
            "modification": rights.get("modification") or "UNKNOWN",
            "rights_status": rights.get("rights_status") or "UNCLEAR",
            "rights_notes": rights.get("rights_notes") or "No rights metadata.",
            "width": width,
            "height": height,
            "file_format": file_format,
            "download_status": status,
            "download_error": error,
            "content_sha256": digest,
            "perceptual_hash": perceptual,
            "perceptual_hash_status": hash_status,
            "similarity_group_id": None,
            "duplicate_role": "UNIQUE",
            "duplicate_reason": None,
            "relevance_score": relevance_score,
            "relevance_status": relevance_status,
            "date_status": date_status,
            "review_status": "PENDING_REVIEW",
            "eligible": 0,
            "stored_relative_path": relative,
            "category": draft.get("category"),
        }

    def _write_failure_summary(self, job_id: str, request: dict, reports: list[dict], detail: str) -> None:
        summary = {
            "required": int(request["minimum_unique"]),
            "discovered": 0,
            "duplicates": 0,
            "broken": 0,
            "rejected_by_relevance": 0,
            "rights_unclear": 0,
            "rights_restricted": 0,
            "eligible": 0,
            "shortfall": int(request["minimum_unique"]),
            "accepted": 0,
            "rejected": 0,
            "pending_review": 0,
            "needs_verification": 0,
            "tally": f"{request['minimum_unique']} required | 0 discovered | 0 eligible",
            "target_met": False,
            "percent_complete": None,
            "provider_reports": reports,
        }
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE image_research_jobs SET summary_json=?, error=?, updated_at=? WHERE job_id=?",
                (json.dumps(summary), detail, self._now(), job_id),
            )

    def _save_records(self, job_id: str, records: list[dict], reports: list[dict], request: dict) -> None:
        with closing(self._connect()) as connection:
            previous = {
                row["canonical_image_url"]: row
                for row in connection.execute(
                    "SELECT * FROM image_research_candidates WHERE job_id=?", (job_id,)
                ).fetchall()
            }
            decisions = {
                (row["content_sha256"] or ""): row["review_status"]
                for row in previous.values()
                if row["review_status"] != "PENDING_REVIEW" and row["content_sha256"]
            }
            connection.execute("BEGIN IMMEDIATE")
            seen = set()
            for record in records:
                prior = previous.get(record["canonical_image_url"])
                if prior is not None:
                    record["image_id"] = prior["image_id"]
                    record["review_status"] = prior["review_status"]
                    if prior["stored_relative_path"] and record["stored_relative_path"] != prior["stored_relative_path"]:
                        self._unlink_relative(record["stored_relative_path"])
                        record["stored_relative_path"] = prior["stored_relative_path"]
                elif record.get("content_sha256") in decisions:
                    record["review_status"] = decisions[record["content_sha256"]]
                if record["review_status"] == "ACCEPTED" and record["rights_status"] != "VERIFIED":
                    record["review_status"] = "NEEDS_VERIFICATION"
                seen.add(record["canonical_image_url"])
                _upsert_candidate(connection, record)
            for url, prior in previous.items():
                if url in seen or prior["review_status"] == "PENDING_REVIEW":
                    if url not in seen and prior["review_status"] == "PENDING_REVIEW":
                        connection.execute("DELETE FROM image_research_candidates WHERE image_id=?", (prior["image_id"],))
                        self._unlink_relative(prior["stored_relative_path"])
            summary = _summarize(connection, job_id, request, reports)
            status = "COMPLETED" if summary["shortfall"] == 0 else "COMPLETED_WITH_SHORTFALL"
            now = self._now()
            connection.execute(
                "UPDATE image_research_jobs SET status=?, error=NULL, summary_json=?, updated_at=? WHERE job_id=?",
                (status, json.dumps(summary), now, job_id),
            )
            connection.execute("COMMIT")

    def _refresh_summary(self, connection, job_id: str) -> None:
        row = connection.execute("SELECT request_json, summary_json FROM image_research_jobs WHERE job_id=?", (job_id,)).fetchone()
        request = json.loads(row["request_json"])
        previous = json.loads(row["summary_json"] or "{}")
        summary = _summarize(connection, job_id, request, previous.get("provider_reports") or [])
        status = "COMPLETED" if summary["shortfall"] == 0 else "COMPLETED_WITH_SHORTFALL"
        connection.execute(
            "UPDATE image_research_jobs SET status=?, summary_json=?, updated_at=? WHERE job_id=?",
            (status, json.dumps(summary), self._now(), job_id),
        )

    def _write_image(self, job_id: str, image_id: str, file_format: str, data: bytes) -> str:
        extension = _EXTENSIONS.get(file_format or "", ".bin")
        relative = f"image-research/{job_id}/{image_id}{extension}"
        path = self._resolve_stored(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(path)
        return relative

    def _release_duplicate_files(self, records: list[dict]) -> None:
        kept: dict[str, str] = {}
        for record in records:
            if record.get("duplicate_role") != "DUPLICATE":
                if record.get("content_sha256") and record.get("stored_relative_path"):
                    kept[record["content_sha256"]] = record["stored_relative_path"]
                continue
        for record in records:
            if record.get("duplicate_role") != "DUPLICATE":
                continue
            path = record.get("stored_relative_path")
            preferred = kept.get(record.get("content_sha256") or "")
            if path and path != preferred:
                self._unlink_relative(path)
            record["stored_relative_path"] = preferred or None
            if record["download_status"] == "OK":
                record["download_status"] = "DUPLICATE_NOT_STORED"

    def _resolve_stored(self, relative: str) -> Path:
        if not relative or "\\" in relative or relative.startswith("/"):
            raise ImageResearchError(400, "Stored image path is not allowed")
        parts = Path(relative).parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ImageResearchError(400, "Stored image path is not allowed")
        root = (self.storage_root / "image-research").resolve()
        path = (self.storage_root / relative).resolve()
        if root != path and root not in path.parents:
            raise ImageResearchError(400, "Stored image path escapes the image-research root")
        return path

    def _unlink_relative(self, relative: str | None) -> None:
        if not relative:
            return
        try:
            path = self._resolve_stored(relative)
        except ImageResearchError:
            return
        path.unlink(missing_ok=True)

    def _set_status(self, job_id: str, status: str, error: str | None) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE image_research_jobs SET status=?, error=?, updated_at=? WHERE job_id=?",
                (status, error, self._now(), job_id),
            )

    def _job_row(self, connection, job_id: str, owner_id: str):
        if not _SEGMENT.fullmatch(job_id or ""):
            raise ImageResearchError(404, "Image research job not found")
        row = connection.execute(
            "SELECT * FROM image_research_jobs WHERE job_id=? AND owner_id=?",
            (job_id, owner_id),
        ).fetchone()
        if row is None:
            raise ImageResearchError(404, "Image research job not found")
        return row

    def _connect(self):
        connection = connect(self.database_file)
        ensure_schema(connection)
        return connection


def _normalize_request(body: dict) -> dict:
    topic = (body.get("topic") or "").strip()
    if len(topic) < 2:
        raise ImageResearchError(422, "A topic is required")
    sources = body.get("sources") or ["wikimedia_commons", "openverse"]
    if not isinstance(sources, list) or not sources:
        raise ImageResearchError(422, "At least one source must be selected")
    unknown = [item for item in sources if item not in _ALLOWED_SOURCES]
    if unknown:
        raise ImageResearchError(422, f"Unknown image source: {', '.join(unknown)}")
    minimum = int(body.get("minimum_unique") or 30)
    if minimum < 1 or minimum > 200:
        raise ImageResearchError(422, "minimum_unique must be between 1 and 200")
    return {
        "topic": topic,
        "keywords": _strings(body.get("keywords"), 20),
        "language": (body.get("language") or "en").strip()[:12] or "en",
        "date_from": _optional_date(body.get("date_from")),
        "date_to": _optional_date(body.get("date_to")),
        "categories": _strings(body.get("categories"), 12),
        "sources": list(dict.fromkeys(sources)),
        "minimum_unique": minimum,
        "research_topic_id": _optional_id(body.get("research_topic_id")),
        "script_id": _optional_id(body.get("script_id")),
        "language_filter_applied": False,
        "categories_used_as_search_terms": bool(body.get("categories")),
    }


def _strings(value, limit: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ImageResearchError(422, "Expected a list of strings")
    cleaned = []
    for item in value[:limit]:
        text = str(item).strip()
        if text:
            cleaned.append(text[:80])
    return cleaned


def _optional_date(value) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise ImageResearchError(422, "Dates must be YYYY-MM-DD")
    return text


def _optional_id(value) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not _SEGMENT.fullmatch(text):
        raise ImageResearchError(422, "Related id contains unsupported characters")
    return text


def _pixels(draft: dict) -> int:
    return _as_int(draft.get("width")) * _as_int(draft.get("height"))


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _tokens(text: str) -> set[str]:
    found = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {token for token in found if token not in _STOP and (len(token) >= 3 or any(ch.isdigit() for ch in token))}


def _relevance(request: dict, draft: dict) -> tuple[float, str]:
    query = _tokens(" ".join([request["topic"], *request["keywords"]]))
    text = _tokens(" ".join([draft.get("title") or "", draft.get("caption") or ""]))
    if not query or not text or not (query & text):
        return 0.0, "IRRELEVANT"
    score = round(len(query & text) / len(query), 4)
    return score, "RELEVANT"


def _date_status(request: dict, publication_date: str | None) -> str:
    if not request.get("date_from") and not request.get("date_to"):
        return "NOT_FILTERED"
    if not publication_date:
        return "UNKNOWN"
    if request.get("date_from") and publication_date < request["date_from"]:
        return "OUT_OF_RANGE"
    if request.get("date_to") and publication_date > request["date_to"]:
        return "OUT_OF_RANGE"
    return "IN_RANGE"


def _is_eligible(record: dict) -> bool:
    return (
        record["download_status"] == "OK"
        and record["duplicate_role"] in {"UNIQUE", "PREFERRED"}
        and record["relevance_status"] == "RELEVANT"
        and record["rights_status"] == "VERIFIED"
        and record["commercial_use"] == "ALLOWED"
        and record["date_status"] != "OUT_OF_RANGE"
    )


def _accept_block(row, action: str) -> str | None:
    if action != "ACCEPT":
        return None
    if row["rights_status"] != "VERIFIED" or row["commercial_use"] != "ALLOWED":
        return "Accept is blocked until rights are verified for reuse. Unclear rights cannot be bulk-cleared or accepted."
    if row["duplicate_role"] == "DUPLICATE":
        return "Accept the preferred original. Duplicate variants are not separate usable images."
    if not row["eligible"]:
        return "This candidate is not eligible (broken, irrelevant, out of date range, or rights not cleared)."
    return None


def _status_for(action: str) -> str:
    return {"ACCEPT": "ACCEPTED", "REJECT": "REJECTED", "NEEDS_VERIFICATION": "NEEDS_VERIFICATION"}[action]


def _upsert_candidate(connection, record: dict) -> None:
    columns = (
        "image_id", "job_id", "provider", "original_image_url", "canonical_image_url", "thumbnail_url",
        "source_website", "page_url", "canonical_page_url", "creator", "title", "caption", "publication_date",
        "acquired_at", "license_name", "license_url", "attribution", "commercial_use", "modification",
        "rights_status", "rights_notes", "width", "height", "file_format", "download_status", "download_error",
        "content_sha256", "perceptual_hash", "perceptual_hash_status", "similarity_group_id", "duplicate_role",
        "duplicate_reason", "relevance_score", "relevance_status", "date_status", "review_status", "eligible",
        "stored_relative_path", "category",
    )
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column not in {"image_id", "job_id"})
    connection.execute(
        f"""INSERT INTO image_research_candidates ({', '.join(columns)}) VALUES ({placeholders})
            ON CONFLICT(image_id) DO UPDATE SET {updates}""",
        tuple(record[column] for column in columns),
    )


def _summarize(connection, job_id: str, request: dict, reports: list[dict]) -> dict:
    rows = connection.execute("SELECT * FROM image_research_candidates WHERE job_id=?", (job_id,)).fetchall()
    buckets = {key: 0 for key in (
        "discovered", "duplicates", "broken", "rejected_by_relevance", "date_excluded",
        "rights_unclear", "rights_restricted", "eligible",
    )}
    reviews = {key: 0 for key in _REVIEW_STATUSES}
    for row in rows:
        buckets["discovered"] += 1
        reviews[row["review_status"]] = reviews.get(row["review_status"], 0) + 1
        if row["duplicate_role"] == "DUPLICATE":
            buckets["duplicates"] += 1
        elif row["download_status"] not in {"OK"}:
            buckets["broken"] += 1
        elif row["relevance_status"] != "RELEVANT":
            buckets["rejected_by_relevance"] += 1
        elif row["date_status"] == "OUT_OF_RANGE":
            buckets["date_excluded"] += 1
        elif row["rights_status"] == "UNCLEAR":
            buckets["rights_unclear"] += 1
        elif row["rights_status"] == "RESTRICTED":
            buckets["rights_restricted"] += 1
        elif row["eligible"]:
            buckets["eligible"] += 1
    required = int(request["minimum_unique"])
    shortfall = max(0, required - buckets["eligible"])
    tally = (
        f"{required} required | {buckets['discovered']} discovered | {buckets['duplicates']} duplicates | "
        f"{buckets['rejected_by_relevance']} rejected by relevance | {buckets['rights_unclear']} rights unclear | "
        f"{buckets['rights_restricted']} rights restricted | {buckets['broken']} broken | {buckets['eligible']} eligible"
    )
    return {
        **buckets,
        "required": required,
        "shortfall": shortfall,
        "accepted": reviews.get("ACCEPTED", 0),
        "rejected": reviews.get("REJECTED", 0),
        "pending_review": reviews.get("PENDING_REVIEW", 0),
        "needs_verification": reviews.get("NEEDS_VERIFICATION", 0),
        "tally": tally,
        "target_met": shortfall == 0,
        "percent_complete": None,
        "provider_reports": reports,
        "language_filter_applied": False,
        "crop_detection": "NOT_IMPLEMENTED",
        "date_filter_note": "Undated images stay visible and are not treated as inside the requested range.",
    }


def _job_summary(row) -> dict:
    summary = json.loads(row["summary_json"] or "null")
    return {
        "job_id": row["job_id"],
        "topic": row["topic"],
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "summary": summary,
    }


def _job_detail(row) -> dict:
    detail = _job_summary(row)
    detail.update({
        "request": json.loads(row["request_json"]),
        "research_topic_id": row["research_topic_id"],
        "script_id": row["script_id"],
        "stages": _stages(row["status"]),
    })
    return detail


def _stages(status: str) -> list[dict]:
    names = ("search", "acquire", "deduplicate", "rights")
    if status == "QUEUED":
        return [{"name": name, "status": "NOT_STARTED"} for name in names]
    if status == "RUNNING":
        return [{"name": name, "status": "RUNNING" if name == "search" else "NOT_STARTED"} for name in names]
    if status == "FAILED":
        return [{"name": name, "status": "FAILED" if name == "search" else "NOT_STARTED"} for name in names]
    return [{"name": name, "status": "COMPLETED"} for name in names]


def _candidate(row) -> dict:
    return {key: row[key] for key in row.keys()}
