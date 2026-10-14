"""Transcript/SRT/VTT ingestion and parsing into a normalized segment list."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TranscriptSegment:
    index: int
    start: float
    end: float
    text: str


_SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _srt_ts_to_seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt_or_vtt(text: str) -> list[TranscriptSegment]:
    """Parse SRT or WebVTT text into segments. Both formats share the same
    HH:MM:SS,mmm --> HH:MM:SS,mmm timing line structure (VTT may use '.'
    instead of ',' and has a WEBVTT header, both handled by the regex)."""
    lines = text.splitlines()
    segments: list[TranscriptSegment] = []
    i = 0
    idx = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line or line.upper() == "WEBVTT":
            i += 1
            continue
        m = _SRT_TIME_RE.search(line)
        if m:
            start = _srt_ts_to_seconds(*m.group(1, 2, 3, 4))
            end = _srt_ts_to_seconds(*m.group(5, 6, 7, 8))
            i += 1
            text_lines = []
            while i < n and lines[i].strip() != "":
                text_lines.append(lines[i].strip())
                i += 1
            idx += 1
            segments.append(TranscriptSegment(idx, start, end, " ".join(text_lines)))
        else:
            i += 1
    return segments


def parse_plain_text(text: str) -> list[TranscriptSegment]:
    """Plain transcript: split into sentences; no timing is known yet (the
    forced-alignment stage derives timing). Each 'segment' here is a
    sentence with start=end=0.0, to be filled in by alignment."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    return [TranscriptSegment(i + 1, 0.0, 0.0, s) for i, s in enumerate(sentences)]


def load_transcript(raw_text: str, source_format: str) -> list[TranscriptSegment]:
    if source_format in ("srt", "vtt"):
        return parse_srt_or_vtt(raw_text)
    return parse_plain_text(raw_text)
