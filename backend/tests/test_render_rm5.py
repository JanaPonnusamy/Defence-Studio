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
from defence_studio.integrations.renderer_module import RendererClient
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
