"""REAL renders: Pillow frames -> real ffmpeg -> ffprobe + full decode. Nothing mocked."""
from __future__ import annotations

import io
import subprocess
import time

import pytest
from PIL import Image

from renderer.config import Settings
from renderer.errors import Cancelled, RendererError
from renderer.media import _NOWIN, process_cmdline
from renderer.package import load_package, sha256_file
from renderer.render import Hooks, render_package
from renderer.synthetic import small_package
from tests.conftest import insert_job, rewrite_timeline, wait_for

pytestmark = pytest.mark.ffmpeg


def frame_at(tools, mp4, t: float) -> Image.Image:
    r = subprocess.run([tools.ffmpeg, "-v", "error", "-ss", str(t), "-i", str(mp4), "-frames:v", "1", "-f", "image2pipe",
                        "-vcodec", "png", "-"], capture_output=True, **_NOWIN)
    assert r.returncode == 0 and r.stdout
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def test_small_package_renders_and_passes_all_checks(pkg_dir, tools, settings, tmp_path):
    pkg = load_package(pkg_dir)
    out = tmp_path / "out"
    rep = render_package(pkg, tools, settings, out, meta={"job_id": "t1"})
    assert rep["passed"] and all(c["ok"] for c in rep["checks"])
    names = {c["name"] for c in rep["checks"]}
    assert {"container_mp4", "video_stream_h264", "audio_stream_aac", "resolution", "frame_rate",
            "duration_within_tolerance", "full_decode_no_errors", "frame_count"} <= names
    mp4 = out / "output.mp4"
    assert mp4.is_file() and not (out / "output.partial.mp4").exists()
    assert rep["output"]["sha256"] == sha256_file(mp4)
    assert rep["decode"]["frames"] == round(20.0 * 12) == 240 and rep["decode"]["error_count"] == 0
    assert rep["probe"]["video"]["codec_name"] == "h264" and rep["probe"]["audio"]["codec_name"] == "aac"
    assert (out / "qc_report.json").is_file() and rep["ffmpeg"]["license_flags"]["summary"]
    assert rep["timings_s"]["total"] > 0 and rep["memory_peak_mb"]["renderer_process"]
    assert abs(float(rep["probe"]["format"]["duration"]) - 20.0) <= 0.1


def test_gap_shot_is_an_explicit_card_never_an_image(pkg_dir, tools, settings, tmp_path):
    out = tmp_path / "out"
    render_package(load_package(pkg_dir), tools, settings, out)
    gap = frame_at(tools, out / "output.mp4", 12.5)  # shot 2 (10-15 s) is the GAP
    w, h = gap.size
    for x, y in [(w // 5, h // 5), (4 * w // 5, h // 5), (w // 5, 4 * h // 5)]:
        r, g, b = gap.getpixel((x, y))
        assert abs(r - 28) < 14 and abs(g - 28) < 14 and abs(b - 34) < 14, (x, y, (r, g, b))
    ready = frame_at(tools, out / "output.mp4", 2.0)  # a real image frame is far from flat
    assert len({ready.getpixel((x, y)) for x in range(0, w, 16) for y in range(0, h, 16)}) > 50


def test_outputs_are_never_overwritten(pkg_dir, tools, settings, tmp_path):
    out = tmp_path / "out"
    render_package(load_package(pkg_dir), tools, settings, out)
    before = sha256_file(out / "output.mp4")
    with pytest.raises(RendererError) as e:
        render_package(load_package(pkg_dir), tools, settings, out)
    assert e.value.code == "CONFLICT" and sha256_file(out / "output.mp4") == before


def test_duration_mismatch_over_5s_is_rejected_before_encoding(tmp_path, tools, settings):
    root = small_package(tmp_path / "p", wav_seconds=26.0, profile={"width": 320, "height": 180, "fps": 12})
    with pytest.raises(RendererError) as e:
        render_package(load_package(root), tools, settings, tmp_path / "out")
    assert e.value.code == "DURATION_MISMATCH" and "6.000s" in e.value.message
    assert not list((tmp_path / "out").glob("*.mp4"))


def test_duration_mismatch_over_half_second_warns_and_still_matches_audio(tmp_path, tools, settings):
    root = small_package(tmp_path / "p", wav_seconds=20.8, profile={"width": 320, "height": 180, "fps": 12})
    rep = render_package(load_package(root), tools, settings, tmp_path / "out")
    assert rep["passed"] and any(w["code"] == "DURATION_MISMATCH_WARNING" for w in rep["warnings"])
    assert abs(float(rep["probe"]["format"]["duration"]) - 20.8) <= 0.1  # video covers the narration


def test_warnings_for_unsupported_motion_transition_and_script(tmp_path, tools, settings):
    root = small_package(tmp_path / "p", profile={"width": 320, "height": 180, "fps": 12})

    def edit(t):
        t["shots"][0]["motion"] = {"type": "PARALLAX"}
        t["shots"][3]["transition"] = "WIPE"
        t["shots"][1]["overlays"].append({"type": "source_caption", "text": "ஆதாரம்: செயற்கை"})
    rewrite_timeline(root, edit)
    rep = render_package(load_package(root), tools, settings, tmp_path / "out")
    codes = {w["code"] for w in rep["warnings"]}
    assert rep["passed"] and {"MOTION_FALLBACK", "TRANSITION_UNSUPPORTED", "OVERLAY_NON_LATIN"} <= codes


def test_encoder_failure_is_reported_and_leaves_no_output(pkg_dir, tools, tmp_path):
    bad = Settings(storage_root=tmp_path / "s", x264_preset="not-a-preset")
    with pytest.raises(RendererError) as e:
        render_package(load_package(pkg_dir), tools, bad, tmp_path / "out")
    assert e.value.code == "ENCODER_FAILED"
    assert not list((tmp_path / "out").glob("*.mp4"))


def test_cancel_hook_stops_and_kills_only_its_ffmpeg(pkg_dir, tools, settings, tmp_path):
    seen, n = {}, {"calls": 0}

    def cancel_after_some():
        n["calls"] += 1
        return n["calls"] > 5

    hooks = Hooks(on_pid=lambda pid: seen.setdefault("pid", pid) if pid else None, should_cancel=cancel_after_some)
    with pytest.raises(Cancelled):
        render_package(load_package(pkg_dir), tools, settings, tmp_path / "out", hooks)
    assert seen["pid"] and process_cmdline(seen["pid"]) is None  # ffmpeg is gone
    assert not list((tmp_path / "out").glob("output*.mp4"))


def test_cancel_running_job_end_to_end(tmp_path, tools, settings, service):
    root = small_package(tmp_path / "p", profile={"width": 640, "height": 360, "fps": 25})
    job, _ = service.create(str(root))
    deadline = time.time() + 120
    while time.time() < deadline:
        j = service.get(job.job_id)
        if j.status == "RUNNING" and j.frames_done >= 50 and j.ffmpeg_pid:
            break
        time.sleep(0.1)
    else:
        pytest.fail("job never started rendering")
    pid = j.ffmpeg_pid
    service.cancel(job.job_id)
    done = wait_for(service, job.job_id, {"CANCELLED", "SUCCEEDED", "FAILED"}, 60)
    assert done.status == "CANCELLED" and done.error_code == "JOB_CANCELLED" and done.ffmpeg_pid is None
    assert process_cmdline(pid) is None
    d = service.job_dir(job.job_id)
    assert not (d / "output.mp4").exists() and not (d / "output.partial.mp4").exists()
    assert done.frames_done < done.frames_total  # stopped early


def test_job_end_to_end_succeeds_with_progress_and_qc(pkg_dir, service):
    job, _ = service.create(str(pkg_dir))
    j = wait_for(service, job.job_id, {"SUCCEEDED", "FAILED"}, 240)
    assert j.status == "SUCCEEDED", (j.error_code, j.error_message)
    assert j.progress == 1.0 and j.frames_done == j.frames_total == 240
    assert j.output_path.endswith("output.mp4") and j.output_sha256 == sha256_file(j.output_path)
    assert j.output_path != service.get(job.job_id).package_path
