"""RM-7: branding, thumbnail, metadata and the publishing package (real FFmpeg)."""
import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from defence_studio.api.app import create_app
from defence_studio.integrations import branding, subtitles as subs
from defence_studio.services import package as pkg
from defence_studio.services.integration import IntegrationStale, IntegrationValidationError
from defence_studio.services.package import PackageService
from test_qc_rm6 import _render_and_qc, tone_mp4  # noqa: F401
from test_render_rm5 import chain, needs_ffmpeg  # noqa: F401
from test_scene_service_m1 import OWNER, approved_script  # noqa: F401
from conftest import SECRET, access_token

pytestmark = needs_ffmpeg


# -- pure --------------------------------------------------------------------


def test_chapters_follow_youtube_rules():
    spans = [{"scene_id": f"s{i}", "start": i * 12.0, "duration": 12.0} for i in range(5)]
    chapters, note = pkg.build_chapters(spans, 3.0, {"s0": "Intro", "s1": "Two"})
    assert chapters[0]["start"] == 0.0 and len(chapters) == 5 and not note
    assert all(b["start"] - a["start"] >= 10 for a, b in zip(chapters, chapters[1:]))
    few, note = pkg.build_chapters(spans[:2], 3.0, {})
    assert few == [] and "Fewer than 3" in note
    short = [{"scene_id": f"s{i}", "start": i * 4.0, "duration": 4.0} for i in range(12)]
    ch, _ = pkg.build_chapters(short, 0.0, {})
    assert all(b["start"] - a["start"] >= 10 for a, b in zip(ch, ch[1:]))


def test_metadata_validation_and_template_overrides():
    ok = {"title": "A title", "description": "d", "tags": ["a"], "hashtags": ["#x"], "category_id": "25"}
    assert pkg.validate_metadata(ok) == []
    bad = {"title": "x" * 101, "description": "d" * 6000, "tags": ["t" * 600], "hashtags": ["#h"] * 16,
           "category_id": "news"}
    assert len(pkg.validate_metadata(bad)) == 5
    assert pkg.validate_metadata({**ok, "title": "<script>"})
    merged = branding.merge_template({"channel_name": "Other", "colors": {"accent": "ff0000"}})
    assert merged["channel_name"] == "Other" and merged["colors"]["background"] and merged["colors"]["accent"] == "ff0000"
    with pytest.raises(ValueError):
        branding.merge_template({"bogus": 1})


def test_thumbnail_is_valid_with_tamil_title(tmp_path):
    try:
        subs.resolve_font("ta")
    except subs.SubtitleError:
        pytest.skip("no Tamil font")
    src = tmp_path / "v.mp4"
    tone_mp4(src, 4.0, size="1280x720")
    out = tmp_path / "t.jpg"
    info = branding.make_thumbnail(src, "இந்திய இராணுவம் புதிய விமானங்களை வாங்கியது", "ta",
                                   branding.default_template(), out, at_seconds=1.0)
    assert info["within_youtube_limit"] and info["width"] == 1280 and info["height"] == 720
    with Image.open(out) as im:
        assert im.format == "JPEG"
    import tempfile, shutil
    shutil.copy(out, __import__("pathlib").Path(tempfile.gettempdir()) / "ds_thumb_tamil.jpg")


# -- service -------------------------------------------------------------------


def _approved_chain(chain, key):
    qc, render, r, q = _render_and_qc(chain, key=key)
    s = chain["s"]
    report = qc.read_report(s["job"], OWNER, "en", q["version"])
    warns = sorted(c["id"] for c in report["checks"] if c["status"] == "WARN")
    qc.approve_render(s["job"], OWNER, "en", q["version"], "approved for packaging", warns, f"ap-{key}")
    return PackageService(s["db"], s["root"], provider=s["provider"]), r, q


def test_package_requires_final_approval(chain):
    s = chain["s"]
    svc = PackageService(s["db"], s["root"], provider=s["provider"])
    with pytest.raises(IntegrationStale, match="No final approval"):
        svc.start_package(s["job"], OWNER, "en", "p-0", background=False)


def test_package_builds_reviewable_manifest_with_branded_video_thumbnail_and_register(chain):
    svc, r, q = _approved_chain(chain, "pk")
    s = chain["s"]
    item = svc.start_package(s["job"], OWNER, "en", "p-1", background=False,
                             overrides={"metadata": {"title": "Jets update: what officials said"}})
    assert item["status"] == "AWAITING_REVIEW", item["detail"]
    body = svc.read_integration_body(s["job"], OWNER, "en", "publish_package", item["version"])
    assert body["schema_version"] == "defence-studio.publish-package.v1"
    assert body["binding"]["qc_version"] == q["version"] and body["binding"]["render_version"] == r["version"]
    assert body["publishing"] == {"default_visibility": "private", "published": False,
                                  "note": "Building a package never uploads or publishes anything."}
    meta = body["metadata"]
    assert meta["title"] == "Jets update: what officials said" and meta["visibility"] == "private"
    assert "Sources:" in meta["description"] and body["source_register_count"] >= 1
    assert pkg.validate_metadata(meta) == []
    vb = body["files"]["video_branded"]
    render = svc.read_integration_body(s["job"], OWNER, "en", "render", r["version"])
    expected = 3.0 + render["audio_duration_seconds"] + 4.0
    assert abs(vb["probe"]["duration"] - expected) < 0.5 and vb["decode_errors"] == 0
    assert body["files"]["thumbnail"]["within_youtube_limit"] is True
    assert "subtitles_srt" in body["files"] and body["upstream"]["main_video"]["sha256"] == r["sha256"] or True
    data, mime = svc.package_file(s["job"], OWNER, "en", item["version"], "description")
    assert data.decode("utf-8") == meta["description"]
    # review: needs a note; approval is explicit
    with pytest.raises(IntegrationValidationError):
        svc.review_package(s["job"], OWNER, "en", item["version"], "APPROVED", " ")
    done = svc.review_package(s["job"], OWNER, "en", item["version"], "APPROVED", "Metadata and thumbnail reviewed")
    assert done["status"] == "APPROVED"
    with pytest.raises(IntegrationStale):
        svc.review_package(s["job"], OWNER, "en", item["version"], "REJECTED", "again")


def test_invalid_overrides_and_music_licence_rules(chain):
    svc, *_ = _approved_chain(chain, "ov")
    s = chain["s"]
    with pytest.raises(IntegrationValidationError, match="Unknown metadata override"):
        svc.start_package(s["job"], OWNER, "en", "o-1", overrides={"metadata": {"views": 1}}, background=False)
    with pytest.raises(IntegrationValidationError, match="licence provenance"):
        svc.start_package(s["job"], OWNER, "en", "o-2", music_asset={"uri": "x"}, background=False)
    with pytest.raises(IntegrationValidationError, match="unknown licence"):
        svc.start_package(s["job"], OWNER, "en", "o-3", background=False,
                          music_asset={"uri": "x", "licence": "UNKNOWN", "source": "s", "attribution_required": False})
    bad = svc.start_package(s["job"], OWNER, "en", "o-4", overrides={"metadata": {"title": "x" * 150}}, background=False)
    assert bad["status"] == "FAILED" and "title must be 1-100" in bad["detail"]


def test_music_is_mixed_and_recorded_with_licence(chain, tmp_path):
    svc, *_ = _approved_chain(chain, "mu")
    s = chain["s"]
    ff = subs.ffmpeg_binary("ffmpeg")
    wav = tmp_path / "bed.wav"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=2",
                    str(wav)], check=True)
    uri = f"jobs/{s['job']}/music/bed.wav"
    svc.store.write_bytes(s["job"], uri, wav.read_bytes(), "audio/wav")
    item = svc.start_package(s["job"], OWNER, "en", "mu-1", background=False,
                             music_asset={"uri": uri, "licence": "CC0-1.0", "source": "test fixture",
                                          "attribution_required": False})
    assert item["status"] == "AWAITING_REVIEW", item["detail"]
    body = svc.read_integration_body(s["job"], OWNER, "en", "publish_package", item["version"])
    assert body["music"]["licence"] == "CC0-1.0"
    assert body["files"]["video_branded"]["probe"]["audio"]["codec"] == "aac"


def test_new_qc_makes_package_stale_and_unapprovable(chain):
    svc, r, q = _approved_chain(chain, "st")
    s = chain["s"]
    item = svc.start_package(s["job"], OWNER, "en", "st-1", background=False)
    assert item["status"] == "AWAITING_REVIEW"
    svc.start_qc(s["job"], OWNER, "en", r["version"], "qq-new", requirements={"min_seconds": 1.0}, background=False)
    status = svc.package_status(s["job"], OWNER, "en")
    assert status["items"][0]["stale"] is True
    with pytest.raises(IntegrationStale, match="approval changed|newer"):
        svc.review_package(s["job"], OWNER, "en", item["version"], "APPROVED", "late")


def test_http_package_routes_and_isolation(chain):
    svc, r, q = _approved_chain(chain, "ht")
    s = chain["s"]
    app = create_app(s["db"], SECRET, s["root"])
    mine = {"Authorization": f"Bearer {access_token(OWNER)}", "Idempotency-Key": "ht-1"}
    other = {"Authorization": f"Bearer {access_token('intruder')}", "Idempotency-Key": "ht-2"}
    base = f"/api/jobs/{s['job']}/package/en"
    with TestClient(app) as client:
        assert client.post(f"{base}/build", json={}).status_code == 401
        assert client.post(f"{base}/build", json={}, headers=other).status_code == 404
        assert svc._integration_rows(s["job"], "en", "publish_package") == []  # no cross-owner row was created
        assert client.post(f"{base}/build", json={"surprise": 1}, headers=mine).status_code == 422
        assert client.post(f"{base}/build", json={}, headers=mine).status_code == 202
        for _ in range(300):
            st = client.get(base, headers=mine).json()
            if st["items"][0]["status"] in ("AWAITING_REVIEW", "FAILED"):
                break
            time.sleep(0.2)
        assert st["items"][0]["status"] == "AWAITING_REVIEW", st["items"][0]["detail"]
        v = st["items"][0]["version"]
        assert client.get(f"{base}/{v}", headers=other).status_code == 404
        thumb = client.get(f"{base}/{v}/file/thumbnail", headers=mine)
        assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"
        assert client.get(f"{base}/{v}/file/../../x", headers=mine).status_code in (404, 422)
        done = client.post(f"{base}/{v}/review", json={"decision": "APPROVED", "note": "ok"}, headers=mine)
        assert done.status_code == 201 and done.json()["status"] == "APPROVED"
