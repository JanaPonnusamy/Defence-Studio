"""Small PCM helpers (standalone; no dependency on voice_analyzer)."""
from __future__ import annotations
import io, os, shutil, subprocess, wave
import numpy as np

OUT_SR = 48000


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("only 16-bit PCM wav supported")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x, sr


def to_wav_bytes(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def resample(x: np.ndarray, sr: int, target: int = OUT_SR) -> np.ndarray:
    """Resample via ffmpeg when available, else linear interpolation."""
    if sr == target:
        return x
    ff = os.environ.get("VOICE_FFMPEG") or shutil.which("ffmpeg")
    if ff:
        r = subprocess.run([ff, "-v", "error", "-f", "wav", "-i", "pipe:0", "-ar", str(target), "-ac", "1",
                            "-f", "wav", "pipe:1"], input=to_wav_bytes(x, sr), capture_output=True)
        if r.returncode == 0 and r.stdout:
            return read_wav(r.stdout)[0]
    n = int(len(x) * target / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def speech_span_ms(x: np.ndarray, sr: int, rel: float = 0.02) -> tuple[int, int]:
    """Measured first/last sample above `rel` * peak, in ms."""
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak <= 0:
        return 0, 0
    idx = np.nonzero(np.abs(x) > rel * peak)[0]
    return int(idx[0] / sr * 1000), int((idx[-1] + 1) / sr * 1000)
