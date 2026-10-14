"""Branding assets: title cards, intro/outro segments, music ducking, thumbnails.

Cards and thumbnails are drawn with FFmpeg/libass (not Pillow) so Tamil text
shapes correctly; plain Latin brand text goes through the same path for one
code path and one font policy. Everything is derived from explicit template
values and recorded in the package manifest; nothing is invented.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from defence_studio.integrations.subtitles import SubtitleError, ffmpeg_binary, resolve_font

THUMB_MAX_BYTES = 2 * 1024 * 1024  # YouTube custom-thumbnail limit
THUMB_SIZE = (1280, 720)


def default_template() -> dict:
    path = Path(os.environ.get("DEFENCE_STUDIO_BRANDING_CONFIG",
                               Path(__file__).resolve().parents[2] / "config" / "branding.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def merge_template(overrides: dict | None) -> dict:
    base = default_template()
    allowed = set(base) - {"_comment"}
    overrides = overrides or {}
    unknown = set(overrides) - allowed
    if unknown:
        raise ValueError(f"Unknown branding field(s): {sorted(unknown)}")
    merged = {**base, **overrides}
    merged.pop("_comment", None)
    if "colors" in overrides:
        merged["colors"] = {**base["colors"], **overrides["colors"]}
    return merged


def _ass_color(hex_rgb: str) -> str:
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H00{b}{g}{r}"


def _card_ass(lines: list[str], width: int, height: int, family: str, accent: str, text: str) -> str:
    big, small = int(height * 0.09), int(height * 0.05)
    events = []
    y = int(height * 0.40)
    for i, line in enumerate(lines):
        size = big if i == 0 else small
        clean = line.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
        events.append(f"Dialogue: 0,0:00:00.00,9:00:00.00,Default,,0,0,0,,{{\\an5\\pos({width // 2},{y})\\fs{size}"
                      f"\\1c{_ass_color(accent if i == 0 else text)}}}{clean}")
        y += int(size * 1.7)
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\nStyle: Default,%s,%d,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
            "1,0,0,0,100,100,0,0,1,0,0,5,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
            "MarginR, MarginV, Effect, Text\n" % (width, height, family, big)) + "\n".join(events) + "\n"


def _run(cmd: list[str], cwd: Path, what: str, timeout: int = 900) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise SubtitleError("ffmpeg_failed", f"{what}: ffmpeg exited {proc.returncode}: {proc.stderr[-500:]}")


def make_card_video(lines: list[str], seconds: float, width: int, height: int, fps: int, language: str,
                    template: dict, out_path: Path) -> None:
    """A silent-audio title card clip (H.264/AAC mono 48 kHz) matching the main video's profile."""
    font_file, family = resolve_font(language)
    ff = ffmpeg_binary("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="ds-card-") as work:
        w = Path(work)
        (w / "fonts").mkdir()
        shutil.copy2(font_file, w / "fonts" / font_file.name)
        c = template["colors"]
        (w / "c.ass").write_text(_card_ass(lines, width, height, family, c["accent"], c["text"]), encoding="utf-8")
        _run([ff, "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
              f"color=c=0x{c['background']}:s={width}x{height}:d={seconds}:r={fps}",
              "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(seconds),
              "-vf", "ass=c.ass:fontsdir=fonts,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "20", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1", "out.mp4"], w, "card")
        shutil.move(str(w / "out.mp4"), out_path)


def concat_videos(parts: list[Path], out_path: Path, width: int, height: int, fps: int) -> None:
    """Concatenate clips (video re-encoded to a common profile; audio resampled to mono 48 kHz)."""
    ff = ffmpeg_binary("ffmpeg")
    cmd = [ff, "-hide_banner", "-nostdin", "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    chains, labels = [], ""
    for i in range(len(parts)):
        chains.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{i}]")
        chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=mono[a{i}]")
        labels += f"[v{i}][a{i}]"
    chains.append(f"{labels}concat=n={len(parts)}:v=1:a=1[v][a]")
    cmd += ["-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset",
            "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_path)]
    _run(cmd, out_path.parent, "concat", timeout=3600)


def mix_music(video_in: Path, music_in: Path, out_path: Path, duck_db: float) -> None:
    """Mix a music bed under the narration, ducking it with sidechain compression."""
    ff = ffmpeg_binary("ffmpeg")
    gain = f"volume={duck_db}dB"
    flt = (f"[1:a]aresample=48000,aformat=channel_layouts=mono,{gain},apad[m];"
           "[0:a]asplit=2[nar][sc];[m][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];"
           "[nar][duck]amix=inputs=2:duration=first:dropout_transition=0[a]")
    _run([ff, "-hide_banner", "-nostdin", "-y", "-i", str(video_in), "-stream_loop", "-1", "-i", str(music_in),
          "-filter_complex", flt, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-shortest", "-movflags", "+faststart", str(out_path)], out_path.parent, "music", timeout=3600)


def make_thumbnail(video: Path, title: str, language: str, template: dict, out_path: Path, at_seconds: float) -> dict:
    """A 1280x720 JPEG: a frame from the video, darkened, with the title (libass shaping) and brand."""
    font_file, family = resolve_font(language)
    ff = ffmpeg_binary("ffmpeg")
    w, h = THUMB_SIZE
    c = template["colors"]
    with tempfile.TemporaryDirectory(prefix="ds-thumb-") as work:
        wd = Path(work)
        (wd / "fonts").mkdir()
        shutil.copy2(font_file, wd / "fonts" / font_file.name)
        size = int(h * 0.085)
        wrapped = _wrap(title, 26)
        text = "\\N".join(t.replace("{", "(").replace("}", ")") for t in wrapped)
        ass = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\n\n[V4+ Styles]\nFormat: Name, Fontname, "
               "Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
               "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, "
               "Encoding\nStyle: T,%s,%d,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,0,1,60,60,70,1\n"
               "Style: B,%s,%d,%s,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,60,60,40,1\n\n"
               "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
               "Dialogue: 0,0:00:00.00,0:00:05.00,T,,0,0,0,,%s\nDialogue: 0,0:00:00.00,0:00:05.00,B,,0,0,0,,%s\n"
               ) % (w, h, family, size, family, int(h * 0.05), _ass_color(c["accent"]), text,
                    template["channel_name"].upper().replace("{", "("))
        (wd / "t.ass").write_text(ass, encoding="utf-8")
        shutil.copy2(video, wd / "in.mp4")
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},eq=brightness=-0.25," \
             f"drawbox=x=0:y=ih*0.55:w=iw:h=ih*0.45:color=black@0.45:t=fill,ass=t.ass:fontsdir=fonts"
        _run([ff, "-hide_banner", "-nostdin", "-y", "-ss", f"{at_seconds:.2f}", "-i", "in.mp4", "-frames:v", "1",
              "-vf", vf, "-q:v", "3", "thumb.jpg"], wd, "thumbnail", timeout=300)
        data = (wd / "thumb.jpg").read_bytes()
        out_path.write_bytes(data)
    from PIL import Image
    with Image.open(out_path) as im:
        size_px = im.size
    return {"width": size_px[0], "height": size_px[1], "bytes": len(data), "format": "JPEG",
            "within_youtube_limit": len(data) <= THUMB_MAX_BYTES and size_px == THUMB_SIZE}


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines[:4]
