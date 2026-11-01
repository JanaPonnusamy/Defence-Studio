"""Image Research: acquisition, dedup, rights, review persistence, API restart."""
import io
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, access_token
from defence_studio.api.app import create_app
from defence_studio.image_research.dedup import assign_duplicates, canonical_url
from defence_studio.image_research.providers import ProviderError, SocialMediaSource, parse_openverse, parse_wikimedia
from defence_studio.image_research.rights import classify_rights
from defence_studio.image_research.service import ImageResearchError, ImageResearchService


def _png(width, height, color, level=9):
    raw = bytearray()
    for _y in range(height):
        raw.append(0)
        for _x in range(width):
            raw.extend(color)
    compressed = zlib.compress(bytes(raw), level)

    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


RED = _png(16, 16, (180, 20, 20), 9)
RED_RECOMPRESSED = _png(16, 16, (180, 20, 20), 1)
BLUE = _png(16, 16, (20, 20, 180), 9)
GREEN = _png(16, 16, (20, 160, 40), 9)


class MapDownloader:
    def __init__(self, mapping, broken=()):
        self.mapping = mapping
        self.broken = set(broken)

    def fetch(self, url):
        if url in self.broken:
            return "FAILED", None, "HTTP 404"
        data = self.mapping.get(url)
        if data is None:
            return "FAILED", None, "fixture has no bytes"
        return "OK", data, None


class FixtureProvider:
    name = "wikimedia_commons"

    def __init__(self, items, error=None):
        self.items = items
        self.error = error

    def describe(self):
        return {"name": self.name, "label": "Fixture", "status": "AVAILABLE", "detail": "test fixture"}

    def search(self, query, limit):
        if self.error:
            raise ProviderError("provider_error", self.error)
        return list(self.items)


def _draft(url, title, license_name="CC BY 4.0", license_url="https://creativecommons.org/licenses/by/4.0/",
           page=None, **extra):
    rights = classify_rights(license_name=license_name, license_url=license_url)
    item = {
        "provider": "wikimedia_commons",
        "original_image_url": url,
        "thumbnail_url": url,
        "page_url": page or url + "/page",
        "source_website": "commons.wikimedia.org",
        "creator": "Example Photographer",
        "title": title,
        "caption": title,
        "publication_date": "2024-01-02",
        "license_name": license_name,
        "license_url": license_url,
        "attribution": "Example Photographer",
        "rights": rights,
        "width": 16,
        "height": 16,
        "file_format": "png",
        "category": None,
    }
    item.update(extra)
    return item


def _service(tmp_path, items, mapping, sources=("wikimedia_commons",), **kwargs):
    service = ImageResearchService(
        tmp_path / "img.sqlite3",
        tmp_path / "storage",
        providers=[FixtureProvider(items), SocialMediaSource()],
        downloader=MapDownloader(mapping),
        **kwargs,
    )
    job = service.create_job("user-1", "idem-1", {"topic": "Rafale fighter", "minimum_unique": 30, "sources": list(sources)})
    return service, service.run(job["job_id"], "user-1")


def test_rights_are_not_verified_without_a_license_url():
    unclear = classify_rights(license_name="CC BY 4.0", license_url="")
    restricted = classify_rights(license_name="CC BY-NC 4.0", license_url="https://creativecommons.org/licenses/by-nc/4.0/")
    assert unclear["rights_status"] == "UNCLEAR"
    assert restricted["rights_status"] == "RESTRICTED"


def test_shortfall_dedup_and_rights_are_counted_honestly(tmp_path):
    items = [
        _draft("https://example.com/rafale.png", "Rafale fighter"),
        _draft("https://example.com/rafale.png?utm_source=news", "Rafale fighter copy"),
        _draft("https://cdn.example/rafale-recompressed.png", "Rafale fighter recompressed"),
        _draft("https://example.com/blue.png", "Rafale fighter blue airframe"),
        _draft("https://example.com/portrait.png", "Rafale fighter unmarked", license_name="", license_url=""),
        _draft("https://example.com/broken.png", "Rafale fighter broken link"),
        _draft("https://example.com/other-page-a.png", "Rafale fighter left", page="https://example.com/gallery"),
        _draft("https://example.com/other-page-b.png", "Rafale fighter right", page="https://example.com/gallery"),
    ]
    mapping = {
        "https://example.com/rafale.png": RED,
        "https://cdn.example/rafale-recompressed.png": RED_RECOMPRESSED,
        "https://example.com/blue.png": BLUE,
        "https://example.com/portrait.png": GREEN,
        "https://example.com/other-page-a.png": RED,
        "https://example.com/other-page-b.png": BLUE,
    }
    service, job = _service(tmp_path, items, mapping)
    summary = job["summary"]
    assert summary["percent_complete"] is None
    assert summary["target_met"] is False
    assert summary["shortfall"] == 30 - summary["eligible"]
    assert summary["eligible"] < 30
    assert "30 required" in summary["tally"]
    assert summary["duplicates"] >= 1
    assert summary["rights_unclear"] >= 1
    assert summary["broken"] >= 1
    candidates = service.candidates(job["job_id"], "user-1")["candidates"]
    groups = service.duplicates(job["job_id"], "user-1")["groups"]
    assert groups
    assert all(group["preferred"]["duplicate_role"] == "PREFERRED" for group in groups)
    same_page = [item for item in candidates if item["canonical_page_url"] and item["canonical_page_url"].endswith("/gallery")]
    assert len(same_page) == 2
    assert {item["similarity_group_id"] for item in same_page} != {same_page[0]["similarity_group_id"]} or same_page[0]["content_sha256"] == same_page[1]["content_sha256"]
    # Different photographs on one page stay in different groups.
    assert same_page[0]["similarity_group_id"] != same_page[1]["similarity_group_id"]


def test_reject_survives_rerun_and_unclear_rights_cannot_be_accepted(tmp_path):
    items = [
        _draft("https://example.com/rafale.png", "Rafale fighter"),
        _draft("https://example.com/unknown.png", "Rafale fighter unknown", license_name="", license_url=""),
    ]
    mapping = {"https://example.com/rafale.png": RED, "https://example.com/unknown.png": BLUE}
    service, job = _service(tmp_path, items, mapping)
    candidates = service.candidates(job["job_id"], "user-1")["candidates"]
    clear = next(item for item in candidates if item["rights_status"] == "VERIFIED" and item["eligible"])
    unclear = next(item for item in candidates if item["rights_status"] == "UNCLEAR")
    with pytest.raises(ImageResearchError, match="rights"):
        service.review(unclear["image_id"], "user-1", "ACCEPT", "")
    rejected = service.review(clear["image_id"], "user-1", "REJECT", "wrong airframe")
    assert rejected["review_status"] == "REJECTED"
    failed = ImageResearchService(
        service.database_file, service.storage_root,
        providers=[FixtureProvider([], error="down")],
        downloader=service.downloader,
    )
    failed._set_status(job["job_id"], "FAILED", "down")
    # A failed job retries against the same fixture set and must not clear the rejection.
    retry = ImageResearchService(
        service.database_file, service.storage_root,
        providers=[FixtureProvider(items), SocialMediaSource()],
        downloader=MapDownloader(mapping),
    )
    rerun = retry.run(job["job_id"], "user-1")
    again = retry.candidates(rerun["job_id"], "user-1")["candidates"]
    kept = next(item for item in again if item["canonical_image_url"] == clear["canonical_image_url"])
    assert kept["review_status"] == "REJECTED"
    assert kept["image_id"] == clear["image_id"]
    bulk = retry.bulk_review(job["job_id"], "user-1", "ACCEPT", [unclear["image_id"]], "")
    assert bulk["applied"] == []
    assert bulk["skipped"][0]["image_id"] == unclear["image_id"]
    assert retry.candidates(job["job_id"], "user-1")["candidates"]
    still = next(item for item in retry.candidates(job["job_id"], "user-1")["candidates"] if item["image_id"] == kept["image_id"])
    assert still["review_status"] == "REJECTED"


def test_duplicate_bytes_are_not_stored_twice(tmp_path):
    items = [
        _draft("https://example.com/rafale.png", "Rafale fighter"),
        _draft("https://cdn.example/rafale-recompressed.png", "Rafale fighter recompressed"),
    ]
    service, job = _service(tmp_path, items, {
        "https://example.com/rafale.png": RED,
        "https://cdn.example/rafale-recompressed.png": RED_RECOMPRESSED,
    })
    root = tmp_path / "storage" / "image-research" / job["job_id"]
    files = [path for path in root.iterdir() if path.is_file()]
    assert len(files) == 1
    assert job["summary"]["eligible"] == 1
    assert job["summary"]["duplicates"] == 1


def test_disk_guard_and_total_provider_failure_do_not_invent_results(tmp_path):
    service = ImageResearchService(
        tmp_path / "img.sqlite3", tmp_path / "storage",
        providers=[FixtureProvider([], error="commons down"), FixtureProvider([])],
        downloader=MapDownloader({}),
        disk_free=lambda: 0,
    )
    # Second fixture is also named wikimedia; replace with an openverse-named provider.
    other = FixtureProvider([], error="openverse down")
    other.name = "openverse"
    service.providers = [FixtureProvider([], error="commons down"), other]
    job = service.create_job("user-1", "disk", {"topic": "Rafale", "sources": ["wikimedia_commons"]})
    with pytest.raises(ImageResearchError, match="INSUFFICIENT_DISK"):
        service.run(job["job_id"], "user-1")
    stored = service.get_job(job["job_id"], "user-1")
    assert stored["status"] == "FAILED"
    assert service.candidates(job["job_id"], "user-1")["candidates"] == []

    service._disk_free = lambda: 10**12
    service._set_status(job["job_id"], "FAILED", "retry")
    with pytest.raises(ImageResearchError, match="No candidates were invented"):
        service.run(job["job_id"], "user-1")


def test_path_traversal_is_rejected(tmp_path):
    service = ImageResearchService(tmp_path / "img.sqlite3", tmp_path / "storage", providers=[], downloader=MapDownloader({}))
    with pytest.raises(ImageResearchError):
        service._resolve_stored("image-research/../secret.png")
    with pytest.raises(ImageResearchError):
        service.preview_path("../secret", "user-1")


def test_tracking_parameters_do_not_create_a_second_image():
    assert canonical_url("https://Example.com/a.png?utm_source=x&id=1") == canonical_url("https://example.com/a.png?id=1")


def test_provider_parsers_do_not_label_missing_licenses_verified():
    wiki = parse_wikimedia({"query": {"pages": {"1": {
        "title": "File:Rafale.jpg",
        "imageinfo": [{"url": "https://upload.wikimedia.org/rafale.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Rafale.jpg",
                       "width": 10, "height": 10, "mime": "image/jpeg",
                       "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}, "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"}}}],
    }}}})
    assert wiki[0]["rights"]["rights_status"] == "VERIFIED"
    missing = parse_openverse({"results": [{"url": "https://example.com/a.jpg", "title": "Rafale", "license": "by"}]})
    assert missing[0]["rights"]["rights_status"] == "UNCLEAR"


def test_api_persists_across_a_new_app_instance(tmp_path, monkeypatch):
    items = [_draft("https://example.com/rafale.png", "Rafale fighter")]
    mapping = {"https://example.com/rafale.png": RED}

    def wrapped(self, database_file, storage_root, **kwargs):
        kwargs.setdefault("providers", [FixtureProvider(items), SocialMediaSource()])
        kwargs.setdefault("downloader", MapDownloader(mapping))
        ImageResearchService.__init__.__wrapped__(self, database_file, storage_root, **kwargs) if False else None

    original = ImageResearchService.__init__

    def init(self, database_file, storage_root, **kwargs):
        kwargs.setdefault("providers", [FixtureProvider(items), SocialMediaSource()])
        kwargs.setdefault("downloader", MapDownloader(mapping))
        original(self, database_file, storage_root, **kwargs)

    monkeypatch.setattr(ImageResearchService, "__init__", init)
    database = tmp_path / "api.sqlite3"
    storage = tmp_path / "storage"
    app = create_app(database, SECRET, storage_root_path=storage)
    headers = {"Authorization": f"Bearer {access_token('user-1')}", "Idempotency-Key": "job-1"}
    with TestClient(app) as client:
        created = client.post("/api/image-research/jobs", headers=headers, json={"topic": "Rafale fighter", "minimum_unique": 30})
        assert created.status_code == 201, created.text
        job_id = created.json()["job_id"]
        ran = client.post(f"/api/image-research/jobs/{job_id}/run", headers=headers)
        assert ran.status_code == 200, ran.text
        assert ran.json()["summary"]["eligible"] == 1
        assert ran.json()["summary"]["shortfall"] == 29
        providers = client.get("/api/image-research/providers", headers=headers)
        social = next(item for item in providers.json()["providers"] if item["name"] == "social_media")
        assert social["status"] == "UNSUPPORTED"
        image_id = client.get(f"/api/image-research/jobs/{job_id}/candidates", headers=headers).json()["candidates"][0]["image_id"]
        preview = client.get(f"/api/image-research/candidates/{image_id}/preview", headers=headers)
        assert preview.status_code == 200
        assert preview.content.startswith(b"\x89PNG")
        denied = client.post(
            f"/api/image-research/candidates/{image_id}/review",
            headers={**headers, "Idempotency-Key": "review-1"},
            json={"action": "ACCEPT", "reason": "usable"},
        )
        assert denied.status_code == 200
        assert denied.json()["review_status"] == "ACCEPTED"
    restarted = create_app(database, SECRET, storage_root_path=storage)
    with TestClient(restarted) as client:
        again = client.get(f"/api/image-research/jobs/{job_id}", headers=headers)
        assert again.status_code == 200
        assert again.json()["summary"]["accepted"] == 1
        exported = client.get(f"/api/image-research/jobs/{job_id}/export", headers=headers)
        assert exported.json()["accepted_count"] == 1


def test_assign_duplicates_explains_hash_match():
    records = [
        {"image_id": "a", "canonical_image_url": "https://a.example/1.png", "content_sha256": "abc", "perceptual_hash": None,
         "source_website": "a.example", "width": 10, "height": 10, "download_status": "OK"},
        {"image_id": "b", "canonical_image_url": "https://b.example/1.png", "content_sha256": "abc", "perceptual_hash": None,
         "source_website": "b.example", "width": 20, "height": 20, "download_status": "OK"},
    ]
    assign_duplicates(records)
    roles = {record["image_id"]: record["duplicate_role"] for record in records}
    assert roles == {"a": "DUPLICATE", "b": "PREFERRED"}
    assert "different source websites" in next(record["duplicate_reason"] for record in records if record["image_id"] == "a")
