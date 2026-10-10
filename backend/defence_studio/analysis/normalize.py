"""Deterministic source normalization.

Converts raw acquired bytes into a clean text representation per source type
without destroying meaning. The original bytes are never modified; this layer
only reads them and emits a separate normalized string plus a hash computed over
that normalized content.

Design constraints:

* Unicode is normalized to NFC and whitespace is collapsed, but paragraph
  structure (blank-line boundaries) is preserved so sentence/passage offsets
  stay meaningful.
* Qualifiers, negations, numbers, units, dates and technical/military
  designations are NEVER removed — only markup, boilerplate wrappers and
  redundant whitespace are touched.
* Subtitle/caption cues keep their timings so passages can cite a timestamp.
* Unsupported or undecodable content fails loudly (raises) so the caller can
  record a failure and continue with other sources, rather than inventing text.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass


class NormalizationError(ValueError):
    """The bytes could not be normalized into trustworthy text."""


# Media types this layer knows how to turn into text. Anything else is refused
# rather than guessed at.
_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_SUBTITLE_TYPES = {"text/vtt", "application/x-subrip", "text/srt"}
_PLAIN_TYPES = {"text/plain", "text/markdown", "application/json", ""}

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_BLOCK_TAG = re.compile(r"</?(p|div|br|li|tr|h[1-6]|section|article)\b[^>]*>", re.IGNORECASE)
# Tamil Unicode block; used for a conservative script-based language guess.
_TAMIL = re.compile(r"[஀-௿]")
_LATIN = re.compile(r"[A-Za-z]")

_CUE_INDEX = re.compile(r"^\d+$")
_TS = r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
_CUE_TIMING = re.compile(rf"{_TS}\s*-->\s*{_TS}")


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str
    # Character offsets of this cue's text inside the joined normalized text.
    text_start: int
    text_end: int


@dataclass(frozen=True)
class NormalizedText:
    text: str
    language: str
    cues: tuple[SubtitleCue, ...] = ()


def detect_language(text: str) -> str:
    """Conservative, deterministic script-based guess: 'ta', 'en', or 'und'.

    This is a script heuristic, not full language identification; it only
    distinguishes Tamil from Latin text and abstains ('und') otherwise."""
    tamil = len(_TAMIL.findall(text))
    latin = len(_LATIN.findall(text))
    if tamil == 0 and latin == 0:
        return "und"
    if tamil >= latin:
        return "ta"
    return "en"


def _collapse_whitespace(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    # Normalize line endings, trim trailing spaces, collapse 3+ blank lines to a
    # single paragraph break, and collapse intra-line runs of spaces/tabs.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip()


def normalize_html(raw: str) -> NormalizedText:
    stripped = _SCRIPT_STYLE.sub(" ", raw)
    # Turn block-level tags into newlines so paragraph structure survives, then
    # drop all remaining tags and decode entities.
    stripped = _BLOCK_TAG.sub("\n", stripped)
    stripped = _TAG.sub(" ", stripped)
    stripped = html.unescape(stripped)
    text = _collapse_whitespace(stripped)
    return NormalizedText(text=text, language=detect_language(text))


def _parse_ts(groups: tuple[str, ...]) -> float:
    h, m, s, frac = groups
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / (10 ** len(frac))


def normalize_subtitles(raw: str) -> NormalizedText:
    """Parse WebVTT or SRT into joined cue text, preserving per-cue timings and
    the character span each cue occupies in the joined text."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", raw.strip())
    cues: list[SubtitleCue] = []
    pieces: list[str] = []
    cursor = 0
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].strip().upper() == "WEBVTT":
            lines = lines[1:]
        if lines and _CUE_INDEX.match(lines[0].strip()):
            lines = lines[1:]
        if not lines:
            continue
        timing = _CUE_TIMING.search(lines[0])
        if timing is None:
            continue
        start = _parse_ts(timing.groups()[0:4])
        end = _parse_ts(timing.groups()[4:8])
        cue_text = _collapse_whitespace(" ".join(lines[1:]))
        cue_text = _TAG.sub("", cue_text)  # VTT inline tags like <c>, <i>
        if not cue_text:
            continue
        if pieces:
            cursor += 1  # the "\n" joiner we will insert
        text_start = cursor
        pieces.append(cue_text)
        cursor += len(cue_text)
        cues.append(SubtitleCue(start=start, end=end, text=cue_text,
                                text_start=text_start, text_end=cursor))
    joined = "\n".join(pieces)
    if not joined:
        raise NormalizationError("No subtitle cues could be parsed")
    return NormalizedText(text=joined, language=detect_language(joined), cues=tuple(cues))


def normalize_plain(raw: str) -> NormalizedText:
    text = _collapse_whitespace(raw)
    if not text:
        raise NormalizationError("Source contained no readable text after normalization")
    return NormalizedText(text=text, language=detect_language(text))


def normalize_bytes(data: bytes, media_type: str) -> NormalizedText:
    """Dispatch on media type. Raises NormalizationError for unsupported types or
    undecodable bytes so the caller records a failure instead of fabricating text."""
    media = (media_type or "").split(";", 1)[0].strip().lower()
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            raw = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise NormalizationError(f"Bytes are not valid UTF-8 text: {exc}") from None
    if media in _HTML_TYPES:
        result = normalize_html(raw)
    elif media in _SUBTITLE_TYPES:
        result = normalize_subtitles(raw)
    elif media in _PLAIN_TYPES:
        result = normalize_plain(raw)
    else:
        raise NormalizationError(f"Unsupported media type for normalization: {media!r}")
    if not result.text:
        raise NormalizationError("Normalization produced empty text")
    return result
