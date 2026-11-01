"""Tamil text, subtitle cue parsing, and PCM measurements for speech reference.

Cue timestamps are preserved. Word spans inside a multi-word cue are estimates.
Pitch is not inferred. 16-bit PCM WAV is the only format measured here.
"""
from __future__ import annotations

import io
import math
import re
import struct
import unicodedata
import wave

NORMALIZATION_NOTE = (
    "Normalized version is NFC with whitespace collapsed and WebVTT tags removed. "
    "The original transcript text and original cue strings are stored unchanged."
)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_WORD = re.compile(r"[0-9A-Za-z\u0B80-\u0BFF]+(?:['’][0-9A-Za-z\u0B80-\u0BFF]+)?")
_RANGE = re.compile(
    r"((?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*((?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{1,3})"
)
_IPA = re.compile(r"^[A-Za-z\u00C0-\u02FF\u1D00-\u1DBFˈˌ.ːˑ‿|\s-]+$")


class CueParseError(ValueError):
    pass


def normalize_tamil(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = _TAG.sub("", text)
    text = _WS.sub(" ", text.replace("\n", " ")).strip()
    return text


def tokenize(text: str) -> list[str]:
    return _WORD.findall(normalize_tamil(text))


def _ms(stamp: str) -> int:
    stamp = stamp.replace(",", ".")
    parts = stamp.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = "0", parts[0], parts[1]
    else:
        raise CueParseError(f"Unrecognised timestamp {stamp}")
    sec, frac = seconds.split(".")
    frac = (frac + "000")[:3]
    return ((int(hours) * 60 + int(minutes)) * 60 + int(sec)) * 1000 + int(frac)


def parse_cues(raw: str) -> tuple[list[dict], list[str]]:
    """Return cue dicts and warnings. Does not invent cues when none parse."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    warnings: list[str] = []
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines or lines[0].strip().upper().startswith("WEBVTT"):
            if lines and lines[0].strip().upper().startswith("WEBVTT"):
                lines = lines[1:]
            if not lines:
                continue
        if lines and re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        if not lines:
            continue
        match = _RANGE.search(lines[0])
        if match is None:
            continue
        try:
            start, end = _ms(match.group(1)), _ms(match.group(2))
        except (CueParseError, ValueError):
            warnings.append(f"Skipped a cue with an unreadable timestamp: {lines[0][:80]}")
            continue
        if end <= start:
            warnings.append(f"Skipped a cue whose end is not after its start ({start}-{end})")
            continue
        original = " ".join(ln.strip() for ln in lines[1:]).strip()
        if not original:
            warnings.append(f"Skipped an empty cue at {start} ms")
            continue
        cues.append({
            "start_ms": start,
            "end_ms": end,
            "original_text": original,
            "text": normalize_tamil(original),
        })
    return cues, warnings


def sentence_position(index: int, count: int) -> str:
    if count <= 1:
        return "only"
    if index == 0:
        return "initial"
    if index == count - 1:
        return "final"
    return "medial"


def rate_band(words_per_minute: float | None) -> str | None:
    """Caption-timing bands. These are groupings of an estimate, not categories of speech."""
    if words_per_minute is None:
        return None
    if words_per_minute < 90:
        return "slow"
    if words_per_minute <= 150:
        return "normal"
    return "fast"


def speaking_rate(word_count: int, duration_ms: int) -> float | None:
    if word_count <= 0 or duration_ms <= 0:
        return None
    return round(word_count / (duration_ms / 1000) * 60, 3)


def proportional_spans(start_ms: int, end_ms: int, tokens: list[str]) -> list[tuple[int, int]] | None:
    span = end_ms - start_ms
    if not tokens or span < len(tokens):
        return None
    weights = [max(len(token), 1) for token in tokens]
    total = sum(weights)
    cursor = start_ms
    spans: list[tuple[int, int]] = []
    for index, weight in enumerate(weights):
        nxt = end_ms if index == len(weights) - 1 else cursor + max(1, int(span * weight / total))
        if nxt >= end_ms and index != len(weights) - 1:
            nxt = end_ms - (len(weights) - index - 1)
        if nxt <= cursor:
            return None
        spans.append((cursor, nxt))
        cursor = nxt
    if spans[-1][1] != end_ms or any(b <= a for a, b in spans):
        return None
    return spans


def validate_ipa(value: str) -> bool:
    return bool(value) and bool(_IPA.fullmatch(value))


def measure_wav(data: bytes) -> dict:
    """Header duration plus RMS for 16-bit PCM. Pitch is left unmeasured."""
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            channels = handle.getnchannels()
            rate = handle.getframerate()
            width = handle.getsampwidth()
            frames = handle.getnframes()
            raw = handle.readframes(frames)
    except wave.Error as exc:
        raise CueParseError(f"WAV could not be read: {exc}") from exc
    duration_ms = int(frames / rate * 1000) if rate else 0
    base = {
        "sample_rate_hz": rate,
        "channels": channels,
        "duration_ms": duration_ms,
        "energy_mean": None,
        "pitch_median_hz": None,
        "method": "wav_header_only",
        "limitation": "Pitch was not measured. RMS energy is measured only for 16-bit PCM.",
    }
    if width != 2 or frames <= 0 or not raw:
        return base
    count = len(raw) // 2
    samples = struct.unpack("<" + "h" * count, raw[: count * 2])
    if channels > 1:
        mono = []
        for index in range(0, count - channels + 1, channels):
            mono.append(sum(samples[index:index + channels]) / channels)
        samples = mono
    if not samples:
        return base
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    base.update({
        "energy_mean": round(rms, 6),
        "method": "pcm_rms_v1",
        "limitation": "Pitch was not measured. Energy is RMS of the 16-bit PCM samples, not a perceptual loudness model.",
    })
    return base


def segment_energy_contour(data: bytes, start_ms: int, end_ms: int, bins: int = 8) -> list[dict] | None:
    """Measured RMS bins across a caption span. Returns None when the WAV cannot support it."""
    try:
        measured = measure_wav(data)
    except CueParseError:
        return None
    if measured["method"] != "pcm_rms_v1":
        return None
    with wave.open(io.BytesIO(data), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    count = len(raw) // 2
    samples = list(struct.unpack("<" + "h" * count, raw[: count * 2]))
    if channels > 1:
        mono = []
        for index in range(0, count - channels + 1, channels):
            mono.append(sum(samples[index:index + channels]) / channels)
        samples = mono
    start = max(0, int(start_ms / 1000 * rate))
    end = min(len(samples), int(end_ms / 1000 * rate))
    window = samples[start:end]
    if len(window) < bins:
        return None
    step = len(window) / bins
    contour = []
    for index in range(bins):
        chunk = window[int(index * step):int((index + 1) * step)]
        if not chunk:
            continue
        rms = math.sqrt(sum(sample * sample for sample in chunk) / len(chunk)) / 32768
        contour.append({
            "offset_ms": int(start_ms + (end_ms - start_ms) * index / bins),
            "rms": round(rms, 6),
        })
    return contour or None
