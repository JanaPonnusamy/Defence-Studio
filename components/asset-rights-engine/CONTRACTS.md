# Contracts v1

All inputs are frozen dataclasses. Stable identifiers use 1–128 ASCII letters, digits,
underscore, period, colon or hyphen, beginning with a letter or digit. SHA-256 fields
require exactly 64 lowercase hexadecimal characters. Revisions are positive integers;
expected initial rights revision alone is zero. Timestamps must carry an explicit UTC
offset. Evaluation uses caller-supplied time, never an implicit wall clock.

AssetDescriptor metadata_version=1 supports image/video/audio/graphic/other. Name,
provenance, original source and creation/registration timestamps are required. Media
bytes, file existence and verification are never assumed. Known MIME must be compatible
with image/video/audio categories; size is a nonnegative integer. VALID means metadata
contract validation only. Registered descriptors are immutable; corrections use a new ID.

RightsRecord contract_version=1 states: UNKNOWN, PENDING_REVIEW, APPROVED, REJECTED,
EXPIRED, REVOKED. Persisted input APPROVED is rejected. Successful review creates a
separate approved review state over immutable pending content. Explicit rights basis,
licence name and evidence notes are required for approval. Custom licence names are
accepted as declarations, not interpreted as a known licence. URLs are inert strings.
Permissions and attribution requirement are tri-state Boolean/unknown. Unknown required
permissions block; false permissions block requested operations. A required attribution
must exactly match the declared text. Territory restrictions use uppercase two-letter
codes; omission of a territory blocks a restricted grant. Empty territory list declares
no territory restriction. Valid-from is inclusive; valid-until is exclusive.

UseContext records commercial, modification, distribution, territory, attribution text
and evaluated_at. Unrequested modification/distribution need no permission. Record
approval never implies a permission. An evaluation before its rights review blocks.

SceneBinding carries host_application, job_id, scene_id, plan_version, plan_sha256 and
optional content_digest. All fields participate in exact binding comparison. The host
must supply its authoritative approved plan. The core cannot authenticate that assertion.
AssignmentDescriptor contract_version=1 carries binding, asset ID, role, use context,
rights revision/hash and state. Assignment revision and identity live in RevisionView.
DRAFT/PENDING_REVIEW/BLOCKED may be supplied at creation or revision. APPROVED requires
review. A changed rights revision yields derived STALE. A changed scene plan produces
STALE_SCENE_BINDING at evaluation, without mutating or rebinding the historical record.

Every decision requires a nonempty note. First decision per entity revision wins;
additional decisions require a new revision. Idempotency keys are unique across all
review kinds. Their payload includes binding and usage context. Exact replay returns
one original event; all other reuse conflicts. Reviews reference content digests and
expected revisions. Approval of blocked assignments is refused.

Evaluation returns eligibility, blocker codes, warnings, human actions, rights revision,
rights hash and deterministic timestamp. Readiness requires current approved rights,
compatible use, exact scene binding, current assignment approval, and matching usage.
Only evaluated_at may change without creating a new assignment revision. Readiness always
warns MEDIA_AVAILABILITY_UNVERIFIED; it is rights readiness, not render readiness.

The exported pure evaluator accepts an optional immutable ReviewEvent, checks decision,
revision, digest and time, and defaults to blocked without review evidence. For an
untrusted caller always use Engine.evaluate_effective_rights, which loads its own history.
Passing fabricated events to the pure function has no effect on persisted approval.

## ARE-A2 optional host contracts v1

HostScope, HostSceneKey, HostSceneContext, HostPrincipal and HostOperationRequest add
immutable framework-neutral assertions. Positive integer host plan revisions map to
ARE-A1 plan_version strings. Full scene/plan equality and consistent approval assertions
are checked before gateway operations. Identifiers/assertions confer no authentication.
ARE-A1 contracts are unchanged. See [HOST_ADAPTER.md](docs/HOST_ADAPTER.md) for exact
validation, tenant ownership, trust levels, staleness and idempotency semantics.

## ARE-A3.1 persisted-database validation

Public signatures, schema version 1 and successful validate_database() response
{"valid": True, "schema_version": 1} remain unchanged. Invalid supported-schema structures,
ledger metadata or stored JSON/contracts/references raise existing DomainError with code
VALIDATION and table/row diagnostics. SQLite failures retain code DATABASE. Malformed
persisted JSON and missing migration rows never escape as raw parsing/indexing exceptions.
Validation rejects the input transaction without repairing or discarding records.

Validation is performed at construction and on explicit request, not before every call
on an already constructed instance. The trusted custodian must prevent external changes
during operations and quarantine rejected restores. This is an unreleased source candidate;
accepted 1.0.0/1.1.0 wheel behavior and historical reports remain unchanged.
