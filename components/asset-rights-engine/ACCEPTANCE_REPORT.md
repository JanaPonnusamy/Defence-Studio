# Phase A1 acceptance report

Component: Asset & Rights Engine / ARE-A1 / 1.0.0
Directory: E:\DefenceStudio-Components\asset-rights-engine
Date: 2026-10-12 (Asia/Calcutta)

## Results

- Independent wheel build: PASS, no-index/no-deps/no-build-isolation.
- Offline installation into component-local .venv: PASS.
- Installed-wheel test suite: 50 tests, 0 failures, 0 errors.
- Network-denied suite (scripts/test_offline.py): 50 tests, PASS.
- Documented synthetic example: PASS; eligible=True, no blockers,
  MEDIA_AVAILABILITY_UNVERIFIED warning retained.
- Initialization and validation CLI: PASS, schema_version=1.
- SQLite backup and restored snapshot validation: PASS.
- WAL committed-data snapshot test: PASS.
- Immutable revision/history triggers, stale revision/digest rejection,
  review idempotency/conflicts, concurrent first-decision conflict: PASS.
- Rollback, foreign keys, migration reinitialization, unsupported schema rejection,
  unversioned database preservation and migration SQL mirror: PASS.

Wheel: dist/asset_rights_engine-1.0.0-py3-none-any.whl
SHA-256: 5abb6fcb0c86cb53482afc2148eaa0652340b0a457167070a469d875bbf614dd

## Isolation

All implementation writes were directed to the standalone component. No Defence Studio,
QVault or Whisper Studio implementation was imported or used as a dependency. No external
project database was opened. No branch changes, commits or pushes were performed. No global
packages were installed, no media fetched, and no system configuration changed. Core tests
use temporary SQLite databases. The persistent data/ database created by the administrator
CLI is empty component-owned storage; synthetic records exist only in temporary examples.

## Inventory and boundary

contracts.py: metadata, rights, usage, bindings, assignment and review contracts.
evaluator.py: pure declared-rights decisions.
engine.py: transactional SQLite, revision/review services, readiness and snapshots.
schema.sql / migrations/001_initial.sql: schema v1 and migration metadata.
scripts/: initialization/snapshot CLI, synthetic example and network-denied tests.
tests/: 50 unittest cases including parameterized contract cases.
README.md: installation, all required public API operations and example instructions.
CONTRACTS.md, ARCHITECTURE.md, SECURITY.md, CHANGELOG.md: contracts, adapter boundary,
migration strategy and limits.

Schema v1 tables: assets, rights_revisions, assignments, assignment_revisions,
review_state, review_history, migrations. Current rights/assignment records are views of
the latest immutable revision plus review state, avoiding duplicate mutable metadata.

## Remaining limitations

No host integration, REST server, media verifier or licence interpretation is included.
Reviewer authentication and authoritative plan delivery belong to a future explicit host
adapter. Declared rights do not establish legal ownership. SQLite triggers/hashes provide
application auditability, not protection against a privileged administrator altering files.
No later schema upgrade is shipped; future versions must add sequential additive migrations.
