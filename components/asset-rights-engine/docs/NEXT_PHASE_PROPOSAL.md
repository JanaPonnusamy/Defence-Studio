# Smallest safe next phase — proposed ARE-A4

Status: recommendation only. No implementation started, source patched, package installed,
release issued or host access authorized in ARE-A3.

## Scope: database validation and error-contract hardening

First address the two independently reproduced core issues D01/D02 before building a real
adapter: missing migration metadata/malformed JSON can escape as TypeError/JSONDecodeError,
and a missing required review_state table can be accepted as valid. These are bounded,
independently testable changes with no host dependency. Existing identity/authorization
limits are deliberately out of scope; repairing validation does not make production ready.

### Exact proposed files

- `src/asset_rights_engine/engine.py`: private initialization/validation helpers, explicit
  migration-row/checksum presence/type validation, required schema structure validation,
  expected trigger/table association/definitions, and structured handling of persisted JSON
  or contract parse errors. Validate supported revision content/reference relationships
  without interpreting licences or authenticating reviewer IDs. Keep public signatures,
  schema v1, transaction boundaries, idempotency and immutable history semantics intact.
- `tests/test_database_validation.py` (new): temporary supported/corrupt/incomplete copied
  databases, safe parse failures, missing migration row/table/column/trigger, same-name
  weakened trigger, invalid descriptor/rights/binding payload and digest/reference mismatch;
  require consistent DomainError and no destructive mutation. Good accepted v1 snapshots,
  existing initialization/review/readiness and 94-test compatibility must remain green.
- `README.md`: correct the proven stale 1.0.0 heading only in this explicitly authorized
  update, and document validation checks/limits and safe diagnostic commands.
- `SECURITY.md`, `ARCHITECTURE.md`, `CONTRACTS.md`: document the stronger validator and its
  remaining privileged-tampering limits; do not claim authentication or tamper-proof storage.
- `CHANGELOG.md`: document actual validated repairs; release version decided after review.
- A new phase acceptance report and future release artifact only if separately authorized.

Do not alter `schema.sql`, `migrations/001_initial.sql`, `contracts.py`, `evaluator.py`, adapter
contracts/APIs, accepted wheels or existing acceptance reports. If a correct fix proves a
schema/API change necessary, stop and propose that separately instead of widening scope.

### Contract and security controls

Preserve `Engine.validate_database() -> dict` on valid schema v1. Rejected DB state should
raise existing structured DomainError, using consistent existing error codes and safe
messages. Preserve malformed caller input versus persisted storage diagnostics. Do not
catch all programming exceptions as success or hide failures. No invalid DB may be marked
valid, and no diagnostic rejection may discard/reinitialize the user's data.

Schema validation can detect missing/mismatched required structures and accidental restore
errors; it cannot defeat an administrator who edits the package/database/manifest together.
Identity and legal rights are outside its authority. Filesystem custody and external audit
remain G01–G07 deployment prerequisites. No automatic migration, restore or corruption repair.

### Acceptance gates

Reproduce D01/D02 on disposable databases first. Then prove their intended rejection with
structured errors and exact preserved input bytes. Exercise compatible schema v1 and
snapshot/WAL behavior, all 94 existing tests, new validation tests, sockets-denied execution,
and source/installed-wheel parity if a future release is authorized. Preserve original
1.0.0/1.1.0 wheels and reports. No external host/project/database or production server access.
A25/A26 are planned; A3 probes are not accepted repaired regression tests.

## Human decisions and future integration sequence

An explicit next-phase instruction is required before editing accepted source. Agree the
validation-depth boundary, expected error code policy and whether/when to issue a release.
A3 recommends the narrow repair; it does not automatically choose a new release version.

Before a later real adapter phase, approve the storage custody/per-scope mapping choice,
identity/offline-session provider, revocation linearization semantics, host lock/version
reservation, durable intent/receipt and provenance policy, recovery authority/RPO/RTO,
retention/encryption/key custody and local media immutability. Then authorize specific
host access in a separate integration task and require isolated environment acceptance.
Do not use this proposal as permission to inspect the live host.

If those decisions are ready sooner, a separately scoped host-boundary implementation may
follow the repair, but authentication, permission maps, ownership and receipt logic must be
real host controls and independently tested. Another fixture provider alone would not
close P0 gaps. No large engine/host rewrite is justified by the current evidence.
