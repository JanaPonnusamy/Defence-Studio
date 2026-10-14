# Integration threat model â€” ARE-A3

Audit target: accepted 1.1.0, SQLite schema v1. This document assesses the actual standalone
component, not an inspected host deployment. No external host was accessed. Evidence and
probe commands are in [A3_VERIFICATION_REPORT.md](A3_VERIFICATION_REPORT.md).

Runtime evidence names such as `engine.py` resolve to `src/asset_rights_engine/engine.py`;
all line numbers refer to the unchanged accepted source. Test files are under `tests/`.

## Assets, boundaries and classifications

Protect scope/resource identity, approved plan/version/digest, rights/assignment revisions,
review decisions, reviewer provenance, storage integrity and availability. Boundaries are:
untrusted request -> authenticated host -> trusted adapter -> Engine -> component storage;
host state and component transactions are separate. Media storage is a further boundary.
A DTO is validated data; its approval/principal are assertions; only a verified host session
and policy decision could establish authenticated authority. No such host ships here.

VERIFIED means the named local control was inspected and exercised; it does not certify
production deployment. PARTIALLY VERIFIED means a conditional/local control exists but the
production boundary remains unverified. NOT IMPLEMENTED means the required production
control is absent. NOT APPLICABLE is reserved for a feature excluded from the scoped
single-machine architecture, not for avoiding a relevant threat. All listed threats apply;
none are dismissed as NOT APPLICABLE. Test IDs refer to INTEGRATION_ACCEPTANCE_PLAN.md.

## T01 â€” Forged reviewer identity â€” NOT IMPLEMENTED

Scenario: attacker supplies the ID of a granted reviewer. Existing control: identifier
syntax, nonempty review note and reviewer/principal equality; there is no session binding.
Evidence: `adapter_contracts.py:HostPrincipal.__post_init__` (87),
`host_adapter.py:AdapterGateway._review_identity` (73),
`simulator.py:SyntheticHost.operation_context` (73); probe P02 accepted an independently
constructed copy of a granted synthetic ID. Residual: string equality cannot establish
who acted; forged decisions are possible if the fixture or raw core is exposed.
Required production control: derive a non-reusable principal subject from authenticated
host state, ignore caller identity as an authority source and record identity provenance.
Regression: A01, A02, A22.

## T02 â€” Unauthenticated requests â€” NOT IMPLEMENTED

Scenario: a caller invokes an operation with structurally valid DTOs but no authenticated
session. Existing control: missing HostPrincipal is rejected; no authentication service,
session check or Engine authorization callback exists. Evidence:
`adapter_contracts.py:HostOperationRequest.__post_init__` (100),
`host_adapter.py:HostAdapter.operation_context` (11) is a Protocol obligation, not an
implementation; `engine.py:Engine.review_rights` (205); P01 succeeds directly.
Residual: access to the library or service process is access to the engine's authority.
Required control: deny at a trusted entry boundary; do not expose arbitrary Engine methods,
providers, CLI paths or fixture grants. Regression: A01, A07.

## T03 â€” Cross-tenant access â€” PARTIALLY VERIFIED

Scenario: IDs overlap between tenants or a caller selects another tenant's engine file.
Existing control: scope equality in DTOs, fixture registry and resource checks before
engine use. Evidence: `adapter_contracts.py:HostOperationRequest.__post_init__` (107),
`simulator.py:SyntheticHost.operation_context` (76â€“87),
`tests/test_host_adapter.py:HostGatewayTests.test_tenant_and_owner_boundaries_before_engine`
(160). SQLite tables/SceneBinding do not carry tenant_id.
Residual: no durable tenant mapping or tenant-aware core; compliant host behavior is
mandatory and duplicate local scene IDs cannot distinguish tenants in the core.
Required control: trusted immutable mapping to a dedicated scope-specific component
instance, deny caller-selected paths and validate ownership independently. Regression: A03, A04.

## T04 â€” Cross-owner access â€” PARTIALLY VERIFIED

Scenario: an authorized tenant user accesses another owner's asset or assignment.
Existing control: owner_id equality and fixture asset/assignment target maps; assignment
asset/host/job/scene are cross-checked after entering the provider.
Evidence: `simulator.py:SyntheticHost.declare_assignment_target` (50),
`host_adapter.py:AdapterGateway._check_assignment` (51),
`tests/test_host_adapter.py:HostGatewayTests.test_asset_scope_boundary` (169).
Residual: owner_id is caller-visible metadata, not authenticated ownership, and is not
stored in SQLite. Required control: authorize each resolved resource in the host registry,
use a dedicated (host, tenant, owner) storage boundary initially; forbid implicit sharing.
Regression: A03, A04, A08.

## T05 â€” Revoked/expired reviewer permission â€” PARTIALLY VERIFIED

Scenario: retries or cached grants survive session/permission revocation. Existing control:
fixture operation grants are rechecked per call, including exact review replays.
Evidence: `simulator.py:SyntheticHost.operation_context` (78),
`tests/test_host_adapter.py:HostGatewayTests.test_replay_rechecks_current_host_policy` (200).
Residual: no real session expiry/revocation, policy epochs, or distributed grant invalidation.
Required control: current host authorization at admission under a stable operation boundary;
define revocation ordering and bounded operations, deny queued expired sessions.
Regression: A02, A05, A06, A13. See specification for admission versus commit-time semantics.

## T06 â€” Unapproved/stale/substituted scene plan â€” VERIFIED

Scenario: a valid DTO targets a draft or wrong/stale scene/plan. Verified local controls:
strict hashes/revisions/approval consistency, full authoritative-context equality, fixture
assignment-target mapping and existing-assignment scene check. Evidence:
`adapter_contracts.py:HostSceneContext.__post_init__` (56),
`host_adapter.py:AdapterGateway._context` (31), `_check_assignment` (51);
`tests/test_host_adapter.py:test_unapproved_rejected_before_engine` (127),
`test_revision_mismatch_before_engine` (139), `test_scene_substitution_before_engine` (153).
Residual: the provider can lie about authority; tests verify fixture assertions only.
Required control: trusted approved-plan lookup with stable host/tenant/job/scene identity,
not a public Boolean or caller digest. Regression: A08, A09, A10, A11.

## T07 â€” Digest changes after approval or during operation â€” PARTIALLY VERIFIED

Scenario: review context remains cached while plan bytes change. Existing control: gateway
full comparison, Engine readiness binding comparison, fixture RLock across the call.
Evidence: `host_adapter.py:_context` (38); `engine.py:_readiness` (163);
`tests/test_host_adapter.py:test_review_context_stale_blocks_even_replay` (206),
`test_host_state_is_locked_through_engine_operation` (285). Probe P04 showed a noncompliant
provider changes to revision 2 during the call while revision 1 is stored.
Residual: Protocol is not a lock; process-local fixture locks do not span real host instances.
Required control: one coherent host authorization/plan snapshot and stable operation
reservation; reject stale requests, explicitly revise/review assignments; re-evaluate before use.
Regression: A11, A13, A14.

## T08 â€” Direct Engine bypass â€” NOT IMPLEMENTED

Scenario: user code, admin CLI or another worker calls the core instead of the gateway.
Existing control: Engine still enforces metadata, revisions, digest and rights rules; it
does not enforce host permission/plan approval. Evidence: `engine.py:create_assignment`
(137), `review_rights` (205); `scripts/database.py` (9); probe P01.
Residual: validated SceneBinding has no approval proof; host grants can be bypassed.
Required control: isolated custodian process/service identity and filesystem ACLs, deny
arbitrary execution in that process and expose only authorized application operations.
The Protocol cannot contain a malicious same-process caller. Regression: A07.

## T09 â€” Concurrent review/assignment decisions â€” PARTIALLY VERIFIED

Scenario: two reviewers decide opposite outcomes or a revision races a decision/readiness.
Existing control: BEGIN IMMEDIATE, expected revision/digest, first-review check, unique
idempotency key, one SQLite transaction, snapshot reads. Evidence:
`engine.py:_connection` (27), `_review` (181â€“202), `revise_assignment` (146);
`tests/test_engine.py:test_concurrent_review` (89), `test_transaction_rollback` (121);
`simulator.py:run_simulation` (195â€“221) has concurrent opposite decisions.
Residual: fixture serializes host calls; no multi-process host/plan/review race test, no
shared-host transaction. A readiness result is time-bound, not a reservation for render.
Required control: single writer/custodian per scope, consistent lock order and host version
reservations; conflicts never overwrite; fresh final-use evaluation. Regression: A12â€“A15.

## T10 â€” Host/SQLite partial commit â€” PARTIALLY VERIFIED

Scenario: SQLite commits but host receipt, context exit or reply fails. Existing control:
Engine transaction and exact review replay, but no distributed transaction/operation ledger.
Evidence: `engine.py:_review` (181), `_connection` (29); gateway returns inside provider
context (`host_adapter.py:review_rights`, 84); P03 returned an error after one committed
review and exact retry returned that event without another row.
Residual: an error does not prove rollback across boundaries; non-review revision writes
lack generic idempotency receipts. Required control: durable host intent/receipt ledger,
explicit indeterminate outcomes and conservative reconciliation; never blindly reverse a
committed review. Regression: A16â€“A19.

## T11 â€” Host outage â€” NOT IMPLEMENTED

Scenario: authoritative identity/policy/plan service fails before or during an operation.
Existing control: provider exceptions propagate; no cached fallback is implemented.
Evidence: `host_adapter.py:AdapterGateway._context` (32); P03 exercises a synthetic exit
failure, not a real deployment outage. Residual: no production health gating or recovery
policy; provider failure after a core commit remains indeterminate.
Required control: deny new work without authoritative state, preserve existing receipts,
retry only after reauthorization and reconcile ambiguous commits. Regression: A16, A18.

## T12 â€” Component DB outage/lock/resource exhaustion â€” PARTIALLY VERIFIED

Scenario: SQLite is inaccessible, busy, corrupted, or storage fills. Existing control:
connection timeout=10, rollback on SQLite error, DomainError DATABASE for SQLite errors,
connection closure. Evidence: `engine.py:_connection` (24, 30â€“37).
Residual: no service backpressure/health policy or failure-injection coverage for full disks;
non-SQLite parsing errors can escape (P05/P08). Required control: sanitized unavailable
responses, bounded queues, no readiness fallback, unknown-commit reconciliation and
structured validation hardening D01. Regression: A17â€“A19, A25â€“A26.

## T13 â€” SQLite replacement/copying/privileged tampering â€” PARTIALLY VERIFIED

Scenario: a valid wrong-scope DB replaces storage, a stale copy is served, or a local admin
changes rows/triggers. Existing control: configured basename/path containment, FK/physical
integrity checks, revision hashes and update/delete triggers. Evidence:
`engine.py:__init__` (11), `_initialize` (49), `validate_database` (58);
`tests/test_engine.py:test_immutable_history` (129). P06 accepts a DB missing review_state;
P07 accepts a same-name weakened trigger and deletion then succeeds on disposable data.
Residual: validation does not prove scope, semantic consistency or trigger definitions;
no DB identity, signed ledger, confidentiality or protection against privileged file access.
Required control: protected scope registry/directories, backup manifests and external audit
watermarks, quarantine mismatches; harden schema validation D02. Privileged tamper-proof
claims require separate independently verified controls. Regression: A20, A21, A22, A26.

## T14 â€” False/misleading rights declarations â€” PARTIALLY VERIFIED

Scenario: an attacker supplies plausible but false evidence/licence metadata.
Existing control: explicit pending review, nonempty evidence/basis/licence/attribution,
permission restrictions and fail-closed unknown state; no licence inference.
Evidence: `engine.py:_review` (191â€“195), `evaluator.py:evaluate_effective_rights` (4);
`tests/test_engine.py:test_unknown_licence_not_inferred` (27), `test_no_caller_approval` (52).
Residual: evidence is text and human review does not establish ownership or lawfulness.
Required control: authenticated accountable review, evidence provenance/retention and an
organization-defined review policy; never label declared eligibility legal verification.
Regression: A22, A24, A27.

## T15 â€” Media disappearance/change after review â€” NOT IMPLEMENTED

Scenario: rights-ready asset is absent or media bytes change after registration.
Existing control: optional declared size/SHA in AssetDescriptor and readiness warning;
no media opening/checking occurs. Evidence: `contracts.py:AssetDescriptor` (53),
`engine.py:_readiness` (172), `tests/test_engine.py:test_no_media_required` (51).
Residual: rights readiness cannot authorize delivery of unspecified changed bytes.
Required control: trusted local resolver, versioned immutable media/read handles, independent
size/digest/availability verification immediately before production use; deny unknown status.
Regression: A23, A24. No media is fetched or verified in A3.

## T16 â€” Audit cannot prove decision-maker â€” NOT IMPLEMENTED

Scenario: history names a reviewer without session evidence or contains forged/backdated
metadata. Existing control: immutable history rows, canonical request payload, revision,
digest, note, key and sequence. Evidence: `engine.py:_review` (201),
`contracts.py:ReviewEvent` (178), `schema.sql:review_history` (6).
Residual: no identity issuer/session provenance, scope, correlation ID or externally
verifiable signature; app history is not tamper-proof evidence.
Required control: append-only host provenance/receipt events tied to engine sequence,
trusted timestamps and independent retention/verification. Regression: A22, A27.

## T17 â€” Migration/backup/restore/rollback failure â€” PARTIALLY VERIFIED

Scenario: WAL-only copy, wrong backup, old restored approvals, failed migration or duplicate
replay into rolled-back state. Existing control: backup API, no overwrite, schema version
and initial checksum, initial transaction, foreign unversioned rejection, WAL snapshot test.
Evidence: `engine.py:_initialize` (39), `create_database_snapshot` (213);
`tests/test_engine.py:test_snapshot_restore` (134), `test_wal_snapshot_includes_committed_rows`
(155), `test_unversioned_database_preserved` (146). P05/P06 expose validation gaps.
Residual: only migration 001 exists; no version upgrade/downgrade, cross-store recovery,
RPO/RTO, scope manifest or operational restore drill is implemented.
Required control: paired component snapshot/host ledger watermarks, offline quarantine,
validated restore and reauthorization; additive upgrade rehearsal and compatible binary
rollback policy. Regression: A20, A21, A25, A26.

## T18 â€” Expiry evasion through caller time â€” PARTIALLY VERIFIED

Scenario: caller evaluates an expired grant using a past evaluated_at or caches readiness.
Existing control: deterministic timezone-aware timestamp validation, validity interval and
review-after-evaluation blockers. Evidence: `contracts.py:UseContext.__post_init__` (125),
`evaluator.py` (9,17â€“18); `tests/test_engine.py:test_expired_by_date` (62),
`test_review_cannot_be_used_before_timestamp` (173).
Residual: the engine intentionally accepts caller-specified time and has no trusted clock,
maximum review age or continuous rights-revocation feed.
Required control: host-generated evaluation/review time for operative decisions; label
historical queries as historical, re-evaluate validity at actual use and define renewal policy.
Regression: A05, A24, A27.

Production is blocked by missing identity, authorization, durable scope ownership and
cross-store consistency controls. Local test success does not remove those blockers.
