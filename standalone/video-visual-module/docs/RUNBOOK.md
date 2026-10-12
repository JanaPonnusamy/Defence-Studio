# Runbook

All commands are run from the module root: `D:\Jana Soft\defenceStudio\standalone\video-visual-module` (Windows PowerShell 5.1).

## Install / upgrade

```powershell
python -m venv .venv                                   # once (Python 3.10+; tested 3.13.5)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env                            # optional; edit values
```

## Start / stop

```powershell
.\.venv\Scripts\python.exe scripts\serve.py                    # uses VIDEO_MODULE_HOST/PORT (default 127.0.0.1:8765)
.\.venv\Scripts\python.exe scripts\serve.py --port 9000
# or: .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/ui/`. Stop with Ctrl+C in that console. A Ctrl+C shuts down the worker pool cleanly. If the process is killed while jobs run, the next start marks those jobs FAILED/`JOB_INTERRUPTED`.

**Port already in use**: uvicorn exits with `ERROR: [Errno 10048] error while attempting to bind on address ...` (exit code 1). Pick another port with `--port`, or find the owner with `Get-NetTCPConnection -LocalPort 8765 | Select OwningProcess`.

## Health and diagnostics

| Check | How |
|---|---|
| Liveness | `Invoke-RestMethod http://127.0.0.1:8765/health` → `status: ok, database: ok` |
| Diagnostics | `GET /diagnostics`: storage dirs, job counts by status, providers, limits (also the UI "Diagnostics" tab) |
| Logs | `storage\logs\video_module.log` (rotating 2 MB × 3, credential-redacted) or `GET /diagnostics/logs?lines=200` |
| Errors | Every API error has a `request_id`. Search the log for it to find the server-side traceback. |

## Configuration

Environment variables (`VIDEO_MODULE_*`) or `.env` in the module root:

| Variable | Purpose |
|---|---|
| `STORAGE_ROOT` | Where the DB and files live. Relative paths resolve against the module root. |
| `DATABASE_URL` | Optional override |
| `JOB_WORKER_THREADS` | Worker threads |
| `DEFAULT_IMAGE_PROVIDER`, `ENABLED_IMAGE_PROVIDERS` | Provider selection |
| `GENERATION_MAX_RETRIES` | Retries for transient provider errors |
| `DEFAULT_AI_IMAGE_BUDGET_USD`, `DEFAULT_MAX_AI_IMAGES_PER_PLAN` | AI budget and cap |
| `MAX_UPLOAD_BYTES` | Upload limit |
| `MAX_SHOT_SECONDS`, `MIN_SHOT_SECONDS` | Fallback split thresholds |
| `WORDS_PER_MINUTE` | Narration timing estimate |
| `API_KEY` | Enables the `X-API-Key` requirement |
| `LOG_LEVEL` | Log verbosity |

Restart the server after changing configuration.

## Database / migrations

- Startup runs `alembic upgrade head` automatically (idempotent).
- Manual run: `.\.venv\Scripts\python.exe -c "from app.db.init_db import init_db; init_db()"`.
- New schema change: edit `app/db/models.py`, then run `.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "<msg>"`. Review the file and commit it. `tests/test_isolation_security.py::test_alembic_migration_matches_models` fails if models and migrations drift.

## Jobs

| Task | How |
|---|---|
| List | `GET /jobs?status=FAILED` |
| Inspect | `GET /jobs/{id}` (`error_code`, `error_message`, `stage`) |
| Cancel | `POST /jobs/{id}/cancel` |
| Retry | `POST /jobs/{id}/retry` (creates a new job linked by `retry_of`) |

After a crash, check the startup log line `Job recovery: {'interrupted': n, 'requeued': m}`. Interrupted jobs are never shown as SUCCEEDED, so retry them.

Common job failures:
- `BUDGET_EXCEEDED`: raise the project budget (create a new project with a higher `ai_image_budget_usd`) or generate fewer images.
- `MISSING_NARRATION_ANCHOR`: create scenes first.
- `VALIDATION_ERROR`: overlapping scenes; recreate them with `replace_existing: true`.

## Plans with GAP shots

A GAP means no usable, relevant source existed for that narration. AI is never substituted for factual content. To fix it:
1. Import the missing material (document, map, statistic or photo), with tags that match the narration words and entities.
2. Clear rights (`RIGHTS_UNKNOWN` gaps), or build with `allow_unknown_rights: true`. Those shots are then flagged.
3. Rebuild the plan.

## Backup / restore

Stop the server, then copy the whole `storage\` folder: the DB, files, sidecars, plans and logs. To restore, copy it back and start the server.

## Tests / self-test

```powershell
.\.venv\Scripts\python.exe -m pytest
powershell -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1    # add -UseRealStorage to test against storage\
.\.venv\Scripts\python.exe scripts\benchmark_10min.py --out bench.json
```

The self-test starts its own backend on a free port with isolated temp storage, stops only the processes it started, and exits 0 only if all required checks pass. It does not need Administrator rights.
