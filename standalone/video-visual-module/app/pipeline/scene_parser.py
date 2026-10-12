"""
Scene Parser (spec §3, §13, §30).

Two inputs are supported:
  1. Plain narration text -> paragraphs become scenes, sentences become
     narration segments. Timing is ESTIMATED from word count at a configured
     words-per-minute (explicitly marked timing_source=ESTIMATED).
  2. Timed segments from an external timing contract (e.g. an audio module's
     sentence start/end output, spec §30) -> timing_source=SUPPLIED (was "PROVIDED" in v1.0). This
     module never reads another module's internals; it only consumes the
     contract {text, start, end[, scene]}.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.errors import VideoModuleError

_SENT_RE = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9])")
MAX_SEGMENTS_PER_SCENE = 8
MAX_TEXT_CHARS = 400_000


@dataclass
class SegmentDraft:
    segment_id: str
    text: str
    start: float
    end: float
    timing_source: str = "ESTIMATED"
    normalized: dict | None = None

    def as_dict(self) -> dict:
        d = {"segment_id": self.segment_id, "text": self.text, "start": self.start, "end": self.end,
             "timing_source": self.timing_source}
        if self.normalized:
            d["normalized"] = self.normalized
        return d


@dataclass
class SceneDraft:
    title: str
    start_time: float
    end_time: float
    segments: list[SegmentDraft] = field(default_factory=list)
    timing_source: str = "ESTIMATED"
    importance: str = "NORMAL"
    transition: str = "CUT"

    @property
    def narration_text(self) -> str:
        return " ".join(s.text for s in self.segments)


def split_sentences(text: str) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []
    return [s.strip() for s in _SENT_RE.split(text) if s.strip()]


def estimate_seconds(sentence: str, wpm: float) -> float:
    words = len(sentence.split())
    return round(max(1.2, words * 60.0 / max(60.0, wpm) + 0.3), 3)  # +0.3s natural pause


def _title_for(sentence: str, idx: int) -> str:
    words = re.sub(r"[^\w\s'-]", "", sentence).split()
    return (" ".join(words[:7]) or f"Scene {idx + 1}")[:120]


def _chunk(sentences: list[str], size: int) -> list[list[str]]:
    if len(sentences) <= size:
        return [sentences]
    n = math.ceil(len(sentences) / size)
    per = math.ceil(len(sentences) / n)
    return [sentences[i:i + per] for i in range(0, len(sentences), per)]


def parse_narration(text: str, *, wpm: float = 150.0, start_offset: float = 0.0,
                    segment_prefix: str = "seg") -> list[SceneDraft]:
    if not text or not text.strip():
        raise VideoModuleError("MISSING_NARRATION_ANCHOR", "Narration text is empty; scenes need narration anchors.")
    if len(text) > MAX_TEXT_CHARS:
        raise VideoModuleError("PAYLOAD_TOO_LARGE", "Narration text too long.")
    paragraphs = [p for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    scenes: list[SceneDraft] = []
    cursor = float(start_offset)
    seg_no = 0
    for para in paragraphs:
        for group in _chunk(split_sentences(para), MAX_SEGMENTS_PER_SCENE):
            if not group:
                continue
            segs = []
            for sentence in group:
                seg_no += 1
                dur = estimate_seconds(sentence, wpm)
                segs.append(SegmentDraft(f"{segment_prefix}-{seg_no:04d}", sentence, round(cursor, 3), round(cursor + dur, 3)))
                cursor += dur
            scenes.append(SceneDraft(title=_title_for(group[0], len(scenes)), start_time=segs[0].start,
                                     end_time=segs[-1].end, segments=segs))
    if scenes:
        scenes[0].importance = "HIGH"  # opening / hook
    return scenes


MIN_SEGMENT_SECONDS = 0.05          # a supplied segment shorter than this is malformed
MAX_SEGMENT_SECONDS = 600.0          # one narration segment longer than 10 min is malformed
MAX_WORDS_PER_SECOND = 15.0          # faster than any real speech -> timing inconsistent with text
OVERLAP_TOLERANCE_S = 0.010          # overlaps up to 10 ms (rounding jitter) are normalised, larger ones rejected


def scenes_from_timed_segments(segments: list[dict], *, segment_prefix: str = "seg") -> list[SceneDraft]:
    """Validate a supplied timing contract (timing_source=SUPPLIED). Rules (documented in API_CONTRACTS):
    missing text/start/end -> MISSING_NARRATION_ANCHOR; non-finite, negative, zero/negative or too short/long
    durations, duplicate segment ids, implausible speech rate, out-of-order or overlapping segments ->
    INVALID_TIMING. Overlaps <= 10 ms are normalised by moving the later start to the previous end (recorded
    in `normalized`). Gaps (pauses) are allowed; the planner holds the previous visual across them."""
    if not segments:
        raise VideoModuleError("MISSING_NARRATION_ANCHOR", "No timed narration segments supplied.")
    parsed: list[tuple[SegmentDraft, str | None]] = []
    prev_end = -1.0
    seen_ids: set[str] = set()
    for i, raw in enumerate(segments):
        if not isinstance(raw, dict) or raw.get("text") is None or raw.get("start") is None or raw.get("end") is None:
            raise VideoModuleError("MISSING_NARRATION_ANCHOR", f"Segment {i} needs text, start and end (timestamps missing).")
        try:
            text = " ".join(str(raw["text"]).split())
            start, end = float(raw["start"]), float(raw["end"])
        except (TypeError, ValueError) as exc:
            raise VideoModuleError("INVALID_TIMING", f"Segment {i} start/end must be numbers (seconds).") from exc
        if not text:
            raise VideoModuleError("MISSING_NARRATION_ANCHOR", f"Segment {i} has empty text.")
        if not (math.isfinite(start) and math.isfinite(end)):
            raise VideoModuleError("INVALID_TIMING", f"Segment {i} has non-finite timing.")
        if start < 0 or end <= start:
            raise VideoModuleError("INVALID_TIMING", f"Segment {i} has invalid timing (need 0 <= start < end; got "
                                                     f"start={start}, end={end}).")
        dur = end - start
        if dur < MIN_SEGMENT_SECONDS:
            raise VideoModuleError("INVALID_TIMING", f"Segment {i} duration {dur:.3f}s is below {MIN_SEGMENT_SECONDS}s.")
        if dur > MAX_SEGMENT_SECONDS:
            raise VideoModuleError("INVALID_TIMING", f"Segment {i} duration {dur:.1f}s exceeds {MAX_SEGMENT_SECONDS:.0f}s.")
        words = len(text.split())
        if words >= 3 and words / dur > MAX_WORDS_PER_SECOND:
            raise VideoModuleError("INVALID_TIMING", f"Segment {i}: {words} words in {dur:.3f}s is faster than any real "
                                                     "speech; timing is inconsistent with the text.")
        normalized = None
        if start < prev_end - 1e-9:
            overlap = prev_end - start
            if overlap > OVERLAP_TOLERANCE_S + 1e-9:
                raise VideoModuleError("INVALID_TIMING", f"Segment {i} overlaps the previous segment by {overlap:.3f}s; "
                                                         "segments must be ordered and non-overlapping.")
            normalized = {"start_moved_by_s": round(overlap, 4), "rule": "overlap<=10ms clamped to previous end"}
            start = prev_end
            if end - start < MIN_SEGMENT_SECONDS:
                raise VideoModuleError("INVALID_TIMING", f"Segment {i} is too short after overlap normalisation.")
        prev_end = end
        seg_id = str(raw.get("segment_id") or f"{segment_prefix}-{i + 1:04d}")[:64]
        if seg_id in seen_ids:
            raise VideoModuleError("INVALID_TIMING", f"Duplicate segment_id {seg_id!r} at segment {i}.")
        seen_ids.add(seg_id)
        seg = SegmentDraft(seg_id, text[:2000], round(start, 3), round(end, 3))
        if normalized:
            seg.normalized = normalized
        parsed.append((seg, raw.get("scene")))

    scenes: list[SceneDraft] = []
    current: list[SegmentDraft] = []
    current_label = None
    for seg, label in parsed:
        boundary = bool(current) and (
            (label is not None and label != current_label)
            or (label is None and len(current) >= MAX_SEGMENTS_PER_SCENE)
            or (seg.start - current[-1].end > 2.0)
        )
        if boundary:
            scenes.append(_mk_scene(current, current_label, len(scenes)))
            current = []
        current.append(seg)
        current_label = label if label is not None else current_label
    if current:
        scenes.append(_mk_scene(current, current_label, len(scenes)))
    scenes[0].importance = "HIGH"
    return scenes


def _mk_scene(segs: list[SegmentDraft], label, idx: int) -> SceneDraft:
    title = str(label)[:120] if label else _title_for(segs[0].text, idx)
    for s in segs:
        s.timing_source = "SUPPLIED"
    return SceneDraft(title=title, start_time=segs[0].start, end_time=segs[-1].end, segments=segs,
                      timing_source="SUPPLIED")
