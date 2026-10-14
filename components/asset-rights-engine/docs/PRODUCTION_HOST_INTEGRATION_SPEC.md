# Production host integration specification — ARE-A3

Status: proposed architecture and operations, not implemented endpoints. Accepted package
1.1.0 and schema v1 remain unchanged. The host application has not been inspected; all
host-specific authentication, database, endpoint and plan-format decisions remain open.
This specification does not grant access to any host or authorize implementation.

## 1. Architecture and authority

Use one trusted local custodian per configured (host, tenant, owner) scope initially,
with a private component database and deny-by-default service/process boundary. The host
owns authenticated sessions, authorization, ownership/job/scene registry, approved plans,
operation intent/receipt ledger and final production-use authorization. Engine owns its
metadata, immutable rights/assignment revisions and review events. Host receipts reference
Engine facts; they do not create a second mutable source of rights-review truth.

Only trusted service code constructs Engine/HostAdapter and selects configured paths. No
client chooses a provider, tenant directory, SQL, snapshot path or reviewer subject. Arbitrary
Python code in the custodian can bypass the gateway, so untrusted plugins/code cannot share
that trust boundary. Direct Engine APIs remain available for compatibility, but are not
public authorization endpoints. Host must authorize reads/list/history/admin actions too.
No HTTP API, authentication server, OAuth or service endpoint exists in this specification.

Existing implementation evidence:
- `src/asset_rights_engine/adapter_contracts.py`: frozen scope/scene/context/principal/request DTOs.
- `src/asset_rights_engine/host_adapter.py:HostAdapter.operation_context` (11): contractual trusted boundary.
- `AdapterGateway._context` (31): returned context/type, exact scene, both approval assertions, full equality.
- `_review_identity` (73): equality check only; `_check_assignment` (51): asset and host/job/scene checks.
- `src/asset_rights_engine/engine.py:_connection/_review`: local SQLite transactions/review concurrency.
- `src/asset_rights_engine/simulator.py:SyntheticHost`: only fixture grants and process-local RLock.

## 2. Identity, policy and inputs

Authenticate with the host's approved session mechanism; choose issuer/subject semantics,
expiry, revocation and offline behavior before implementation. Derive a stable internal
principal ID from that verified subject using an explicit trusted mapping compatible with
ARE identifier syntax. Display names and caller-supplied reviewer IDs are never identity.
If a client submits a reviewer field, compare it only as an expected hint or reject it;
actual ReviewRequest.reviewer comes from the host. Host audit retains issuer/subject and
session provenance so an opaque normalized engine ID remains attributable.

For service-to-service calls, use a separately configured, authenticated service identity
and explicit least-privilege delegation. A service cannot impersonate a human reviewer;
log the service actor and any verified initiating human separately. No service authentication
is implemented now, and no new credential/network flow is selected by this design.

Authorize every operation against current subject, scope, role, resolved resource ownership,
job/scene state and reviewer policy. Use separate permissions for metadata registration,
rights editing/review, assignment editing/review, reading/use authorization, audit export
and administration. Deny unknown subjects/resources/roles, missing authoritative state and
unsupported sharing. Tenant/owner strings in a DTO do not satisfy any check by themselves.
Never trust ownership declared in media metadata. Prefer per-scope instance separation at
launch; a durable host registry binds all asset/assignment IDs to their permitted scope.

A revoked session or permission prevents new work and unauthorized retries. Decide separately
whether revoking a reviewer account invalidates earlier legitimate decisions: Engine does
not implement that policy. If a rights grant is revoked, create an explicit REVOKED rights
revision under authorized host control; it invalidates stale assignments. Do not mutate or
delete an earlier decision to disguise its history.

## 3. Approved-plan consistency and time

Host provider independently loads current approved plan under stable host/job/scene identity,
verifies canonical approved content digest, revision and optional scene digest, and constructs
HostSceneContext. A client's expected DTO is compared against this result, never installed
as authoritative state. ARE-A2 uses a positive integer revision mapped to SceneBinding's
decimal plan_version string; if the future host has opaque versions, define a stable,
lossless approved-revision mapping and preserve original host version in host audit. Do not
assume the unseen host supports numeric revisions or hash its content differently.

The provider must authorize/validate before yield, retain an appropriate host reservation
through the Engine call, and release after it. Stable immutable DTOs do not lock the host.
No production reservation exists today. In the initial single-custodian topology, serialize
policy/plan mutations against admitted operations with one consistent lock order. Reject
already expired/revoked sessions after queueing and before Engine entry. Generate canonical
UTC evaluation/review time from the host's trusted clock; for assignment review both strings
must exactly match because the current engine compares them as strings.

Choose and document a linearization policy: the proposed initial policy authorizes a bounded
operation at admission inside the reservation; concurrent revocation is ordered after that
operation, and final use always performs a new authorization/time check. Do not claim
instantaneous commit-time revocation: Engine has no transactional authorization callback or
cancellation/deadline hook. If policy requires session/permission/plan revalidation at the exact
SQLite commit instant across distributed hosts, this API/topology is insufficient and a
separate engine/transaction extension must be designed before deployment. A context-exit
check cannot undo an Engine commit. Human approval of admission-time semantics is required.

Readiness is a supplied-time observation, not a durable render permit. Actual use combines
fresh host permission/plan checks, Engine readiness and trusted immutable media verification.
Historical evaluations are labeled historical and cannot authorize present use. Determine
maximum review age/renewal requirements in host policy; only declared validity dates are
currently evaluated by Engine.

## 4. Operation lifecycle, audit and retries

Proposed host operation envelope (not a new ARE contract): authenticated session reference,
trusted resolved principal/scope/instance generation, logical operation/resource IDs,
expected host context and engine revision/hash, client request key, canonical request
fingerprint, trusted UTC time and correlation ID. No secrets or raw authentication tokens
belong in engine metadata/history. Fingerprint the semantic request, including operation,
scope, resource IDs, requested decision/note, plan context and relevant use; distinguish
request correlation IDs from semantic idempotency. Persist trusted initial semantic time
for exact review retries; new final-use evaluations use fresh time.

Namespace review keys in host code (e.g. stable digest of scope/operation/resource/client
key) to avoid Engine's global key collisions while respecting its identifier limit. Require
same semantic fingerprint for replay. Engine review payload contains kind/entity/review;
assignment reviews additionally include binding/use, while rights reviews do not include
scene context. The gateway still reauthorizes/compares current context before rights replay.
A new approved scene context can permit exact rights-event replay, but cannot revive approval
of an old rights revision. A changed assignment binding is a different review payload.

Record append-only host events: intent/admission, denial/conflict, engine-result receipt,
indeterminate outcome, reconciliation and operative-use decision. Include verified identity
provenance, effective authority/role/policy revision, scope/component instance and recovery
generation, correlation, request fingerprint/key, trusted time, host plan identity/revision/
hash, engine entity/revision/hash and review sequence where present. Store approved/rejected
review decision/reason as Engine fields, and link external host provenance to the receipt.
No identity issuer/session, correlation or tenant field currently exists in ReviewEvent.

Application triggers/sequence/hash are application auditability, not tamper-proof evidence.
External signed/exported append-only retention may be selected and independently tested;
do not claim it exists. Prefer no raw sensitive session details in retained audit logs.

Failures: invalid structure -> safe VALIDATION; unauthenticated/unauthorized -> deny before
Engine; stale/wrong scope/resources -> conflict/denial without mutation; Engine domain
conflicts -> retain first decision and require refresh. SQLITE outage/busy errors are
sanitized unavailable outcomes, not readiness; raw SQLite paths/messages are internal
only. Current raw parse failures D01 require engine hardening. If commitment is uncertain,
return INDETERMINATE with trusted correlation, not a false rollback/success statement.
No HTTP status codes or actual endpoint names are specified.

For write retry, reauthenticate/authorize first; use original semantic payload/key and never
change a review timestamp just to force replay. Never retry a conflicting revision by
silently choosing the latest one. Exact reviews return original Engine event; write receipt
recording can be retried independently. Non-review revisions are not generically idempotent:
exclusive host resource fencing + intent ledger must prove expected+1/current hash or
quarantine if intervening writes prevent proof. No public historical revision-query API is
assumed. A separately authorized recovery actor may reconcile without returning data to a
revoked requester. Do not bypass host checks because an Engine idempotency key is known.

## 5. Proposed logical operations

The following are service-layer designs, not implemented endpoints. The five scene-bound
operations use existing AdapterGateway methods. Registration/rights editing/read/admin
operations need new trusted host wrappers; no current HostOperation enum entries are
invented for them. Each operation below specifies the same nine fields.

### O01 — Register metadata

- Trusted inputs: authenticated subject, registry scope/instance, requested validated AssetDescriptor and request fingerprint.
- Preconditions: resolved resource ownership, configured storage healthy, no untrusted metadata filesystem use.
- Authorization: metadata-write and resource creation/ownership permission, scope lock; deny shared inheritance.
- Engine operation: Engine.register_asset (no gateway method exists).
- Success: returned immutable descriptor and host ownership mapping/receipt; exact same metadata may replay.
- Failures: denied scope/session, VALIDATION for invalid or different metadata under existing ID, FILESYSTEM/DATABASE or indeterminate.
- Retry: reauthorize; same ID and exact descriptor only; reconcile registry/SQLite partial creation, never infer by ID alone.
- Audit: metadata-registration intent/result with asset ID, canonical metadata hash, scope and identity provenance.
- Acceptance: A00,A01,A03,A04,A06,A07,A15,A18,A19.

### O02 — Create a rights revision (including expiry/revocation metadata)

- Trusted inputs: rights editor identity, owned asset, validated RightsRecord, expected current revision and fingerprint.
- Preconditions: asset exists in mapped instance; APPROVED input is forbidden; evidence/rights basis are declarations.
- Authorization: rights-edit permission and scope/asset authority; renewal/revocation policy.
- Engine operation: Engine.create_rights_revision(asset_id, record, expected_revision).
- Success: new immutable rights RevisionView; previous review cannot approve new content; assignments tied to old rights become stale.
- Failures: denied ownership, VALIDATION, NOT_FOUND, CONFLICT for changed revision, DATABASE or indeterminate.
- Retry: no automatic increment retry; prove exact result under ledger/exclusive fence or quarantine.
- Audit: rights-change intent/result, previous/new revision/hash, declared state, evidence reference and editor provenance.
- Acceptance: A00,A01,A03,A06,A15,A18,A19,A24,A27.

### O03 — Review rights

- Trusted inputs: host-derived reviewer, authoritative approved scene context, owned asset, expected rights revision/digest, decision/note/key and stable trusted review time.
- Preconditions: review permission current; approved scene context for this gateway; first decision of this rights revision, except exact replay.
- Authorization: rights-review and resource policy; bind ReviewRequest.reviewer to authenticated subject, not supplied ID.
- Engine operation: AdapterGateway.review_rights -> Engine.review_rights inside provider context.
- Success: one immutable ReviewEvent; approval applies only to reviewed revision and declared permissions/evidence.
- Failures: principal/scope/plan denial, VALIDATION for missing note/evidence, CONFLICT for key/digest/revision/prior decision, DATABASE or indeterminate.
- Retry: exact payload/key under renewed host authorization returns same event; do not silently update reviewed revision.
- Audit: reviewer provenance/policy/correlation linked to Engine sequence/revision/digest/decision/note/key.
- Acceptance: A01,A02,A05,A06,A08–A13,A15–A19,A22,A27.

### O04 — Create scene assignment

- Trusted inputs: editor identity, immutable host assignment-target/asset mapping, current expected approved plan, role/use and internal assignment ID.
- Preconditions: exact scene/plan context, owned asset with a rights revision; creation does not claim readiness or approval.
- Authorization: assignment-create on both job/scene and asset, owner/tenant mapping; state must be DRAFT/PENDING_REVIEW/BLOCKED.
- Engine operation: AdapterGateway.create_assignment -> Engine.create_assignment.
- Success: revision 1 capturing binding, usage and current rights revision/hash, unreviewed unless a later review succeeds.
- Failures: host denial, unapproved/stale/substituted plan, VALIDATION, NOT_FOUND, CONFLICT for existing ID, DATABASE or indeterminate.
- Retry: exact host receipt replay after reauthorization; existing Engine ID alone is not success proof.
- Audit: assignment-target/context/role/use fingerprint and Engine revision/hash receipt, editor provenance.
- Acceptance: A01,A03,A04,A06–A11,A13–A19.

### O05 — Revise assignment

- Trusted inputs: editor identity, immutable same-scene/asset mapping, current approved context, requested role/use, expected assignment revision.
- Preconditions: same identity; full current plan known; approved prior review cannot survive new revision. No silent rebinding.
- Authorization: assignment-edit/resource/plan policy with stable context reservation.
- Engine operation: AdapterGateway.revise_assignment -> Engine.revise_assignment; gateway first cross-checks existing asset/host/job/scene.
- Success: new immutable RevisionView, references current rights revision/hash, fresh review required.
- Failures: unauthorized resources, wrong scene, stale context/revision, VALIDATION, NOT_FOUND, CONFLICT, DATABASE or indeterminate.
- Retry: ledger/fence proof only; do not append an additional revision merely because the reply was lost.
- Audit: before/after revision/hash and plan/use mapping change with editor provenance and reason in host ledger.
- Acceptance: A03,A06,A08–A11,A13–A19.

### O06 — Review assignment

- Trusted inputs: authorized reviewer, current approved context, immutable assignment/asset mapping, expected assignment revision/digest, decision/note/key and same-string trusted usage/review time.
- Preconditions: approval requires current reviewed rights compatible with usage, attribution and exact binding; first decision or exact replay.
- Authorization: assignment-review permission and resource/plan authority; decision and reviewer policy remain current.
- Engine operation: AdapterGateway.review_assignment -> Engine.review_assignment; approve calls Engine._readiness without requiring an earlier assignment approval.
- Success: immutable ReviewEvent and revision-specific approved or blocked derived state; no media-verification claim.
- Failures: rights/use/attribution blockers (current Engine VALIDATION), stale binding/revision/digest, prior-decision/key CONFLICT, host denial, DATABASE or indeterminate.
- Retry: exact payload/key and binding/use only, reauthorize; changed plan requires explicit revision then new review.
- Audit: authenticated reviewer, policy, plan/use fingerprint and immutable decision receipt.
- Acceptance: A01–A03,A05–A19,A22,A24,A27.

### O07 — Evaluate and authorize actual use

- Trusted inputs: authenticated operator, current authoritative approved plan/ownership, exact intended use, host-generated current time and immutable verified media version.
- Preconditions: no unresolved resource write, healthy authority/storage, rights/current assignment reviewed and approved, independent media check successful.
- Authorization: read/use/export operation plus resources and destination restrictions; historical context is not operative permission.
- Engine operation: AdapterGateway.evaluate_assignment_readiness -> Engine.evaluate_assignment_readiness; actual-use authorization is a new host decision, not an Engine API.
- Success: return Engine result plus separate host decision; allow production only if eligible and host/media checks pass. MEDIA_AVAILABILITY_UNVERIFIED is not proof of availability.
- Failures: structured blockers/denial, stale plan/time/media, unavailable host/DB; never cached fallback-ready.
- Retry: read-only re-evaluation with fresh time and context; old eligibility is not a replayable use permit.
- Audit: operator/provenance, current plan/use/media digest, rights/assignment receipts and allow/deny reason.
- Acceptance: A01,A03,A04,A07,A09–A14,A16,A17,A23,A24,A27.

### O08 — Get/list descriptors, rights, assignments and audit history

- Trusted inputs: authorized reader/auditor, mapped scope and bounded resource/filter request.
- Preconditions: resolve scope internally; lists/history are entire component scope, not public cross-tenant queries.
- Authorization: read and optional audit-export permission; current subject and owner scope before any Engine read.
- Engine operation: existing get_asset/list_assets/get_rights/get_assignment/list_assignments/get_review_history; no gateway wrappers exist for these reads.
- Success: permitted typed records/host-filtered result and provenance references; audit sequence is component-local, not global identity proof.
- Failures: denied/unknown ownership, NOT_FOUND, DATABASE/unstructured storage issues pending D01; sanitize externally.
- Retry: safe read reauthorization; no data disclosure on revoked retry; generation-aware pagination if implemented later.
- Audit: sensitive audit export/read access, actor/scope/correlation and result bounds.
- Acceptance: A01,A03,A04,A06,A07,A17,A22.

### O09 — Validate and snapshot

- Trusted inputs: authenticated administrator/service actor, registry-owned instance, safe unused snapshot basename and host backup operation ID.
- Preconditions: no unresolved intents; scope locked/drained to correlate host watermark; no arbitrary client filesystem configuration.
- Authorization: scope-specific backup/validation permission and protected destination custody.
- Engine operation: Engine.validate_database/create_database_snapshot; hardened validation D01/D02 is required before deployment restore gates.
- Success: completed SQLite API snapshot plus host scope/instance/generation/wheel/schema/ledger/checksum manifest and verification receipt.
- Failures: unsupported/invalid storage, path rejection, existing target, partial SNAPSHOT/DATABASE failure; partial destinations never published as usable backups.
- Retry: new controlled backup operation or validate/clean a quarantined partial destination; never overwrite a known accepted snapshot.
- Audit: administrator, source/destination manifest hashes, watermark, success/failure and retention policy.
- Acceptance: A04,A06,A07,A17,A20–A22,A25,A26,A29.

### O10 — Restore/reconcile/controlled rollback

- Trusted inputs: approved recovery actor/change ticket, immutable snapshot and protected matching host manifest/watermark, compatible binary/schema and current host policy.
- Preconditions: work stopped, copied storage quarantined; validate required schema/content; verify scope/generation and evidence of lost operations.
- Authorization: explicit recovery authority; a restored reviewer ID never authenticates the recovery actor.
- Engine operation: construct Engine only on validated restored configured storage and use getters/review replay under recovery authority where unambiguous. No restore orchestration API exists.
- Success: reconciled instance under a new host recovery generation; current plan/rights/permissions rechecked; work resumed only after sign-off.
- Failures: wrong scope/hash/schema, semantic corruption D01/D02, incompatible binary, unresolved receipts or stale permissions -> quarantine, no service readiness.
- Retry: controlled recovery from preserved snapshots/receipts, never destructive overwrite or silent historical decision rewrite.
- Audit: recovery actor, original/new generation, manifest, reconciliation/conflicts and explicit resume decision.
- Acceptance: A04,A17–A22,A25,A26,A30.

### O11 — Future migration

- Trusted inputs: approved administrator, reviewed compatible release/migration artifacts, immutable backup and host/component recovery plan.
- Preconditions: stopped work, rehearsed upgrade/rollback on copies, version compatibility and restored evidence validated.
- Authorization: deployment/migration permission independent of reviewer permissions.
- Engine operation: future additive migration through a separately reviewed release; current Engine only supports schema 0-empty -> 1 and rejects unknown future versions.
- Success: validated supported schema, preserved immutable history and compatible receipts; no update exists in A3.
- Failures: unsupported version/checksum/invalid state, upgrade interruption -> quarantine/compatible restored deployment, no forced downgrade.
- Retry: according to reviewed transactional migration plan; no repeated arbitrary SQL and no mutation of accepted migration 001.
- Audit: release/wheel/migration hashes, before/after schema/generation, backup/recovery receipt and approval.
- Acceptance: A20,A21,A25,A26,A30.

## 6. Outages, reconciliation and release gates

No host authority -> deny admission. No component DB -> no new rights/readiness approval;
return sanitized unavailability and retain pending intent. Retry only with current authority.
A context-exit failure after SQLite commit is indeterminate even if the host rolled back.
First decision remains immutable; reconciliation cannot replace it with a different answer.
If permission later changes, authorized recovery may record that a historical effect occurred
without authorizing current use or returning private data to the original revoked requester.

Dedicated receipt/state reconciliation jobs are future host operations, not currently
implemented tasks. They must compare scope/generation, expected revisions/hashes, Engine
review sequence/key and host fingerprint. Ambiguous non-review revision outcomes require
resource quarantine, not optimistic success. Backup/restore policy is in the storage decision.

All applicable P0 gaps and acceptance tests must pass in an isolated environment before a
real adapter is authorized. D01/D02 are narrow verified core issues that can be addressed
first without host access. Authentication/provenance and distributed/operational guarantees
remain unverified. See INTEGRATION_GAP_REGISTER.md and NEXT_PHASE_PROPOSAL.md.
