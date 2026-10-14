# Changelog

## 1.1.0 � 2026-10-12 (ARE-A2)

Backward-compatible optional host scope/scene/plan/principal DTOs, HostAdapter Protocol,
AdapterGateway and deterministic temporary-database simulator. Adds host ownership,
exact context and reviewer-identifier boundary checks without claiming authentication.
ARE-A1 APIs, core implementation, baseline tests, schema v1 and migrations unchanged.
Original 1.0.0 wheel preserved. No real host integration or production server.

## 1.0.0 — 2026-10-12

Standalone ARE-A1 contracts, pure effective-rights evaluator, SQLite schema v1,
immutable revisions and review history, explicit concurrent/idempotent review,
exact scene binding and usage readiness, SQLite snapshots, offline tests and example.

## Unreleased — ARE-A3.1 database validation hardening

- Normalize invalid migration ledger/metadata and malformed stored JSON/contracts into
  existing DomainError; preserve invalid database bytes without repair.
- Validate all required schema-v1 tables/columns/constraints and critical trigger bodies,
  digests and historical revision/review references. Public APIs and schema remain v1.
- Add disposable-database regression coverage and correct README base-version label.
- Accepted 1.0.0/1.1.0 wheels and historical acceptance reports remain frozen.
