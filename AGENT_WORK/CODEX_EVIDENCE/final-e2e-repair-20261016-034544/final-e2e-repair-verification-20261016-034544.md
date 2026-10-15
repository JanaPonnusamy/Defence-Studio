# Defence Studio final E2E repair and re-verification — 2026-10-16

## Verdict

**VERIFIED_WITH_LIMITATIONS.** One confirmed end-to-end truthfulness defect in the standalone renderer was reproduced, repaired, and regression-tested. The isolated renderer path now persists terminal cancellation without accepting late progress/PID callbacks. Core full-episode E2E remains **BLOCKED** by the live RSS discovery prerequisite and unavailable standalone Audio/Visual/Renderer services; no production-readiness claim is made.

Status meanings used here:

- `PASS`: the named check ran and its expected assertion/result was observed.
- `FAIL`: the check ran and exposed a reproducible defect.
- `BLOCKED`: the check could not reach its required external/runtime prerequisite.
- `E2E_VERIFIED`: the real application/component path completed on isolated data.
- `ARTIFACT_VERIFIED`: the resulting file was independently checked for existence, hash linkage, stream metadata, and complete decode.
- `HUMAN_ACCEPTED`: a human language/editorial/rights review occurred. None was claimed by this pass.

## Baseline and protection checks

- Repository was on `main`; before editing, `HEAD == origin/main == 2999a250f8561ab91292183c4ef43e3c64d5fbe7`.
- Existing concurrent modifications were recorded and preserved: `AGENT_WORK/CURSOR.md`, Claude/Cursor audit reports, trial WAVs, exit markers, model cache, and virtual environments. No `git reset`, clean, force-push, or broad staging was used.
- No production database, schema, credentials, `.env.local`, protected job `522dc4ce-16d0-4680-a450-fcd841180c69`, or review history was opened for mutation. All new workflow runs used disposable data under this evidence directory or pytest temporary directories.
- Service probes: standalone Audio `8001`, Visual `8765`, and Renderer `8780` refused connections. The unrelated local ports `8041`/`8042` returned 404 for `/health`; they were not stopped.

## Fresh baseline results

| Check | Result | Evidence |
|---|---|---|
| Core backend affected suites (worker, voice, audio, render, QC, package, assets, scenes) | **PASS** — 112 passed, 2 skipped, exit 0, 406.77s | `backend-focused-baseline.log` / `.exit` |
| Standalone renderer with system interpreter | **BLOCKED** — collection failed because system Python lacked `jsonschema`; no source failure inferred. `requirements-dev.txt` already declares it. | terminal baseline; dedicated venv rerun below |
| Standalone renderer dedicated venv baseline | **PASS** — 80 passed, 1 skipped, 1 warning, exit 0, 94.99s | `renderer-focused-baseline.log` / `.exit` |
| Frontend unit suite | **PASS** — 65 passed, 0 failed, 0 skipped, exit 0 | `frontend-baseline.log` / `.exit` |
| Core `scripts/e2e_acceptance.py` on disposable DB | **BLOCKED/FAIL** — exit 1 at `discovery: real RSS feeds` with `AssertionError: no candidates from live feeds`; no downstream success was recorded. | `core-e2e-acceptance.json`, `.log`, `.exit` |

Historical figures in earlier reports remain historical and were not copied into the fresh baseline: backend 642/8 skipped, lifecycle 72/2 skipped, audio 61, voice engine 59, renderer 117/1 skipped.

## Confirmed finding and repair

**E2E-RENDER-001 — P1 correctness/status integrity — CONFIRMED and FIXED.**

Reproduction used an isolated synthetic renderer job and a monkeypatched render function. After the worker persisted `CANCELLED`, a queued progress callback changed persisted progress from `0.0` to `1.0`, and a stale PID callback wrote `ffmpeg_pid=12345`. The pre-fix regression exited 1 with `assert final.progress == 0.0` but actual `1.0`. Evidence: `renderer-late-callback-before.log` / `.exit`.

Root cause: `standalone/video-renderer/renderer/jobs.py` updated `RenderJob` by `job_id` only in both callbacks. The terminal transition itself was fenced, but late callback writes were not.

Fix: both `progress()` and `on_pid()` now update only when `RenderJob.status == "RUNNING"`. Terminal cancellation, failure, or restart recovery therefore wins over stale worker callbacks. No schema or API change was required.

Changed files:

- `standalone/video-renderer/renderer/jobs.py` — add the RUNNING predicate to progress and PID updates.
- `standalone/video-renderer/tests/test_jobs.py` — add an isolated late-callback cancellation regression covering both progress and PID fencing.

## Post-fix verification

- Focused regression plus cancellation/crash recovery: **4 passed**, exit 0, 20.12s (`renderer-late-callback-after.log` / `.exit`).
- Full standalone renderer suite: **118 passed, 1 skipped, 1 warning**, exit 0, 100.49s (`renderer-full-after.log` / `.exit`). The one skip is `tests/test_package.py:99` because symlinks are not permitted on this Windows environment. The warning is Starlette/httpx deprecation only.
- Frontend and core backend affected baselines remained green; no backend source was changed by this repair.

## Real isolated artifact

The actual renderer job path was exercised with a disposable synthetic package, persisted SQLite job, worker thread, FFmpeg, and output validation. It produced:

- Job `r_91a68e5007c44e`, persisted `SUCCEEDED`, `progress=1.0`, `240/240` frames.
- Output [synthetic-render.mp4](<E:/Jana Soft/DefenceStudio/AGENT_WORK/CODEX_EVIDENCE/final-e2e-repair-20261016-034544/synthetic-render.mp4>) — 575,233 bytes, SHA-256 `7f2ee1dddd193ffc843e570312e22eb42ceb0cb167f13dd05bf39f07a9b3183d`; the persisted job hash matched exactly.
- `ffprobe` exit 0: MP4, 20.000000s, H.264 320x180 video, AAC mono 48 kHz audio, 20.000000s.
- Full decode `ffmpeg -v error -i synthetic-render.mp4 -f null -` exit 0.

This is `E2E_VERIFIED` and `ARTIFACT_VERIFIED` for the renderer component only. It is a synthetic fixture, not a human-reviewed episode, Tamil narration, rights acceptance, or publication artifact. Raw metadata and commands are in `real-render-e2e.json`, `synthetic-render-ffprobe.json`, `synthetic-render-decode.log`, and `real-render-artifact-validation.log`.

## Core E2E and misleading-output review

The core acceptance harness truthfully recorded `ok=false` after live RSS discovery returned no candidates; it did not proceed to script, scene, audio, visual, render, QC, or package. The report did not claim downstream success. The blocked cause is runtime/feed availability, not a reproduced core state-integrity defect.

The service probes also show why a full three-module core E2E cannot be claimed in this environment. Existing gates and focused tests continue to fail closed when modules or approvals are missing. The protected English script remains `AWAITING_REVIEW`; no approval was fabricated.

Tamil remains `BLOCKED`: existing Parler WAVs are mechanically validated only, have no native pronunciation review, and are not registered through the production voice contract. `DEFENCE_ERA_FEMALE_001` remains unprovisioned.

## Independent review

Claude completed a read-only review in `AGENT_WORK/CLAUDE_EVIDENCE/final-e2e-repair-review-20261016.md`. It confirmed the reproduced callback defect, both RUNNING predicates, the regression test, the 118/1 renderer result, the synthetic MP4 hash/ffprobe/decode evidence, and the live-RSS core E2E block. Claude did not rerun tests or media tools; its review is an evidence consistency check, not a second execution.

## Known gaps and checks not run

- Full backend suite was not rerun because this repair changed only the standalone renderer; the fresh affected backend suites passed.
- Core three-module acceptance remained blocked by live RSS/module availability; no paid providers, uploads, OCR, hosted search, or YouTube calls were made.
- The approximately 10-minute renderer acceptance artifact was not generated; the isolated 20-second real MP4 is not a substitute.
- Human Tamil listening, rights/legal acceptance, and production episode approval remain unverified.
- Whole-tree `git diff --check` retains the pre-existing EOF blank-line warning in concurrently modified `AGENT_WORK/CURSOR.md`; the Codex-owned staged diff is checked separately and clean.

## Git handoff

Before staging this repair, only the two renderer files above were Codex-owned source/test changes. Concurrent files and retained evidence remain excluded. The final staged diff will be inspected and only explicitly reviewed files will be staged, never with `git add .`.

Repair commit `b40c31fcdc655d06a3d942296b1e1c7a4d389f27` was pushed to `origin/main` with exit 0. Fresh fetch and `git ls-remote origin refs/heads/main` confirm `HEAD_MATCH=True` at that hash. The staged diff check exited 0; the whole-tree check still reports only the pre-existing Cursor-owned EOF blank-line warning.
