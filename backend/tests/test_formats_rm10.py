"""RM-10: additional output formats reuse the same pipeline (real modules)."""
import pytest
from fastapi.testclient import TestClient

from defence_studio import formats
from defence_studio.api.app import create_app
from defence_studio.integrations.audio_module import AudioModuleClient
from defence_studio.integrations.renderer_module import RendererClient
from defence_studio.integrations.visual_module import VisualModuleClient
from defence_studio.services.audio import AudioService
from defence_studio.services.render import RenderService
from defence_studio.services.visual import VisualService
from defence_studio.services.youtube import YouTubeService
from test_assets_ingest_rm9 import _approved_assigned_asset, _english_plan_for_assets  # noqa: F401
from test_scene_service_m1 import OWNER, approved_script  # noqa: F401
from conftest import SECRET, access_token


def test_format_presets_are_valid_and_vertical_shorts_are_bounded():
    names = {f["name"] for f in formats.listing()}
    assert {"landscape_1080p", "vertical_short", "square"} <= names
    v = formats.get("vertical_short")
    assert (v["width"], v["height"]) == (1080, 1920) and v["qc"]["max_seconds"] == 60.0
    assert formats.visual_options("landscape_720p") == {"output_width": 1280, "output_height": 720, "frame_rate": 25}
    assert formats.get(None)["name"] == formats.DEFAULT_FORMAT
    with pytest.raises(formats.UnknownFormat):
        formats.get("imax")
    for f in formats.FORMATS.values():
        assert f["width"] % 2 == 0 and f["height"] % 2 == 0  # encoder-safe


def test_http_formats_route_and_unknown_format_rejected(approved_script):
    s = approved_script
    app = create_app(s["db"], SECRET, s["root"])
    mine = {"Authorization": f"Bearer {access_token(OWNER)}", "Idempotency-Key": "fmt-1"}
    with TestClient(app) as client:
        assert client.get("/api/formats").status_code == 401
        got = client.get("/api/formats", headers=mine).json()
        assert got["default"] == "landscape_1080p" and len(got["formats"]) >= 4
        r = client.post(f"/api/jobs/{s['job']}/visual/en/plan", json={"format": "imax"}, headers=mine)
        assert r.status_code == 422


def _modules_up():
    try:
        return (VisualModuleClient(request_timeout=2).health().get("service") == "video-visual-module"
                and AudioModuleClient(request_timeout=2).health().get("service") == "audio-module"
                and RendererClient(request_timeout=2).health().get("ffmpeg", {}).get("status") == "AVAILABLE")
    except Exception:
        return False


@pytest.mark.skipif(not _modules_up(), reason="real Visual/Audio/Renderer modules not all running")
def test_vertical_short_end_to_end_with_real_modules(approved_script):
    s = approved_script
    plan = _english_plan_for_assets(s)
    _approved_assigned_asset(s, plan, scene_index=0, key="short")
    # assign the same approved asset to the remaining scenes so no visual gaps remain
    svc0 = VisualService(s["db"], s["root"], provider=s["provider"])
    aid = svc0.assigned_assets(s["job"], "en", svc0._approved_plan(s["job"], OWNER, "en")[0]["plan_sha256"])[0]["asset_id"]
    for i, sc in enumerate(plan["scenes"][1:], start=1):
        svc0.assign_asset(s["job"], OWNER, "en", sc["scene_id"], aid, f"short-as-{i}")
    audio = AudioService(s["db"], s["root"], provider=s["provider"],
                         audio_client=AudioModuleClient(poll_interval=0.3, job_timeout_seconds=300))
    a = audio.start_audio(s["job"], OWNER, "en", "sh-a", align=False, background=False)
    assert a["status"] == "SUCCEEDED", a["detail"]
    visual = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=VisualModuleClient(poll_interval=0.2))
    opts = formats.visual_options("vertical_short_720")
    v = visual.start_visual_plan(s["job"], OWNER, "en", "sh-v", opts, background=False, audio_version=a["version"])
    assert v["status"] == "SUCCEEDED", v["detail"]
    render = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=RendererClient(poll_interval=0.5),
                           visual_client=VisualModuleClient())
    r = render.start_render(s["job"], OWNER, "en", "sh-r", audio_version=a["version"], visual_version=v["version"],
                            background=False)
    assert r["status"] == "SUCCEEDED", r["detail"]
    body = render.read_integration_body(s["job"], OWNER, "en", "render", r["version"])
    probe = body["video_subtitled"]["probe"]["video"]
    assert (probe["width"], probe["height"]) == (720, 1280)
    top = YouTubeService(s["db"], s["root"], provider=s["provider"])
    q = top.start_qc(s["job"], OWNER, "en", r["version"], "sh-qc", requirements=formats.qc_requirements("vertical_short_720"),
                     background=False)
    assert q["status"] == "SUCCEEDED", q["detail"]
    report = top.read_report(s["job"], OWNER, "en", q["version"])
    checks = {c["id"]: c["status"] for c in report["checks"]}
    assert checks["CONTAINER_STREAMS"] == "PASS" and checks["VISUAL_COVERAGE"] == "PASS", checks
    assert report["binding"]["requirements"]["max_seconds"] == 60.0
    warns = sorted(c["id"] for c in report["checks"] if c["status"] == "WARN")
    assert report["summary"]["passed"], {k: v for k, v in checks.items() if v == "FAIL"}
    top.approve_render(s["job"], OWNER, "en", q["version"], "approved", warns, "sh-ap")
    pkg = top.start_package(s["job"], OWNER, "en", "sh-pk", background=False)
    assert pkg["status"] == "AWAITING_REVIEW", pkg["detail"]
    manifest = top.read_integration_body(s["job"], OWNER, "en", "publish_package", pkg["version"])
    assert "#Shorts" in manifest["metadata"]["hashtags"]
    assert manifest["files"]["video_branded"]["probe"]["video"]["height"] == 1280
    assert manifest["branding"]["intro_seconds"] <= 1.0
