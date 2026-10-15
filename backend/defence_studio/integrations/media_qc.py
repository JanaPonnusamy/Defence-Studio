"""Media analysis for QC: FFmpeg filter passes and font glyph coverage.

All measurements come from FFmpeg/ffprobe output or the font file itself;
nothing is estimated from the renderer's own report.
"""
from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path

from defence_studio.integrations.subtitles import ffmpeg_binary


def analyze_media(path: Path, *, silence_db: int = -45, silence_seconds: float = 3.0,
                  black_seconds: float = 0.5, freeze_seconds: float = 4.0, timeout: int = 3600) -> dict:
    """One decode pass: loudness (EBU R128), peak volume, long silences,
    black intervals and frozen intervals."""
    cmd = [ffmpeg_binary("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(path),
           "-af", f"volumedetect,silencedetect=n={silence_db}dB:d={silence_seconds},ebur128=peak=true",
           "-vf", f"blackdetect=d={black_seconds}:pix_th=0.10,freezedetect=n=-60dB:d={freeze_seconds}",
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    err = proc.stderr
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg analysis failed ({proc.returncode}): {err[-400:]}")

    def f(pattern: str):
        m = re.search(pattern, err)
        return float(m.group(1)) if m else None

    summary = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    integrated = None
    m = re.search(r"I:\s+(-?\d+(?:\.\d+)?|-inf)\s+LUFS", summary)
    if m and m.group(1) != "-inf":
        integrated = float(m.group(1))
    true_peak = None
    m = re.search(r"Peak:\s+(-?\d+(?:\.\d+)?|-inf)\s+dBFS", summary)
    if m and m.group(1) != "-inf":
        true_peak = float(m.group(1))
    silences = [{"start": float(a), "end": float(b)} for a, b in
                re.findall(r"silence_start: (-?[\d.]+)[\s\S]*?silence_end: ([\d.]+)", err)]
    blacks = [{"start": float(a), "end": float(b)} for a, b in
              re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    freezes = [{"start": float(a), "end": float(b)} for a, b in
               re.findall(r"freeze_start: ([\d.]+)[\s\S]*?freeze_end: ([\d.]+)", err)]
    return {
        "max_volume_db": f(r"max_volume: (-?[\d.]+) dB"), "mean_volume_db": f(r"mean_volume: (-?[\d.]+) dB"),
        "integrated_lufs": integrated, "true_peak_dbfs": true_peak,
        "silences": silences, "black_intervals": blacks, "frozen_intervals": freezes,
    }


# -- TrueType cmap (format 4 / 12) ---------------------------------------------


def font_codepoints(font_path: Path) -> set[int]:
    """Code points mapped to nonzero glyphs in TTF or TTC face zero.

    Face zero matches the default face used by the subtitle font probe.
    Collection table offsets remain absolute from the beginning of the file.
    """
    data = Path(font_path).read_bytes()
    face = 0
    if data[:4] == b'ttcf':
        if len(data) < 16 or struct.unpack('>I', data[8:12])[0] < 1:
            raise ValueError('empty or truncated font collection')
        face = struct.unpack('>I', data[12:16])[0]
    num_tables = struct.unpack(">H", data[face + 4:face + 6])[0]
    cmap_off = None
    for i in range(num_tables):
        tag, _chk, off, _len = struct.unpack(">4sIII", data[face + 12 + 16 * i: face + 28 + 16 * i])
        if tag == b"cmap":
            cmap_off = off
            break
    if cmap_off is None:
        raise ValueError("no cmap table")
    _ver, n = struct.unpack(">HH", data[cmap_off:cmap_off + 4])
    points: set[int] = set()
    for i in range(n):
        _pid, _eid, sub_off = struct.unpack(">HHI", data[cmap_off + 4 + 8 * i: cmap_off + 12 + 8 * i])
        base = cmap_off + sub_off
        fmt = struct.unpack(">H", data[base:base + 2])[0]
        if fmt == 4:
            segx2 = struct.unpack(">H", data[base + 6:base + 8])[0]
            seg = segx2 // 2
            ends = struct.unpack(f">{seg}H", data[base + 14: base + 14 + segx2])
            starts = struct.unpack(f">{seg}H", data[base + 16 + segx2: base + 16 + 2 * segx2])
            deltas = struct.unpack(f'>{seg}h', data[base + 16 + 2 * segx2:base + 16 + 3 * segx2])
            range_base = base + 16 + 3 * segx2
            offsets = struct.unpack(f'>{seg}H', data[range_base:range_base + segx2])
            for index, (s, e) in enumerate(zip(starts, ends)):
                if s == 0xFFFF:
                    continue
                for codepoint in range(s, e + 1):
                    if offsets[index]:
                        address = range_base + 2 * index + offsets[index] + 2 * (codepoint - s)
                        glyph = struct.unpack('>H', data[address:address + 2])[0]
                        if glyph:
                            glyph = (glyph + deltas[index]) & 0xFFFF
                    else:
                        glyph = (codepoint + deltas[index]) & 0xFFFF
                    if glyph:
                        points.add(codepoint)
        elif fmt == 12:
            n_groups = struct.unpack(">I", data[base + 12: base + 16])[0]
            for g in range(n_groups):
                s, e, first_glyph = struct.unpack(">III", data[base + 16 + 12 * g: base + 28 + 12 * g])
                points.update(range(s + (first_glyph == 0), e + 1))
    return points


def missing_glyphs(text: str, font_path: Path) -> list[str]:
    have = font_codepoints(font_path)
    ignorable = {0x200B, 0x200C, 0x200D, 0x0A, 0x0D, 0x09}  # ZW chars/space controls
    return sorted({ch for ch in text if ord(ch) not in have and ord(ch) not in ignorable and not ch.isspace()})
