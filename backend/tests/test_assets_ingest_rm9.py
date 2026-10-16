"""Asset byte ingestion -> rights review -> assignment -> Visual Module import (RM-5 gap closure)."""
import io
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from defence_studio.api.app import create_app
from defence_studio.integrations.visual_module import VisualModuleClient
from defence_studio.services.integration import IntegrationValidationError
from defence_studio.services.visual import VisualService
from test_scene_service_m1 import OWNER, SCENE_CONFIG, _generate_plan, _plan_sha, approved_script  # noqa: F401
from test_visual_rm3 import FakeVisualModule, _client as visual_client
from conftest import SECRET, access_token


def png(color=(30, 90, 160), size=(160, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


RIGHTS = {"schema_version": "defence-studio.asset-rights.v1", "rights_basis": "creator_owned",
          "attribution_required": True, "attribution_text": "Photo: Test Desk", "permitted_uses": ["broadcast", "web"],
          "commercial_use": "allowed"}


class ImportingVisualModule(FakeVisualModule):
    """Honours imports like the real module: scenes sharing a keyword with the import get it."""

    def __init__(self):
        super().__init__()
        self.imports = []

    def handler(self, request):
        path = request.url.path
        if path == "/assets/import":
            text = request.content.decode("utf-8", "ignore")
            kw = re.search(r'\{"keywords": (\[.*?\]), "core_asset_id": "([0-9a-f]+)"\}', text)
            rights = re.search(r'name="rights_status"\r\n\r\n(\w+)', text)
            n = len(self.imports) + 1
            self.imports.append({"keywords": json.loads(kw.group(1)), "core": kw.group(2),
                                 "rights": rights.group(1) if rights else None, "id": f"vasset{n}"})
            return httpx.Response(201, json={"asset_id": f"vasset{n}"})
        if path == "/visual-plans/vp1/timeline":
            shots = []
            for i, seg in enumerate(self.segments):
                words = set(re.findall(r"[^\W\d_]{4,}", seg["text"].lower()))
                hit = next((m for m in self.imports if words & set(m["keywords"])), None)
                shots.append({"shot_id": f"sh{i}", "order": i, "start": seg["start"], "end": seg["end"],
                              "status": "READY" if hit else "GAP", "gap_code": None if hit else "SOURCE_UNAVAILABLE",
                              "asset_id": hit["id"] if hit else None, "visual_type": "SOURCE_PHOTO",
                              "narration_anchor": {"segment_ids": [seg["segment_id"]], "text": seg["text"]}})
            return httpx.Response(200, json={"schema": "video-visual-timeline", "version": "1.1",
                                             "duration": self.segments[-1]["end"], "shots": shots, "assets": {},
                                             "attributions": []})
        return super().handler(request)


def _english_plan_for_assets(s):
    from test_audio_rm4 import _english_plan
    return _english_plan(s)


def _approved_assigned_asset(s, plan, scene_index=0, key="a1", rights=RIGHTS):
    svc = VisualService(s["db"], s["root"], provider=s["provider"])
    asset = svc.ingest_asset_upload(s["job"], OWNER, png(), {"title": "Test photo", "creator": "Test Desk",
                                                            "documented_origin": "own camera"}, f"up-{key}")
    aid = asset["asset_id"]
    a = svc.set_rights(s["job"], OWNER, aid, rights, f"r-{key}")
    svc.review_asset(s["job"], OWNER, aid, "APPROVED", a["asset_sha256"], a["rights_sha256"], 0, "rights checked",
                     f"rv-{key}")
    scene_id = plan["scenes"][scene_index]["scene_id"]
    svc.assign_asset(s["job"], OWNER, "en", scene_id, aid, f"as-{key}")
    return svc, aid, scene_id, asset


def test_expiry_is_rechecked_when_selecting_previously_approved_asset(approved_script, monkeypatch):
    s = approved_script
    plan = _english_plan_for_assets(s)
    svc, _, _, _ = _approved_assigned_asset(s, plan, rights={**RIGHTS, "expires_at": "2090-01-01T00:00:00+00:00"})
    sha = _plan_sha(s["scene"], s["job"], plan["version"], "en")
    assert len(svc.assigned_assets(s["job"], "en", sha)) == 1
    monkeypatch.setattr("defence_studio.services.assets._now", lambda: "2091-01-01T00:00:00+00:00")
    assert svc.assigned_assets(s["job"], "en", sha) == []


def test_non_web_rights_are_not_imported_for_web_video(approved_script):
    s = approved_script
    plan = _english_plan_for_assets(s)
    svc, _, _, _ = _approved_assigned_asset(s, plan, rights={**RIGHTS, "permitted_uses": ["broadcast"]})
    sha = _plan_sha(s["scene"], s["job"], plan["version"], "en")
    assert svc.assigned_assets(s["job"], "en", sha) == []


def test_upload_measures_real_bytes_and_rejects_non_images(approved_script):
    s = approved_script
    svc = VisualService(s["db"], s["root"], provider=s["provider"])
    asset = svc.ingest_asset_upload(s["job"], OWNER, png(), {"title": "Photo"}, "u1")
    c = asset["descriptor"]["content"]
    assert c["status"] == "MEASURED" and len(c["content_sha256"]) == 64 and c["media_type"] == "image/png"
    assert asset["review_status"] == "DRAFT" and asset["effective_rights"] != "CLEAR"  # ingestion is never approval
    assert asset["file"]["width"] == 160
    for bad, msg in ((b"not an image", "valid, complete image"), (b"", "1 byte"),
                     (png()[:-20], "valid, complete image")):
        with pytest.raises(IntegrationValidationError, match=msg):
            svc.ingest_asset_upload(s["job"], OWNER, bad, {"title": "x"}, f"u-{len(bad)}")
    with pytest.raises(IntegrationValidationError, match="title"):
        svc.ingest_asset_upload(s["job"], OWNER, png((1, 2, 3)), {}, "u-nt")
    with pytest.raises(Exception):
        svc.ingest_asset_upload(s["job"], "intruder", png((9, 9, 9)), {"title": "x"}, "u-x")


def test_assigned_approved_asset_is_imported_and_honoured_in_timeline(approved_script):
    s = approved_script
    plan = _english_plan_for_assets(s)
    svc, aid, scene_id, asset = _approved_assigned_asset(s, plan)
    fake = ImportingVisualModule()
    visual = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=visual_client(fake))
    item = visual.start_visual_plan(s["job"], OWNER, "en", "iv-1", background=False)
    assert item["status"] == "SUCCEEDED", item["detail"]
    assert len(fake.imports) == 1 and fake.imports[0]["core"] == aid and fake.imports[0]["rights"] == "LICENSED"
    body = visual.read_integration_body(s["job"], OWNER, "en", "visual_timeline", item["version"])
    assert body["assignments"] == [{"scene_id": scene_id, "core_asset_id": aid, "honored": True}]
    ready = [sh for sh in body["timeline"]["shots"] if sh["status"] == "READY"]
    assert ready and body["summary"]["gap_count"] == len(body["timeline"]["shots"]) - len(ready)
    assert body["binding"]["assets"][0]["asset_id"] == aid


def test_unapproved_or_unassigned_assets_are_never_imported(approved_script):
    s = approved_script
    _english_plan_for_assets(s)
    svc = VisualService(s["db"], s["root"], provider=s["provider"])
    asset = svc.ingest_asset_upload(s["job"], OWNER, png((5, 6, 7)), {"title": "Draft only"}, "d-1")
    fake = ImportingVisualModule()
    visual = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=visual_client(fake))
    item = visual.start_visual_plan(s["job"], OWNER, "en", "iv-2", background=False)
    assert item["status"] == "SUCCEEDED" and fake.imports == []
    assert asset["review_status"] == "DRAFT"


def test_changing_assignments_changes_the_binding_so_work_is_not_reused(approved_script):
    s = approved_script
    plan = _english_plan_for_assets(s)
    fake = ImportingVisualModule()
    visual = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=visual_client(fake))
    first = visual.start_visual_plan(s["job"], OWNER, "en", "cb-1", background=False)
    _approved_assigned_asset(s, plan, key="cb")
    second = visual.start_visual_plan(s["job"], OWNER, "en", "cb-2", background=False)
    assert second["version"] == first["version"] + 1 and len(fake.imports) == 1


def test_http_upload_route_multipart_and_isolation(approved_script):
    s = approved_script
    app = create_app(s["db"], SECRET, s["root"])
    mine = {"Authorization": f"Bearer {access_token(OWNER)}", "Idempotency-Key": "http-up-1"}
    other = {"Authorization": f"Bearer {access_token('intruder')}", "Idempotency-Key": "http-up-2"}
    url = f"/api/jobs/{s['job']}/assets/upload"
    with TestClient(app) as client:
        assert client.post(url, files={"file": ("p.png", png(), "image/png")}, data={"title": "T"}).status_code == 401
        assert client.post(url, files={"file": ("p.png", png(), "image/png")}, data={"title": "T"},
                           headers=other).status_code == 404
        ok = client.post(url, files={"file": ("p.png", png(), "image/png")}, data={"title": "T", "creator": "C"},
                         headers=mine)
        assert ok.status_code == 201 and ok.json()["review_status"] == "DRAFT"
        bad = client.post(url, files={"file": ("p.txt", b"hello", "text/plain")}, data={"title": "T"},
                          headers={**mine, "Idempotency-Key": "http-up-3"})
        assert bad.status_code == 422


# -- INTEGRATED with the real Visual Module ---------------------------------------


def _vvm_up():
    try:
        return VisualModuleClient(request_timeout=2).health().get("service") == "video-visual-module"
    except Exception:
        return False


@pytest.mark.skipif(not _vvm_up(), reason="real Visual Module not running")
def test_real_module_places_rights_cleared_asset_on_its_scene(approved_script):
    s = approved_script
    plan = _english_plan_for_assets(s)
    _approved_assigned_asset(s, plan, scene_index=1, key="real")
    visual = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=VisualModuleClient(poll_interval=0.2))
    item = visual.start_visual_plan(s["job"], OWNER, "en", "real-iv", background=False)
    assert item["status"] == "SUCCEEDED", item["detail"]
    body = visual.read_integration_body(s["job"], OWNER, "en", "visual_timeline", item["version"])
    shots = body["timeline"]["shots"]
    assert any(sh["status"] == "READY" for sh in shots), "the real module did not use the imported asset"
    assert body["summary"]["gap_count"] < len(shots)
    att = body["timeline"].get("attributions", [])
    assert any(a.get("attribution") == "Photo: Test Desk" for a in att)
