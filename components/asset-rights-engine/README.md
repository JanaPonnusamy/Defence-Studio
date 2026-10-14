# Asset & Rights Engine

ARE-A3.1 · base version 1.1.0 (unreleased hardening) · Python 3.11+ · standard-library runtime

Standalone location: `E:\DefenceStudio-Components\asset-rights-engine`.
This component has no host imports, media IO, network calls, or server requirement.
It evaluates declared metadata; it does not establish ownership or provide legal advice.

## Install accepted wheel and verify current source (PowerShell)

```powershell
python -m venv .venv-a2
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
.venv-a2\Scripts\python -m pip install --no-index --no-deps dist/asset_rights_engine-1.1.0-py3-none-any.whl
# Load the unreleased hardening source for the following checks.
$env:PYTHONPATH=(Resolve-Path src).Path
.venv-a2\Scripts\python -m unittest discover -s tests -v
.venv-a2\Scripts\python scripts/test_offline.py
.venv-a2\Scripts\python scripts/example.py
.venv-a2\Scripts\python scripts/adapter_simulator.py
# Use a temporary database for CLI verification, without writing synthetic data to data/.
.venv-a2\Scripts\python scripts/check_temporary_database.py
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
```

The supplied wheel installs offline. To rebuild with an available setuptools >=77:
`python -m pip wheel . --no-index --no-deps --no-build-isolation --wheel-dir verification/a3_1/candidate`.
No global installation is required. Core tests use temporary databases only.

The complete executable integration example is `scripts/example.py`. All example
records and approvals are SYNTHETIC TEST DATA, never real rights evidence. It registers
metadata without a media file, creates and reviews rights, binds an assignment to an
exact scene plan, reviews the assignment, evaluates readiness, and restores a snapshot.

## Python API

Import `Engine`, `AssetDescriptor`, `RightsRecord`, `SceneBinding`, `UseContext`,
`ReviewRequest` from `asset_rights_engine`. Construct `Engine(Path("data"))` with a
trusted, dedicated directory. The default database is `data/asset_rights.sqlite3`.

| Operation | Inputs / result |
|---|---|
| register_asset | AssetDescriptor -> AssetDescriptor; identical registration replays |
| get_asset / list_assets | identifier -> AssetDescriptor / ordered tuple |
| create_rights_revision | asset ID, RightsRecord, expected_revision (0 initially) -> RevisionView |
| get_rights | asset ID -> current RevisionView |
| evaluate_effective_rights | asset ID, UseContext -> Evaluation |
| create_assignment | assignment ID, asset ID, SceneBinding, role, UseContext, optional draft/pending/blocked state -> RevisionView |
| revise_assignment | assignment ID, binding, role, usage, expected_revision, optional state -> RevisionView |
| get_assignment / list_assignments | current RevisionView / ordered tuple |
| review_rights | asset ID, ReviewRequest -> immutable ReviewEvent |
| review_assignment | assignment ID, ReviewRequest, current binding, context -> ReviewEvent |
| evaluate_assignment_readiness | assignment ID, current binding, context -> Evaluation |
| get_review_history | ordered immutable ReviewEvent tuple |
| validate_database | integrity, foreign keys, hashes and immutability checks -> status dict |
| create_database_snapshot | unused local .sqlite3 basename -> Path; SQLite backup API |

A revision view provides entity_id, revision, content, content_digest and derived state.
ReviewRequest requires reviewer, APPROVED/REJECTED, nonempty note, expected_revision,
content_digest, idempotency_key and reviewed_at. Assignment approval also requires the
current binding and a context whose evaluated_at equals reviewed_at. No caller approval
flag is accepted by the engine. Unknown records require an explicit pending revision.

DomainError has `code` and `message`: VALIDATION, CONFLICT, NOT_FOUND, DATABASE,
FILESYSTEM or SNAPSHOT. Failures never commit partial updates. Exact idempotent review
replays return their original event, including after subsequent revisions; they do not
restore old approval. Different payloads under the same key conflict.

See CONTRACTS.md, ARCHITECTURE.md and SECURITY.md for integration boundaries and limits.

## Optional ARE-A2 adapter

[Host adapter contract](docs/HOST_ADAPTER.md) documents immutable host scope, scene,
plan and principal assertions, the HostAdapter Protocol and AdapterGateway operations.
Import these explicitly from adapter_contracts and host_adapter; core imports are unchanged.
The simulator runs 18 synthetic scenarios in a temporary database. It authenticates no
identity and is not production integration. A real host must supply authentication,
authorization, ownership mappings and authoritative plan verification.

The accepted 1.0.0 wheel is retained alongside 1.1.0. Offline build/test commands use
installed wheels; unset PYTHONPATH before installed-wheel checks. Use
`python -m unittest discover -s tests -p test_host_adapter.py -v` and the analogous
`test_adapter_simulator.py` command for focused checks.

## ARE-A3.1 source validation

The accepted wheels retain their original behavior. The hardening is an unreleased source
candidate based on 1.1.0; installing the accepted wheel does not install these repairs.
The installation command above preserves the accepted release, while the subsequent
checks explicitly load current source. New regression tests deliberately fail when run
against the unmodified accepted wheel. Source-only verification (component directory):

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
python -B -m unittest discover -s tests -p test_database_validation.py -v
python -B -m unittest discover -s tests -v
python -B scripts/test_offline.py
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
```

Startup and explicit validation reject incomplete migration metadata, malformed stored
JSON/contracts, missing required tables/columns/constraints, weakened critical triggers
and inconsistent stored references using DomainError. They do not repair data. Schema v1,
public APIs and valid snapshots remain compatible. See ARCHITECTURE.md for supported DDL
and SECURITY.md for limits: database validation is not protection against privileged
administrators and does not close the production host prerequisites.

For diagnostics, validate a completed disposable snapshot in an isolated directory with
`python scripts/database.py <temporary-copy-directory>` under source PYTHONPATH. Do not
point initialization/diagnostic commands at unrelated or production storage.
`scripts/verify_release.py` is the preserved historical A2 source-baseline verifier and
is not the acceptance verifier for this changed source. Current evidence is in
`verification/a3_1/` and `ARE_A3_1_VALIDATION_REPORT.md`.
