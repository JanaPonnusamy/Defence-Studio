# ARE-A2 host adapter contract v1

ARE-A2 supplies a framework-neutral Python Protocol, validated immutable DTOs, an
optional gateway, and a deterministic offline simulator. It is not a production adapter,
authentication system, tenant-aware database or rights/ownership verifier.

## Import and operations

```python
from asset_rights_engine.adapter_contracts import (
    HostScope, HostSceneKey, HostSceneContext, HostPrincipal, PlanState, HostOperation
)
from asset_rights_engine.host_adapter import HostAdapter, AdapterGateway

# engine: existing ARE-A1 Engine in a trusted component-owned directory
# trusted_host: a production implementation of the Protocol described below
# Do not populate these from public request flags alone.
# gateway = AdapterGateway(engine, trusted_host)
```

The gateway exposes create_assignment, revise_assignment, review_rights,
review_assignment and evaluate_assignment_readiness. Every operation takes the expected
HostSceneContext and HostPrincipal. Assignment operations additionally take both the
internal assignment_id and internal asset_id; these are checked against host mappings
and engine content, never derived from host scene identifiers. Other parameters retain
the corresponding ARE-A1 meaning. Rights/assignment reviews accept the existing
ReviewRequest, and require request.reviewer to equal principal.principal_id. The gateway
calls actual Engine APIs and does not duplicate rights evaluation or persistence.

Importing `asset_rights_engine` continues to expose ARE-A1 names and does not import
adapter or simulator modules. Explicitly import the optional submodules when needed.

## DTOs and validation

| Contract | Required fields |
|---|---|
| HostScope | host_application, tenant_id, owner_id |
| HostSceneKey | HostScope, host-scoped job_id, stable scene_id |
| HostSceneContext | scene key, positive integer plan_revision, lowercase SHA-256 plan_sha256, PlanState, strict Boolean host_asserts_approved |
| HostPrincipal | HostScope and nonempty principal_id; an identifier only |
| HostOperationRequest | operation, expected_context, principal, internal asset_id, assignment_id except for rights review |

Identifiers follow ARE-A1's stable identifier rules. Booleans are not revisions.
HostSceneContext optionally carries scene_content_digest and contract_version=1.
PlanState is DRAFT, PENDING_REVIEW, APPROVED or REVOKED. The approval assertion must
be true exactly when state is APPROVED; contradictory states are rejected. Missing
or malformed hashes never create a valid context. A structurally valid draft context
can be represented, but all gateway operations require a currently asserted approved
plan. Its scene_binding method translates approved metadata into ARE-A1 SceneBinding:
plan_version is the decimal positive revision string. This adapter contract deliberately
uses integer plan revisions; ARE-A1 still accepts opaque plan-version identifiers.

Tenant/owner fields are host authorization scope, not new SQLite columns or substitutes
for engine asset/assignment IDs. They are not encoded into ARE-A1 scene IDs. Use trusted
resource mappings and dedicated engines/storage per scope; equal job/scene IDs in another
tenant do not imply shared identity. No implicit tenant isolation is added to Engine.

## Three distinct trust levels

1. **Validated:** constructing a DTO checks syntax, type, revision, digest and consistent
   assertions. It does not inspect a host or authenticate anybody.
2. **Host asserted:** DTO approval/principal fields describe a host's claim. A Boolean,
   principal string or even a matching scope is not proof of authority.
3. **Authenticated and authorized:** the future production host must obtain identity
   from its authenticated session, authorize the exact operation and resource, enforce
   ownership/tenant scope, and read its authoritative approved plan before invoking the
   engine. No implementation of this level ships in ARE-A2.

HostAdapter has one structural method:

```python
def operation_context(request: HostOperationRequest) -> AbstractContextManager[HostSceneContext]:
    ...
```

The provider must reject unauthorized operations before yielding. It must independently
check principal/session correspondence, permissions, host/job/scene ownership, asset
ownership, immutable assignment-to-scene/asset mappings and approved authoritative plan.
It must keep that state stable through the engine call (e.g. a host transaction/lock or
appropriate version reservation). Authorization deferred until context exit is too late:
SQLite may already have committed. Host and component transactions are separate; there
is no distributed rollback protocol. Production must define commit/failure recovery.

The gateway compares the provider's returned scene and full context against the expected
context, rejects unapproved or stale plans, checks existing assignment asset/scene identity,
and delegates inside the provider's context lifetime. Engine handles reviewed digest,
expected revision, idempotency conflicts and first-review concurrency transactionally.

This is a dependency-injected trusted execution boundary, not a security sandbox. There
is no engine authorization callback. A malicious provider can lie, and direct Engine
calls remain available for backward compatibility. Do not expose the raw Engine or let
end users choose providers, grants, mappings or authenticated identity. A production host
must enforce access control independently; the Protocol cannot prove compliance.

## Errors, staleness and replay

Adapter errors use existing DomainError(code, message). Additional codes include
PLAN_NOT_APPROVED, STALE_PLAN, SCENE_MISMATCH, SCOPE_MISMATCH, RESOURCE_MISMATCH,
PRINCIPAL_MISMATCH and HOST_POLICY_DENIED (synthetic provider). Engine errors remain
unchanged. Readiness receives the current approved binding and returns Engine blockers;
a changed binding yields STALE_SCENE_BINDING without silently rebinding assignments.
A newly current plan requires explicit assignment revision and fresh review.

Every replay rechecks current host context and policy first. For rights reviews, ARE-A1's
idempotency payload contains rights identity/content and ReviewRequest, but not scene
context. If authorization still succeeds under a later approved scene context, an exact
rights review replay returns the original event; it does not append history or restore
an old rights revision's approval. Assignment review replay includes the exact scene
binding/usage and therefore cannot reuse a changed plan's binding. Altered review payloads
under one key conflict. Two competing decisions produce one success and one conflict;
which decision wins is intentionally unspecified.

## Offline simulator

```powershell
.venv-a2\Scripts\python scripts/adapter_simulator.py
```

SyntheticHost is a development fixture, not a production HostAdapter. It uses an
in-memory authoritative scene registry, explicit synthetic grants and immutable resource
maps. An RLock holds synthetic host state stable through engine calls. It denies unknown
principals/operations and out-of-scope resources, but even a granted fixture identifier
is not authenticated. Its dictionaries have no durable host storage or real account link.
It cannot be used to claim production authentication or authorization.

run_simulation creates and removes a temporary database. It returns a deterministic
18-scenario report including approved context, rejected draft, missing/malformed hashes,
scene substitution, stale revision before assignment, valid reviewed readiness, changed
digest rejection and stale readiness, missing/ungranted principal, tenant/owner mismatch,
idempotent replay/conflict, concurrent opposing decisions, integrity and cleanup. Fixed
fixture timestamps and identifiers are TEST DATA ONLY. There are no persistent simulator
options, media fetches, remote calls or writes into data/. The CLI accepts no database path.
The network-denied tests demonstrate that these scenarios run without sockets.

Unsupported: real host authentication/authorization, proof of approval, durable tenant
mapping, distributed host/engine transactions, real media verification, production server,
REST/OAuth and live Defence Studio integration. These are explicit production prerequisites,
not skipped scenarios claimed as implemented.

## Release and migration

Version 1.1.0 is a backward-compatible minor addition. All ARE-A1 APIs, contracts, schema
v1, migrations, engine behavior and baseline tests remain unchanged. The accepted 1.0.0
wheel is preserved. No database migration is needed. Existing hosts can ignore the adapter.
A future real Defence Studio adapter requires separately authorized host access and tests.
