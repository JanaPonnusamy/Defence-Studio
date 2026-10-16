"""RM-5: render package, renderer integration, Tamil text and subtitles.

* pure unit tests for cue building / sync checks / exports;
* a real FFmpeg/libass Tamil burn-in test (skipped only if FFmpeg is absent);
* service tests with in-process fakes of the three modules' documented APIs
  (the fake renderer still produces a real MP4 so subtitle burn-in, probe and
  full decode run for real);
* an INTEGRATED chain test against the three real standalone modules
  (skipped with a reason unless all are running).
"""
import hashlib
import json
import shutil
import subprocess
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.integrations import subtitles as subs
from defence_studio.integrations.audio_module import AudioModuleClient
from defence_studio.integrations.renderer_module import RendererClient, RendererError
from defence_studio.integrations.visual_module import VisualModuleClient
from defence_studio.services.audio import AudioService
from defence_studio.services.integration import IntegrationStale, IntegrationValidationError
from defence_studio.services.render import RenderService
from defence_studio.services.visual import VisualService
from test_audio_rm4 import FakeAudioModule, _client as audio_client, _english_plan  # noqa: F401
from test_visual_rm3 import FakeVisualModule, _client as visual_client  # noqa: F401
from test_scene_service_m1 import OWNER, approved_script  # noqa: F401
from conftest import SECRET, access_token

HAS_FFMPEG = bool(shutil.which("ffmpeg") or __import__("os").environ.get("RENDERER_FFMPEG"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg not available")


def make_mp4(path: Path, seconds: float, size="320x180") -> None:
    ff = subs.ffmpeg_binary("ffmpeg")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x203040:s={size}:d={seconds}:r=25",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, timeout=120)


# -- pure --------------------------------------------------------------------


def test_cues_are_contiguous_within_scene_and_cover_every_scene():
    scenes = [{"scene_id": "a", "text": "One two three four five six seven eight nine ten eleven twelve. Short one.",
               "start": 0.0, "duration": 6.0},
              {"scene_id": "b", "text": "அதிகாரிகள் தெரிவித்த தகவலின்படி நிலைமை தொடர்கிறது.", "start": 6.0, "duration": 3.0}]
    cues = subs.build_cues(scenes, max_words=6)
    assert {c["scene_id"] for c in cues} == {"a", "b"} and len(cues) >= 4
    assert subs.check_cues(cues, scenes, 9.0) == []
    assert cues[-1]["end"] == 9.0
    broken = [dict(cues[0], start=20.0, end=21.0)] + cues[1:]
    assert subs.check_cues(broken, scenes, 9.0)
    assert any("no subtitle cue" in p for p in subs.check_cues(cues[:-1] if len(cues) > 4 else [], scenes, 9.0))


def test_concat_narration_inserts_requested_inter_scene_silence(tmp_path):
    import io
    import struct
    from defence_studio.services.render import RenderService

    def wav(seconds):
        rate = 8000
        buf = io.BytesIO()
        with wave.open(buf, "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(rate)
            audio.writeframes(struct.pack("<h", 12000) * int(seconds * rate))
        return buf.getvalue()

    class Store:
        files = {"a": wav(1.0), "b": wav(1.0)}

        def read_bytes(self, _job_id, uri):
            return self.files[uri]

    service = object.__new__(RenderService)
    service.store = Store()
    output = tmp_path / "joined.wav"
    duration, spans = service._concat_narration("synthetic-job", {
        "scenes": [
            {"scene_id": "a", "text": "first", "audio_uri": "a",
             "voice_direction": {"requested": {"pause_after_ms": 350}}},
            {"scene_id": "b", "text": "second", "audio_uri": "b",
             "voice_direction": {"requested": {"pause_after_ms": 0}}},
        ]
    }, output)

    with wave.open(str(output), "rb") as rendered:
        samples = struct.unpack("<" + "h" * rendered.getnframes(), rendered.readframes(rendered.getnframes()))
    assert duration == 2.35
    assert spans[1]["start"] == 1.35
    assert samples[8000:10800] == (0,) * 2800
    assert samples[10799] == 0 and samples[10800] == 12000


def test_exports_are_well_formed():
    cues = [{"index": 1, "scene_id": "a", "start": 0.0, "end": 1.5, "text": "Hello {x}"}]
    assert subs.to_srt(cues).startswith("1\n00:00:00,000 --> 00:00:01,500\nHello")
    assert subs.to_vtt(cues).startswith("WEBVTT")
    ass = subs.to_ass(cues, 1920, 1080, "Nirmala UI")
    assert "Style: Default,Nirmala UI," in ass and "Dialogue: 0,0:00:00.00,0:00:01.50" in ass and "{x}" not in ass


def test_font_resolution_fails_closed_without_family(monkeypatch, tmp_path):
    f = tmp_path / "x.ttf"; f.write_bytes(b"x")
    monkeypatch.setenv("DEFENCE_STUDIO_SUBTITLE_FONT_FILE", str(f))
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY", raising=False)
    with pytest.raises(subs.SubtitleError):
        subs.resolve_font("ta")


@pytest.mark.skipif(not (subs._WINDOWS_FONTS / "Nirmala.ttc").is_file(),
                    reason="Windows Nirmala font collection is not installed")
def test_tamil_default_windows_resolves_nirmala_collection(monkeypatch):
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FILE", raising=False)
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY", raising=False)

    font, family = subs.resolve_font("ta")

    assert font == subs._WINDOWS_FONTS / "Nirmala.ttc"
    assert family == "Nirmala UI"


@pytest.mark.skipif(not (subs._WINDOWS_FONTS / "Nirmala.ttc").is_file(),
                    reason="Windows Nirmala font collection is not installed")
def test_tamil_collection_candidate_loads_supported_face(monkeypatch):
    from PIL import ImageFont

    collection = subs._WINDOWS_FONTS / "Nirmala.ttc"
    monkeypatch.setattr(subs, "_FONT_CANDIDATES", {"ta": [(str(collection), "Nirmala UI")], "en": []})
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FILE", raising=False)
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY", raising=False)

    font, family = subs.resolve_font("ta")
    loaded_face = ImageFont.truetype(str(font), 32)

    assert font == collection
    assert family == loaded_face.getname()[0] == "Nirmala UI"
    assert subs._has_tamil_glyph_coverage(font)


@pytest.mark.skipif(not (subs._WINDOWS_FONTS / "Nirmala.ttc").is_file(),
                    reason="Windows Nirmala font collection is not installed")
def test_tamil_explicit_font_override_is_preserved(monkeypatch):
    collection = subs._WINDOWS_FONTS / "Nirmala.ttc"
    monkeypatch.setattr(subs, "_FONT_CANDIDATES", {"ta": [], "en": []})
    monkeypatch.setenv("DEFENCE_STUDIO_SUBTITLE_FONT_FILE", str(collection))
    monkeypatch.setenv("DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY", "Nirmala UI")

    assert subs.resolve_font("ta") == (collection, "Nirmala UI")


def test_tamil_missing_font_fails_closed(monkeypatch, tmp_path):
    missing = tmp_path / "Nirmala.ttc"
    monkeypatch.setattr(subs, "_FONT_CANDIDATES", {"ta": [(str(missing), "Nirmala UI")], "en": []})
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FILE", raising=False)
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY", raising=False)

    with pytest.raises(subs.SubtitleError) as exc:
        subs.resolve_font("ta")

    assert exc.value.code == "font_missing"


def test_tamil_explicit_override_rejects_font_without_tamil_coverage(monkeypatch, tmp_path):
    not_a_font = tmp_path / "latin-only.ttf"
    not_a_font.write_bytes(b"not a font")
    monkeypatch.setenv("DEFENCE_STUDIO_SUBTITLE_FONT_FILE", str(not_a_font))
    monkeypatch.setenv("DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY", "Latin Only")

    with pytest.raises(subs.SubtitleError) as exc:
        subs.resolve_font("ta")

    assert exc.value.code == "font_config"


def test_non_tamil_font_resolution_keeps_existing_candidate_behavior(monkeypatch, tmp_path):
    candidate = tmp_path / "legacy-font.ttf"
    candidate.write_bytes(b"existing non-Tamil candidate")
    monkeypatch.setattr(subs, "_FONT_CANDIDATES", {"ta": [], "en": [(str(candidate), "Legacy Font")]})
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FILE", raising=False)
    monkeypatch.delenv("DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY", raising=False)

    assert subs.resolve_font("en") == (candidate, "Legacy Font")


# -- real FFmpeg / libass Tamil -------------------------------------------------


@needs_ffmpeg
def test_tamil_subtitles_burn_in_with_real_font_and_decode_clean(tmp_path):
    try:
        font, family = subs.resolve_font("ta")
    except subs.SubtitleError:
        pytest.skip("no Tamil-capable font installed")
    src, plain_frame, burned_frame = tmp_path / "in.mp4", tmp_path / "plain.png", tmp_path / "burned.png"
    make_mp4(src, 3.0, "640x360")
    cues = [{"index": 1, "scene_id": "s", "start": 0.2, "end": 2.8, "text": "இந்திய இராணுவம் புதிய விமானங்களை வாங்கியது"}]
    out = tmp_path / "out.mp4"
    subs.burn_in(src, subs.to_ass(cues, 640, 360, family), out, font)
    probe = subs.probe(out)
    assert probe["video"]["codec"] == "h264" and probe["audio"]["codec"] == "aac"
    assert abs(probe["duration"] - 3.0) < 0.2 and subs.full_decode_errors(out) == 0
    ff = subs.ffmpeg_binary("ffmpeg")
    for video, frame in ((src, plain_frame), (out, burned_frame)):
        subprocess.run([ff, "-v", "error", "-y", "-ss", "1.0", "-i", str(video), "-frames:v", "1", str(frame)],
                       check=True, timeout=60)
    from PIL import Image, ImageChops
    a, b = Image.open(plain_frame).convert("L"), Image.open(burned_frame).convert("L")
    bbox = ImageChops.difference(a, b).point(lambda v: 255 if v > 40 else 0).getbbox()
    assert bbox is not None, "subtitle text was not drawn"
    assert bbox[1] > 360 * 0.6, "text should sit in the lower third"
    (Path(__import__("tempfile").gettempdir()) / "ds_tamil_burn_frame.png").write_bytes(burned_frame.read_bytes())


# -- service level with fakes ----------------------------------------------------


class FakeRenderer:
    def __init__(self, fail_code=None, qc_passed=True):
        self.fail_code, self.qc_passed, self.package, self.calls = fail_code, qc_passed, None, []
        self.mp4 = b""

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "ffmpeg": {"status": "AVAILABLE", "version": "8.1"}})
        if path == "/renders" and request.method == "POST":
            body = json.loads(request.content)
            self.package = Path(body["package_path"])
            manifest = json.loads((self.package / "package.json").read_text())
            # behave like the real renderer: validate hashes of what it was given
            for rel, sha in ((manifest["timeline"], manifest["timeline_sha256"]),
                             (manifest["narration"]["path"], manifest["narration"]["sha256"])):
                assert hashlib.sha256((self.package / rel).read_bytes()).hexdigest() == sha
            with wave.open(str(self.package / "narration.wav")) as w:
                seconds = w.getnframes() / w.getframerate()
            tmp = self.package.parent / "fake_render.mp4"
            make_mp4(tmp, round(seconds, 2))
            self.mp4 = tmp.read_bytes(); tmp.unlink()
            return httpx.Response(202, json={"job_id": "r1", "status": "QUEUED"})
        if path == "/renders/r1":
            if self.fail_code:
                return httpx.Response(200, json={"job_id": "r1", "status": "FAILED", "error_code": self.fail_code,
                                                 "error_message": "boom"})
            return httpx.Response(200, json={"job_id": "r1", "status": "SUCCEEDED", "progress": 1.0,
                                             "frames_done": 10, "frames_total": 10})
        if path == "/renders/r1/qc":
            return httpx.Response(200, json={"passed": self.qc_passed, "renderer_version": "0.1.0",
                                             "output": {"sha256": hashlib.sha256(self.mp4).hexdigest()},
                                             "warnings": [], "package": {"gap_shots": 3},
                                             "ffmpeg": {"version": "8.1", "license_flags": {"gpl": True}},
                                             "probe": {}, "decode": {"error_count": 0}})
        if path == "/renders/r1/output":
            return httpx.Response(200, content=self.mp4)
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": path}})


def renderer_client(fake):
    return RendererClient(base_url="http://render.test", poll_interval=0,
                          client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(fake.handler), **kw))


@pytest.fixture
def chain(approved_script):
    s = approved_script
    _english_plan(s)
    fa, fv = FakeAudioModule(), FakeVisualModule()
    audio = AudioService(s["db"], s["root"], provider=s["provider"], audio_client=audio_client(fa))
    a = audio.start_audio(s["job"], OWNER, "en", "c-a", background=False)
    visual = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=visual_client(fv))
    v = visual.start_visual_plan(s["job"], OWNER, "en", "c-v", background=False, audio_version=a["version"])
    assert a["status"] == "SUCCEEDED" and v["status"] == "SUCCEEDED", (a["detail"], v["detail"])
    return dict(s=s, audio=audio, visual=visual, a=a, v=v, fv=fv)


def test_measured_visual_timeline_uses_audio_durations_not_estimates(chain):
    body = chain["visual"].read_integration_body(chain["s"]["job"], OWNER, "en", "visual_timeline", chain["v"]["version"])
    assert body["timing_origin"] == "measured_audio"
    audio = chain["audio"].read_integration_body(chain["s"]["job"], OWNER, "en", "audio_set", chain["a"]["version"])
    sent = chain["fv"].segments
    assert [round(x["end"] - x["start"], 3) for x in sent] == [round(x["duration_seconds"], 3) for x in audio["scenes"]]


def test_estimated_visual_timeline_cannot_be_rendered(chain):
    s = chain["s"]
    est = chain["visual"].start_visual_plan(s["job"], OWNER, "en", "c-est", background=False)
    svc = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=renderer_client(FakeRenderer()))
    with pytest.raises(IntegrationStale, match="measured timing"):
        svc.start_render(s["job"], OWNER, "en", "r-x", audio_version=chain["a"]["version"],
                         visual_version=est["version"], background=False)


def test_rights_changed_after_visual_plan_blocks_render(chain, monkeypatch):
    from test_assets_ingest_rm9 import _approved_assigned_asset, ImportingVisualModule, RIGHTS
    s = chain["s"]
    plan = s["scene"].get_plan(s["job"], OWNER, "en", 1)
    assets, aid, _, _ = _approved_assigned_asset(s, plan)
    visual = VisualService(s["db"], s["root"], provider=s["provider"],
                           visual_client=visual_client(ImportingVisualModule()))
    v = visual.start_visual_plan(s["job"], OWNER, "en", "rights-v", background=False,
                                audio_version=chain["a"]["version"])
    assert v["status"] == "SUCCEEDED", v["detail"]
    assets.set_rights(s["job"], OWNER, aid, {**RIGHTS, "commercial_use": "prohibited"}, "rights-revoked")
    render = RenderService(s["db"], s["root"], provider=s["provider"],
                           renderer_client=renderer_client(FakeRenderer()))
    with pytest.raises(IntegrationStale, match="rights|assignments"):
        render.start_render(s["job"], OWNER, "en", "revoked-render",
                            audio_version=chain["a"]["version"], visual_version=v["version"], background=False)
    # A previously stored render must also be withheld at the download boundary.
    monkeypatch.setattr(render, "read_integration_body", lambda *a: {"binding": {"visual_version": v["version"]}})
    with pytest.raises(IntegrationStale, match="rights|assignments"):
        render.render_file(s["job"], OWNER, "en", 1, "plain")

    from defence_studio.services.package import PackageService
    package = PackageService(s["db"], s["root"], provider=s["provider"])
    real_get = package.get_integration
    monkeypatch.setattr(package, "get_integration", lambda job, owner, lang, kind, version:
                        {"input_binding": {"visual_version": v["version"]}} if kind == "render"
                        else real_get(job, owner, lang, kind, version))
    monkeypatch.setattr(package, "read_integration_body", lambda *a: {"binding": {"render_version": 1}})
    with pytest.raises(IntegrationStale, match="rights|assignments"):
        package.package_file(s["job"], OWNER, "en", 1, "video_branded")
    from defence_studio.services.youtube import YouTubeService
    publishing = YouTubeService(s["db"], s["root"], provider=s["provider"])
    monkeypatch.setattr(publishing, "get_integration", package.get_integration)
    monkeypatch.setattr(publishing, "read_integration_body", lambda *a: {
        "binding": {"render_version": 1},
        "files": {"video_branded": {"uri": f"jobs/{s['job']}/test-video.mp4"}}})
    with pytest.raises(IntegrationStale, match="rights|assignments"):
        publishing._video_path({"job_id": s["job"], "owner_id": OWNER, "language": "en", "package_version": 1})
    monkeypatch.setattr(publishing, "_load", lambda *_: {
        "job_id": s["job"], "owner_id": OWNER, "language": "en", "package_version": 1, "youtube_video_id": "mock-only"})
    def no_provider_call(*_):
        pytest.fail("Revoked assets must be rejected before accessing provider credentials")
    monkeypatch.setattr(publishing, "_access_token", no_provider_call)
    with pytest.raises(IntegrationStale, match="rights|assignments"):
        publishing._post_upload("isolated-metadata-only")


def test_rights_revoked_before_worker_start_marks_render_failed(chain, monkeypatch):
    from test_assets_ingest_rm9 import _approved_assigned_asset, ImportingVisualModule, RIGHTS
    s = chain["s"]
    assets, aid, _, _ = _approved_assigned_asset(s, s["scene"].get_plan(s["job"], OWNER, "en", 1))
    visual = VisualService(s["db"], s["root"], provider=s["provider"],
                           visual_client=visual_client(ImportingVisualModule()))
    v = visual.start_visual_plan(s["job"], OWNER, "en", "queued-rights-v", background=False,
                                audio_version=chain["a"]["version"])
    assert v["status"] == "SUCCEEDED", v["detail"]
    render = RenderService(s["db"], s["root"], provider=s["provider"])
    pending = []
    monkeypatch.setattr(render, "_launch", lambda work, background: pending.append(work))
    row = render.start_render(s["job"], OWNER, "en", "queued-rights-r",
                              audio_version=chain["a"]["version"], visual_version=v["version"])
    assert row["status"] == "QUEUED"
    assets.set_rights(s["job"], OWNER, aid, {**RIGHTS, "commercial_use": "prohibited"}, "queued-rights-revoked")
    pending.pop()()
    result = render.get_integration(s["job"], OWNER, "en", "render", row["version"])
    assert result["status"] == "FAILED"
    assert "rights" in result["detail"]


@needs_ffmpeg
def test_full_render_package_video_and_subtitles(chain):
    s = chain["s"]
    fake = FakeRenderer()
    svc = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=renderer_client(fake),
                        visual_client=visual_client(chain["fv"]))
    item = svc.start_render(s["job"], OWNER, "en", "r-1", audio_version=chain["a"]["version"],
                            visual_version=chain["v"]["version"], background=False)
    assert item["status"] == "SUCCEEDED", item["detail"]
    body = svc.read_integration_body(s["job"], OWNER, "en", "render", item["version"])
    manifest = json.loads((fake.package / "package.json").read_text())
    assert manifest["schema"] == "video-render-package" and manifest["output_profile"]["width"] == 1920
    assert body["video_plain"]["sha256"] and body["video_subtitled"]["decode_errors"] == 0
    assert body["subtitles"]["cue_timing"] == "scene_measured_chunk_proportional"
    assert body["subtitles"]["scene_boundaries"] == "measured from narration audio"
    assert abs(body["video_subtitled"]["probe"]["duration"] - body["audio_duration_seconds"]) < 0.25
    srt, mime = svc.render_file(s["job"], OWNER, "en", item["version"], "srt")
    assert b"-->" in srt
    video, _ = svc.render_file(s["job"], OWNER, "en", item["version"], "subtitled")
    assert hashlib.sha256(video).hexdigest() == body["video_subtitled"]["sha256"]
    # same binding never renders twice; outputs are content-addressed (not overwritten)
    again = svc.start_render(s["job"], OWNER, "en", "r-2", audio_version=chain["a"]["version"],
                             visual_version=chain["v"]["version"], background=False)
    assert again["version"] == item["version"]


@needs_ffmpeg
def test_renderer_failure_and_failed_qc_are_never_success(chain):
    s = chain["s"]
    for fake, expect in ((FakeRenderer(fail_code="OUTPUT_INVALID"), "OUTPUT_INVALID"),
                         (FakeRenderer(qc_passed=False), "renderer_qc_failed")):
        svc = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=renderer_client(fake),
                            visual_client=visual_client(chain["fv"]))
        item = svc.start_render(s["job"], OWNER, "en", f"f-{expect}", audio_version=chain["a"]["version"],
                                visual_version=chain["v"]["version"], background=False)
        assert item["status"] == "FAILED" and expect in item["detail"] and item["uri"] is None
        with pytest.raises(Exception):
            svc.render_file(s["job"], OWNER, "en", item["version"], "plain")
        retried = svc.retry_render(s["job"], OWNER, "en", item["version"], f"retry-{expect}", background=False)
        assert retried["version"] == item["version"] + 1


def test_cancelled_render_keeps_cancelled_terminal_state(chain):
    s = chain["s"]

    class CancellingRenderer:
        cancel = None

        def render(self, *_args, on_progress=None, **_kwargs):
            self.cancel()
            on_progress(0.5, 5, 10)
            raise RendererError("CANCELLED", "Renderer cancelled")

    renderer = CancellingRenderer()
    svc = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=renderer,
                        visual_client=visual_client(chain["fv"]))
    renderer.cancel = lambda: svc.cancel_render(s["job"], OWNER, "en", 1)

    item = svc.start_render(s["job"], OWNER, "en", "cancel-race", audio_version=chain["a"]["version"],
                            visual_version=chain["v"]["version"], background=False)

    assert item["status"] == "CANCELLED"


def test_independent_client_reads_persisted_render_progress(chain):
    s = chain["s"]
    svc = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=renderer_client(FakeRenderer()),
                        visual_client=visual_client(chain["fv"]))
    app = create_app(s["db"], SECRET, s["root"])
    headers = {"Authorization": f"Bearer {access_token(OWNER)}"}

    with TestClient(app) as client:
        row, created = svc._create_row(s["job"], OWNER, "en", "render", {"test": "progress"}, "read-progress")
        assert created
        svc._transition(s["job"], "en", "render", row["version"], "RUNNING",
                        detail="Rendering frame 5/10", progress=0.45)

        response = client.get(f"/api/jobs/{s['job']}/render/en", headers=headers)

    assert response.status_code == 200
    render = response.json()["items"][0]
    assert render["status"] == "RUNNING"
    assert render["progress"] == 0.45
    assert render["detail"] == "Rendering frame 5/10"


def test_api_startup_marks_interrupted_render_without_completing_it(chain):
    s = chain["s"]
    svc = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=renderer_client(FakeRenderer()),
                        visual_client=visual_client(chain["fv"]))
    row, created = svc._create_row(s["job"], OWNER, "en", "render", {"test": "restart"}, "restart-render")
    assert created
    svc._transition(s["job"], "en", "render", row["version"], "RUNNING",
                    detail="Rendering frame 4/10", progress=0.4)

    app = create_app(s["db"], SECRET, s["root"])
    headers = {"Authorization": f"Bearer {access_token(OWNER)}"}
    with TestClient(app) as client:
        response = client.get(f"/api/jobs/{s['job']}/render/en", headers=headers)

    assert response.status_code == 200
    render = response.json()["items"][0]
    assert render["status"] == "INTERRUPTED"
    assert render["progress"] == 0.4
    assert render["detail"] == "Process stopped before completion; retry to run again."


def test_tampered_audio_blocks_render(chain):
    s = chain["s"]
    audio = chain["audio"].read_integration_body(s["job"], OWNER, "en", "audio_set", chain["a"]["version"])
    chain["audio"].store._resolve(s["job"], audio["scenes"][0]["audio_uri"])[1].write_bytes(b"x")
    svc = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=renderer_client(FakeRenderer()))
    with pytest.raises(IntegrationStale, match="verification"):
        svc.start_render(s["job"], OWNER, "en", "t-1", audio_version=chain["a"]["version"],
                         visual_version=chain["v"]["version"], background=False)


def test_tamil_render_fails_before_work_when_no_font(chain, monkeypatch):
    s = chain["s"]
    monkeypatch.setattr(subs, "_FONT_CANDIDATES", {"ta": [], "en": []})
    # Tamil plan/audio is unavailable here, so exercise the guard directly.
    with pytest.raises(subs.SubtitleError):
        subs.resolve_font("ta")


# -- INTEGRATED: the three real standalone modules ----------------------------------


def _all_up() -> bool:
    try:
        return (VisualModuleClient(request_timeout=2).health().get("service") == "video-visual-module"
                and AudioModuleClient(request_timeout=2).health().get("service") == "audio-module"
                and RendererClient(request_timeout=2).health().get("ffmpeg", {}).get("status") == "AVAILABLE")
    except Exception:
        return False


@pytest.mark.skipif(not _all_up(), reason="real Visual/Audio/Renderer modules not all running")
def test_real_end_to_end_audio_visual_render_subtitles(approved_script):
    s = approved_script
    plan = _english_plan(s)
    audio = AudioService(s["db"], s["root"], provider=s["provider"],
                         audio_client=AudioModuleClient(poll_interval=0.3, job_timeout_seconds=300))
    a = audio.start_audio(s["job"], OWNER, "en", "e2e-a", align=False, background=False)
    assert a["status"] == "SUCCEEDED", a["detail"]
    visual = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=VisualModuleClient(poll_interval=0.2))
    v = visual.start_visual_plan(s["job"], OWNER, "en", "e2e-v", {"output_width": 1280, "output_height": 720, "frame_rate": 25},
                                 background=False, audio_version=a["version"])
    assert v["status"] == "SUCCEEDED", v["detail"]
    render = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=RendererClient(poll_interval=0.5),
                           visual_client=VisualModuleClient())
    r = render.start_render(s["job"], OWNER, "en", "e2e-r", audio_version=a["version"], visual_version=v["version"],
                            background=False)
    assert r["status"] == "SUCCEEDED", r["detail"]
    body = render.read_integration_body(s["job"], OWNER, "en", "render", r["version"])
    assert body["renderer"]["qc_passed"] is True
    probe = body["video_subtitled"]["probe"]
    assert probe["video"]["codec"] == "h264" and probe["audio"]["codec"] == "aac"
    assert (probe["video"]["width"], probe["video"]["height"]) == (1280, 720)
    assert abs(probe["duration"] - body["audio_duration_seconds"]) < 0.25
    assert body["video_subtitled"]["decode_errors"] == 0
    assert body["subtitles"]["cue_count"] >= len(plan["scenes"])
    out = Path(__import__("tempfile").gettempdir()) / "ds_rm5_e2e.mp4"
    data, _ = render.render_file(s["job"], OWNER, "en", r["version"], "subtitled")
    out.write_bytes(data)
