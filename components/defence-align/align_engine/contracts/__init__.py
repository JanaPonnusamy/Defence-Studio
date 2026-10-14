"""Versioned public JSON contracts for the alignment component.

These are the ONLY surface a future TTS module couples to (see
``INTEGRATION_CONTRACT.md``). They import nothing from Defence Studio Phase 1-3
or from the TTS component.
"""
from align_engine.contracts.request import (
    REQUEST_SCHEMA, SUPPORTED_LANGUAGES, AlignmentConfig, AlignmentRequestV1,
    TranscriptSection, TranscriptSegment, transcript_digest,
)
from align_engine.contracts.output import (
    OUTPUT_SCHEMA, AlignmentOutputV1, OutputSection, OutputSegment,
    UnalignedToken, ValidationIssue, ValidationResult, WordRecord,
    build_output, output_artifact_digest,
)

__all__ = [
    "REQUEST_SCHEMA", "SUPPORTED_LANGUAGES", "AlignmentConfig", "AlignmentRequestV1",
    "TranscriptSection", "TranscriptSegment", "transcript_digest",
    "OUTPUT_SCHEMA", "AlignmentOutputV1", "OutputSection", "OutputSegment",
    "UnalignedToken", "ValidationIssue", "ValidationResult", "WordRecord",
    "build_output", "output_artifact_digest",
]
