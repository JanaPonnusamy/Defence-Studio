"""Versioned alignment-request contract (``defence-align.request.v1``).

Strict (``extra="forbid"``): unknown fields, invalid language codes, empty
transcripts, duplicate section/segment IDs, and malformed hashes are rejected
at parse time. The original transcript text is preserved exactly; any
``normalized_text`` is carried as *separate* optional metadata and never
replaces the original. Tamil is never silently transliterated and English
technical terms are never stripped — this contract only transports text, it
does not rewrite it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from align_engine._hashing import digest

REQUEST_SCHEMA = "defence-align.request.v1"
SUPPORTED_LANGUAGES = ("ta", "en")
_SHA256 = r"^[0-9a-f]{64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscriptSegment(StrictModel):
    segment_id: str = Field(min_length=1, max_length=200)
    original_text: str = Field(min_length=1, max_length=20000)
    # Optional, kept strictly separate from original_text; never used to
    # overwrite or re-tokenize the original.
    normalized_text: str | None = Field(default=None, max_length=20000)
    pronunciation: dict | None = None


class TranscriptSection(StrictModel):
    section_id: str = Field(min_length=1, max_length=200)
    segments: list[TranscriptSegment] = Field(min_length=1, max_length=5000)


class AlignmentConfig(StrictModel):
    engine: str = Field(default="deterministic-test", min_length=1, max_length=120)
    granularity: Literal["word", "sentence"] = "word"
    # A requested granularity is a *request*; the output always reports the
    # granularity actually produced by the engine (never mislabeled).
    allow_synthetic: bool = Field(
        default=False,
        description="Must be true to accept the deterministic-test (synthetic) engine. "
                    "Prevents synthetic timings being mistaken for measured alignment.",
    )


class AlignmentRequestV1(StrictModel):
    schema_version: Literal["defence-align.request.v1"]
    alignment_job_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    audio_artifact_id: str = Field(min_length=1, max_length=200)
    # Relative reference resolved against the configured input root (never an
    # absolute or traversing path — validated in align_engine.audio.paths).
    audio_path: str = Field(min_length=1, max_length=2000)
    audio_sha256: str = Field(pattern=_SHA256)
    transcript_id: str = Field(min_length=1, max_length=200)
    transcript_sha256: str = Field(pattern=_SHA256)
    language: Literal["ta", "en"]
    sections: list[TranscriptSection] = Field(min_length=1, max_length=2000)
    config: AlignmentConfig = Field(default_factory=AlignmentConfig)

    @model_validator(mode="after")
    def _unique_ids_and_nonempty(self) -> "AlignmentRequestV1":
        section_ids: set[str] = set()
        segment_ids: set[str] = set()
        for section in self.sections:
            if section.section_id in section_ids:
                raise ValueError(f"Duplicate section_id: {section.section_id!r}")
            section_ids.add(section.section_id)
            if not section.segments:
                raise ValueError(f"Section {section.section_id!r} has no segments")
            for segment in section.segments:
                if segment.segment_id in segment_ids:
                    raise ValueError(f"Duplicate segment_id: {segment.segment_id!r}")
                segment_ids.add(segment.segment_id)
        return self


def transcript_digest(request: AlignmentRequestV1) -> str:
    """Deterministic digest of the transcript content only (not the envelope).

    This is what ``transcript_sha256`` must equal: a hash over the ordered
    sections/segments and their text, independent of job/project identity, so
    the same transcript always yields the same digest regardless of which job
    references it.
    """
    body = [
        {
            "section_id": section.section_id,
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "original_text": segment.original_text,
                    "normalized_text": segment.normalized_text,
                    "pronunciation": segment.pronunciation,
                }
                for segment in section.segments
            ],
        }
        for section in request.sections
    ]
    return digest({"schema_version": REQUEST_SCHEMA, "language": request.language, "sections": body})
