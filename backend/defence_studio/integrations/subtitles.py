"""Subtitle cue building, export (SRT/VTT/ASS) and burn-in via FFmpeg/libass.

Why libass: the renderer's Pillow build has no complex-text shaping (raqm,
harfbuzz and fribidi all report False on the audited machine), so Tamil drawn
by Pillow would be unshaped. The local FFmpeg is built with libass +
HarfBuzz + FriBiDi, which shape Tamil correctly with a Tamil-capable font
(Nirmala UI, shipped with Windows, is used by default).

Timing honesty: scene boundaries are *measured* from the real narration;
cue boundaries inside a scene are proportional to text length and are labelled
as such (``cue_timing = scene_measured_chunk_proportional``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CUE_TIMING_METHOD = "scene_measured_chunk_proportional"
MIN_CUE_SECONDS = 0.6

_WINDOWS_FONTS = Path(os.environ.get("WINDIR") or os.environ.get("SYSTEMROOT") or "C:/Windows") / "Fonts"
_FONT_CANDIDATES = {
    "ta": [(str(_WINDOWS_FONTS / "Nirmala.ttc"), "Nirmala UI"),
           (str(_WINDOWS_FONTS / "Nirmala.ttf"), "Nirmala UI"),
           ("/usr/share/fonts/truetype/noto/NotoSansTamil-Regular.ttf", "Noto Sans Tamil")],
    "en": [("C:/Windows/Fonts/segoeui.ttf", "Segoe UI"), ("C:/Windows/Fonts/arial.ttf", "Arial"),
           ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVu Sans")],
}
_TAMIL_COVERAGE_PROBE = "தமிழ் செய்தி இந்தியா"


class SubtitleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _has_tamil_glyph_coverage(path: Path) -> bool:
    """Check a font face can load and contains the Tamil glyphs used by our probe.

    This checks cmap coverage, not complex-script shaping. The FFmpeg/libass
    burn-in test exercises the configured shaping stack separately.
    """
    try:
        from PIL import ImageFont

        font = ImageFont.truetype(str(path), 32)
        missing_glyph = font.getmask("\U0010ffff")
        missing_signature = (missing_glyph.size, missing_glyph.mode, bytes(missing_glyph))
        for character in set(_TAMIL_COVERAGE_PROBE.replace(" ", "")):
            glyph = font.getmask(character)
            if (glyph.size, glyph.mode, bytes(glyph)) == missing_signature:
                return False
        return True
    except (OSError, ValueError):
        return False


def resolve_font(language: str) -> tuple[Path, str]:
    """(font file, family name). ``DEFENCE_STUDIO_SUBTITLE_FONT_FILE`` +
    ``..._FAMILY`` override. Fails closed rather than drawing tofu boxes."""
    override = os.environ.get("DEFENCE_STUDIO_SUBTITLE_FONT_FILE")
    if override:
        family = os.environ.get("DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY")
        if not family or not Path(override).is_file():
            raise SubtitleError("font_config", "DEFENCE_STUDIO_SUBTITLE_FONT_FILE needs an existing file and "
                                               "DEFENCE_STUDIO_SUBTITLE_FONT_FAMILY")
        if language == "ta" and not _has_tamil_glyph_coverage(Path(override)):
            raise SubtitleError("font_config", "The configured subtitle font does not provide the required Tamil glyphs")
        return Path(override), family
    for path, family in _FONT_CANDIDATES.get(language, _FONT_CANDIDATES["en"]):
        candidate = Path(path)
        if candidate.is_file() and (language != "ta" or _has_tamil_glyph_coverage(candidate)):
            return candidate, family
    raise SubtitleError("font_missing", f"No font with {language!r} coverage found; set "
                                        "DEFENCE_STUDIO_SUBTITLE_FONT_FILE and ..._FAMILY")


def ffmpeg_binary(name: str = "ffmpeg") -> str:
    env = os.environ.get("RENDERER_FFMPEG" if name == "ffmpeg" else "RENDERER_FFPROBE")
    found = env or shutil.which(name)
    if not found:
        raise SubtitleError("ffmpeg_missing", f"{name} not found (set RENDERER_{name.upper()} or PATH)")
    return found


# -- cues --------------------------------------------------------------------


def _chunks(text: str, max_words: int) -> list[str]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text.strip()) if s.strip()]
    out: list[str] = []
    for sentence in sentences or [text.strip()]:
        words = sentence.split()
        if len(words) <= max_words:
            out.append(sentence)
            continue
        n = -(-len(words) // -(-len(words) // max_words))  # balanced chunk size
        out.extend(" ".join(words[i:i + n]) for i in range(0, len(words), n))
    return [c for c in out if c]


def build_cues(scenes: list[dict], max_words: int = 9) -> list[dict]:
    """``scenes``: ``[{scene_id, text, start, duration}]`` (seconds, measured)."""
    cues: list[dict] = []
    for scene in scenes:
        chunks = _chunks(scene["text"], max_words)
        weights = [max(len(c), 1) for c in chunks]
        total = float(sum(weights))
        t = float(scene["start"])
        end_scene = float(scene["start"]) + float(scene["duration"])
        for i, (chunk, weight) in enumerate(zip(chunks, weights)):
            span = float(scene["duration"]) * weight / total
            end = end_scene if i == len(chunks) - 1 else min(t + span, end_scene)
            cues.append({"index": len(cues) + 1, "scene_id": scene["scene_id"], "start": round(t, 3),
                         "end": round(end, 3), "text": chunk})
            t = end
    return cues


def check_cues(cues: list[dict], scenes: list[dict], audio_duration: float, tolerance: float = 0.05) -> list[str]:
    """Deterministic sync checks against the measured audio spans."""
    problems: list[str] = []
    spans = {s["scene_id"]: (float(s["start"]), float(s["start"]) + float(s["duration"])) for s in scenes}
    prev_end = 0.0
    for c in cues:
        lo, hi = spans.get(c["scene_id"], (None, None))
        if lo is None:
            problems.append(f"cue {c['index']}: unknown scene {c['scene_id']}")
            continue
        if c["start"] < lo - tolerance or c["end"] > hi + tolerance:
            problems.append(f"cue {c['index']}: outside its scene's audio span")
        if c["end"] <= c["start"]:
            problems.append(f"cue {c['index']}: non-positive duration")
        if c["start"] < prev_end - tolerance:
            problems.append(f"cue {c['index']}: overlaps the previous cue")
        prev_end = max(prev_end, c["end"])
    if cues and cues[-1]["end"] > audio_duration + tolerance:
        problems.append("last cue ends after the narration")
    covered = {c["scene_id"] for c in cues}
    for sid in spans:
        if sid not in covered:
            problems.append(f"scene {sid} has no subtitle cue")
    return problems


# -- formats -----------------------------------------------------------------


def _ts(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[dict]) -> str:
    return "\n".join(f"{c['index']}\n{_ts(c['start'], ',')} --> {_ts(c['end'], ',')}\n{c['text']}\n" for c in cues)


def to_vtt(cues: list[dict]) -> str:
    body = "\n".join(f"{_ts(c['start'], '.')} --> {_ts(c['end'], '.')}\n{c['text']}\n" for c in cues)
    return "WEBVTT\n\n" + body


def _ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def to_ass(cues: list[dict], width: int, height: int, family: str) -> str:
    size = max(int(height * 0.045), 18)
    margin_v = int(height * 0.06)
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,%s,%d,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1.2,2,%d,%d,%d,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ) % (width, height, family, size, int(width * 0.06), int(width * 0.06), margin_v)
    lines = []
    for c in cues:
        text = c["text"].replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Default,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"


# -- ffmpeg ------------------------------------------------------------------


def burn_in(video_in: Path, ass_text: str, video_out: Path, font_file: Path, *, timeout: int = 3600) -> dict:
    """Burn ``ass_text`` into ``video_in`` with libass. Runs in a temp working
    dir so no Windows drive-letter path needs filter escaping. Video is
    re-encoded (x264, CRF 20); audio is copied untouched."""
    ffmpeg = ffmpeg_binary("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="ds-burn-") as work:
        work_dir = Path(work)
        (work_dir / "fonts").mkdir()
        shutil.copy2(font_file, work_dir / "fonts" / font_file.name)
        (work_dir / "subs.ass").write_text(ass_text, encoding="utf-8")
        shutil.copy2(video_in, work_dir / "in.mp4")
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", "in.mp4",
               "-vf", "ass=subs.ass:fontsdir=fonts", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", "out.mp4"]
        try:
            proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise SubtitleError("burn_timeout", "Subtitle burn-in timed out") from exc
        if proc.returncode != 0 or not (work_dir / "out.mp4").is_file():
            raise SubtitleError("burn_failed", f"ffmpeg exited {proc.returncode}: {proc.stderr[-600:]}")
        video_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work_dir / "out.mp4"), video_out)
    return {"ffmpeg": ffmpeg, "font_file": font_file.name}


def probe(path: Path) -> dict:
    proc = subprocess.run([ffmpeg_binary("ffprobe"), "-v", "error", "-print_format", "json", "-show_format",
                           "-show_streams", str(path)], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise SubtitleError("probe_failed", proc.stderr[-400:])
    data = json.loads(proc.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    return {"container": data["format"].get("format_name"), "duration": float(data["format"].get("duration", 0)),
            "video": v and {"codec": v["codec_name"], "width": v["width"], "height": v["height"],
                            "pix_fmt": v.get("pix_fmt"), "avg_frame_rate": v.get("avg_frame_rate")},
            "audio": a and {"codec": a["codec_name"], "sample_rate": int(a.get("sample_rate", 0)),
                            "channels": a.get("channels")}}


def full_decode_errors(path: Path) -> int:
    proc = subprocess.run([ffmpeg_binary("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"],
                          capture_output=True, text=True, timeout=3600)
    return len([l for l in proc.stderr.splitlines() if l.strip()]) + (1 if proc.returncode else 0)
