# Architecture

contracts.py defines validated immutable input contracts and structured results/errors.
evaluator.py is a deterministic pure metadata evaluator. engine.py provides transactional
SQLite persistence, version selection, explicit review workflows and readiness.
__init__.py is the public integration surface. schema.sql is packaged runtime migration
001; migrations/001_initial.sql is its reviewable source mirror. scripts/ contains an
administrator initialization/snapshot CLI and a synthetic temporary-database example.
Tests use unittest and temporary directories, with no paid or network services.

## Database v1

assets stores immutable metadata JSON. rights_revisions stores immutable versioned
rights JSON/hash keyed by asset/revision, with a foreign key to assets. assignments
stores identity and an asset foreign key. assignment_revisions stores versioned binding,
usage and rights references. review_state holds the latest decision per kind/entity;
review_history holds reviewer, decision, note, revision, digest, timestamp, idempotency
key and canonical full request. migrations holds version, application timestamp and
SQL checksum. PRAGMA user_version is 1. Assets/revisions/history/migration records are
protected by update/delete triggers. Media bytes are never stored.

Each operation opens/closes its own connection. Foreign keys are enabled on every
connection. BEGIN IMMEDIATE serializes writes; expected revisions and first-review
checks reject concurrent conflicts. Read operations use one snapshot transaction for
consistent readiness across concurrent changes. Parameterized values are used throughout;
dynamic SQL identifiers come exclusively from fixed internal table names.

## Migrations

Version 0 with no tables migrates atomically to version 1. Existing unversioned tables
are rejected without modification. Initialization reruns validate the packaged migration
checksum and integrity instead of overwriting data. Unknown future versions fail closed.
Future releases must add numbered, additive migration scripts and sequential transactional
upgrade steps; never edit migration 001 or rewrite historical rows. No later migrations
are shipped in Phase A1. Backup before upgrades. Both mirror and packaged SQL must match.

Snapshots use sqlite3.Connection.backup, including committed WAL content, and reject
existing destination files. Restore by constructing Engine in the configured directory
with the snapshot basename, or copying a completed offline snapshot to another trusted,
dedicated directory. Never copy only a live WAL database file as a backup.

## Future host adapter

The adapter belongs in the host, not this package. It must translate an authoritative
approved host plan into SceneBinding, identify authenticated reviewers, select trusted
component-owned data storage, translate usage into UseContext, and call readiness with
the current binding immediately before use. Changed plans require explicit assignment
revision and fresh review; changed rights require explicit reassignment to the current
rights revision. The adapter handles media resolution/verification and host permissions.
The core never imports host scene classes or connects to the host database. No integration
with Defence Studio, QVault or Whisper Studio has been performed.

## ARE-A2 optional boundary

adapter_contracts.py defines validated host DTOs; host_adapter.py defines a one-method
HostAdapter Protocol and AdapterGateway. The gateway delegates to unchanged Engine APIs
inside the host provider's stable context, and validates exact authoritative context and
assignment identity. simulator.py supplies a synthetic in-memory provider and temporary
18-scenario runner; scripts/adapter_simulator.py is its CLI. No adapter table, schema
change or host-framework runtime dependency is added. Real host resource mapping and
authentication belong outside the core. See docs/HOST_ADAPTER.md.

## ARE-A3.1 validation hardening (unreleased source candidate)

Initialization validates schema v1 within its existing transaction before accepting an
existing database; explicit validate_database uses a read transaction and the same private
validator. The packaged schema remains the authority. All seven required tables, ordered
columns, types, nullability, primary/unique keys, foreign keys, positive-revision CHECKs and
autoincrement definition are checked. Supported table definitions must match packaged v1
after cosmetic SQL normalization; alternative equivalent DDL is not certified. Extra
unrelated tables/indexes/triggers are allowed. Ten immutable triggers must retain their
expected table, event, timing and unconditional abort body; names alone do not suffice.

The single version-1 migration entry needs a timezone-bearing timestamp and the packaged
checksum. Stored descriptor/revision/review JSON and typed contracts, digests, historical
rights references and review-state/history consistency are checked. Stale assignments may
reference historical rights revisions and remain valid databases. No schema or migration
changes, automatic repair, reset or external host operation are introduced.
