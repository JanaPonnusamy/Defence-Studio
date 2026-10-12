"""Asset tests (spec §33): import, validation, metadata, checksum, rights state."""
from __future__ import annotations

import hashlib
import json

import pytest

from fixtures.synthetic import (
    bitflipped_png, fake_png_header_garbage, mp4_bytes, pdf_bytes, png_bytes, truncated_png,
)
from tests.conftest import import_png


def _post(client, project, content: bytes, filename="x.png", **fields):
    data = {"project_id": project, "asset_type": "SOURCE_PHOTO", "source_type": "EXTERNAL_SOURCE",
            "rights_status": "LICENSED", "licence": "L", **fields}
    return client.post("/assets/import", data=data, files={"file": (filename, content, "application/octet-stream")})


def test_import_png_preserves_metadata_and_checksum(client, project, test_root):
    content = png_bytes(800, 450, 11)
    r = client.post("/assets/import", data={
        "project_id": project, "asset_type": "SOURCE_PHOTO", "source_type": "EXTERNAL_SOURCE",
        "rights_status": "LICENSED", "licence": "Agency licence #42", "title": "Frigate at sea",
        "attribution": "Photo: Example Agency", "creator": "J. Doe", "source_url": "https://example.org/photo/1",
        "extra_metadata": json.dumps({"tags": ["Frigate", "navy"], "focus_point": {"x": 0.4, "y": 0.5}}),
    }, files={"file": ("frigate.png", content, "image/png")})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    assert (a["width"], a["height"], a["mime_type"]) == (800, 450, "image/png")
    assert a["licence"] == "Agency licence #42" and a["attribution"] == "Photo: Example Agency"
    assert a["creator"] == "J. Doe" and a["source_url"] == "https://example.org/photo/1"
    assert a["editorial_category"] == "FACTUAL_EVIDENCE"
    assert a["extra_metadata"]["tags"] == ["frigate", "navy"]
    assert a["deduplicated"] is False
    # stored inside module storage, relative path, provenance sidecar written
    assert not a["local_path"].startswith("/") and ".." not in a["local_path"]
    assert (test_root / a["local_path"]).is_file()
    side = json.loads((test_root / "source_metadata" / f"{a['asset_id']}.json").read_text())
    assert side["rights_status"] == "LICENSED" and side["checksum_sha256"] == a["checksum_sha256"]
    # file endpoint serves the exact bytes
    f = client.get(a["file_url"])
    assert f.status_code == 200 and f.content == content


def test_jpeg_import_and_checksum_dedup(client, project):
    content = png_bytes(320, 200, 5, fmt="JPEG")
    r1 = _post(client, project, content, "a.jpg")
    r2 = _post(client, project, content, "renamed.jpg", title="other title")
    assert r1.status_code == 201 and r1.json()["mime_type"] == "image/jpeg"
    assert r2.status_code == 200 and r2.json()["deduplicated"] is True
    assert r1.json()["asset_id"] == r2.json()["asset_id"]


@pytest.mark.parametrize("content,label", [
    (truncated_png(), "truncated"),
    (bitflipped_png(), "bit-flipped"),
    (fake_png_header_garbage(), "png-header-garbage"),
    (b"just some text, not an image", "text"),
    (b"MZ\x90\x00" + b"\x00" * 200, "exe-like"),
])
def test_corrupt_or_wrong_files_rejected(client, project, content, label):
    r = _post(client, project, content, f"{label}.png")
    assert r.status_code == 422, (label, r.text)
    assert r.json()["error_code"] == "ASSET_INVALID"


def test_empty_file_and_missing_file_rejected(client, project):
    assert _post(client, project, b"").json()["error_code"] in ("ASSET_INVALID", "VALIDATION_ERROR")
    r = client.post("/assets/import", data={"project_id": project, "asset_type": "SOURCE_PHOTO"})
    assert r.status_code == 422 and r.json()["error_code"] == "ASSET_INVALID"


def test_dimension_limits(client, project):
    r = _post(client, project, png_bytes(8, 8, 1))
    assert r.status_code == 422 and "dimensions" in r.json()["error_message"]


def test_size_limit(client, project, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
    r = _post(client, project, png_bytes(640, 360, 9))
    assert r.status_code == 413 and r.json()["error_code"] == "PAYLOAD_TOO_LARGE"


def test_path_traversal_filename_is_neutralised(client, project, test_root):
    r = _post(client, project, png_bytes(100, 100, 77), filename="..\\..\\..\\evil/../../x.png")
    assert r.status_code == 201
    p = (test_root / r.json()["local_path"]).resolve()
    assert test_root.resolve() in p.parents
    assert ".." not in r.json()["local_path"]


def test_stored_path_resolution_refuses_escape():
    from app.errors import VideoModuleError
    from app.storage import resolve_stored_path

    for bad in ("../outside.png", "..\\x", "C:/Windows/win.ini", "/etc/passwd"):
        with pytest.raises(VideoModuleError) as e:
            resolve_stored_path(bad)
        assert e.value.code == "PATH_TRAVERSAL_REJECTED"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x.org/a",
                                 "https://user:pass@example.org/a", "not a url", "https://"])
def test_url_validation(client, project, url):
    r = _post(client, project, png_bytes(100, 100, hash(url) % 1000), source_url=url)
    assert r.status_code == 422 and r.json()["error_code"] == "INVALID_URL"


def test_unknown_rights_never_labelled_open(client, project):
    r = import_png(client, project, seed=21, rights_status="UNKNOWN", licence=None, source_type="UNKNOWN")
    assert r.status_code == 201
    a = r.json()
    assert a["rights_status"] == "UNKNOWN"
    assert "open" not in a["rights_label"].lower().replace("never treat as open source", "")
    assert "not cleared" in a["rights_label"]
    assert a["editorial_category"] == "UNKNOWN"


@pytest.mark.parametrize("fields,code", [
    ({"source_type": "UNKNOWN", "rights_status": "OPEN", "licence": "CC BY 4.0"}, "RIGHTS_UNKNOWN"),
    ({"source_type": "EXTERNAL_SOURCE", "rights_status": "OPEN", "licence": ""}, "RIGHTS_UNKNOWN"),
    ({"source_type": "EXTERNAL_SOURCE", "rights_status": "UNKNOWN", "licence": "open source"}, "RIGHTS_UNKNOWN"),
    ({"source_type": "EXTERNAL_SOURCE", "rights_status": "UNKNOWN", "licence": "Creative Commons BY"}, "RIGHTS_UNKNOWN"),
    ({"source_type": "EXTERNAL_SOURCE", "rights_status": "BANANA"}, "VALIDATION_ERROR"),
])
def test_rights_rules(client, project, fields, code):
    data = {"project_id": project, "asset_type": "SOURCE_PHOTO", "title": "t", **fields}
    r = client.post("/assets/import", data=data, files={"file": ("a.png", png_bytes(100, 100, 300 + len(str(fields))), "image/png")})
    assert r.status_code == 422, r.text
    assert r.json()["error_code"] == code


def test_ai_imagery_cannot_be_labelled_evidence(client, project):
    r = import_png(client, project, seed=31, asset_type="AI_IMAGE", source_type="EXTERNAL_SOURCE")
    assert r.status_code == 422 and r.json()["error_code"] == "EDITORIAL_POLICY"
    r = import_png(client, project, seed=32, asset_type="SOURCE_PHOTO", source_type="AI_GENERATED")
    assert r.status_code == 422 and r.json()["error_code"] == "EDITORIAL_POLICY"
    r = import_png(client, project, seed=33, asset_type="AI_IMAGE", source_type="AI_GENERATED", rights_status="PERMITTED")
    assert r.status_code == 201 and r.json()["editorial_category"] == "AI_GENERATED"


def test_metadata_is_sanitised_and_inert(client, project):
    evil = {"tags": ["ok"], "cmd": "$(rm -rf /); `shutdown -s`", "nested": {"a": "\x00\x07bell"}}
    r = client.post("/assets/import", data={
        "project_id": project, "asset_type": "SOURCE_PHOTO", "source_type": "USER_PROVIDED", "rights_status": "PERMITTED",
        "title": "  title\x00with\x1bcontrol  ", "extra_metadata": json.dumps(evil),
    }, files={"file": ("a.png", png_bytes(120, 120, 41), "image/png")})
    assert r.status_code == 201
    a = r.json()
    assert a["title"] == "titlewithcontrol"
    assert a["extra_metadata"]["cmd"] == "$(rm -rf /); `shutdown -s`"  # stored as plain data, never executed
    assert a["extra_metadata"]["nested"]["a"] == "bell"


def test_bad_extra_metadata_rejected(client, project):
    for bad in ("not json", "[1,2]", json.dumps({"protected_regions": [{"x": 0.9, "y": 0, "w": 0.5, "h": 0.5}]})):
        r = client.post("/assets/import", data={"project_id": project, "asset_type": "SOURCE_PHOTO", "extra_metadata": bad},
                        files={"file": ("a.png", png_bytes(100, 100, 51), "image/png")})
        assert r.status_code == 422, bad


def test_pdf_document_import_and_truncated_pdf(client, project):
    r = client.post("/assets/import", data={"project_id": project, "asset_type": "DOCUMENT", "source_type": "EXTERNAL_SOURCE",
                                            "rights_status": "PERMITTED", "title": "Official release"},
                    files={"file": ("release.pdf", pdf_bytes(3), "application/pdf")})
    assert r.status_code == 201, r.text
    assert r.json()["mime_type"] == "application/pdf" and r.json()["extra_metadata"]["pages"] == 3
    r = client.post("/assets/import", data={"project_id": project, "asset_type": "DOCUMENT"},
                    files={"file": ("bad.pdf", pdf_bytes(2)[:60], "application/pdf")})
    assert r.status_code == 422 and r.json()["error_code"] == "ASSET_INVALID"


def test_video_clip_requires_duration_and_valid_points(client, project):
    base = {"project_id": project, "asset_type": "VIDEO_CLIP", "source_type": "EXTERNAL_SOURCE", "rights_status": "LICENSED", "licence": "L"}
    r = client.post("/assets/import", data=base, files={"file": ("c.mp4", mp4_bytes(), "video/mp4")})
    assert r.status_code == 422 and "duration" in r.json()["error_message"]
    r = client.post("/assets/import", data={**base, "duration": "20", "extra_metadata": json.dumps({"clip": {"in_point": 15, "out_point": 25}})},
                    files={"file": ("c.mp4", mp4_bytes(), "video/mp4")})
    assert r.status_code == 422
    r = client.post("/assets/import", data={**base, "duration": "20", "extra_metadata": json.dumps({"clip": {"in_point": 2, "out_point": 9}})},
                    files={"file": ("c.mp4", mp4_bytes(), "video/mp4")})
    assert r.status_code == 201 and r.json()["extra_metadata"]["clip"]["duration"] == 7
    r = client.post("/assets/import", data={**base, "duration": "5"}, files={"file": ("c.mp4", png_bytes(50, 50, 1), "video/mp4")})
    assert r.status_code == 422


def test_unknown_project_and_list_filters(client, project):
    r = _post(client, "00000000-0000-0000-0000-000000000000", png_bytes(100, 100, 61))
    assert r.status_code == 404
    import_png(client, project, seed=62, rights_status="PERMITTED", licence=None)
    lst = client.get("/assets", params={"project_id": project, "rights_status": "PERMITTED"}).json()
    assert lst and all(a["rights_status"] == "PERMITTED" for a in lst)
    assert client.get("/assets", params={"asset_type": "NOPE"}).status_code == 422


def test_variant_creates_real_cropped_file_and_respects_protected_regions(client, project, test_root):
    meta = {"protected_regions": [{"x": 0.05, "y": 0.05, "w": 0.2, "h": 0.2, "label": "hull number"}]}
    a = import_png(client, project, seed=71, w=1000, h=500, meta=meta).json()
    bad = client.post(f"/assets/{a['asset_id']}/variants", json={"crop": {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}})
    assert bad.status_code == 422 and bad.json()["error_code"] == "EDITORIAL_POLICY"
    v = client.post(f"/assets/{a['asset_id']}/variants", json={"crop": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.6}})
    assert v.status_code == 201, v.text
    v = v.json()
    assert (v["width"], v["height"]) == (500, 300)
    assert v["extra_metadata"]["variant_of"] == a["asset_id"]
    assert v["rights_status"] == a["rights_status"] and v["editorial_category"] == a["editorial_category"]
    assert v["checksum_sha256"] != a["checksum_sha256"] and (test_root / v["local_path"]).is_file()
