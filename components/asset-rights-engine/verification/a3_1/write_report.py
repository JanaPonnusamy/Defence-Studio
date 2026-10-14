from pathlib import Path
import json,hashlib,zipfile,difflib,re
root=Path.cwd();evidence=root/'verification/a3_1'
checks={
 'focused-tests.log':46,'full-tests.log':140,'offline-tests.log':140,
 'installed-full-tests.log':140,'installed-offline-tests.log':140}
for name,count in checks.items():
 data=(evidence/name).read_bytes();text=data.decode('utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
 assert re.search(rf'Ran {count} tests? in',text) and text.rstrip().endswith('OK'),name
report=json.loads((evidence/'compatibility-and-preservation.json').read_text())
protected=json.loads((evidence/'protected-before.json').read_text())
after={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in protected}
(evidence/'protected-after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
assert all(after[name]==sha for name,sha in protected.items() if name.replace('\\','/') not in report['authorized_changed_existing_files'])
with zipfile.ZipFile(root/'dist/asset_rights_engine-1.1.0-py3-none-any.whl') as z:
 old=z.read('asset_rights_engine/engine.py').decode().splitlines(keepends=True)
new=(root/'src/asset_rights_engine/engine.py').read_text().splitlines(keepends=True)
(evidence/'engine.diff').write_text(''.join(difflib.unified_diff(old,new,fromfile='accepted 1.1.0 engine.py',tofile='ARE-A3.1 source engine.py')),encoding='utf-8')
text=f'''# ARE-A3.1 database-validation hardening report

Date: 2026-10-12. Workspace: `E:\\DefenceStudio-Components\\asset-rights-engine`.
Accepted release remains **1.1.0**. This is an unreleased source/candidate verification,
not publication, production acceptance or authorization to integrate a host.

## Defects reproduced before edits

Accepted installed 1.1.0 was exercised only against disposable SQLite databases.
`verification/a3_1/reproduced-before.json` records:

- **D01:** deleting the migration-ledger entry and restoring its immutable trigger caused
  constructor `TypeError: 'NoneType' object is not subscriptable`.
- **D01:** malformed rights revision JSON, with its expected trigger restored, caused
  raw `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` during validation.
- **D02:** dropping `review_state` still returned `{{"valid": true, "schema_version": 1}}`.
- **D02 / A3 P07:** replacing the rights-delete trigger with a same-name `SELECT 1`
  no-op also returned valid. A3's original privileged-write probe remains unchanged.
- **D03:** README heading said 1.0.0 while package metadata, accepted wheel and install
  instructions said 1.1.0.

## Changes

Existing files changed, and no others in the protected baseline:

- `src/asset_rights_engine/engine.py`: private startup/validation helpers; safe ledger and
  JSON/contract error translation; complete required schema and trigger-body checks;
  stored descriptor, revision, digest, historical rights reference and review consistency.
- `README.md`: correct the base-version heading, distinguish unreleased source verification
  from accepted-wheel installation, and document diagnostics/limits.
- `ARCHITECTURE.md`, `CONTRACTS.md`, `SECURITY.md`: append validation behavior and limits.
- `CHANGELOG.md`: append an unreleased hardening entry without changing prior release text.

New implementation test: `tests/test_database_validation.py` (46 test methods, with
additional parameterized subcases). New evidence/scripts/logs, a private candidate
installation and candidate wheel are confined to `verification/a3_1/`. This report is new.
`verification/a3_1/engine.diff` shows the engine change relative to accepted 1.1.0.

The same private validator runs during initialization before its existing transaction
commits, and on explicit validation in a read transaction. Validation raises existing
`DomainError("VALIDATION", ...)` for invalid schema, ledger, JSON/contracts or references;
SQLite failures retain `DATABASE`. JSON diagnostics include table/entity/revision and
line/column without dumping the payload. Rejection does not repair, reset or migrate data.

All seven tables are required: assets, rights_revisions, assignments, assignment_revisions,
review_state, review_history, migrations. Column order/type/nullability/keys and normalized
packaged-v1 table definitions check foreign keys, unique idempotency keys, positive revision
CHECKs and AUTOINCREMENT. Ten immutable trigger definitions must match their required
association/event/timing/unconditional abort body. Cosmetic SQL spelling is normalized;
extra unrelated objects are allowed. Alternative semantically equivalent table DDL is
not certified: supported definitions are the shipped schema-v1 definitions. This is a
restore/consistency gate, not protection against administrators modifying SQLite/validator.

## Verification commands and results

All commands ran from the standalone workspace, with process TEMP/TMP redirected to
`verification/a3_1/tmp`, `-B` preventing test bytecode writes, and no network/global installs.

| Command | Result |
|---|---|
| `.venv-a2/Scripts/python.exe -I -B verification/a3_1/reproduce_before.py` | Four original defect probes recorded before editing |
| `python -B -m unittest discover -s tests -v` (first implementation check) | All original 94 tests passed |
| `$env:PYTHONPATH=(Resolve-Path src).Path; python -B -m unittest discover -s tests -p test_database_validation.py -v` | 46 passed |
| `python -B -m unittest discover -s tests -v` (source) | 140 passed |
| `python -B scripts/test_offline.py` (source, sockets denied) | 140 passed |
| `python -B verification/a3_1/build_candidate.py` (PYTHONPATH removed) | Offline wheel build from disposable source stage passed |
| `python -B -m venv verification/a3_1/candidate-env` | Private local environment created |
| `verification/a3_1/candidate-env/Scripts/python.exe -I -B -m pip install --no-index --no-deps --force-reinstall verification/a3_1/candidate/asset_rights_engine-1.1.0-py3-none-any.whl` | Local candidate installed |
| `verification/a3_1/candidate-env/Scripts/python.exe -I -B verification/a3_1/verify_candidate.py` | Installed origin, source/wheel parity, APIs, old-release fixtures, snapshots and preservation passed |
| `verification/a3_1/candidate-env/Scripts/python.exe -I -B -m unittest discover -s tests -v` | Installed candidate: 140 passed |
| `verification/a3_1/candidate-env/Scripts/python.exe -I -B scripts/test_offline.py` | Installed candidate: 140 passed with sockets denied |
| `verification/a3_1/candidate-env/Scripts/python.exe -I -B scripts/adapter_simulator.py` | Original synthetic simulator verified; no production adapter implemented |

The build script copies only package source and pyproject.toml to a disposable staging
folder, then runs `python -B -m pip wheel <stage> --no-index --no-deps --no-build-isolation
--wheel-dir <component>/verification/a3_1/candidate`. It does not rebuild into `dist/`,
change source packaging metadata or use the original source build/egg-info directories.
Final logs are the corresponding `*-tests.log`, build.log, install.log, compatibility.log
and installed-simulator.log; compatibility-and-preservation.json contains machine evidence.

Regressions cover empty/extra/wrong-version/malformed migration metadata; malformed,
non-object, duplicate-key and non-finite JSON; invalid descriptor/rights/binding/usage
contracts; missing tables/columns, view substitution and weakened constraints;
missing/no-op/conditional/misdirected triggers; digest/FK/reference/review inconsistencies;
valid initialization, reinitialization, historical stale assignments, snapshots/restores;
structured-error consistency and exact invalid-database byte/record preservation.
Original tests also cover committed WAL snapshots, idempotency, review concurrency and rollback.

## Schema, API and data compatibility

SQLite user_version remains **1**. `schema.sql` and migration 001 are byte-for-byte
unchanged; no migration added. contracts.py, evaluator.py, adapter contracts/implementation,
public exports, version metadata and business methods from register_asset onward are
unchanged. All **16 public callable signatures** match accepted 1.1.0.

Accepted installed **1.0.0 and 1.1.0** each created a new populated, reviewed fixture under
our disposable temp root. The installed candidate accepted each database, preserved its
bytes during startup/validation, retained readiness/history and restored its snapshot.
These were newly created fixtures, not persistent component or external databases.

All **{report['protected_files']} existing protected files** were hashed before editing and checked
again. Only the six authorized existing files above changed; no protected file is missing.
Accepted wheels, all earlier acceptance reports/A3 evidence, schema/migration, existing tests,
other runtime modules, persistent data and unrelated baseline artifacts remain unchanged.
Persistent databases were read as bytes for hashes only, never opened with SQLite/Engine:

| File | Unchanged SHA-256 |
|---|---|
| `data/asset_rights.sqlite3` | `{report['persistent_data_sha256']['data/asset_rights.sqlite3']}` |
| `data/snapshot.sqlite3` | `{report['persistent_data_sha256']['data/snapshot.sqlite3']}` |

Failure-preservation checks compare exact bytes after intentional temporary corruption
and after both explicit validation and constructor rejection, plus surviving asset rows.
No automatic repair/reinitialization occurs. No live Defence Studio, QVault, Whisper Studio,
external databases, media, network, global package installation or Git operation was used.

## Wheel status

No release version was chosen or published. The candidate intentionally retains base
metadata **1.1.0** and has a separate path/hash; it is not a replacement accepted release.
The accepted wheels still contain their original behavior, including the demonstrated bugs.

| Artifact | SHA-256 |
|---|---|
| Accepted `dist/asset_rights_engine-1.0.0-py3-none-any.whl` | `{report['accepted_wheel_sha256']['dist/asset_rights_engine-1.0.0-py3-none-any.whl']}` |
| Accepted `dist/asset_rights_engine-1.1.0-py3-none-any.whl` | `{report['accepted_wheel_sha256']['dist/asset_rights_engine-1.1.0-py3-none-any.whl']}` |
| Unpublished `verification/a3_1/candidate/asset_rights_engine-1.1.0-py3-none-any.whl` | `{report['candidate_sha256']}` |

Installed package origin was `{report['installed_module']}`. All package source files and
schema bytes in the candidate wheel match current source. Nothing was published or committed.

## Remaining production P0 risks

A3 G01–G07 remain open: authenticated identity/session revocation; tenant/owner mapping
and resource authorization; exclusive custody preventing direct-core bypass; authoritative
host reservations and revocation linearization; durable intents/receipts/reconciliation
for ambiguous commits; authenticated audit provenance; trusted scope/storage custody and
restore manifests. See unchanged `docs/INTEGRATION_GAP_REGISTER.md` for requirements.

Validation does not authenticate reviewers, prove legal rights or media availability,
certify extra schema objects, detect every self-consistent forged/substituted database,
or defeat privileged administrators. It runs at construction/explicit request rather
than on every operation against an already-open instance. Filesystem custody remains
necessary. Host integration and other production controls were not implemented.

Work stops at the authorized database-validation hardening.
'''
(root/'ARE_A3_1_VALIDATION_REPORT.md').write_text(text,encoding='utf-8')
print('Report written; test logs and protected hashes verified.')
