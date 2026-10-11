"""Phase 3 source-grounded script contracts.

Two boundaries live here, deliberately kept separate:

1. The *provider I/O* boundary -- ``ScriptRequestV1`` / ``ScriptResponseV1`` /
   ``validate_response`` -- the minimal, strict shape a text-generation provider
   must return and that can be validated offline against an approved evidence
   package. This boundary predates the Phase 3 service and other code (and
   tests) depend on it unchanged.

2. The *persisted artifact* boundary -- ``ScriptRevisionV1`` and friends -- the
   immutable, versioned, source-grounded script revision the service stores,
   reviews, and approves. A revision carries its own structure (title, ordered
   narration sections, closing), the machine-readable evidence mapping kept
   separate from the spoken narration, deterministic word-count/duration
   figures with their estimation method recorded, provider/model + cost
   provenance, and the validation result.

Structural validation cannot establish truth or semantic faithfulness. Tamil
and English variants are independent; approving one never approves the other.
Every material factual statement must map to approved evidence claim IDs; a
script's existence is never evidence of approval.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from defence_studio.contracts.evidence import StrictModel, digest

# -- identities ------------------------------------------------------------

SCRIPT_REVISION_SCHEMA = "defence-studio.script-revision.v1"
SCRIPT_REQUEST_SCHEMA = "defence-studio.script-request.v1"
SCRIPT_RESPONSE_SCHEMA = "defence-studio.script-response.v1"
PROMPT_VERSION = "defence-studio.source-grounded-script.v1"

LANGUAGES = ("ta", "en")
# Decisions mirror the evidence-review lifecycle vocabulary but are script-scoped.
SCRIPT_REVIEW_DECISIONS = ("CHANGES_REQUESTED", "APPROVED")

# A script revision moves through these statuses. Each is an explicit, auditable
# state; approval is never implied by generation, validation, or export.
SCRIPT_STATUSES = (
    "VALIDATION_FAILED",     # generated but failed evidence/structural validation
    "AWAITING_REVIEW",       # passed validation, waiting for a human decision
    "CHANGES_REQUESTED",     # a reviewer asked for changes; regenerate a new revision
    "APPROVED",              # explicitly approved by a human, bound to this revision hash
    "SUPERSEDED",            # a newer approval or a changed package invalidated it
)

# How the estimated narration duration is derived from the word count. Stored on
# every revision so the figure is reproducible and never presented as an exact
# recording time.
DURATION_METHOD = "words-per-minute.v1"


# -- provider I/O boundary (unchanged public surface) ----------------------


class ScriptRequestV1(StrictModel):
    schema_version: Literal["defence-studio.script-request.v1"]
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: Literal["ta", "en"]
    model: str = Field(min_length=1, max_length=200)
    prompt_version: Literal["defence-studio.source-grounded-script.v1"]
    target_words: int = Field(default=1300, ge=100, le=5000)
    tone: str = Field(default="measured", min_length=1, max_length=200)
    audience: str = Field(default="general", min_length=1, max_length=200)
    depth: Literal["overview", "standard", "detailed"] = "standard"
    terminology: dict[str, str] = Field(default_factory=dict)
    # A configured budget, not a promise of actual measured cost.
    max_text_cost_inr: float = Field(default=10.0, gt=0)


class StatementV1(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    claim_ids: list[str] = Field(min_length=1)
    qualification: str = Field(min_length=1, max_length=2000)


class ScriptResponseV1(StrictModel):
    schema_version: Literal["defence-studio.script-response.v1"]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: Literal["ta", "en"]
    model: str = Field(min_length=1)
    statements: list[StatementV1] = Field(min_length=1, max_length=500)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    retries: int = Field(ge=0)
    measured_text_cost_inr: float = Field(ge=0)
    status: Literal["REQUIRES_HUMAN_REVIEW"]


def validate_response(response: dict, request: ScriptRequestV1, approved_package: dict) -> dict:
    value = ScriptResponseV1.model_validate(response)
    if approved_package.get("status") != "APPROVED" or not approved_package.get("approval") or not approved_package.get("ready"):
        raise ValueError("An approved evidence package is required")
    if (digest(approved_package) != request.package_sha256 or value.package_sha256 != request.package_sha256
        or value.request_sha256 != digest(request.model_dump()) or value.language != request.language or value.model != request.model):
        raise ValueError("Script response does not match request and approved evidence")
    allowed = set(approved_package["eligible_claim_ids"])
    for statement in value.statements:
        if not statement.text.strip() or not statement.qualification.strip() or not set(statement.claim_ids) <= allowed:
            raise ValueError("Every statement must cite approved claims and preserve qualification")
    if value.measured_text_cost_inr > request.max_text_cost_inr:
        raise ValueError("Measured text cost exceeds configured budget")
    return value.model_dump()


# -- persisted script-revision artifact ------------------------------------


class GenerationConfigV1(StrictModel):
    """Bounded, caller-supplied generation settings. No secret ever lives here."""
    language: Literal["ta", "en"]
    audience: str = Field(min_length=1, max_length=200)
    tone: str = Field(min_length=1, max_length=200)
    script_format: str = Field(min_length=1, max_length=200)
    target_duration_seconds: int = Field(ge=30, le=3600)
    target_words: int = Field(ge=100, le=5000)
    model: str = Field(min_length=1, max_length=200)
    prompt_version: Literal["defence-studio.source-grounded-script.v1"]
    max_text_cost_inr: float = Field(gt=0, le=1000)
    terminology: dict[str, str] = Field(default_factory=dict)


class EvidenceRefV1(StrictModel):
    """Machine-readable mapping from one narration section to approved evidence.

    Kept *out* of the spoken narration text so citation identifiers are never
    accidentally voiced. ``claim_ids`` must all be in the approved package's
    eligible set; ``source_ids`` are informational provenance for the reviewer.
    """
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    qualification: str = Field(default="", max_length=2000)


class NarrationSectionV1(StrictModel):
    section_id: str = Field(min_length=1, max_length=120)
    heading: str = Field(default="", max_length=400)
    # The spoken narration for this section. Free of citation IDs and metadata.
    narration: str = Field(min_length=1, max_length=20000)
    # Whether this section asserts material facts that require evidence support.
    requires_evidence: bool = True
    evidence: EvidenceRefV1 = Field(default_factory=EvidenceRefV1)
    # Optional presentation/transition note, clearly separated from narration.
    presentation_note: str = Field(default="", max_length=2000)


class UsageV1(StrictModel):
    """Provider usage + cost provenance. Measured vs estimated is explicit."""
    model: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=120)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    tokens_available: bool
    retries: int = Field(ge=0)
    measured_text_cost_inr: float | None = Field(default=None, ge=0)
    cost_is_measured: bool
    pricing_source: str = Field(min_length=1, max_length=200)
    currency: str = Field(default="INR", min_length=1, max_length=10)


class ValidationIssueV1(StrictModel):
    code: str = Field(min_length=1, max_length=120)
    severity: Literal["ERROR", "WARNING"]
    section_id: str = Field(default="", max_length=120)
    detail: str = Field(min_length=1, max_length=2000)


class ValidationResultV1(StrictModel):
    passed: bool
    validator_version: str = Field(min_length=1, max_length=120)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_at: str = Field(min_length=1)
    issues: list[ValidationIssueV1] = Field(default_factory=list)

    @model_validator(mode="after")
    def _passed_iff_no_errors(self):
        has_error = any(i.severity == "ERROR" for i in self.issues)
        if self.passed == has_error:
            raise ValueError("passed must be true exactly when there are no ERROR issues")
        return self


class WordCountV1(StrictModel):
    """Deterministic, testable narration sizing. An estimate, never an exact time."""
    words: int = Field(ge=0)
    words_per_minute: int = Field(gt=0, le=400)
    estimated_duration_seconds: int = Field(ge=0)
    method: Literal["words-per-minute.v1"]
    is_estimate: Literal[True] = True


class ScriptApprovalV1(StrictModel):
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: int = Field(gt=0)
    reviewer_id: str = Field(min_length=1)
    note: str = Field(min_length=1, max_length=4000)
    approved_at: str = Field(min_length=1)


class ScriptRevisionV1(StrictModel):
    schema_version: Literal["defence-studio.script-revision.v1"]
    job_id: str = Field(min_length=1)
    language: Literal["ta", "en"]
    script_type: Literal["narration"]
    version: int = Field(gt=0)
    # Binds this revision to the exact approved evidence package it was built on.
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=500)
    opening_hook: str = Field(default="", max_length=4000)
    sections: list[NarrationSectionV1] = Field(min_length=1, max_length=200)
    closing: str = Field(min_length=1, max_length=8000)
    config: GenerationConfigV1
    usage: UsageV1
    word_count: WordCountV1
    validation: ValidationResultV1
    status: Literal[
        "VALIDATION_FAILED", "AWAITING_REVIEW", "CHANGES_REQUESTED", "APPROVED", "SUPERSEDED"
    ]
    created_at: str = Field(min_length=1)
    # The eligible claim-id set from the approved package at generation time,
    # retained so validation and review are reproducible without re-reading it.
    eligible_claim_ids: list[str] = Field(default_factory=list)
    approval: ScriptApprovalV1 | None = None

    @model_validator(mode="after")
    def _status_integrity(self):
        if self.status == "APPROVED" and self.approval is None:
            raise ValueError("An APPROVED revision must carry an approval record")
        if self.status != "APPROVED" and self.approval is not None:
            raise ValueError("Only an APPROVED revision may carry an approval record")
        if self.status == "APPROVED" and not self.validation.passed:
            raise ValueError("A revision cannot be APPROVED while validation failed")
        if self.config.language != self.language:
            raise ValueError("Config language must match the revision language")
        if self.validation.package_sha256 != self.package_sha256:
            raise ValueError("Validation must be bound to this revision's package hash")
        return self


def validate_revision(value: dict) -> dict:
    """Validate a persisted revision artifact and return its canonical dict."""
    return ScriptRevisionV1.model_validate(value).model_dump()


# -- deterministic narration sizing ----------------------------------------


def count_words(text: str) -> int:
    """Count whitespace-delimited tokens.

    Tamil narration is written in Tamil script with spaces between words, so a
    whitespace split is a defensible, deterministic word count for both Tamil
    and English. The figure feeds a duration *estimate* only.
    """
    return len((text or "").split())


def narration_word_count(revision_body: dict) -> int:
    """Total spoken words across hook + every section narration + closing.

    Only spoken text is counted -- headings, presentation notes, and the
    machine-readable evidence mapping are excluded so they are never mistaken
    for narration or spoken aloud.
    """
    total = count_words(revision_body.get("opening_hook", ""))
    for section in revision_body.get("sections", []):
        total += count_words(section.get("narration", ""))
    total += count_words(revision_body.get("closing", ""))
    return total


def estimate_duration(words: int, words_per_minute: int = 150) -> dict:
    """Deterministic duration estimate. Explicitly an estimate, not a timing."""
    if words_per_minute <= 0:
        raise ValueError("words_per_minute must be positive")
    seconds = round(words / words_per_minute * 60)
    return {
        "words": words,
        "words_per_minute": words_per_minute,
        "estimated_duration_seconds": int(seconds),
        "method": DURATION_METHOD,
        "is_estimate": True,
    }
