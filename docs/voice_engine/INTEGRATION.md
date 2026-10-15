# Voice engine integration (schema v12)

## Mapping
* Voice jobs are `workflow_jobs` rows (`request_schema` = `defence-studio.voice-analysis.v1` / `-generation.v1`) with ONE stage
  (`voice_analysis` / `voice_generation`). The existing `WorkerService` claims, leases, retries and recovers them.
  `voice_job_requests` holds payload, progress note, result reference and the (temporary) reference-file path.
* Engine tables live in the same SQLite file (their own `CREATE IF NOT EXISTS` schemas, applied by migration v11->v12).
* `repositories/jobs.list_owned_jobs` excludes `defence-studio.voice-%` so the research dashboard is unchanged.
* Workers: `python -m defence_studio.research_worker` now also handles voice stages; or set
  `DEFENCE_STUDIO_VOICE_WORKER=1` to run an in-app voice worker thread.

## API (`/api/voice/*`, owner-authenticated)
| Route | Purpose |
|---|---|
| `GET /providers` | voices, readiness, licenses, aligner status, limitations, direction ranges |
| `POST /analysis` (multipart, `Idempotency-Key`) | upload + start analysis job (202) |
| `GET /profiles`, `/profiles/{id}?version=`, `/profiles/{id}/segments`, `/profiles/{id}/compare?a=&b=` | read-only profile access |
| `POST /generations` (`Idempotency-Key`) | start generation job (202) |
| `GET /generations/{id}`, `/generations/{id}/audio`, `/generations/{id}/alignment` | result, WAV (owner-checked), alignment |
| `GET /jobs?kind=`, `/jobs/{id}`; `POST /jobs/{id}/cancel`, `/jobs/{id}/retry`; `DELETE /jobs/{id}/reference` | history + lifecycle |

Errors: 404 not found/other owner, 409 idempotency conflict/invalid transition, 413 too large, 422 validation.

## Retention
Uploaded reference audio is stored under `<storage>/voice/uploads/<random>.<ext>` and deleted when analysis succeeds
(unless `DEFENCE_STUDIO_VOICE_RETAIN_REFERENCE=1`). After failure/cancel it is kept only so a retry is possible and can be
deleted with `DELETE /jobs/{id}/reference`; orphans are purged at startup. Only measurements + provenance are persisted.

## Migration safety / rollback
Additive only (new tables). Back up the SQLite file before first start on v11. Rollback = restore the backup (an
application of v12 never alters v1-v11 tables; the voice tables may simply be left unused or dropped manually).
Tested: v11 DB with rows -> v12 rows preserved; copy of the repository's real dev DB (v10) -> chain migrates, integrity ok.

## Cancellation semantics
Cancel fails the active claim, so `WorkerService.complete()` raises `ClaimLost` and no result is committed; handlers also
check cancellation immediately before persisting. A narrow window exists where an engine row is written before the
handler observes the cancel; the job still reports CANCELLED and exposes no result.
