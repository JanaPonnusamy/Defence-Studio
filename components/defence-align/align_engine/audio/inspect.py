"""Safe, memory-bounded audio inspection.

The core path uses the Python stdlib ``wave`` module: it reads only the file
header/params to compute the *actual* measured duration (``nframes /
framerate``) without reading the PCM frames into memory, so a large file is
never fully loaded. Duration is derived from real audio, never estimated.

WAV/PCM is the built-in, dependency-free format. Broader formats (MP3, FLAC,
OGG) are intentionally out of the zero-dependency core; ``probe_audio`` raises
a clear ``UnsupportedAudioFormatError`` naming the optional ``soundfile``
backend rather than guessing a duration.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

from align_engine.errors import (
    MissingAudioError, UnsupportedAudioFormatError,
)

SUPPORTED_FORMATS = ("wav",)


@dataclass(frozen=True)
class AudioInfo:
    duration_seconds: float
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frames: int
    codec: str
    byte_size: int


def probe_audio(path: Path, *, max_bytes: int) -> AudioInfo:
    path = Path(path)
    if not path.is_file():
        raise MissingAudioError(f"Audio file not found: {path.name}")
    size = path.stat().st_size
    if size > max_bytes:
        raise UnsupportedAudioFormatError(
            f"Audio file is {size} bytes, exceeding the configured limit of {max_bytes}"
        )
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_FORMATS:
        raise UnsupportedAudioFormatError(
            f"Unsupported audio format {suffix!r}. The dependency-free core supports "
            f"{SUPPORTED_FORMATS}. Install the optional 'soundfile' backend and extend "
            "probe_audio to inspect additional formats."
        )
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            frame_rate = handle.getframerate()
            frames = handle.getnframes()
    except (wave.Error, EOFError) as exc:
        raise UnsupportedAudioFormatError(f"Not a valid PCM WAV file: {exc}") from exc
    if frame_rate <= 0:
        raise UnsupportedAudioFormatError("WAV frame rate is zero; cannot determine duration")
    duration = frames / float(frame_rate)
    return AudioInfo(
        duration_seconds=round(duration, 6), sample_rate=frame_rate, channels=channels,
        sample_width_bytes=sample_width, frames=frames, codec="pcm_wav", byte_size=size,
    )
