"""Typed contracts for F5 — Evidence Review & Approval.

F5 is a human-review boundary, not an automatic fact-verification system. It
consumes the real, versioned artifacts F3 (acquisition) and F4 (analysis)
already produced and records explicit, authenticated human decisions over
them. It introduces no parallel project/job/source/claim identity: a review
decision always references the exact `job_id` + `claim_id`/`source_id` +
artifact SHA-256 the reviewer actually looked at.

Boundary rules these shapes enforce:

* A decision never mutates the artifact it reviews. F4's `analysis_result`
  and F3's `acquisition_manifest` artifacts are read-only inputs here.
* A decision is always pinned to `reviewed_artifact_sha256` — the exact bytes
  of the analysis/acquisition artifact the reviewer saw. If a newer artifact
  version exists for the job, submitting against the old hash is a stale
  submission and is rejected (see `services/review.py::StaleRevision`); the
  reviewer must explicitly re-review the current revision.
* Decisions are revisable (a human may change their mind), but every change
  is optimistic-concurrency-checked against `expected_revision` and appended
  to an immutable history table — nothing is physically overwritten or
  deleted. The four decision states below are reachable from any other state
  (and from no prior decision); the enforced constraint is revision-exactness
  and optimistic concurrency, not a restrictive one-way state graph.
* `APPROVED_FOR_USE` records only that an authenticated reviewer judged this
  claim/source acceptable for potential downstream use, against the exact
  evidence revision cited. It does NOT certify the claim as objectively true,
  does not independently verify the source, and does not authorize any
  specific downstream script/content. `REJECTED`, `NEEDS_MORE_EVIDENCE`, and
  `CONFLICT_UNRESOLVED` block downstream use until superseded by a later
  explicit decision over the latest revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


REVIEW_DECISION_SCHEMA = "defence-studio.review-decision.v1"
REVIEW_HISTORY_SCHEMA = "defence-studio.review-history.v1"

# The locked pipeline stage F5 review activity is associated with (see
# contracts/jobs.py JOB_STAGES[4]). Review decisions do not themselves drive
# this stage's PENDING/RUNNING/... transition; they are a separate durable
# record layered on top of it, consistent with how F3/F4 artifacts sit under
# their stages without the artifact table owning stage status.
REVIEW_STAGE = "evidence_analysis_and_review"


class ReviewDecision(str, Enum):
    APPROVED_FOR_USE = "APPROVED_FOR_USE"
    REJECTED = "REJECTED"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"


REVIEW_DECISIONS = frozenset(member.value for member in ReviewDecision)


class ReviewSubjectType(str, Enum):
    CLAIM = "CLAIM"
    SOURCE = "SOURCE"


@dataclass(frozen=True)
class ReviewDecisionRequestV1:
    """One reviewer submission. `expected_revision` is 0 when the reviewer is
    recording the first-ever decision for this subject; otherwise it must
    equal the current stored `revision` or the submission is rejected as a
    concurrent-conflict (not silently merged/overwritten)."""

    decision: ReviewDecision
    reviewed_artifact_sha256: str
    expected_revision: int
    note: str = ""
    schema_version: str = REVIEW_DECISION_SCHEMA


@dataclass(frozen=True)
class ReviewDecisionRecordV1:
    """Current decision for one claim or source within one job. Mutable in
    place (one row per subject) but every change is mirrored into an
    append-only `ReviewHistoryEntryV1` row first within the same transaction."""

    job_id: str
    subject_type: ReviewSubjectType
    subject_id: str
    decision: ReviewDecision
    reviewed_artifact_type: str
    reviewed_artifact_sha256: str
    reviewer_id: str
    note: str
    revision: int
    created_at: str
    updated_at: str
    schema_version: str = REVIEW_DECISION_SCHEMA


@dataclass(frozen=True)
class ReviewHistoryEntryV1:
    """Append-only audit record. Never updated or deleted; a correction is a
    new entry with a higher `revision`."""

    history_id: str
    job_id: str
    subject_type: ReviewSubjectType
    subject_id: str
    decision: ReviewDecision
    reviewed_artifact_type: str
    reviewed_artifact_sha256: str
    reviewer_id: str
    note: str
    revision: int
    previous_decision: ReviewDecision | None
    occurred_at: str
    idempotency_key: str
    schema_version: str = REVIEW_HISTORY_SCHEMA
