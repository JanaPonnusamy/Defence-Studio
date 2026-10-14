"""RM-6: automated QC, deliberately invalid fixtures, and exact-version approval."""
import io
import json
import subprocess
import wave
from pathlib import Path

import httpx
import pytest
from PIL import Image

from defence_studio.integrations import subtitles as subs
from defence_studio.services.integration import IntegrationStale, IntegrationValidationError
from defence_studio.services.qc import QcService
from defence_studio.services.render import RenderService
from test_render_rm5 import (  # noqa: F401
    FakeRenderer, chain, renderer_client, needs_ffmpeg, make_mp4,
)
from test_visual_rm3 import FakeVisualModule, _client as visual_client  # noqa: F401
from test_scene_service_m1 import OWNER, approved_script  # noqa: F401

pytestmark = needs_ffmpeg


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (30, 90, 160)).save(buf, "PNG")
    return buf.getvalue()


class ReadyVisualModule(FakeVisualModule):
    """Timeline whose shots are READY (rights-cleared asset), not GAPs."""

    def handler(self, request):
        path = request.url.path
        if path == "/visual-plans/vp1/timeline":
            segs = self.segments
            shots = [{"shot_id": f"sh{i}", "order": i, "start": s["start"], "end": s["end"], "status": "READY",
                      "gap_code": None, "asset_id": "asset1", "visual_type": "SOURCE_PHOTO", "motion": {"type": "STATIC"},
                      "overlays": [], "narration_anchor": {"segment_ids": [s["segment_id"]], "text": s["text"]}}
                     for i, s in enumerate(segs)]
            return httpx.Response(200, json={"schema": "video-visual-timeline", "version": "1.1", "duration": segs[-1]["end"],
                                             "shots": shots, "attributions": [],
                                             "assets": {"asset1": {"asset_id": "asset1", "mime_type": "image/png",
                                                                  "rights_status": "LICENSED", "provenance_class": "ORIGINAL_SOURCE"}}})
        if path == "/assets/asset1/file":
            return httpx.Response(200, content=png_bytes())
        return super().handler(request)


class CustomVideoRenderer(FakeRenderer):
    """Fake renderer that outputs a specific (possibly defective) MP4."""

    def __init__(self, builder, **kw):
        super().__init__(**kw)
        self.builder = builder

    def handler(self, request):
        response = super().handler(request)
        if request.url.path == "/renders" and request.method == "POST":
            with wave.open(str(self.package / "narration.wav")) as w:
                seconds = w.getnframes() / w.getframerate()
            tmp = self.package.parent / "custom.mp4"
            self.builder(tmp, round(seconds, 2))
            self.mp4 = tmp.read_bytes(); tmp.unlink()
        return response


def tone_mp4(path: Path, seconds: float, *, black=False, gain_db=0, size="1280x720"):
    ff = subs.ffmpeg_binary("ffmpeg")
    color = "black" if black else "0x3a5f8a"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={seconds}:r=25",
                    "-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=48000:duration={seconds}",
                    "-af", f"volume={gain_db}dB" if gain_db else "anull", "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, timeout=120)


def _render_and_qc(chain, builder=None, *, ready=True, requirements=None, key="q"):
    s = chain["s"]
    fv = ReadyVisualModule() if ready else chain["fv"]
    audio = chain["audio"]
    from defence_studio.services.visual import VisualService
    visual = VisualService(s["db"], s["root"], provider=s["provider"], visual_client=visual_client(fv))
    v = visual.start_visual_plan(s["job"], OWNER, "en", f"qv-{key}", {"output_width": 1280, "output_height": 720},
                                 background=False, audio_version=chain["a"]["version"])
    assert v["status"] == "SUCCEEDED", v["detail"]
    fake = CustomVideoRenderer(builder or (lambda p, d: tone_mp4(p, d, gain_db=-20)))
    render = RenderService(s["db"], s["root"], provider=s["provider"], renderer_client=renderer_client(fake),
                           visual_client=visual_client(fv))
    r = render.start_render(s["job"], OWNER, "en", f"qr-{key}", audio_version=chain["a"]["version"],
                            visual_version=v["version"], background=False)
    assert r["status"] == "SUCCEEDED", r["detail"]
    qc = QcService(s["db"], s["root"], provider=s["provider"])
    q = qc.start_qc(s["job"], OWNER, "en", r["version"], f"qq-{key}", requirements=requirements, background=False)
    assert q["status"] == "SUCCEEDED", q["detail"]
    return qc, render, r, q


def _by_id(report):
    return {c["id"]: c for c in report["checks"]}


def test_gap_timeline_fails_qc_and_cannot_be_approved(chain):
    qc, render, r, q = _render_and_qc(chain, ready=False, key="gap")
    report = qc.read_report(chain["s"]["job"], OWNER, "en", q["version"])
    checks = _by_id(report)
    assert checks["VISUAL_COVERAGE"]["status"] == "FAIL" and checks["VISUAL_COVERAGE"]["severity"] == "BLOCKER"
    assert report["summary"]["passed"] is False and "assets" in report["summary"]["rerun_stages"]
    assert checks["FULL_DECODE"]["status"] == "PASS" and checks["CONTAINER_STREAMS"]["status"] == "PASS"
    with pytest.raises(IntegrationValidationError, match="blocking failures"):
        qc.approve_render(chain["s"]["job"], OWNER, "en", q["version"], "looks fine", [], "ap-gap")
    rejected = qc.reject_render(chain["s"]["job"], OWNER, "en", q["version"], "gaps remain", "rj-gap")
    assert rejected["status"] == "REJECTED"
    with pytest.raises(IntegrationStale):
        qc.approved_render(chain["s"]["job"], OWNER, "en")
    assert "VISUAL_COVERAGE" in qc.read_report_markdown(chain["s"]["job"], OWNER, "en", q["version"])


def test_valid_render_passes_and_approval_is_bound_to_exact_versions(chain):
    qc, render, r, q = _render_and_qc(chain, key="ok")
    job = chain["s"]["job"]
    report = qc.read_report(job, OWNER, "en", q["version"])
    checks = _by_id(report)
    assert checks["VISUAL_COVERAGE"]["status"] == "PASS" and checks["ASSET_RIGHTS"]["status"] == "PASS"
    assert checks["OUTPUT_INTEGRITY"]["status"] == "PASS" and checks["DURATION_MATCH"]["status"] == "PASS"
    assert checks["SUBTITLE_SYNC"]["status"] == "PASS" and checks["SUBTITLE_GLYPHS"]["status"] == "PASS"
    assert checks["AUDIO_CLIPPING"]["status"] == "PASS" and checks["BLACK_FRAMES"]["status"] == "PASS"
    assert checks["BRANDING_ASSETS"]["status"] == "NOT_CHECKED"
    assert report["measurements"]["analysis"]["integrated_lufs"] is not None
    assert report["summary"]["passed"] is True
    warns = sorted(c["id"] for c in report["checks"] if c["status"] == "WARN")
    if warns:
        with pytest.raises(IntegrationValidationError, match="Acknowledge every warning"):
            qc.approve_render(job, OWNER, "en", q["version"], "ok", [], "ap-1")
    approval = qc.approve_render(job, OWNER, "en", q["version"], "Reviewed the render", warns, "ap-2")
    assert approval["status"] == "APPROVED"
    gate = qc.approved_render(job, OWNER, "en")
    assert gate["qc_version"] == q["version"] and gate["render_version"] == r["version"]
    assert gate["video_sha256"] == report["render"]["video_sha256"]
    # a newer QC report invalidates the approval (it applied to the exact report)
    q2 = qc.start_qc(job, OWNER, "en", r["version"], "qq-again", requirements={"min_seconds": 1.0}, background=False)
    assert q2["version"] == q["version"] + 1
    with pytest.raises(IntegrationStale, match="newer QC"):
        qc.approved_render(job, OWNER, "en")
    with pytest.raises(IntegrationStale, match="newer QC"):
        qc.approve_render(job, OWNER, "en", q["version"], "late", warns, "ap-3")


def test_black_video_clipped_audio_and_wrong_resolution_are_detected(chain):
    qc, _, _, q = _render_and_qc(chain, lambda p, d: tone_mp4(p, d, black=True, gain_db=40, size="640x360"), key="bad")
    checks = _by_id(qc.read_report(chain["s"]["job"], OWNER, "en", q["version"]))
    assert checks["BLACK_FRAMES"]["status"] == "FAIL"
    assert checks["AUDIO_CLIPPING"]["status"] == "FAIL"
    assert checks["CONTAINER_STREAMS"]["status"] == "FAIL" and "640x360" in checks["CONTAINER_STREAMS"]["detail"]


def test_tampered_stored_video_fails_integrity(chain):
    qc, render, r, _ = _render_and_qc(chain, key="tamper")
    s = chain["s"]
    body = render.read_integration_body(s["job"], OWNER, "en", "render", r["version"])
    render.store._resolve(s["job"], body["video_subtitled"]["uri"])[1].write_bytes(b"corrupt")
    q = qc.start_qc(s["job"], OWNER, "en", r["version"], "qq-tamper-2", requirements={"min_seconds": 2.0}, background=False)
    report = qc.read_report(s["job"], OWNER, "en", q["version"])
    assert _by_id(report)["OUTPUT_INTEGRITY"]["status"] == "FAIL" and report["summary"]["passed"] is False


def test_duration_limits_and_unknown_requirements(chain):
    qc, render, r, q = _render_and_qc(chain, requirements={"max_seconds": 2.0}, key="lim")
    assert _by_id(qc.read_report(chain["s"]["job"], OWNER, "en", q["version"]))["DURATION_LIMITS"]["status"] == "FAIL"
    with pytest.raises(IntegrationValidationError, match="Unknown QC requirement"):
        qc.start_qc(chain["s"]["job"], OWNER, "en", r["version"], "qq-unk", requirements={"nope": 1}, background=False)


def test_qc_requires_a_succeeded_render(chain):
    s = chain["s"]
    qc = QcService(s["db"], s["root"], provider=s["provider"])
    with pytest.raises(Exception):
        qc.start_qc(s["job"], OWNER, "en", 99, "qq-none", background=False)
