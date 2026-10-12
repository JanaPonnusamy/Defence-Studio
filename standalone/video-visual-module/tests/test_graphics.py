"""Graphics tests (spec §33): statistics, maps, charts, documents."""
from __future__ import annotations

import json

import pytest
from PIL import Image

from fixtures.synthetic import png_bytes
from tests.conftest import wait_job

STAT = {"label": "Warships", "value": 40, "unit": "ships", "source": "MoD release", "as_of": "2026-09-30", "confidence": "OFFICIAL"}


def test_stat_card_from_structured_data(client, project, test_root):
    r = client.post("/assets/stats", json={"project_id": project, "spec": {"graphic_type": "STAT_CARD", "title": "Warships deployed", "data": [STAT]}})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["asset_type"] == "STAT_CARD" and a["source_type"] == "SYSTEM_GENERATED"
    assert a["editorial_category"] == "FACTUAL_EVIDENCE"
    assert a["extra_metadata"]["stat"]["data"][0] == {**STAT, "event": None}
    assert a["extra_metadata"]["numbers"] == ["40"]
    assert "MoD release (as of 2026-09-30)" in a["attribution"]
    with Image.open(test_root / a["local_path"]) as im:
        assert im.size == (1280, 720)


@pytest.mark.parametrize("point,why", [
    ({k: v for k, v in STAT.items() if k != "source"}, "missing source"),
    ({k: v for k, v in STAT.items() if k != "as_of"}, "missing as_of"),
    ({k: v for k, v in STAT.items() if k != "value"}, "missing value"),
    ({k: v for k, v in STAT.items() if k != "unit"}, "missing unit"),
    ({**STAT, "value": "forty"}, "non numeric"),
    ({**STAT, "value": True}, "bool"),
    ({**STAT, "confidence": "TRUST ME"}, "bad confidence"),
])
def test_invalid_stat_data_rejected(client, project, point, why):
    r = client.post("/assets/stats", json={"project_id": project, "spec": {"graphic_type": "STAT_CARD", "title": "t", "data": [point]}})
    assert r.status_code == 422, why
    assert r.json()["error_code"] == "INVALID_STAT_DATA"


def test_charts_bar_line_comparison_timeline(client, project):
    pts = [{**STAT, "label": "2024", "value": 30}, {**STAT, "label": "2025", "value": 35}, {**STAT, "label": "2026", "value": 40}]
    for gtype in ("BAR_CHART", "LINE_CHART", "COMPARISON"):
        r = client.post("/assets/stats", json={"project_id": project, "spec": {"graphic_type": gtype, "title": f"{gtype} test", "data": pts}})
        assert r.status_code == 201, (gtype, r.text)
        assert r.json()["asset_type"] == "CHART"
    mixed = [pts[0], {**pts[1], "unit": "tanks"}]
    r = client.post("/assets/stats", json={"project_id": project, "spec": {"graphic_type": "BAR_CHART", "title": "mixed", "data": mixed}})
    assert r.status_code == 422 and "mixed units" in r.json()["error_message"]
    tl = [{"label": "Jan 2026", "event": "Exercise announced", "source": "MoD", "as_of": "2026-01-05"},
          {"label": "Mar 2026", "event": "Ships deployed", "source": "MoD", "as_of": "2026-03-01"}]
    r = client.post("/assets/stats", json={"project_id": project, "spec": {"graphic_type": "TIMELINE", "title": "Timeline", "data": tl}})
    assert r.status_code == 201 and r.json()["editorial_category"] == "UNKNOWN"  # unverified confidence -> not evidence


def test_map_from_supplied_coordinates(client, project, test_root):
    spec = {"title": "Kessan Strait", "data_source": "Fixture coordinates (synthetic)",
            "markers": [{"label": "Port Arvel", "lat": 44.6, "lon": 33.5}, {"label": "Lunet", "lat": 45.1, "lon": 35.0}],
            "route": [{"lat": 44.6, "lon": 33.5}, {"lat": 45.1, "lon": 35.0}],
            "region": {"label": "Exercise zone", "polygon": [{"lat": 44.0, "lon": 34.0}, {"lat": 44.5, "lon": 35.5}, {"lat": 43.8, "lon": 35.2}]},
            "movements": [{"label": "convoy", "order": 1, "from": {"lat": 44.0, "lon": 33.0}, "to": {"lat": 44.5, "lon": 34.0}}]}
    r = client.post("/assets/maps", json={"project_id": project, "spec": spec, "tags": ["strait"]})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["asset_type"] == "MAP" and a["attribution"] == "Map data: Fixture coordinates (synthetic)"
    regions = a["extra_metadata"]["regions"]
    assert {"Exercise zone", "route", "Port Arvel", "Lunet"} <= {r["label"] for r in regions}
    for reg in regions:
        assert 0 <= reg["x"] <= 1 and 0 < reg["w"] <= 1
    assert "kessan" in a["extra_metadata"]["tags"] and "strait" in a["extra_metadata"]["tags"]
    with Image.open(test_root / a["local_path"]) as im:
        assert im.size == (1280, 720)


@pytest.mark.parametrize("spec,why", [
    ({"title": "t", "markers": [{"label": "a", "lat": 1, "lon": 1}]}, "no data_source"),
    ({"title": "t", "data_source": "s", "markers": [{"label": "a", "lat": 91, "lon": 1}]}, "lat out of range"),
    ({"title": "t", "data_source": "s"}, "nothing to plot"),
    ({"title": "t", "data_source": "s", "route": [{"lat": 1, "lon": 1}]}, "1-point route"),
    ({"title": "t", "data_source": "s", "markers": [{"label": "a", "lat": 10, "lon": 10}],
      "bbox": {"min_lat": 0, "max_lat": 5, "min_lon": 0, "max_lon": 5}}, "point outside bbox"),
])
def test_invalid_map_rejected(client, project, spec, why):
    r = client.post("/assets/maps", json={"project_id": project, "spec": spec})
    assert r.status_code == 422, why
    assert r.json()["error_code"] == "MAP_RENDER_FAILED"


def test_map_selected_for_geography_and_highlight_regions_used(client, project):
    spec = {"title": "Kessan Strait", "data_source": "fixture", "markers": [{"label": "Port Arvel", "lat": 44.6, "lon": 33.5}]}
    m = client.post("/assets/maps", json={"project_id": project, "spec": spec}).json()
    long_geo = " ".join(["The fleet is concentrated near the Kessan Strait and Port Arvel this week according to monitors."] * 4)
    client.post("/scenes", json={"project_id": project, "narration": long_geo, "replace_existing": True})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project, "options": {"max_shot_seconds": 3}}).json()["job_id"])
    shots = client.get(f"/shots/{done['result_id']}").json()["shots"]
    assert all(s["asset_id"] == m["asset_id"] for s in shots)
    hl = [s for s in shots if s["motion"]["type"] == "HIGHLIGHT_REGION"]
    assert hl and hl[0]["motion"]["region"]["w"] > 0
    assert any(o["type"] == "source_caption" for o in shots[0]["overlays"])


def test_document_regions_drive_crop_focus(client, project):
    meta = {"tags": ["document", "order", "released"], "regions": [{"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.2, "label": "heading", "page": 1}]}
    r = client.post("/assets/import", data={"project_id": project, "asset_type": "DOCUMENT", "source_type": "EXTERNAL_SOURCE",
                                            "rights_status": "PERMITTED", "title": "Released order", "extra_metadata": json.dumps(meta)},
                    files={"file": ("doc.png", png_bytes(1000, 1400, 801), "image/png")})
    assert r.status_code == 201
    text = " ".join(["According to a released document, the order was signed by the regional command last spring."] * 4)
    client.post("/scenes", json={"project_id": project, "narration": text, "replace_existing": True})
    done = wait_job(client, client.post("/visual-plans/build", json={"project_id": project, "options": {"max_shot_seconds": 3}}).json()["job_id"])
    shots = client.get(f"/shots/{done['result_id']}").json()["shots"]
    assert all(s["visual_type"] == "DOCUMENT" for s in shots)
    focus = [s for s in shots if s["motion"]["type"] == "CROP_FOCUS"]
    assert focus and focus[0]["crop"]["composition"] == "region"
    reg = focus[0]["crop"]["region"]
    assert reg["label"] == "heading" and reg["page"] == 1
    assert all(s["motion"]["type"] in ("STATIC", "CROP_FOCUS", "HIGHLIGHT_REGION", "PAN_DOWN") for s in shots)
