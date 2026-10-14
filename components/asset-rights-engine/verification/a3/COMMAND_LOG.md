# ARE-A3 command inventory and results

Project workdir: `E:\DefenceStudio-Components\asset-rights-engine`. No pip/build/install,
Git or network command. Task input read:

```powershell
Get-Content -LiteralPath 'C:\Users\Pharma\.codex\attachments\52dab614-5e35-4f67-a563-a98352a0a7bb\Pasted text.txt'
```

Actual inventory/read commands:

```powershell
Get-Location
rg --files -g AGENTS.md -g '*REPORT*' -g '*.md' -g '*.py' -g pyproject.toml -g '*.sql' -g '*BASELINE*' -g '*.whl' -g '!build/**' -g '!verification/candidate/**'
Get-Content ACCEPTANCE_REPORT.md,A2_ACCEPTANCE_REPORT.md,A2_BASELINE.json,docs/HOST_ADAPTER.md,README.md,CONTRACTS.md,ARCHITECTURE.md,SECURITY.md,CHANGELOG.md
```

Numbered source/test/script reads and hash recording/comparison used standard-library
Python heredocs piped to `python -B -`; results are preserved in numbered-artifacts.txt,
source-symbols.json and protected_before.json/protected_after.json. The current reproducible
numbering helper is inspect_artifacts.py. New documents/helpers were written with local
heredocs; no accepted source file was edited.

Verification commands actually executed:

```powershell
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='E:\DefenceStudio-Components\asset-rights-engine\verification\a3\tmp'
$env:TMP=$env:TEMP
.venv-a2\Scripts\python -B scripts/test_offline.py *> verification/a3/offline-tests.txt
.venv-a2\Scripts\python -B scripts/verify_release.py *> verification/a3/release-verification.txt
.venv-a2\Scripts\python -B scripts/check_temporary_database.py *> verification/a3/temporary-cli.txt
Get-FileHash -LiteralPath 'dist\asset_rights_engine-1.0.0-py3-none-any.whl','dist\asset_rights_engine-1.1.0-py3-none-any.whl'
if (Test-Path -LiteralPath '.git') { Write-Output 'Git marker present (no Git command run)' } else { Write-Output 'Git not initialized; no Git commands run' }
.venv-a2\Scripts\python -I -B verification/a3/audit_probes.py *> verification/a3/probe-output.txt
.venv-a2\Scripts\python -I -B verification/a3/inspect_artifacts.py > verification/a3/numbered-artifacts.txt
```

Exit/results: socket-denied installed suite 94/94, exit 0; release verifier 0; temporary
CLI initialized/snapshotted/restored schema 1 with cleanup; both accepted hashes match;
no .git; audit probe runner 0 with P01–P10 actual observations. Imports resolve to installed
1.1.0, with no pip installation or source-path substitution.

First inspect_artifacts run returned exit 1 because UTF-8-only decoding encountered the
original CHANGELOG.md byte 0x97. Only the audit helper was changed to support explicit
CP1252 fallback, then the same command returned exit 0. Accepted bytes stayed unchanged.
No component test failed. Protected-file check found zero changed artifacts and empty
temporary storage. One documentation-writing orchestration call failed before shell
execution due to quoting and was resubmitted; it did not modify files or run a component check.

Detailed reproducible probe code is audit_probes.py. Signatures, archive comparisons,
negative-storage observations and structured results are in probe-results.json. Expected
TypeError/JSONDecodeError/false-valid observations are findings, not repaired tests marked passed.
