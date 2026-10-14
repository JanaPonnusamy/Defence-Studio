"""ffmpeg / ffprobe discovery, version + licence flags, output probing and full-decode validation.

ffmpeg is supplied by the user and never bundled (see docs/KNOWN_LIMITATIONS.md, licence note).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from renderer.config import Settings
from renderer.errors import RendererError

NOT_AVAILABLE = "NOT_AVAILABLE"
_NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW


@dataclass(frozen=True)
class Tools:
    ffmpeg: str
    ffprobe: str
    version: str
    configuration: str
    license_flags: dict

    def info(self) -> dict:
        return {"ffmpeg": self.ffmpeg, "ffprobe": self.ffprobe, "version": self.version,
                "license_flags": self.license_flags}


def _run(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                          errors="replace", **_NOWIN)


def licence_flags(configuration: str) -> dict:
    cfg = configuration.split()
    return {
        "gpl": "--enable-gpl" in cfg, "version3": "--enable-version3" in cfg,
        "nonfree": "--enable-nonfree" in cfg, "libx264": "--enable-libx264" in cfg,
        "libfdk_aac": "--enable-libfdk-aac" in cfg,
        "summary": ("NONFREE (not redistributable)" if "--enable-nonfree" in cfg else
                    "GPL" + ("v3" if "--enable-version3" in cfg else "") if "--enable-gpl" in cfg else
                    "LGPL/other"),
    }


def locate_tools(settings: Settings) -> Tools | None:
    """Return Tools, or None when ffmpeg/ffprobe cannot be found or run (never fakes availability)."""
    ff = settings.ffmpeg or shutil.which("ffmpeg")
    fp = settings.ffprobe or (str(Path(ff).with_name("ffprobe" + Path(ff).suffix)) if ff and settings.ffmpeg else
                              shutil.which("ffprobe"))
    if not ff or not fp:
        return None
    try:
        out = _run([ff, "-hide_banner", "-version"]).stdout
        _run([fp, "-hide_banner", "-version"])
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"ffmpeg version (\S+)", out)
    c = re.search(r"configuration:\s*(.*)", out)
    cfg = c.group(1) if c else ""
    return Tools(ff, fp, m.group(1) if m else "unknown", cfg, licence_flags(cfg))


def require_tools(settings: Settings) -> Tools:
    t = locate_tools(settings)
    if t is None:
        raise RendererError("FFMPEG_NOT_AVAILABLE",
                            "ffmpeg/ffprobe not found. Install ffmpeg yourself and put it on PATH "
                            "(or set RENDERER_FFMPEG / RENDERER_FFPROBE). Nothing was rendered.")
    return t


def fps_arg(fps: float) -> str:
    f = Fraction(fps).limit_denominator(1001)
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def ffmpeg_encode_cmd(tools: Tools, s: Settings, *, width: int, height: int, fps: float, wav: Path,
                      out: Path) -> list[str]:
    return [
        tools.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-framerate", fps_arg(fps), "-i", "pipe:0",
        "-i", str(wav),
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", s.x264_preset, "-crf", str(s.x264_crf),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-r", fps_arg(fps),
        "-c:a", "aac", "-ar", "48000", "-b:a", s.audio_bitrate,
        "-movflags", "+faststart", "-f", "mp4", str(out),
    ]


def probe(tools: Tools, path: Path) -> dict:
    r = _run([tools.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], 120)
    if r.returncode != 0:
        raise RendererError("OUTPUT_INVALID", f"ffprobe failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def decode_check(tools: Tools, path: Path, timeout: float = 3600) -> dict:
    """Full decode to the null muxer. Returns {errors: [...], frames: int|None}."""
    r = subprocess.run([tools.ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1",
                        "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", **_NOWIN)
    frames = None
    for line in r.stdout.splitlines():
        if line.startswith("frame="):
            try:
                frames = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    errors = [ln for ln in r.stderr.splitlines() if ln.strip()]
    if r.returncode != 0:
        errors.append(f"ffmpeg exit code {r.returncode}")
    return {"errors": errors, "frames": frames}


def _fr(s: str | None) -> float | None:
    try:
        n, d = (s or "").split("/")
        return float(n) / float(d) if float(d) else None
    except ValueError:
        return None


def validate_output(tools: Tools, path: Path, *, width: int, height: int, fps: float, audio_duration: float,
                    expected_frames: int, tolerance_s: float) -> dict:
    """ffprobe + full decode checks. Returns {checks: [...], ok: bool, probe: {...}, decode: {...}}."""
    checks: list[dict] = []

    def chk(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    chk("file_exists_nonempty", path.is_file() and path.stat().st_size > 0, f"{path.stat().st_size if path.is_file() else 0} bytes")
    info = probe(tools, path)
    fmt = info.get("format", {})
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    chk("container_mp4", "mp4" in str(fmt.get("format_name", "")).split(","), f"format_name={fmt.get('format_name')}")
    chk("video_stream_h264", bool(v) and v.get("codec_name") == "h264", f"codec={v and v.get('codec_name')}")
    chk("pixel_format_yuv420p", bool(v) and v.get("pix_fmt") == "yuv420p", f"pix_fmt={v and v.get('pix_fmt')}")
    chk("audio_stream_aac", bool(a) and a.get("codec_name") == "aac", f"codec={a and a.get('codec_name')}")
    chk("audio_sample_rate_48k", bool(a) and str(a.get("sample_rate")) == "48000", f"sample_rate={a and a.get('sample_rate')}")
    chk("resolution", bool(v) and (v.get("width"), v.get("height")) == (width, height),
        f"{v and v.get('width')}x{v and v.get('height')} (requested {width}x{height})")
    afps = _fr(v.get("avg_frame_rate")) if v else None
    chk("frame_rate", afps is not None and abs(afps - fps) < 0.01, f"avg_frame_rate={afps} (requested {fps:g})")
    try:
        dur = float(fmt.get("duration"))
    except (TypeError, ValueError):
        dur = float("nan")
    chk("duration_within_tolerance", abs(dur - audio_duration) <= tolerance_s,
        f"output {dur:.3f}s vs narration {audio_duration:.3f}s (tolerance {tolerance_s}s)")
    dec = decode_check(tools, path)
    chk("full_decode_no_errors", not dec["errors"], "no decoder errors" if not dec["errors"] else "; ".join(dec["errors"][:3]))
    fc = dec["frames"]
    chk("frame_count", fc is not None and abs(fc - expected_frames) <= 1,
        f"decoded {fc} frames, expected {expected_frames} (+/-1)")
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "probe": {"format": {k: fmt.get(k) for k in ("format_name", "duration", "size", "bit_rate")},
                      "video": v and {k: v.get(k) for k in ("codec_name", "profile", "width", "height", "pix_fmt",
                                                             "avg_frame_rate", "nb_frames", "duration")},
                      "audio": a and {k: a.get(k) for k in ("codec_name", "sample_rate", "channels", "duration")}},
            "decode": {"frames": fc, "error_count": len(dec["errors"])}}


# ---- memory (Windows peak working set; POSIX ru_maxrss) -------------------------------------------------
def peak_memory_mb(pid: int | None = None) -> float | None:
    """Peak resident memory in MB of this process (pid=None) or of another process by pid."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        h = k32.GetCurrentProcess() if pid is None else k32.OpenProcess(0x1000 | 0x0010, False, pid)
        if not h:
            return None
        try:
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            if not psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                return None
            return round(c.PeakWorkingSetSize / 1048576, 1)
        finally:
            if pid is not None:
                k32.CloseHandle(h)
    if pid is None:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(kb / (1024 if sys.platform != "darwin" else 1048576), 1)
    return None


def process_cmdline(pid: int) -> str | None:
    """Command line of a running process, or None if it is not running / cannot be read."""
    if sys.platform == "win32":
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                               capture_output=True, text=True, timeout=30, **_NOWIN)
            out = r.stdout.strip()
            return out or None
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        return Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace") or None
    except OSError:
        return None


def kill_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"], capture_output=True, timeout=30, **_NOWIN)
    else:
        import os
        import signal
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass
