# ARE-A3 independent verification and final report

Date: 2026-10-12, Asia/Calcutta. Target: accepted Asset & Rights Engine 1.1.0,
`E:\DefenceStudio-Components\asset-rights-engine`. Mode: audit/threat model/specification.
No release, production integration or modification of accepted implementation occurred.

**Disposition:** the ARE-A3 review is complete; production integration is NOT ready for
authorization. Standalone controls remain verified, but production identity, resource
authorization, custody, provenance and cross-store consistency are not implemented.
Two actual database-validation defects and one version-label drift were observed.

## Deliverables

- [Integration threat model](INTEGRATION_THREAT_MODEL.md): 18 threats with classifications, controls, source evidence, residual risks and regression IDs.
- [Production host integration specification](PRODUCTION_HOST_INTEGRATION_SPEC.md): identity, authorization, stable context, audit/receipts, failures and 11 proposed logical operations; no endpoints invented.
- [Storage and consistency decision](STORAGE_AND_CONSISTENCY_DECISION.md): A/B/C comparison, conditional Option A recommendation and unresolved decisions.
- [Integration gap register](INTEGRATION_GAP_REGISTER.md): D01/D02 observed core issues, D03 documentation drift and G01–G15 production gaps, priorities, owners, dependencies and tests.
- [Integration acceptance plan](INTEGRATION_ACCEPTANCE_PLAN.md): A00–A30 classified tests, separating standalone evidence from future host/environment requirements.
- [Next-phase proposal](NEXT_PHASE_PROPOSAL.md): bounded validation hardening with exact files, contracts, tests and required human decisions. No implementation started.

## 1. Artifacts actually inspected

Read actual package engine.py, contracts.py, evaluator.py, adapter_contracts.py,
host_adapter.py, simulator.py, __init__.py and schema.sql; migration 001; all three test
files; all six CLI/verification/example scripts; pyproject.toml; README.md, CONTRACTS.md,
ARCHITECTURE.md, SECURITY.md, CHANGELOG.md, docs/HOST_ADAPTER.md; original A1/A2 reports,
A2_BASELINE.json and .gitignore. Read both accepted wheel archives, metadata and package
members without extraction/installation. Inspected installed metadata, actual 16 public
Engine signatures and 15 exported API/contract names plus __version__. Hashed persistent
component files as bytes without opening them as SQLite databases.

Numbered originals: `verification/a3/numbered-artifacts.txt`. Actual source-symbol/line
inventory: `source-symbols.json`. Reproducible probes/results: `audit_probes.py` and
`probe-results.json`. Runtime file shorthand elsewhere resolves to
`src/asset_rights_engine/<filename>`; line numbers refer to unchanged accepted source.

This is fresh verification. Historical report results were read, not substituted for new
checks. The current tests, wheel-byte comparisons and probes were executed anew. No wheel
build/install was rerun because A3 prohibits package installation or release changes.

## 2. Commands and actual results

Project commands ran from the standalone root. Command inventory/context is in
`verification/a3/COMMAND_LOG.md`. Environment changes were process-local only:

```powershell
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='E:\DefenceStudio-Components\asset-rights-engine\verification\a3\tmp'
$env:TMP=$env:TEMP
.venv-a2\Scripts\python -B scripts/test_offline.py *> verification/a3/offline-tests.txt
.venv-a2\Scripts\python -B scripts/verify_release.py *> verification/a3/release-verification.txt
.venv-a2\Scripts\python -B scripts/check_temporary_database.py *> verification/a3/temporary-cli.txt
Get-FileHash -LiteralPath 'dist\asset_rights_engine-1.0.0-py3-none-any.whl','dist\asset_rights_engine-1.1.0-py3-none-any.whl'
.venv-a2\Scripts\python -I -B verification/a3/audit_probes.py *> verification/a3/probe-output.txt
.venv-a2\Scripts\python -I -B verification/a3/inspect_artifacts.py > verification/a3/numbered-artifacts.txt
```

| Independently executed check | Actual result |
|---|---|
| Installed 1.1.0 full socket-denied suite | 94 tests, 0 failures/errors, exit 0; 50 A1 + 41 adapter + 3 simulator |
| Release verifier | exit 0; site-packages import, original baseline bytes unchanged, no runtime dependencies, optional core import |
| Existing CLI on temporary SQLite | initialize/validate schema 1, API snapshot restored valid, temporary directory deleted |
| 1.0.0 archive | matching accepted hash/metadata; four core/schema members match source; init excluded because release label differs |
| 1.1.0 archive | matching accepted hash/metadata; all eight package Python/SQL members match source |
| Installed APIs/version | 1.1.0 from .venv-a2/Lib/site-packages; 16 public Engine callables, exact signatures in probe-results.json |
| Schema/migration mirror | exact byte match |
| Simulator in audit probes | 18/18 outcomes true; actual Engine calls on a removed temporary DB; no authentication |
| Original protected artifacts | 37 captured files, zero changed hashes |
| Temporary probe storage | disposable directories removed; verification/a3/tmp empty at confirmation |
| Git | no .git in root; no Git command, initialization, branch, commit or push |

Accepted wheel SHA-256 values independently checked:

- 1.0.0: `5abb6fcb0c86cb53482afc2148eaa0652340b0a457167070a469d875bbf614dd`
- 1.1.0: `b626d9eed519b038f0fdd9e97d9fcb62c5b1128a2f2b4c93de4ea4b987dfa4ca`

Socket denial covers the test runner's Python socket/socket.create_connection APIs, not a
machine-wide network sandbox. Runtime source/AST inspection found no network-fetch behavior.
No network tool/call, install, media fetch or server was used. Log files above are actual
results; planned acceptance tests are not marked passed.

The inventory helper first used UTF-8-only decoding and failed on CHANGELOG.md's existing
Windows-code-page byte. The helper was rerun successfully with explicit CP1252 fallback,
preserving original bytes. This was an audit-tool decoding issue, not a test failure or
source repair. README retains a stale 1.0.0 heading (D03). Accepted docs were not rewritten.

## 3. Independently reproduced probes

**P01:** direct Engine.review_rights accepted APPROVED and Engine.create_assignment created
an assignment without a host. This is the documented library boundary, not authentication.
Raw Engine exposure would bypass host policy.

**P02:** a newly constructed copy of a granted synthetic HostPrincipal ID/scope was accepted.
SyntheticHost matches values; it does not know an authenticated caller. Missing/ungranted
IDs fail tests, but a known ID is not identity proof.

**P03:** injected provider context-exit error after review commit returned
AUDIT_HOST_EXIT_FAILURE while one review remained committed. Exact retry returned that
same event and history stayed at one. Local replay works, but there is no cross-store
rollback. A host transaction/context cannot undo SQLite commit.

**P04:** a deliberately noncompliant provider changed its context to revision 2 inside
the Engine call; the created assignment retained revision 1. Gateway stability depends
on provider obligations. The existing RLock test verifies the fixture against a different
thread, not an arbitrary provider or deployed host.

**P05 / D01:** version-1 temporary SQLite with an empty migrations table caused raw
TypeError at Engine._initialize line 55, not DomainError. Its message was that NoneType
is not subscriptable. This is a structured-error diagnostic defect, not approval bypass.

**P06 / D02:** removing required review_state from a disposable valid DB left
validate_database() reporting valid/schema_version 1; get_rights then failed with DomainError
DATABASE / no such table: review_state. Validation does not cover all required schema objects.
Startup can accept an unusable structurally incomplete DB.

**P07:** privileged replacement of immutable_rights_revisions_delete with a same-name
no-op trigger passed validation; direct DELETE then removed temporary revision rows.
Validation checks names, not definitions. This is a documented privileged-tampering limit,
not a claim that the package promised tamper-proof storage.

**P08 / D01:** malformed revision JSON on a disposable DB produced raw JSONDecodeError
from validate_database. The context manager normalizes SQLite exceptions, not JSON errors.

**P09:** simulator uses real registration, revision/review, assignment creation/review,
readiness and history APIs. Concurrent opposite decisions yield one success/one conflict.
Mappings/grants are fixture logic; business/persistence outcomes use the actual engine.

**P10 / D03:** package metadata 1.1.0 versus README heading 1.0.0. Recorded, not repaired.
These probes are evidence, not repaired accepted regression tests; A25/A26 remain planned.

## 4. Verified gateway sequence

create_assignment constructs HostOperationRequest, validating typed inputs/identifiers and
principal scope. Provider context enters, then gateway checks returned type, exact scene,
both approval assertions and full DTO equality. SceneBinding translation precedes delegation
to Engine.create_assignment. SyntheticHost validates configured host/tenant/owner, operation
grant, asset ownership and immutable assignment target before yield. Gateway itself does
not implement those grants/durable ownership checks independently of the provider.

For revise/readiness/review_assignment, _check_assignment reads Engine.get_assignment after
provider/context checks, then compares asset and host/job/scene before mutation/evaluation.
Some identity cross-checks therefore require an Engine read; provider authorization must
already precede that read. Review methods compare reviewer/principal before entering the
provider. All five gateway operations, including rights review/readiness, require an asserted
approved scene plan. These facts are inspected, not inferred from method names.

SQLite writes use BEGIN IMMEDIATE and transactional expected revision/digest/first-review
checks. Reads use a BEGIN snapshot. Exact Engine review replay precedes current-revision
checks and returns the historical event without reviving approval; gateway rechecks current
host policy/context. Rights-review fingerprints omit scene context; assignment-review
fingerprints include binding/usage. No generic revision-write receipt or public historical
revision-query API is assumed.

## 5. Documentation alignment and unverified claims

Most security/adapter documentation accurately states the absence of authentication, real
authorization, tenant-aware storage, media verification or tamper-proof evidence. Replay
and staleness behavior match code. README's label is stale. The validator is insufficient
as a complete production schema/semantic restore gate and has D01/D02 outside prior coverage.

Unverified: real session issuer/expiry/revocation, ownership mapping, authoritative plan
lookup/hash semantics, service identity, cross-process/distributed locking, real outage/disk/
crash durability, tenant ACL/encryption/custody, external audit signatures, paired recovery,
actual upgrades/downgrades and media availability. No live host/provider/store or production
DB was accessed. Conditional contracts and fixture grants are not implemented production controls.

## 6. Recommendation and next phase

Recommend Option A: single trusted local custodian, scope-specific SQLite, host authoritative
identity/authorization/plan context and durable intent/receipt/provenance ledger. Assumptions:
local protected storage, one custodian per (host,tenant,owner), no arbitrary in-process
untrusted code and no distributed active writers. Option B's competing mutable approval
authorities add reconciliation risk; no observed requirement justifies Option C.

P0 blockers: G01 identity, G02 durable resource authorization/ownership, G03 raw access
perimeter, G04 stable authoritative context, G05 ambiguous-commit recovery, G06 identity
provenance and G07 protected scope storage. P1 includes D01/D02, media/time, recovery,
concurrency/outage tests, non-review retry fencing, evidence policy and confidentiality.
P2 includes version-label drift and capacity/configuration evidence.

Smallest proposed next phase: private DB validation/error-contract hardening, preserving
schema/APIs and host independence. Exact files/tests/security controls and approval decisions
are in NEXT_PHASE_PROPOSAL.md. No implementation starts without a new explicit instruction;
repairing the engine does not close the host P0 gaps.

## 7. Preservation and boundaries

Pre/post hashes cover source, existing tests/scripts, metadata, schema/migration, both wheels,
original acceptance reports/baseline/docs, .gitignore and both persistent SQLite files.
All 37 remain unchanged. Persistent data was hashed as bytes, not opened as SQLite, and
never modified. No public API/schema/migration/release version changed. Only new docs and
audit helpers/logs/manifests were written under the standalone root; all audit/test/CLI DB
writes were disposable under verification/a3/tmp and removed. No persistent synthetic records.

No external project/application/database was accessed or modified. The user attachment
was read solely for task instructions. No packages installed, network/media fetched,
production server started, system configuration changed or Git operation performed.
Both accepted wheels and all earlier acceptance reports are preserved.
