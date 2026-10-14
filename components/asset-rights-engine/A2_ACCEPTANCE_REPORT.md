# ARE-A2 acceptance report

Component: Asset & Rights Engine / ARE-A2 / 1.1.0
Directory: E:\DefenceStudio-Components\asset-rights-engine
Date: 2026-10-12 (Asia/Calcutta)

## Baseline and compatibility

The installed ARE-A1 1.0.0 wheel passed 50 tests, 0 failures/errors before editing.
The component root has no .git directory: branch, HEAD and Git status are unavailable.
No repository was initialized, and no commit, push or branch operation occurred.
A2_BASELINE.json records baseline hashes. The accepted engine.py, contracts.py,
evaluator.py, schema.sql, migrations/001_initial.sql and tests/test_engine.py remain
byte-for-byte unchanged. All prior public Engine APIs and exported contracts remain
compatible; __init__.py only changes release metadata/docstring. Optional adapter imports
are explicit and do not load with an ARE-A1 core import.

1.1.0 is a minor version because the additions are public and backward compatible.
The accepted 1.0.0 wheel is unchanged and retained alongside the new release.

## Files

Created:
- src/asset_rights_engine/adapter_contracts.py: immutable validated assertions.
- src/asset_rights_engine/host_adapter.py: HostAdapter Protocol and AdapterGateway.
- src/asset_rights_engine/simulator.py: synthetic in-memory provider and scenario runner.
- scripts/adapter_simulator.py: offline temporary-database simulator CLI.
- scripts/check_temporary_database.py: existing CLI initialization/snapshot/restore check.
- scripts/verify_release.py: accepted-file hashes, installed import, optionality and source audit.
- tests/test_host_adapter.py: 41 contract/gateway tests.
- tests/test_adapter_simulator.py: 3 simulator tests.
- docs/HOST_ADAPTER.md: exact boundary and production prerequisites.
- A2_BASELINE.json and this report.
- dist/asset_rights_engine-1.1.0-py3-none-any.whl, candidate wheel and verification log.
- Component-local .venv-a2-check and fresh final .venv-a2 environments.

Modified: README.md, CONTRACTS.md, ARCHITECTURE.md, SECURITY.md, CHANGELOG.md,
pyproject.toml, src/asset_rights_engine/__init__.py and .gitignore.
The original A1 ACCEPTANCE_REPORT.md is retained unchanged.

## Host contract and enforcement

HostScope distinguishes host/tenant/owner. HostSceneKey supplies scoped job and stable
scene identity. HostSceneContext supplies positive plan revision, SHA-256, consistent
approval state/assertion and optional content digest. HostPrincipal is an identifier
only, with no authenticated flag. HostOperationRequest distinguishes engine resource
IDs from host IDs and validates principal/context scope.

HostAdapter.operation_context must authorize and verify authoritative host state before
yielding, then keep it stable throughout the engine call. AdapterGateway checks current
full context, rejects unapproved/stale plans, checks resource/scene identity, checks
reviewer correspondence and delegates to the original engine. SyntheticHost enforces
preconfigured fixture grants, immutable resource mappings and host scope under an RLock.
Those checks are synthetic policy, never authentication or production authorization.
There is no core authorization callback; provider trust and prevention of direct untrusted
Engine access remain production responsibilities. See docs/HOST_ADAPTER.md.

## Verification results

| Check | Result |
|---|---|
| ARE-A1 baseline | 50 passed, 0 failures/errors |
| Focused adapter tests | 41 passed |
| Focused simulator tests | 3 passed |
| Complete candidate installed-wheel suite | 94 passed |
| Candidate network-denied suite | 94 passed |
| Independent final offline wheel build | PASS |
| Fresh .venv-a2 no-index/no-deps installation | PASS |
| Final installed-wheel suite | 94 passed, 0 failures/errors |
| Final installed-wheel network-denied suite | 94 passed, 0 failures/errors |
| Existing synthetic example | PASS; eligible, media-unverified warning retained |
| Simulator | 18/18 deterministic scenarios passed |
| Existing CLI on temporary SQLite | Initialization, validation, snapshot, restore and cleanup passed |
| Baseline core/schema/migration/test/wheel/data hashes | All unchanged |
| Isolated core import | No optional adapter/simulator modules loaded |
| Runtime dependency check | None |
| Static runtime source audit | No banned network/host imports, fixed external paths or common API-key patterns |

The audit is not a comprehensive secret detector or proof of production security.
Network-denied output is saved in verification/network-denied.txt.
Installed imports resolve to .venv-a2/Lib/site-packages, not src/. All packaged Python
module bytes match release source. Runtime imports were audited structurally with AST.

Two intermediate verification issues were corrected and rerun: a simulator test expected
18 scenarios before its missing valid-readiness check was added; a lingering source-test
PYTHONPATH caused pip to skip candidate installation. Clearing PYTHONPATH, reinstalling,
and verifying the site-packages path resolved the latter. Final results above have no
failures. There were no pre-existing A1 failures or defects requiring core changes.

## Simulator scenarios

All twelve requested demonstration categories are covered across eighteen checks:
approved DTO validation; rejected unapproved plan; missing and malformed digests; scene
substitution; stale revision before assignment; approved readiness; changed digest rejection
and stale assignment readiness; missing principal; ungranted identifier not authentication;
tenant and owner mismatch; idempotent replay and differing-payload conflict; concurrent
opposite decisions with one success/one conflict and retained first decision; database
integrity; and temporary database cleanup. Reports are deterministic; race winner identity
is deliberately unspecified. Socket-denied execution separately confirms offline operation.

Real authentication, real host authorization, durable tenant mappings, proof of approval,
distributed transactions and media verification are intentionally unsupported; no scenario
claims they are implemented. The simulator does not bypass A1 checks.

## Database, isolation and artifacts

Database/schema changes: NONE. SQLite schema version remains 1. No migration added.
Persistent data/asset_rights.sqlite3 and data/snapshot.sqlite3 have their original hashes;
no synthetic simulator records were inserted there. Tests/simulator/CLI checks create
isolated temporary SQLite databases and remove them afterward.
All project edits, wheels, virtual environments and logs stayed in the standalone root;
transient test databases used temporary storage. No live Defence Studio, QVault or Whisper
Studio application, implementation or database was accessed or modified in this phase.
No external project was imported. No network/media fetch, global install, production server,
authentication service, OAuth flow, system change, commit or push occurred.

New wheel: dist/asset_rights_engine-1.1.0-py3-none-any.whl
SHA-256: b626d9eed519b038f0fdd9e97d9fcb62c5b1128a2f2b4c93de4ea4b987dfa4ca
Preserved 1.0.0 wheel SHA-256:
5abb6fcb0c86cb53482afc2148eaa0652340b0a457167070a469d875bbf614dd

## Before a real Defence Studio adapter

Explicitly authorize a separate host-integration phase. Implement session authentication,
operation/resource authorization, ownership/tenant mapping and authoritative approved-plan
resolution in that host. Provide stable state across engine calls, scope-specific storage,
audit identity provenance and a defined host/SQLite failure-recovery strategy. Keep raw
Engine calls inaccessible to untrusted clients. Independently test host adapter behavior,
revocation, concurrency and storage isolation; add media availability verification separately.
ARE-A2 is a verified contract/simulator increment, not production integration readiness.
