# Security and isolation

No external calls, fetching, downloading, media execution or automatic file opening.
Runtime dependencies are Python standard library only. Metadata references are inert.
The database lives under an explicitly configured trusted component-owned directory;
database and snapshot basenames cannot be absolute, traverse directories, or resolve
through a symlink outside that root. Configuration paths are administrator inputs, never
asset metadata. Tests explicitly supply isolated temporary directories.

Approval is an explicit persisted review action. Input APPROVED flags are rejected;
first decision, expected revisions, digests and idempotency payloads are checked within
one write transaction. Relevant rights or assignment revisions invalidate readiness.
Scene plan changes are detected only when the adapter supplies the new authoritative
binding. History is append-only under normal SQLite writes via immutable triggers.

The caller must authenticate reviewers and constrain data-directory configuration.
This library is not a multi-tenant authorization system. An administrator with direct
filesystem/SQLite access can remove triggers or alter records; there is no cryptographic
signature or external tamper-proof ledger. SQLite integrity, foreign keys, stored hashes
and required triggers are validated, but this is not protection against a privileged
attacker. Filesystem race resistance against hostile local users is not guaranteed.
Protect the configured directory with operating-system permissions.

Rights decisions evaluate declarations, not law, ownership or real-world evidence.
No media-availability claim is made. There is no REST server, media verifier, licence
catalogue or automatic rights inheritance. Backup destinations must be unused; a failed
backup may leave a partial new destination, which must not be used until validated.

## ARE-A2 trust boundary

Validated DTOs and host approval/principal assertions are not authenticated evidence.
AdapterGateway requires a supplied HostAdapter implementation, but cannot certify it.
Production must authenticate sessions, authorize operations/resources, enforce tenant/owner
mappings and keep authoritative plan state stable before/through the engine operation.
No Engine authorization callback or real host authentication is implemented. Direct core
calls retain ARE-A1 behavior; do not expose them as a public multi-tenant API.

SyntheticHost grants are in-memory test policy only, never real authorization. The simulator
uses explicitly synthetic principals and temporary SQLite; it leaves data/ unchanged.
No REST server, OAuth, remote service or real host integration is included. For provider
trust, concurrency and host/SQLite transaction limitations see docs/HOST_ADAPTER.md.

## ARE-A3.1 validation limits

The unreleased source candidate checks complete required schema definitions and critical
trigger bodies, migration identity and stored contract/reference consistency. This detects
missing schema objects and obvious same-name no-op/conditional trigger replacements.
Checks use reads and roll back on failure; invalid storage is not repaired or migrated.

These diagnostics do not establish reviewer identity, rights authenticity, storage scope
or protection against administrators who edit SQLite, the validator or package together.
Self-consistent forged data or substituted databases can still pass. Extra schema objects
are not security-certified. Custody, authentication, authorization, host reservations,
durable receipts and external audit remain production prerequisites (A3 G01–G07).
