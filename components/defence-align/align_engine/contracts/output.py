"""Versioned alignment-output contract (``defence-align.output.v1``).

Design rules enforced here and by the service:
- Token kind is explicit (``word`` | ``punctuation`` | ``segment``). Only
  ``word``/``segment`` tokens carry timing; punctuation is preserved for exact
  reconstruction but never claimed to be a spoken word.
- ``actual_granularity`` reports what the engine truly produced. If an engine
  only emits sentence-level timing, kinds are ``segment`` and this field says
  ``sentence`` — word-level is never claimed falsely.
- ``is_synthetic`` is true only for the deterministic test aligner; its
  timings are test data, not measured alignment of real speech.
- Confidence is optional and only ever carries engine-provided values.
- ``artifact_sha256`` is the digest of the canonical body EXCLUDING
  ``created_at`` and ``artifact_sha256`` themselves, so identical inputs +
  identical engine output serialize to an identical hash.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from align_engine._hashing import digest

OUTPUT_SCHEMA = "defence-align.output.v1"
_SHA256 = r"^[0-9a-f]{64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WordRecord(StrictModel):
    token_id: str
    kind: Literal["word", "punctuation", "segment"]
    original_text: str              # exact substring of the original transcript
    normalized_text: str            # traceable normalized form (original preserved above)
    char_start: int = Field(ge=0)   # offset into the segment's original_text
    char_end: int = Field(ge=0)
    start: float | None = None      # seconds; None when not aligned / non-spoken
    end: float | None = None
    confidence: float | None = None  # engine-provided only; never invented
    is_uncertain: bool = False
    aligned: bool = False


class OutputSegment(StrictModel):
    segment_id: str
    original_text: str
    tokens: list[WordRecord]


class OutputSection(StrictModel):
    section_id: str
    segments: list[OutputSegment]


class UnalignedToken(StrictModel):
    token_id: str
    segment_id: str
    original_text: str
    reason: str                     # machine-readable reason code


class ValidationIssue(StrictModel):
    code: str
    severity: Literal["ERROR", "WARNING", "INFO"]
    detail: str
    token_id: str | None = None


class ValidationResult(StrictModel):
    passed: bool
    validator_version: str
    issues: list[ValidationIssue] = Field(default_factory=list)


class AlignmentOutputV1(StrictModel):
    schema_version: Literal["defence-align.output.v1"]
    alignment_job_id: str
    project_id: str
    audio_artifact_id: str
    audio_sha256: str = Field(pattern=_SHA256)
    transcript_id: str
    transcript_sha256: str = Field(pattern=_SHA256)
    language: Literal["ta", "en"]
    audio_duration_seconds: float = Field(ge=0)
    engine_name: str
    engine_version: str
    is_synthetic: bool
    requested_granularity: Literal["word", "sentence"]
    actual_granularity: Literal["word", "sentence"]
    sections: list[OutputSection]
    unaligned_tokens: list[UnalignedToken] = Field(default_factory=list)
    validation: ValidationResult
    created_at: str
    artifact_sha256: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")


def _hashable_body(output: dict) -> dict:
    """The body used for the content hash: everything except creation-time
    metadata and the hash field itself."""
    return {k: v for k, v in output.items() if k not in ("created_at", "artifact_sha256")}


def output_artifact_digest(output: dict) -> str:
    return digest(_hashable_body(output))


def build_output(body: dict) -> dict:
    """Validate a draft output body, stamp its deterministic ``artifact_sha256``,
    and return the canonical dict. ``created_at`` must already be set by the
    caller (it is excluded from the hash)."""
    draft = {**body, "artifact_sha256": ""}
    AlignmentOutputV1.model_validate(draft)
    sealed = {**draft, "artifact_sha256": output_artifact_digest(draft)}
    return AlignmentOutputV1.model_validate(sealed).model_dump()
