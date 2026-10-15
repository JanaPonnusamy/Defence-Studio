"""Audio ingestion + normalization. Acquisition (URL download) is NOT done here."""
from __future__ import annotations
import io, os, shutil, subprocess, wave
import numpy as np

SR = 16000
SUPPORTED_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac", ".aac"}


class AudioError(Exception):
    code = "AUDIO_ERROR"


class InvalidAudioError(AudioError):
    code = "INVALID_AUDIO"


class NoSpeechError(AudioError):
    code = "NO_SPEECH"


def ffmpeg_bin() -> str:
    p = os.environ.get("VOICE_FFMPEG") or shutil.which("ffmpeg")
    if not p:
        raise InvalidAudioError("ffmpeg not found (set VOICE_FFMPEG)")
    return p


def load_audio(path: str, max_seconds: float = 3600.0) -> np.ndarray:
    """Validate + decode any supported file to mono float32 @16 kHz (original level preserved)."""
    if not os.path.isfile(path):
        raise InvalidAudioError(f"source file not found: {path}")
    if os.path.getsize(path) == 0:
        raise InvalidAudioError("source file is empty")
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXT:
        raise InvalidAudioError(f"unsupported format: {ext or '(none)'}")
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", path, "-vn", "-ac", "1",
                        "-ar", str(SR), "-t", str(max_seconds), "-f", "wav", "pipe:1"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise InvalidAudioError("decode failed: " + r.stderr.decode("utf-8", "replace")[:200])
    try:
        with wave.open(io.BytesIO(r.stdout)) as w:
            raw = w.readframes(w.getnframes())
    except wave.Error as e:
        raise InvalidAudioError(f"decode produced invalid wav: {e}")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if x.size < SR // 20:
        raise InvalidAudioError("audio contains no usable samples")
    return x


def dbfs(v: float) -> float:
    return float(20 * np.log10(max(v, 1e-9)))


def normalize(x: np.ndarray, target_rms_db: float = -23.0, max_gain_db: float = 30.0):
    """RMS-normalize for detection; returns (normalized, gain_db). Original is kept for loudness stats."""
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms < 1e-6:
        return x, 0.0
    gain = min(max_gain_db, target_rms_db - dbfs(rms))
    return np.clip(x * 10 ** (gain / 20), -1, 1).astype(np.float32), gain
