"""Audio I/O and measurement for Voice Lab (numpy + ffmpeg; no other DSP dependency).

All decoding goes through ffmpeg into float32 arrays. Every measurement here is
computed from samples. Nothing is estimated without saying so in its key name.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from defence_studio.voice_lab.common import VoiceLabError

SUPPORTED_EXT = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".webm", ".mp4", ".mkv", ".mov"}
EXPORT_FORMATS = {"wav": ("wav", "pcm_s16le"), "flac": ("flac", "flac"), "mp3": ("mp3", "libmp3lame"),
                  "ogg": ("ogg", "libopus")}


def ffmpeg_bin() -> str:
    p = os.environ.get("VOICE_FFMPEG") or shutil.which("ffmpeg")
    if not p:
        raise VoiceLabError(503, "ffmpeg not found (install it or set VOICE_FFMPEG)")
    return p


def decode(path: Path | str, sr: int, channels: int = 1, max_seconds: float = 4 * 3600) -> np.ndarray:
    """Decode any supported file to float32, shape (n,) for mono or (n, ch)."""
    path = Path(path)
    if not path.is_file():
        raise VoiceLabError(404, f"audio file is missing: {path.name}")
    if path.stat().st_size == 0:
        raise VoiceLabError(422, "audio file is empty")
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", str(channels),
                        "-ar", str(sr), "-t", str(max_seconds), "-f", "f32le", "pipe:1"], capture_output=True)
    if r.returncode != 0:
        raise VoiceLabError(422, "audio decode failed: " + r.stderr.decode("utf-8", "replace")[:300])
    x = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    if x.size < sr // 50:
        raise VoiceLabError(422, "audio contains no usable samples")
    return x if channels == 1 else x.reshape(-1, channels)


def probe_has_audio(path: Path) -> bool:
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0", "-t", "0.1",
                        "-f", "null", "-"], capture_output=True)
    return r.returncode == 0


def wav_bytes(x: np.ndarray, sr: int) -> bytes:
    x = np.asarray(x, dtype=np.float32)
    ch = 1 if x.ndim == 1 else x.shape[1]
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).round().astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def read_wav_info(data: bytes) -> tuple[int, int, int]:
    """(sample_rate, channels, frames) of a PCM WAV."""
    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.getnchannels(), w.getnframes()


def encode(x: np.ndarray, sr: int, fmt: str, out_path: Path, bitrate: str = "192k") -> None:
    if fmt not in EXPORT_FORMATS:
        raise VoiceLabError(422, f"format must be one of {sorted(EXPORT_FORMATS)}")
    if fmt == "wav":
        out_path.write_bytes(wav_bytes(x, sr))
        return
    container, codec = EXPORT_FORMATS[fmt]
    args = [ffmpeg_bin(), "-v", "error", "-nostdin", "-y", "-f", "wav", "-i", "pipe:0", "-c:a", codec]
    if fmt in ("mp3", "ogg"):
        args += ["-b:a", bitrate]
    if fmt == "ogg" and sr not in (8000, 12000, 16000, 24000, 48000):
        args += ["-ar", "48000"]
    r = subprocess.run(args + ["-f", container, str(out_path)], input=wav_bytes(x, sr), capture_output=True)
    if r.returncode != 0 or not out_path.is_file():
        raise VoiceLabError(500, "encode failed: " + r.stderr.decode("utf-8", "replace")[:300])


def run_filter(x: np.ndarray, sr: int, filtergraph: str) -> np.ndarray:
    """Apply an ffmpeg audio filtergraph to a mono/stereo float signal; returns same channel count."""
    ch = 1 if x.ndim == 1 else x.shape[1]
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-nostdin", "-f", "f32le", "-ar", str(sr), "-ac", str(ch),
                        "-i", "pipe:0", "-af", filtergraph, "-ar", str(sr), "-ac", str(ch), "-f", "f32le", "pipe:1"],
                       input=np.ascontiguousarray(x, dtype="<f4").tobytes(), capture_output=True)
    if r.returncode != 0:
        raise VoiceLabError(500, f"filter '{filtergraph[:80]}' failed: " + r.stderr.decode("utf-8", "replace")[:300])
    y = np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)
    return y if ch == 1 else y.reshape(-1, ch)


_I_RE = re.compile(r"I:\s+(-?[\d.]+|-inf)\s+LUFS")
_TP_RE = re.compile(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS")


def loudness(x: np.ndarray, sr: int) -> dict:
    """EBU R128 integrated loudness and true peak, measured by ffmpeg ebur128."""
    ch = 1 if x.ndim == 1 else x.shape[1]
    r = subprocess.run([ffmpeg_bin(), "-v", "info", "-nostdin", "-f", "f32le", "-ar", str(sr), "-ac", str(ch),
                        "-i", "pipe:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       input=np.ascontiguousarray(x, dtype="<f4").tobytes(), capture_output=True)
    err = r.stderr.decode("utf-8", "replace")
    tail = err[err.rfind("Summary:"):] if "Summary:" in err else err
    i, tp = _I_RE.search(tail), _TP_RE.search(tail)
    to_f = lambda m: None if (m is None or m.group(1) == "-inf") else float(m.group(1))
    return {"integrated_lufs": to_f(i), "true_peak_dbfs": to_f(tp), "method": "ffmpeg ebur128"}


def frame_db(x: np.ndarray, sr: int, hop_ms: int = 10, win_ms: int = 30) -> np.ndarray:
    hop, win = max(1, sr * hop_ms // 1000), max(1, sr * win_ms // 1000)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    n = 1 + (len(x) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    rms = np.sqrt((x[idx] ** 2).mean(axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-9))


def speech_regions(x: np.ndarray, sr: int, *, min_silence_ms: int = 300, min_speech_ms: int = 200,
                   hop_ms: int = 10) -> list[tuple[int, int]]:
    """Speech regions in ms using an adaptive energy threshold (same principle as EXT-03 analyzer)."""
    db = frame_db(x, sr, hop_ms)
    if db.size == 0:
        return []
    p10, p95 = np.percentile(db, 10), np.percentile(db, 95)
    if p95 < -55 or (p95 - p10) < 6:
        return []
    thr = max(-55.0, p10 + 0.35 * (p95 - p10))
    act = db > thr
    runs, i, n = [], 0, len(act)
    while i < n:
        if act[i]:
            j = i
            while j < n and act[j]:
                j += 1
            runs.append([i * hop_ms, j * hop_ms])
            i = j
        else:
            i += 1
    merged: list[list[int]] = []
    for s, e in runs:
        if merged and s - merged[-1][1] < min_silence_ms:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    total_ms = int(len(x) * 1000 / sr)
    return [(s, min(e, total_ms)) for s, e in merged if e - s >= min_speech_ms]


def quality(x: np.ndarray, sr: int, noise_floor_db: float | None = None) -> dict:
    """Measured QC numbers for a mono signal.

    ``noise_floor_db``: the floor measured on the WHOLE source recording (pauses included).
    A tightly cut segment has almost no silence, so its own 10th percentile is speech, not noise;
    segments must therefore be judged against their recording's floor.
    """
    if x.ndim > 1:
        x = x.mean(axis=1)
    peak = float(np.abs(x).max()) if x.size else 0.0
    clip_ratio = float((np.abs(x) >= 0.999).mean()) if x.size else 0.0
    db = frame_db(x, sr)
    speech_level = float(np.percentile(db, 90)) if db.size else -120.0
    dur_ms = int(len(x) * 1000 / sr)
    if noise_floor_db is None:
        noise_floor = float(np.percentile(db, 10)) if db.size else -120.0
        speech_ms = sum(e - s for s, e in speech_regions(x, sr))
    else:
        noise_floor = float(noise_floor_db)
        speech_ms = min(dur_ms, int((db > noise_floor + 12).sum()) * 10)
    dc = float(np.mean(x)) if x.size else 0.0
    return {
        "duration_ms": dur_ms,
        "peak_dbfs": round(20 * np.log10(max(peak, 1e-9)), 2),
        "clipping_ratio": round(clip_ratio, 6),
        "rms_dbfs": round(float(20 * np.log10(max(np.sqrt(np.mean(x ** 2)), 1e-9))), 2) if x.size else None,
        "noise_floor_dbfs": round(noise_floor, 2),
        "snr_db_estimate": round(speech_level - noise_floor, 2),
        "speech_ms": speech_ms,
        "silence_ratio": round(1 - speech_ms / dur_ms, 4) if dur_ms else 1.0,
        "dc_offset": round(dc, 5),
    }


def qc_verdict(q: dict, *, min_ms: int = 1000, max_ms: int = 20000) -> tuple[str, list[str]]:
    issues = []
    if q["clipping_ratio"] > 0.001:
        issues.append("CLIPPING")
    if q["peak_dbfs"] < -30:
        issues.append("TOO_QUIET")
    if q["snr_db_estimate"] < 15:
        issues.append("NOISY")
    elif q["snr_db_estimate"] < 25:
        issues.append("NOISE_WARNING")
    if q["duration_ms"] < min_ms:
        issues.append("TOO_SHORT")
    if q["duration_ms"] > max_ms:
        issues.append("TOO_LONG")
    if q["silence_ratio"] > 0.6:
        issues.append("MOSTLY_SILENCE")
    if abs(q["dc_offset"]) > 0.02:
        issues.append("DC_OFFSET")
    hard = {"CLIPPING", "NOISY", "TOO_SHORT", "TOO_LONG", "MOSTLY_SILENCE"}
    status = "FAIL" if any(i in hard for i in issues) else ("WARN" if issues else "PASS")
    return status, issues


def fingerprint(x: np.ndarray, sr: int, bands: int = 16, frames: int = 32) -> str:
    """Coarse spectral fingerprint (bands x time grid, 1 bit per cell vs the cell median) as hex.

    Used for duplicate detection: identical or re-encoded takes give near-identical bit
    patterns; different sentences do not. Combined with a duration check by the caller.
    """
    if x.ndim > 1:
        x = x.mean(axis=1)
    n_fft = 512
    hop = max(1, (len(x) - n_fft) // frames) if len(x) > n_fft else 1
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    idx = np.minimum(idx, len(x) - 1)
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(n_fft)[None, :], axis=1))
    edges = np.unique(np.geomspace(4, spec.shape[1] - 1, bands + 1).astype(int))
    grid = np.stack([spec[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1)
    grid = np.log(grid + 1e-9)
    bits = (grid > np.median(grid)).astype(np.uint8).ravel()
    return np.packbits(bits).tobytes().hex()


def fingerprint_similarity(a: str, b: str) -> float:
    ba, bb = np.frombuffer(bytes.fromhex(a), np.uint8), np.frombuffer(bytes.fromhex(b), np.uint8)
    if ba.size != bb.size or ba.size == 0:
        return 0.0
    diff = np.unpackbits(np.bitwise_xor(ba, bb)).sum()
    return 1.0 - diff / (ba.size * 8)


def peaks(x: np.ndarray, sr: int, px_per_s: int) -> dict:
    """Min/max waveform peaks per pixel bucket for the UI."""
    if x.ndim > 1:
        x = x.mean(axis=1)
    px_per_s = max(1, min(int(px_per_s), 2000))
    bucket = max(1, sr // px_per_s)
    n = int(np.ceil(len(x) / bucket))
    padded = np.pad(x, (0, n * bucket - len(x)))
    m = padded.reshape(n, bucket)
    return {"sample_rate": sr, "samples_per_bucket": bucket, "px_per_s": px_per_s,
            "min": np.round(m.min(axis=1), 4).tolist(), "max": np.round(m.max(axis=1), 4).tolist()}
