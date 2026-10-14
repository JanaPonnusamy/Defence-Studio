"""Render a validated package to MP4: Pillow frames -> ffmpeg rgb24 pipe -> validation -> qc_report.json."""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from renderer import __version__
from renderer.compose import Composer
from renderer.config import Settings
from renderer.errors import Cancelled, RendererError
from renderer.layout import FramePlan, WarnBag, build_frame_plan, locate, shot_progress
from renderer.media import Tools, _NOWIN, ffmpeg_encode_cmd, peak_memory_mb, validate_output
from renderer.package import RenderPackage, sha256_file

PARTIAL_NAME = "output.partial.mp4"
OUTPUT_NAME = "output.mp4"
QC_NAME = "qc_report.json"
LOG_NAME = "ffmpeg.log"


class Hooks:
    """Callbacks the job runner supplies; defaults make render_package usable standalone."""

    def __init__(self, on_progress: Callable[[int, int], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None,
                 on_pid: Callable[[int | None], None] | None = None):
        self.on_progress = on_progress or (lambda d, t: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.on_pid = on_pid or (lambda pid: None)


def plan_for(pkg: RenderPackage, settings: Settings) -> FramePlan:
    return build_frame_plan(pkg.timeline, pkg.narration.duration, pkg.profile.fps, settings.transition_seconds,
                            settings.duration_warn_s, settings.duration_reject_s)


def _tail(path: Path, n: int = 600) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-n:].strip()
    except OSError:
        return ""


def _remove(path: Path) -> None:
    for _ in range(20):  # an exiting ffmpeg may still hold the file for a moment on Windows
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.25)


def render_package(pkg: RenderPackage, tools: Tools, settings: Settings, out_dir: Path, hooks: Hooks | None = None,
                   meta: dict | None = None) -> dict:
    """Render `pkg` into out_dir/output.mp4 (never overwrites) and return the qc report dict.

    Raises Cancelled (partial file removed), RendererError (DURATION_MISMATCH, ENCODER_FAILED,
    OUTPUT_INVALID). The final name is only created after every output check passed.
    """
    hooks = hooks or Hooks()
    t0 = time.perf_counter()
    out_dir.mkdir(parents=True, exist_ok=True)
    final, partial, log = out_dir / OUTPUT_NAME, out_dir / PARTIAL_NAME, out_dir / LOG_NAME
    if final.exists():
        raise RendererError("CONFLICT", f"{final} already exists; outputs are versioned and never overwritten")
    _remove(partial)

    prof = pkg.profile
    plan = plan_for(pkg, settings)  # may raise DURATION_MISMATCH before anything is started
    bag = WarnBag()
    bag.extend(pkg.warnings)
    bag.extend(plan.warnings)
    composer = Composer(pkg.assets, prof.width, prof.height, settings.font_path, bag)
    total = plan.total_frames
    cmd = ffmpeg_encode_cmd(tools, settings, width=prof.width, height=prof.height, fps=prof.fps,
                            wav=pkg.narration_path, out=partial)
    logf = open(log, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=logf,
                            bufsize=0, **_NOWIN)
    hooks.on_pid(proc.pid)
    ff_peak = None
    t_frames = time.perf_counter()
    try:
        k, last_k = 0, -1
        static_bytes: tuple[int, bytes] | None = None
        for i in range(total):
            if i % 8 == 0 and hooks.should_cancel():
                raise Cancelled()
            k = locate(plan, i, k)
            sp = plan.shots[k]
            if k != last_k:
                composer.drop_static({k - 1, k})
                last_k = k
            in_transition = sp.transition_frames and i < sp.frame_start + sp.transition_frames
            if in_transition:
                a = composer.shot_image(plan.shots[k - 1], 1.0)
                b = composer.shot_image(sp, shot_progress(sp, i, prof.fps))
                alpha = (i - sp.frame_start + 1) / (sp.transition_frames + 1)
                data = composer.crossfade(a, b, alpha).tobytes()
            elif composer.is_static(sp):
                if static_bytes is None or static_bytes[0] != k:
                    static_bytes = (k, composer.shot_image(sp, 0.0).tobytes())
                data = static_bytes[1]
            else:
                data = composer.shot_image(sp, shot_progress(sp, i, prof.fps)).tobytes()
            try:
                proc.stdin.write(data)
            except (BrokenPipeError, OSError):
                proc.wait()
                raise RendererError("ENCODER_FAILED", f"ffmpeg stopped accepting frames at {i}/{total}: "
                                                      f"{_tail(log)}") from None
            if (i + 1) % 25 == 0 or i + 1 == total:
                hooks.on_progress(i + 1, total)
        ff_peak = peak_memory_mb(proc.pid)
        proc.stdin.close()
        try:
            rc = proc.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RendererError("ENCODER_FAILED", "ffmpeg did not finish after the last frame") from None
        if rc != 0:
            raise RendererError("ENCODER_FAILED", f"ffmpeg exited with code {rc}: {_tail(log)}")
    except BaseException:
        if proc.poll() is None:
            proc.kill()  # only the process we started
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        logf.close()
        _remove(partial)
        raise
    finally:
        hooks.on_pid(None)
        if not logf.closed:
            logf.close()
    t_encoded = time.perf_counter()

    # ---- output validation (the final name only appears once everything passed)
    result = validate_output(tools, partial, width=prof.width, height=prof.height, fps=prof.fps,
                             audio_duration=pkg.narration.duration, expected_frames=total,
                             tolerance_s=settings.output_duration_tolerance_s)
    t_valid = time.perf_counter()
    report = {
        "schema": "video-render-qc", "version": "1",
        "renderer_version": __version__,
        "job_id": (meta or {}).get("job_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": result["ok"],
        "checks": result["checks"],
        "warnings": bag.as_list(),
        "output": {"path": str(final), "sha256": None, "size_bytes": partial.stat().st_size},
        "requested": {**prof.as_dict(), "frames": total, "audio_duration": round(pkg.narration.duration, 4),
                      "timeline_end": plan.timeline_end, "timeline_start": plan.timeline_start},
        "probe": result["probe"], "decode": result["decode"],
        "package": {"path": str(pkg.root), "manifest_sha256": pkg.manifest_sha256,
                    "shots": len(pkg.timeline["shots"]), "assets": len(pkg.assets),
                    "gap_shots": sum(1 for s in pkg.timeline["shots"] if s["status"] == "GAP"),
                    "visual_plan_id": pkg.timeline.get("visual_plan_id")},
        "ffmpeg": {**tools.info(), "configuration": tools.configuration},
        "encoder": {"preset": settings.x264_preset, "crf": settings.x264_crf, "audio_bitrate": settings.audio_bitrate},
        "timings_s": {"frame_composition_and_encode": round(t_encoded - t_frames, 2),
                      "output_validation": round(t_valid - t_encoded, 2),
                      "total": round(time.perf_counter() - t0, 2)},
        "memory_peak_mb": {"renderer_process": peak_memory_mb(), "ffmpeg_process": ff_peak},
    }
    if not result["ok"]:
        failed = [c for c in result["checks"] if not c["ok"]]
        (out_dir / QC_NAME).write_text(json.dumps(report, indent=2), encoding="utf-8")
        _remove(partial)
        raise RendererError("OUTPUT_INVALID", "output failed validation: " +
                            "; ".join(f"{c['name']} ({c['detail']})" for c in failed))
    report["output"]["sha256"] = sha256_file(partial)
    os.replace(partial, final)
    (out_dir / QC_NAME).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
