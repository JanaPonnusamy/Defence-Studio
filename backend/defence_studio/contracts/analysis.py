"""Typed contracts for F4 — Source Analysis, Normalization & Evidence Extraction.

F4 consumes the *real* artifacts produced by F3 Content Acquisition (see
``contracts/acquisition.py`` and ``storage/artifacts.py``) and converts them into
structured, traceable research material for a later human-review phase (F5).

Boundary rules these shapes enforce:

* Extraction never asserts truth. A claim is a *verbatim span* lifted from a
  normalized source, linked to that source and byte artifact — not a verified
  fact. Qualifiers, negations, numbers and designations survive because the
  claim text is the exact source substring, never a paraphrase.
* The original acquired bytes are preserved untouched by F3; F4 reads them
  read-only and stores a separate *normalized* representation plus a hash
  computed over that clearly-defined normalized content.
* Duplicate grouping records relationships and their basis; it never merges or
  deletes an independent source, and similarity is never treated as
  corroboration or proof.
* Nothing is fabricated: a value that was not supplied stays absent, paired
  with a status explaining why.

These contracts sit on the locked F1 job/stage/artifact identity model and the
F2 durable worker; they introduce no parallel project/job/source identity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


ANALYSIS_REQUEST_SCHEMA = "defence-studio.analysis-request.v1"
ANALYSIS_RESULT_SCHEMA = "defence-studio.analysis-result.v1"
NORMALIZED_SOURCE_SCHEMA = "defence-studio.normalized-source.v1"
EVIDENCE_REPORT_SCHEMA = "defence-studio.evidence-report.v1"

# The locked pipeline stage F4 executes under (see contracts/jobs.py). Its
# predecessor is `topic_search_and_source_collection` (F3 acquisition).
ANALYSIS_STAGE = "extraction_validation_normalization_deduplication"

# Deterministic processor versions. These are recorded on every output so a
# reviewer can tell exactly which logic produced a result and re-derive it.
NORMALIZER_VERSION = "defence-studio.normalizer.v1"
EXTRACTOR_VERSION = "defence-studio.claim-extractor.deterministic.v1"
DEDUP_VERSION = "defence-studio.dedup.v1"

# Artifact types F4 reads and writes (within the locked jobs/<job_id>/ layout).
# ACQUISITION_RESULT_ARTIFACT mirrors the real F3 output type — see
# defence_studio.acquisition.handler.ACQUISITION_MANIFEST_ARTIFACT_TYPE, the
# source of truth. Duplicated here (not imported) to keep contracts/analysis.py
# free of a dependency on the acquisition package, consistent with how other
# locked pipeline identifiers in this file are declared.
ACQUISITION_RESULT_ARTIFACT = "acquisition_manifest"
ANALYSIS_RESULT_ARTIFACT = "analysis_result"


class NormalizationStatus(str, Enum):
    NORMALIZED = "NORMALIZED"
    FAILED = "FAILED"


class DuplicateBasis(str, Enum):
    """Why two source records were grouped. Ordered from strongest to weakest;
    weaker-than-threshold similarity is never grouped at all."""

    SAME_CANONICAL_URL = "SAME_CANONICAL_URL"          # the same URL acquired twice
    IDENTICAL_CONTENT_HASH = "IDENTICAL_CONTENT_HASH"  # identical bytes, different URLs
    NEAR_DUPLICATE_TEXT = "NEAR_DUPLICATE_TEXT"        # slightly modified copy (Jaccard >= threshold)


class ClaimGroupBasis(str, Enum):
    SAME_NORMALIZED_TEXT = "SAME_NORMALIZED_TEXT"
    NEAR_DUPLICATE_TEXT = "NEAR_DUPLICATE_TEXT"


class ConflictBasis(str, Enum):
    NEGATION_MISMATCH = "NEGATION_MISMATCH"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"


class PassageKind(str, Enum):
    TEXT_SPAN = "TEXT_SPAN"
    SUBTITLE_CUE = "SUBTITLE_CUE"


@dataclass(frozen=True)
class AnalysisLimits:
    """Per-request resource bounds. The service clamps a request against hard
    ceilings; a request may only ask for the same or less."""

    max_sources: int = 50
    max_bytes_per_source: int = 25 * 1024 * 1024
    max_text_chars: int = 2 * 1024 * 1024
    max_claims_per_source: int = 200
    # Near-duplicate grouping threshold over token-shingle Jaccard similarity.
    # Independent reports on the same subject typically fall below this; it must
    # never be read as proof two publishers are independent or that a claim is true.
    near_duplicate_threshold: float = 0.82
    min_claim_chars: int = 25
    max_claim_chars: int = 600


@dataclass(frozen=True)
class AnalysisRequestV1:
    job_id: str
    owner_id: str
    # Relative URI of the F3 acquisition-result JSON artifact under jobs/<job_id>/.
    acquisition_result_uri: str
    acquisition_result_sha256: str
    topic: str = ""
    language: str = ""
    project_id: str | None = None
    limits: AnalysisLimits = field(default_factory=AnalysisLimits)
    schema_version: str = ANALYSIS_REQUEST_SCHEMA


# ---------------------------------------------------------------------------
# Normalized sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OriginalArtifactRef:
    """Pointer to the F3-acquired bytes this normalized record derives from.

    ``sha256`` is the digest F3 recorded over the original bytes; F4 re-verifies
    it against what is actually on disk before trusting the content."""

    artifact_type: str
    uri: str
    sha256: str
    bytes: int
    media_type: str


@dataclass(frozen=True)
class SourceDates:
    """Distinct date roles kept separate per the core design; any may be absent."""

    published_at: str | None = None
    acquired_at: str | None = None


@dataclass(frozen=True)
class NormalizedSourceV1:
    source_id: str
    target_id: str
    status: NormalizationStatus
    source_type: str | None
    media_type: str | None
    requested_url: str
    canonical_url: str
    final_url: str | None
    language_detected: str
    normalized_text: str
    normalized_sha256: str
    normalized_char_count: int
    original_artifact: OriginalArtifactRef | None
    original_sha256_verified: bool
    declared_metadata: dict
    dates: SourceDates
    warnings: tuple[str, ...] = ()
    normalization_version: str = NORMALIZER_VERSION
    schema_version: str = NORMALIZED_SOURCE_SCHEMA


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateGroupV1:
    group_id: str
    basis: DuplicateBasis
    member_source_ids: tuple[str, ...]
    representative_source_id: str
    similarity: float | None
    explanation: str


# ---------------------------------------------------------------------------
# Claims and evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidencePassageV1:
    """Exact supporting span, with offsets into the normalized text so a
    reviewer can locate it precisely. For subtitles, cue timings are preserved."""

    kind: PassageKind
    source_id: str
    artifact_uri: str | None
    text: str
    start_offset: int
    end_offset: int
    timestamp_start: float | None = None
    timestamp_end: float | None = None


@dataclass(frozen=True)
class CandidateEntities:
    """Deterministically detected, UNTYPED candidate entities plus reliably
    detectable quantities and dates. F4 does not classify person/org/location
    types — that needs a later NER/AI phase — so it does not pretend to.
    ``designations`` captures military/technical alphanumeric designators."""

    named: tuple[str, ...] = ()
    quantities: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    designations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractedClaimV1:
    claim_id: str
    claim_text: str          # verbatim span from normalized text; never paraphrased
    source_id: str
    original_language: str
    normalized_language: str
    passage: EvidencePassageV1
    source_published_at: str | None
    acquisition_timestamp: str | None
    entities: CandidateEntities
    qualifiers: tuple[str, ...]   # hedges found, e.g. "reportedly", "according to"
    has_negation: bool
    is_explicit: bool             # always True in F4: only verbatim explicit spans
    warnings: tuple[str, ...]
    extraction_method: str
    extractor_version: str = EXTRACTOR_VERSION


@dataclass(frozen=True)
class ClaimGroupV1:
    """Claims that appear to state the same assertion. This is a textual
    relationship ONLY — never corroboration, independence, or proof of truth."""

    group_id: str
    basis: ClaimGroupBasis
    member_claim_ids: tuple[str, ...]
    similarity: float | None
    note: str = "Textual similarity only; not corroboration or verification."


@dataclass(frozen=True)
class ClaimConflictV1:
    group_id: str
    basis: ConflictBasis
    claim_id_a: str
    claim_id_b: str
    explanation: str
    flagged_for_review: bool = True


# ---------------------------------------------------------------------------
# Warnings / failures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessingWarning:
    code: str
    message: str
    source_id: str | None = None


@dataclass(frozen=True)
class ProcessingFailure:
    code: str
    message: str
    retryable: bool
    source_id: str | None = None


@dataclass(frozen=True)
class AnalysisVersions:
    normalization_version: str = NORMALIZER_VERSION
    extractor_version: str = EXTRACTOR_VERSION
    dedup_version: str = DEDUP_VERSION
    schema_version: str = ANALYSIS_RESULT_SCHEMA


@dataclass(frozen=True)
class AnalysisResultV1:
    job_id: str
    analysis_timestamp: str
    # Identity of the exact inputs this result was derived from, so repeat runs
    # over unchanged inputs are detectable and need not be reproduced.
    input_acquisition_uri: str
    input_acquisition_sha256: str
    input_digest: str
    status: str
    normalized_sources: tuple[NormalizedSourceV1, ...]
    duplicate_groups: tuple[DuplicateGroupV1, ...]
    claims: tuple[ExtractedClaimV1, ...]
    claim_groups: tuple[ClaimGroupV1, ...]
    conflicts: tuple[ClaimConflictV1, ...]
    warnings: tuple[ProcessingWarning, ...]
    failures: tuple[ProcessingFailure, ...]
    sources_total: int
    sources_normalized: int
    sources_failed: int
    claims_total: int
    versions: AnalysisVersions = field(default_factory=AnalysisVersions)
    schema_version: str = ANALYSIS_RESULT_SCHEMA


def _clean(value):
    """asdict() post-processing: Enums -> their value; preserve the
    absent-vs-present distinction (None stays None) through serialization."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def result_to_dict(result: AnalysisResultV1) -> dict:
    return _clean(asdict(result))
