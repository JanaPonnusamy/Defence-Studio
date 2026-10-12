"""Phase 2 / D: real video/PDF previews (or explicit 'unavailable'), optional attributed basemap (network
mocked), and matching beyond English-only keywords."""
from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from app.config import get_settings
from app.pipeline.intent import content_tokens
from fixtures.synthetic import mp4_bytes, pdf_bytes, real_mp4_bytes, real_pdf_bytes
from tests.conftest import import_png, wait_job

CAPS = None


def _caps(client):
    global CAPS
    CAPS = CAPS or client.get("/capabilities").json()["previews"]
    return CAPS


def _import(client, project, asset_type, data, name, mime, **extra):
    form = {"project_id": project, "asset_type": asset_type, "source_type": "USER_PROVIDED", "rights_status": "PERMITTED",
            **extra}
    return client.post("/assets/import", data=form, files={"file": (name, data, mime)})


def test_capabilities_report_is_explicit(client):
    caps = _caps(client)
    for key in ("video_frames", "pdf_pages", "basemap_tiles"):
        assert key in caps and "available" in caps[key] and "reason" in caps[key]
    assert caps["basemap_tiles"]["available"] is False  # off by default, no network
    assert caps["basemap_tiles"]["fallback"].startswith("schematic")


def test_video_clip_probe_and_frame_previews(client, project):
    if not _caps(client)["video_frames"]["available"]:
        pytest.skip("video decoding unavailable on this machine: " + str(_caps(client)["video_frames"]["reason"]))
    data = real_mp4_bytes(seconds=2.0)
    r = _import(client, project, "VIDEO_CLIP", data, "clip.mp4", "video/mp4", title="patrol clip")  # no duration given
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["duration"] == pytest.approx(2.0, abs=0.15) and (a["width"], a["height"]) == (160, 90)
    assert a["extra_metadata"]["duration_source"] == "probe" and a["extra_metadata"]["media_probe"]["available"] is True
    p0 = client.get(f"/assets/{a['asset_id']}/preview", params={"t": 0.0})
    p1 = client.get(f"/assets/{a['asset_id']}/preview", params={"t": 1.6})
    assert p0.status_code == 200 and p0.headers["x-preview-kind"] == "video_frame"
    i0, i1 = Image.open(io.BytesIO(p0.content)).convert("RGB"), Image.open(io.BytesIO(p1.content)).convert("RGB")
    assert i0.size == (160, 90)
    assert abs(i0.getpixel((80, 45))[0] - i1.getpixel((80, 45))[0]) > 60  # real, time-dependent frames


def test_undecodable_video_reports_unavailable_never_fakes(client, project):
    r = _import(client, project, "VIDEO_CLIP", mp4_bytes(), "fake.mp4", "video/mp4", duration="5")
    assert r.status_code == 201
    a = r.json()
    p = client.get(f"/assets/{a['asset_id']}/preview")
    assert p.status_code == 409 and p.json()["error_code"] == "PREVIEW_UNAVAILABLE"
    # without a duration and an unreadable container, the import is refused (nothing invented)
    r2 = _import(client, project, "VIDEO_CLIP", mp4_bytes() + b"x", "fake2.mp4", "video/mp4")
    assert r2.status_code == 422 and "duration" in r2.json()["error_message"]


def test_pdf_page_previews(client, project):
    if not _caps(client)["pdf_pages"]["available"]:
        pytest.skip("PDF rendering unavailable: " + str(_caps(client)["pdf_pages"]["reason"]))
    a = _import(client, project, "DOCUMENT", real_pdf_bytes(3), "doc.pdf", "application/pdf", title="memo").json()
    assert a["extra_metadata"]["pages"] == 3 and a["extra_metadata"]["page_count_source"] == "pdfium"
    p2 = client.get(f"/assets/{a['asset_id']}/preview", params={"page": 2, "max_side": 400})
    assert p2.status_code == 200 and p2.headers["x-preview-kind"] == "pdf_page"
    im = Image.open(io.BytesIO(p2.content)).convert("RGB")
    assert max(im.size) == 400 and len(set(im.getdata())) > 2  # real rendered page content
    bad = client.get(f"/assets/{a['asset_id']}/preview", params={"page": 9})
    assert bad.status_code == 409 and "out of range" in bad.json()["error_message"]
    # the minimal fixture PDF (no content) still imports; its page count comes from pdfium
    b = _import(client, project, "DOCUMENT", pdf_bytes(2), "min.pdf", "application/pdf").json()
    assert b["extra_metadata"]["pages"] == 2


def test_shot_preview_uses_real_document_page(client, project):
    if not _caps(client)["pdf_pages"]["available"]:
        pytest.skip("PDF rendering unavailable")
    _import(client, project, "DOCUMENT", real_pdf_bytes(1), "ev.pdf", "application/pdf", title="leaked order document",
            extra_metadata='{"tags": ["leaked", "document", "order"]}')
    client.post("/scenes", json={"project_id": project, "replace_existing": True,
                                 "narration": "According to a leaked document, the order was signed."})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project}).json()["job_id"])
    tl = client.get(f"/visual-plans/{done['result_id']}/timeline").json()
    shot = next(s for s in tl["shots"] if s["visual_type"] == "DOCUMENT")
    png = client.get(f"/shots/{done['result_id']}/{shot['shot_id']}/preview").content
    im = Image.open(io.BytesIO(png)).convert("RGB")
    # a real white page, not the grey 'unavailable' placeholder tile
    assert im.getpixel((im.width // 2, 20))[0] > 150


def _tile_png(color):
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, format="PNG")
    return buf.getvalue()


MAP_SPEC = {"title": "Kestrel coast", "data_source": "Synthetic test coordinates", "basemap": True,
            "markers": [{"label": "Kestrel", "lat": 44.6, "lon": 33.5}, {"label": "Talon", "lat": 45.0, "lon": 34.2}]}


def test_basemap_off_by_default_falls_back_to_schematic(client, project):
    a = client.post("/assets/maps", json={"project_id": project, "spec": MAP_SPEC}).json()
    bm = a["extra_metadata"]["basemap"]
    assert bm["used"] is False and bm["requested"] is True and bm["fallback"] == "schematic"
    assert "no tile source" in bm["reason"] and "no basemap" in a["description"]


def test_basemap_with_configured_attributed_tiles(client, project, monkeypatch):
    import app.basemap as basemap

    calls = []

    def tiles(request):
        calls.append(str(request.url))
        assert request.headers["user-agent"].startswith("video-visual-module")
        return httpx.Response(200, content=_tile_png((10, 120, 40)))

    s = get_settings()
    monkeypatch.setattr(s, "basemap_tile_url", "https://tiles.example.test/{z}/{x}/{y}.png")
    monkeypatch.setattr(s, "basemap_attribution", "(c) Example Tiles contributors")
    monkeypatch.setattr(s, "basemap_network_enabled", True)
    monkeypatch.setattr(basemap, "_transport", httpx.MockTransport(tiles))
    a = client.post("/assets/maps", json={"project_id": project, "spec": MAP_SPEC}).json()
    bm = a["extra_metadata"]["basemap"]
    assert bm["used"] is True and bm["projection"] == "web_mercator" and calls
    assert "Example Tiles" in a["attribution"] and a["attribution_required"] is True
    img = Image.open(get_settings().storage_root / a["local_path"]).convert("RGB")
    assert img.getpixel((60, 120)) == (10, 120, 40)  # tile pixels inside the map frame
    # tile failure -> schematic fallback, recorded
    monkeypatch.setattr(basemap, "_transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    spec2 = {**MAP_SPEC, "title": "Kestrel coast 2", "markers": [{"label": "Far", "lat": 10.0, "lon": 10.0},
                                                                   {"label": "Away", "lat": 11.0, "lon": 11.5}]}
    b = client.post("/assets/maps", json={"project_id": project, "spec": spec2}).json()
    assert b["extra_metadata"]["basemap"]["used"] is False and "503" in b["extra_metadata"]["basemap"]["reason"]


def test_matching_synonyms_and_non_english():
    # synonyms / plural forms share a concept token
    assert "~warship" in content_tokens("Two frigates sailed") & content_tokens("warship")
    assert "~port" in content_tokens("the harbour") & content_tokens("PORT")
    # non-English tags match English narration through the concept table; accents are folded
    assert content_tokens("puerto naval") & content_tokens("the port was busy")
    assert "sevastopol" in content_tokens("Sevastópol") and "sevastopol" in content_tokens("Sevastopol")
    # non-Latin scripts are tokenised (previously dropped entirely)
    assert "ракета" in content_tokens("Ракета") and "~missile" in content_tokens("ракета")
    # plain English tokens are unchanged from v1.0
    assert {"fleet", "concentrated", "black", "sea"} <= content_tokens("The fleet is concentrated near the Black Sea.")


def test_tagged_asset_in_other_language_is_selected(client, project):
    a = import_png(client, project, seed=6101, title="Foto", meta={"tags": ["fragata", "frigates"],
                                                                     "keywords_i18n": {"es": ["buque de guerra"]}}).json()
    client.post("/scenes", json={"project_id": project, "replace_existing": True,
                                 "narration": "The new warships were photographed leaving at dawn yesterday."})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project}).json()["job_id"])
    shots = client.get(f"/shots/{done['result_id']}").json()["shots"]
    assert any(s["asset_id"] == a["asset_id"] for s in shots)
