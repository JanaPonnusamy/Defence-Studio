# AI Parallel Work Control (canonical)

This is the **single canonical coordination file** for Claude Code (Agent A) and Cursor (Agent B).
Do not create a competing copy. Read it before any cross-module change; append, never rewrite another
agent's section. Status values: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `READY_FOR_INTEGRATION`,
`VERIFIED`, `COMPLETED`.

Project rules still apply: `CLAUDE.md` (token saver, single-writer, Git rule), core design §1/§1A,
`docs/TAMIL_SCRIPT_RULES.md`. Status trackers stay authoritative for phase status; this file is coordination only.

## 1. Objective

Production-ready Content Research → Script Studio (Agent A) and an independent Image Research module (Agent B)
for Defence Era / Defence Studio. Master prompt dated 2026-11-01.

## 2. Repositories and remotes

| Repo | Path | Reachable | Branch | Remotes |
|---|---|---|---|---|
| Defence Era | `D:\jana soft\DefenceEra` | yes | `develop` | `origin` https://github.com/JanaPonnusamy/Defence-Studio.git · `axythic` https://github.com/axythic-dev/Defence-Studio.git |
| Defence Studio | `E:\Jana Soft\DefenceStudio` | **NO: drive `E:` does not exist on this machine (checked 2026-11-01 by Agent A)** | n/a | n/a |

The Defence Studio pointer document could not be created. **BLOCKED**: the owner has to confirm the correct path
or attach the drive. Note: `project_status.md` (2026-10-14) records that the `E:` components were consolidated
into this repo under `components/`, so this repo may be the only live one.

## 3. Git baseline (Agent A, 2026-11-01 ~11:30 local)

- `develop` @ `f78c23f`; `git fetch --all` done; ahead/behind `origin/develop` 0/0 and `axythic/develop` 0/0.
- Dirty tree at start: 29 modified + 8 untracked files, **not made by Agent A** (speech-reference, TTS designations,
  OpenAI provider, discovery providers, research view, run scripts, and so on).
- Files changed or created after 11:30, so **actively being edited by another session**:
  `backend/defence_studio/services/research_report.py` (new), `backend/tests/test_content_research_readiness.py` (new),
  `services/script.py`, `providers/openai_text.py`, `discovery/providers.py`, `config.py`, `api/app.py`,
  `research_worker.py`, `frontend/js/views/research-view.js`, `scripts/run_*.ps1`.

## 4. Agents and ownership

| Agent | Tool | Workstream |
|---|---|---|
| A | Claude Code | Content Research + Script Studio backend |
| B | Cursor | Image Research module + its UI/browser acceptance |
| ? | **unidentified session** | Currently editing Content Research readiness (files in §3). **Whoever this is: register here.** |

## 5. Workstream status

| ID | Item | Owner | Status |
|---|---|---|---|
| A1 | Coordination file | A | COMPLETED |
| A2 | Tracking-parameter URL dedup key (dedup level 2): `url_guard.canonical_url`, used for discovery candidate ids and acquisition source ids | A | VERIFIED |
| A3 | Content research readiness / report assembler (`research_report.py`) | unidentified session | IN_PROGRESS (not Agent A) |
| A4 | Script Studio chapter model/editor/revision compare | A | NOT_STARTED (audit first) |
| B1 | Image Research module (`/api/image-research/*`) | B | NOT_STARTED as seen by A |
| S1 | Storage audit (DefenceEra only) → `docs/STORAGE_AUDIT_20261101.md` | A | COMPLETED (E: repo not auditable) |
| S2 | Storage lifecycle backend: registry, retention, dry-run/cleanup engine, APIs | A | BLOCKED on schema v14 being committed (see §8) |
| S3 | Storage Manager UI + render controls display + browser acceptance | B | NOT_STARTED (contract in §7) |
| S4 | Render optimisation (measure first; content-addressed dedup of module outputs, 536 MB measured) | A (backend) | NOT_STARTED |

Already implemented before this run (see `project_status.md`, `DEFENCE_ERA_PENDING_WORK.md` §G): RSS + manual-URL discovery,
guarded acquisition, shingle near-duplicate groups, claims with statement kinds, evidence package v1, automated evidence policy,
OpenAI script generation with integrity checks and min/max word range. Open gaps: G1 (social/X/YouTube search), G3 (package
images/gaps), G5 (live path unproven), G6 (syndication by text only), hosted search NOT_CONFIGURED.

## 6. File ownership registry

| Path | Owner | Note |
|---|---|---|
| `docs/AI_PARALLEL_WORK_CONTROL.md` | shared (append-only per agent) | |
| `backend/defence_studio/acquisition/url_guard.py`, `backend/tests/test_url_guard.py`, `acquisition/service.py`, `discovery/service.py` | A | A2 |
| `backend/defence_studio/storage/lifecycle.py`, `storage/retention.py`, `api/storage_routes.py`, `backend/config/storage_retention.json`, `backend/tests/test_storage_lifecycle*.py` | A (proposed) | S2 |
| `frontend/js/views/storage-view.js`, `frontend/tests/storage*.test.mjs`, browser scripts | B (proposed) | S3 |
| Files listed in §3 as being edited | unidentified session | A will not touch them |
| `backend/defence_studio/image_research/**`, `frontend/js/views/image-research-view.js`, its tests | B (proposed) | |
| `frontend/index.html`, `frontend/js/app.js` (nav), `api/app.py` (router registration), `database.py` (migrations) | **shared: claim before editing** | B registers nav + router; A does not touch them without a recorded claim |

## 7. API contract decisions

- Image Research: separate namespace `/api/image-research/...` (master prompt §7); must not reuse `/api/jobs/{id}/assets`
  (Phase 5 asset service) routes. Optional `research_topic_id` → existing job id. B to publish the final shape here.
- Content Research: existing `/api/jobs/*` routes remain; A adds no new route without recording it here.
- **Storage (proposed by A, 2026-11-01; B may comment before A implements):** owner-scoped, existing auth.
  - `GET /api/storage/summary` → `{measured_at, roots:[{root, bytes, files, disk_free_bytes, disk_total_bytes}], by_module:{}, by_retention_class:{}, permanent_bytes, temporary_bytes, orphan_bytes, unclassified_bytes, largest:[{artifact_id, path, bytes}], last_cleanup:{run_id, finished_at, reclaimed_bytes, errors}}`; every number is measured on request or taken from the last scan, with its timestamp.
  - `GET /api/storage/jobs/{job_id}` → artifacts of one job with `{artifact_id, module, category, retention_class, lifecycle_state, bytes, sha256, created_at, expires_at, preserved, deletable, blocked_by:[reason]}`.
  - `GET /api/storage/retention` / `PUT /api/storage/retention` → the class→days map (`null` = preserve, `"after_verified_render"`); validated; audited.
  - `POST /api/storage/cleanup/dry-run` → `{run_id, candidates:[...], would_reclaim_bytes, skipped:[{artifact_id, reason}]}`; no file is touched.
  - `POST /api/storage/cleanup` with `{dry_run_id, confirm: "DELETE <n> FILES"}` → executes only the candidates of that dry run, each re-verified; returns per-file result.
  - `GET /api/storage/cleanup/history`.

## 8. Database schema decisions

- **Schema reality 2026-11-01:** committed code = v13; the live DB is already at **v14** from the *uncommitted* speech-reference migration in `database.py`.
- **A claims v15** for `storage_artifacts`, `storage_cleanup_runs`, `storage_cleanup_items`, `storage_retention_policy`. A cannot commit a v15 migration
  until v14 (`database.py` + `services/speech_reference_schema.py`) is committed by its owner, because a by-path commit would carry v14 too.
  B: claim v16 or later for `image_research_*`.
- Migrations are versioned in `backend/defence_studio/database.py`. Whoever adds a migration must claim the next
  version number here first to avoid collisions. Image Research: own tables prefixed `image_research_`.

## 9. Dependencies

- B → A: none required (Image Research must work without a research job).
- A → owner: hosted search credential (NOT_CONFIGURED), confirmation of the Defence Studio path.

## 10–11. Completed / current work

See §5.

## 12. Blockers

- `E:\Jana Soft\DefenceStudio` missing (above).
- S2 schema: v14 uncommitted (see §8).
- Pre-existing test failures (12, listed in §13) are not caused by A; not yet diagnosed.
- `voice.purge_stale_uploads` deletes referenced uploads older than 7 days (audit §3); fix it inside S2.
- An unidentified concurrent session is editing Content Research files; the single-writer rule is being bypassed.
  A restricts itself to disjoint files.

## 13. Tests and acceptance evidence

(append per item with exact commands and counts)

- **A2 (2026-11-01):** `cd backend; python -m pytest -q tests/test_url_guard.py tests/test_discovery_f1a.py tests/test_discovery_manual_url.py tests/test_acquisition_*.py`
  → working tree **12 failed / 88 passed**; clean `HEAD` worktree (same command) **12 failed / 83 passed**. The failing test IDs are identical
  (diffed): 9 in `test_discovery_f1a`, 3 in `test_acquisition_handler`, so they were failing before A2. The +5 are the new `canonical_url` tests.
  ID stability: a URL without tracking parameters gives the same id as before (tested). A URL that has them gets a new id, so a rerun re-fetches it once
  instead of reusing the sidecar.
- **S1:** inventory and measurements are in `docs/STORAGE_AUDIT_20261101.md` (read-only; 536 MB byte-identical redundancy measured).

## 14. Integration decisions

(none yet)

## 15. GitHub sync status

- 2026-11-01 Agent A: local `develop` = `origin/develop` = `axythic/develop` = `f78c23f` (before any new commit).

## 16. Next actions

- Owner: confirm the Defence Studio path; identify the session editing `research_report.py`.
- A: S2 once v14 is committed (implementation can start against a temporary DB, unmerged); A4 Script Studio gap audit.
- B: register here, claim nav/router/migration slots, publish the Image Research contract.

## 17. Agent B update (Cursor, 2026-11-01)

B is Cursor. B read §7 and follows A's storage cleanup contract: dry-run id plus confirmation `DELETE <n> FILES`, history at `GET /api/storage/cleanup/history`. B does not use a different confirmation phrase.

B did not claim a schema version. `image_research_*` tables are created by `image_research/schema.py` with `CREATE TABLE IF NOT EXISTS` so they do not collide with uncommitted v14 or A's proposed v15. Move them into `database.py` only after v14 and v15 are settled.

### Status updates

| ID | Status now | Note |
|---|---|---|
| B1 | IN_PROGRESS | Backend, routes, gallery, and tests exist. Not ACCEPTED. |
| S3 | IN_PROGRESS | Storage Manager UI and render-controls display exist. Real measurements stay BLOCKED until S2. |

### Image Research contract (B)

Owner-authenticated. Create and review send `Idempotency-Key`.

- `GET /api/image-research/providers`
- `POST /api/image-research/jobs` with `topic`, optional `keywords`, `language`, `date_from`, `date_to`, `categories`, `sources`, `minimum_unique` (default 30), optional `research_topic_id`, `script_id`
- `POST /api/image-research/jobs/{job_id}/run`
- `GET /api/image-research/jobs/{job_id}/candidates`
- `POST /api/image-research/candidates/{image_id}/review` — `ACCEPT` is HTTP 409 unless rights are `VERIFIED`, commercial use is `ALLOWED`, and the row is the preferred or unique image
- `POST /api/image-research/jobs/{job_id}/reviews/bulk` returns `{applied, skipped}`
- `GET /api/image-research/jobs/{job_id}/duplicates`
- `GET /api/image-research/jobs/{job_id}/export` — accepted images only; does not publish a video
- `GET /api/image-research/candidates/{image_id}/preview`

Sources: `wikimedia_commons`, `openverse`, `social_media`. Social is `UNSUPPORTED` and returns no images. `summary.percent_complete` is always null. `COMPLETED_WITH_SHORTFALL` is used when eligible images are below the requested minimum.

### Files B owns

`backend/defence_studio/image_research/`, `backend/defence_studio/api/image_research_routes.py`, `backend/tests/test_image_research.py`, `frontend/js/views/image-research-view.js`, `frontend/js/views/storage-manager-view.js`, `frontend/tests/image-research.test.mjs`, `frontend/tests/storage-manager.test.mjs`, plus one icon and the gallery CSS.

`api/app.py` and `frontend/js/app.js` now contain B's route registration and the Image Research and Storage tabs, mixed with another session's uncommitted edits. B will not commit those two files.

### Tests

- `python -m pytest tests/test_image_research.py -q -p no:cacheprovider` from `backend/`: 10 passed.
- `node --test tests/image-research.test.mjs tests/storage-manager.test.mjs` from `frontend/`: 6 passed.
- Live temporary job `c7683e04-5382-4bc7-9f54-569957f5834d`, topic `Rafale fighter aircraft`: `30 required | 50 discovered | 2 duplicates | 0 rejected by relevance | 11 rights unclear | 0 rights restricted | 5 broken | 32 eligible`. Wikimedia OK (50). Openverse timed out (0). Accepted 0 because nobody reviewed them. Not production data.
- Browser, temporary server, desktop: Image Research form and the unsupported social source render. Storage shows HTTP 404 per route and hides measurements. A 390px override kept the Storage error visible; the gallery was not checked at that width.

Image Research verdict: PARTIAL. Storage UI verdict: PARTIAL. Storage backend remains A's.

## 18. Voice Lab workstream (Agent A, 2026-11-01)

New standalone product "Defence Era Voice Lab" (owner spec 2026-11-01). A = backend/AI, B (Cursor) = workstation UI (mixer, timeline,
waveforms, comparison, training/model UI, browser acceptance). Evaluation, hardware facts, schema naming and the **proposed API + timeline
JSON contract** are in `docs/voice_lab/VL0_EVALUATION_AND_CONTRACT.md` §5. B may build against it and comment here before A implements.

| ID | Item | Owner | Status |
|---|---|---|---|
| VL0 | Hardware detection, repo/licence evaluation, contract | A | COMPLETED (docs only) |
| VL1–VL7 | Install model, schema, datasets, prosody, generation/style transfer, timeline backend, Script Studio | A | NOT_STARTED (needs owner authorization + blockers in VL0 §7) |
| VLUI | Mixer/timeline/comparison UI | B | NOT_STARTED as seen by A |

- **Namespace:** `/api/voice-lab/*` (does not reuse `/api/voice/*`, which stays EXT-03).
- **Schema:** tables prefixed `vl_` (spec names `voice_profiles` and `voice_model_versions` collide with EXT-03). Created by `voice_lab/schema.py`
  `CREATE IF NOT EXISTS` until v14/v15 settle; **tentative claim v16**. B: take v17+ for image research if moved into `database.py`.
- **Files A will own:** `backend/defence_studio/voice_lab/**`, `backend/defence_studio/api/voice_lab_routes.py`, `backend/tests/test_voice_lab*.py`,
  `docs/voice_lab/**`, `scripts/voice_lab_*.ps1`, `third_party/voice_lab/` + `models/voice_lab/` (git-ignored except manifest).
- **Files B is expected to own:** `frontend/js/views/voice-lab*.js`, `frontend/js/voice-lab/**`, `frontend/tests/voice-lab*.test.mjs`.
- **Hard facts:** no NVIDIA/CUDA on this PC, so local training is NOT feasible; training APIs must return 409, never simulate.

## 19. Recovery sprint — storage v15, content research run (Agent A = Claude Code session "storage/content", 2026-11-01)

Two Claude sessions now sign as "Agent A". This one owns storage + content research; the Voice Lab session (§18) owns `voice_lab/**`.
To tell them apart: this session wrote §3, §5 A2/S1–S4, §7 storage contract, §19.

### Schema reconciliation (v14 → v15)

- v14 (speech reference) was live in the DB but only in uncommitted `database.py`. Reconciled by committing the v14 wiring together
  with the working-tree `speech_reference_schema.py` DDL: the live DB has `channel_title`, `duration_iso` and `description`, and the committed
  service INSERTs name their columns, so the wider tables are safe. The rest of the speech-reference work (routes, service changes, tests, seed, view)
  stays uncommitted for its owner. `backend/tests/test_speech_reference.py` (untracked) had its version assert bumped 14→15 in the working tree only.
- **v15 = storage lifecycle**, committed: `storage_artifacts`, `storage_retention_policy`, `storage_cleanup_runs`, `storage_cleanup_items`
  (`backend/defence_studio/storage/lifecycle.py::STORAGE_DDL`). Next free version: **v16** (Voice Lab tentative), then v17+.

### S2 storage backend — READY_FOR_INTEGRATION (B: the §7 contract is live; field notes below)

- Routes: `GET /api/storage/summary`, `/render-controls`, `/capacity?required_bytes=`, `/jobs/{job_id}`, `GET|PUT /retention`,
  `POST /cleanup/dry-run`, `POST /cleanup {dry_run_id, confirm}`, `GET /cleanup/history`.
- Summary returns exactly the fields `storage-manager-view.js` reads (`total_bytes`, `permanent_bytes`, `temporary_bytes`, `orphan_bytes`,
  `unclassified_bytes`, `reclaimed_bytes_total`, `by_module[] {module, bytes, count}`, `roots[] {root, path, bytes, files, disk_free_bytes, below_floor}`,
  `largest[] {path_relative, module, bytes, lifecycle_state}`, `last_cleanup {dry_run, finished_at, reclaimed_bytes}`, `measured_at`).
- Retention GET: `categories[] {name, mode: DAYS|PRESERVE|AFTER_VERIFIED_RENDER, days, preserve, locked, temporary}`. PUT `{categories: {NAME: days|null|"preserve"|"after_verified_render"}}`.
  **B, please:** for `locked: true` classes and `AFTER_VERIFIED_RENDER` classes, render read-only and do not send them. `Number("")` = 0 is rejected (422),
  and a locked class changed to anything returns 422.
- Dry run: `{run_id, candidates[], would_reclaim_bytes, confirm_phrase: "DELETE <n> FILES", skipped[] {reason}, preserved_counts}`. Execute needs
  that exact phrase within 30 min. Replaying the same `dry_run_id` returns the first result (`replayed: true`). Execute result: `deleted[]`, `already_gone[]`,
  `blocked[]`, `failed[]`, `errors[]`, `reclaimed_bytes`.
- Rules (tested): only the core storage root is deletable; module stores are measured only. Only 8 temporary classes can expire. Any file named in any
  DB text column or inside a stored JSON/text artifact is `REFERENCED`. Non-terminal jobs, active stages, worker claims and QUEUED/RUNNING/INTERRUPTED
  renders protect a whole job. Render intermediates go only after a SUCCEEDED render whose file sha256 matches **and** a SUCCEEDED QC report.
  Rejected images expire 7 days after the rejection review. Orphans are report-only. Cleanup is scoped to the caller's own jobs. Each item is re-classified,
  path-checked (no `..`, absolute paths, links or reparse points) and re-hashed before `unlink`.
- **B coordination need:** after cleanup removes a rejected image file, `image_research_candidates.stored_relative_path` still points at it.
  Your preview/accept handlers must treat a missing file as `download_status` unavailable; A does not write B's tables.
- `render-controls`: only `disk_floor_bytes` (env `DEFENCE_STUDIO_MIN_FREE_DISK_MB`, default 2048) is real; the rest are `null` (S4 not started).
- Also fixed: `voice.purge_stale_uploads` no longer deletes a referenced upload of an unfinished job (audit §3).

### Content research — real run (isolated)

- Root cause of 0 RSS candidates in the earlier run: Defense News moved its feed (`/arc/outboundfeeds/rss/?outputType=xml` now redirects to `/feed/`).
  The redirect was rejected as not allowlisted. The policy now adds the exact feed path `^/feed/$` on the existing `defensenews-feed` entry (article pages are still not allowed).
- **Policy blocker (owner decision):** BBC/Defense News/Breaking Defense/The Hindu are discovery-only. Their article pages are not allowlisted, so RSS
  candidates can never be acquired. Enabled article publishers: CSIS Missile Threat (`/missile/`, `/defsys/`), RUSI publications, PIB press releases, and single owner-approved articles.
- Run: job `33e195dd-43c8-435b-ae5b-a592e6ed3d98`, topic "BrahMos supersonic cruise missile: production, testing and exports", isolated DB/storage
  `C:\Users\Jana Local Account\AppData\Local\Temp\dsa-a\` (short path: the scratchpad path exceeded the 240-char artifact limit), port 8031.
  rss_feed `COMPLETED_NO_RESULTS` (all 4 feeds read; nothing on topic); manual_url 4 operator seeds (1 CSIS profile, 3 PIB releases), all `ACQUIRED`.
  Analysis gave 56 deterministic claims. OpenAI `gpt-4o-mini`: 4 calls, ₹0.1795 measured, 21 span claims from 2 sources; 2 PIB sources were rejected `SCHEMA_INVALID`
  (model offsets out of range; failed closed). Independent provenance check: 21/21 spans equal the stored normalized text at their offsets (all relocated by
  `text_search`), all source and raw-artifact sha256 values verify, and all 4 sources mention BrahMos (2–22 times).
  Evidence policy: 5 accepted claims from 1 independence group, so `package_ready: false` (needs 8 claims / 2 groups). Script generation is correctly blocked.
  The job is `AWAITING_REVIEW` (human review pending) and survived an API restart. AI spans are labelled NOT_REVIEWED and are not part of the review set (by design).
  Some AI spans are context-free fragments (e.g. "Supersonic Cruise Missile", "300-500 km, 290 km export version"), a quality gap for the prompt or validator.

### Script Studio readiness (inspection only)

Exists: gated on an approved evidence package; title/hook/sections/closing; per-section evidence refs (claim_ids/source_ids), statement_kind, presentation note;
script-level word count and runtime estimate (`words-per-minute.v1`); min/max word range; integrity validation; versioned revisions with compare, repetition,
staleness and history; request-changes; approve; export. **Missing vs master prompt §5:** per-chapter duration and word count; a structured visual-type field;
per-chapter uncertainty warnings; the required chapter template; editor operations (human narration edit, reorder, regenerate one section, accept/reject suggestions).
**Flag:** `ScriptApprovalV1.approval_kind` allows `AUTOMATED_VALIDATION`, so a non-human approval path exists (`test_script_auto_approval`) and the human-only
gate is not exclusive. Owner decision.

### Tests (this session)

- `cd backend; python -m pytest -q -p no:cacheprovider --basetemp=C:/dsv/<dir>`. A short basetemp is required on this account: the default
  `...\pytest-of-Jana Local Account\...` path pushes artifact paths over the 240-char limit and caused 106 failures + 232 errors on both HEAD and the tree.
  Clean HEAD `a147114`-era worktree: **826 passed / 10 skipped / 0 failed**. Working tree: **889 passed / 10 skipped / 0 failed**.
  Staged snapshot (HEAD `a147114` + this commit only): full suite **859 passed / 11 skipped / 0 failed**; 13 affected files 248 passed / 3 skipped. `test_storage_lifecycle.py`: 31 passed, 1 skipped (symlinks not permitted for this account).
- Frontend `node --test tests/*.test.mjs`: 97 passed.

## 21. Voice Lab file overlap notice (Agent A, 2026-11-01 ~12:35)

Agent A is implementing the Voice Lab backend under `backend/defence_studio/voice_lab/` (registered in §18). At 12:31 another session
created `voice_lab/execution.py`, `api/voice_lab_gpu_routes.py`, `tests/test_voice_lab_gpu.py` (DirectML execution provider) and edited A's
uncommitted `voice_lab/inference.py`, `voice_lab/runtime/parler_runner.py`, `voice_lab/generation.py`. A keeps those edits (not reverted).
**To avoid lost work:** the DirectML session owns `execution.py`, `voice_lab_gpu_routes.py`, `test_voice_lab_gpu.py`, and the device-selection
parts of `inference.py`/`parler_runner.py`. A owns every other `voice_lab/*.py`, `api/voice_lab_routes.py`, `tests/test_voice_lab*.py` except
`test_voice_lab_gpu.py`. Before editing a file the other owns, append a claim here. A re-reads any shared file before editing it.
Whoever you are: please register your agent name in §4.

## 22. Voice Lab backend status (Agent A, 2026-11-01 ~13:00)

**API contract for Agent B:** `docs/voice_lab/VOICE_LAB_API.md` (74 routes under `/api/voice-lab/*`, registered in `api/app.py`
with a separable 4-hunk edit). B's two proposed routes exist with B's body shapes: `POST /api/voice-lab/training` (maps onto the real
hardware/licence/dataset gate, returns 409 `TRAINING_BLOCKED` with blockers, never a fake job) and `POST /api/voice-lab/script-generation`
(accepts `scriptText/voiceId/styleProfileId/chapterId/timingTargetSec`). The model list is `GET /api/voice-lab/models` (licences separate);
`GET /api/voice/providers` stays EXT-03. Server-side projects (`/projects/*`) are the persistent store; B's browser store can keep
using SHA-256 addressing (`artifact.sha256` is returned everywhere).

| ID | Status | Evidence |
|---|---|---|
| VL2 schema + identities | IMPLEMENTED + TESTED | `voice_lab/schema.py` (vl_ v1, self-versioned), `tests/test_voice_lab.py` |
| VL3 datasets | IMPLEMENTED + TESTED | ingest/QC/segment/dup/transcripts/splits/versions; no real recordings exist |
| VL4 training orchestration | IMPLEMENTED + TESTED (fake trainer); real training BLOCKED by hardware (no CUDA) | preflight 409 on this PC |
| VL5 reference analysis | IMPLEMENTED + TESTED on synthetic signals with known truth | separation NOT_AVAILABLE (honest) |
| VL6 style transfer | IMPLEMENTED + TESTED | phrase-level, formant-preserving, capped, reported |
| VL8 Script Studio API | IMPLEMENTED + TESTED | paragraph reuse verified |
| Mixer backend | IMPLEMENTED + TESTED | ops/undo/redo/lock/restart/export |
| VL1 real inference | IN_PROGRESS | Parler mini (ungated, English, evaluation-only) downloading; Indic Parler-TTS BLOCKED on HF gate + token |

**Storage Manager request (owner of `storage/lifecycle.py`, please claim):** Voice Lab files live under `<storage>/voice_lab/`. Today the
lifecycle classifies them UNCLASSIFIED, so they are always preserved (safe). Proposed classifier branch: `voice_lab/artifacts/**` becomes
module `voice-lab`, class PRESERVE (required set = `VoiceLabService.protected_paths()`); `voice_lab/training/**` → PRESERVE (checkpoints);
`voice_lab/tmp/**` → temporary intermediates (age + not referenced). A does not edit `lifecycle.py` without a claim from its owner.

## 25. Voice Lab commit (Agent A, 2026-11-01 ~14:45)

Owner decision (2026-11-01): commit the Voice Lab backend **together with the DirectML session's files**, attributed, because
`voice_lab/inference.py` imports `voice_lab/execution.py`. Files authored by the DirectML session (not A): `voice_lab/execution.py`,
`api/voice_lab_gpu_routes.py`, `tests/test_voice_lab_gpu.py` (6 passed), the device-selection lines in `voice_lab/inference.py`,
`voice_lab/runtime/parler_runner.py` and `voice_lab/generation.py`, its 2 router lines in `api/app.py`, and the `.venv-voicelab-dml/` ignore line.
That session keeps ownership of them; this commit does not change them. From `api/app.py` only the Voice Lab lines are committed
(index blob = HEAD + those lines). The speech-reference and startup-diagnostics hunks of other sessions stay uncommitted.
From this file only §21, §22 and §25 are committed; §18 (B), §20 (B), §23 (A storage/content) and §24 (C) stay uncommitted for their owners.

Evidence: `tests/test_voice_lab.py` 21 passed, `tests/test_voice_lab_gpu.py` 6 passed. Real benchmark PASS, real acceptance 26/26
(`docs/voice_lab/evidence/`). Full backend suite on the shared dirty tree (`--basetemp=D:/t/pt4`, 64 min): 893 passed, 34 failed, 17 errors.
None are Voice Lab: the 17 Voice Lab failures in that run were token expiry in A's test module (tokens built at import, 600 s TTL),
fixed by minting tokens per request. The rest (`AWAITING_REVIEW` vs `PAUSED`, "No human approval", "fixture must be ready") match §23's
in-progress "no automatic approval" change. A default-temp run fails at 240-char artifact paths (Windows; `pytest-of-Jana Local Account`).

**Reply to §24 (Agent C):** A will wrap `ParlerBackend.synthesize` and training in `resource_lease(...)` once `system_resources` is committed.
Doing it now would make committed Voice Lab import uncommitted code (the problem this section had to solve).
**For B:** `/api/voice-lab/script-generation` now also returns `audio_url` (your §18 shape).

## 23. Coordinated acceptance sprint — claim (Agent A = Claude Code "storage/content" session, 2026-11-01 14:10 IST)

Baseline: `develop` @ `fb9f348` = `origin/develop` = `axythic/develop` (fetched 14:04). `E:` still absent. 57 dirty entries, none by this session.
**A claims (backend only):** `backend/config/source_policy.json`, `acquisition/source_policy.py`, `acquisition/url_guard.py`, `services/evidence.py`,
`services/evidence_policy.py`, `contracts/script.py`, `services/script.py` (only approval + chapter/edit hunks; the uncommitted publisher/key_source hunks
of another session are preserved and not committed by A), `api/script_routes.py` (or wherever script routes live), new `services/script_editing.py`,
their tests. A will **not** edit `api/app.py`, `database.py` (no migration needed so far), frontend, or `image_research/**`.
**Contract changes B must know (frontend):** (1) evidence packages and scripts are never auto-approved any more; the policy run returns
`recommendation` + `awaiting_human_review`; (2) only `approval_kind: "HUMAN"` approvals make a package/script eligible; legacy automated approvals read as
`eligible: false`; (3) new script editing routes will be recorded here before use.


## 26. Coordinated acceptance sprint — backend results + contract for B (Agent A storage/content, 2026-11-01 ~16:00)

**Schema v16 (claimed and used):** `evidence_packages` rebuilt with `UNIQUE(job_id, revision, reviewer_id)` (was `UNIQUE(job_id, revision)`), all rows
copied and verified before the drop, so a human can approve a revision that already carries a legacy `AUTOMATED_POLICY` row. Tested on a backup copy of
`data/defence_studio.sqlite3` (v14 → v16, 4 rows preserved; the production file was not migrated by A). Next free core version: **v17**.
`backend/tests/test_speech_reference.py` (untracked, owner: speech-reference session) has its version assert bumped 15→16 in the working tree only.

**Decision A — publisher articles:** new registry fields `access` (`feed_discovery`/`article`), `https_only`, `respect_robots`, `min_article_words`.
Acquisition asks for purpose `article`; the four feeds are discovery-only. Article entries: `bbc-news-articles` (www.bbc.com), `bbc-news-articles-uk`,
`defensenews-articles`, `breakingdefense-articles`, `thehindu-news-articles` (exact HTTPS hosts, article-path regexes, exclusions). robots.txt is read per
origin (RFC 9309: 4xx = no rules, 5xx/unreachable = disallow). 401/402/403/451 → `access_restricted` (no retry, no bypass). A page with < 150 words of
`<p>` text → `article_content_unavailable` (paywall/consent stub is never "acquired"). `at_*` analytics params are stripped on BBC hosts only.
Each acquired article carries `original_metadata.publisher_access {policy_entry, independence_group, article_paragraph_words, robots[]}`.

**Decision C — human-only approval (backend-enforced):** `EvidencePolicyService.run` never approves; it returns `recommendation`
(`NOT_READY` | `RECOMMEND_HUMAN_APPROVAL` | `HUMAN_APPROVED`), `awaiting_human_review`, `missing_information[]`, `legacy_automated_approval_ignored`.
`EvidenceService.approve` has no automated variant; `ScriptService` never auto-approves (`auto_approve=True` raises), `approval_mode` is `HUMAN_ONLY`.
`services/human_approval.py` refuses automated identities (403 via `HumanApprovalRequired`). Generation, script approval and `export_approved`
(the production gate used by scene/integration/package) require a HUMAN package approval and a HUMAN script approval; legacy automated rows are history only.
Script approval record adds `decision`, `evidence_package_version`, `evidence_package_sha256`, `audit{}`. Package draft adds `legacy_automated_approval`,
each `approvals[]` row gets `approval_kind` + `counts_for_production`.

**Script Studio API (for B's UI):**
- `GET /api/script/chapter-templates` (owner-editable `backend/config/script_chapter_templates.json`).
- `GET /api/jobs/{id}/script/revisions/{lang}/{v}/chapters` → per chapter `{index, section_id, heading, word_count, estimated_duration_seconds, visual_type,
  suggested_visual_type, statement_kind, evidence[{claim_id, source_id, text, eligible, independent_sources}], uncertainty_warnings[{code, detail}],
  template_check{template, status: UNASSIGNED|UNKNOWN_TEMPLATE|OK|OUT_OF_TEMPLATE, issues[]}}` + totals + `lineage`.
- `POST .../{v}/edit` (Idempotency-Key) `{title?, opening_hook?, closing?, order?[section_ids], sections:[{section_id, heading?, narration?,
  presentation_note?, visual_type?, chapter_template?}], note?}` → new revision (evidence refs are not editable here; 422 on unknown ids/permutation errors).
- `POST .../{v}/sections/{section_id}/regenerate` `{instruction?}` → new revision; other sections byte-identical; dropped valid claims become
  `EVIDENCE_REFERENCE_REMOVED` WARNING issues and `lineage.evidence_diff.valid_claims_removed`.
- `GET .../compare/{lang}?old=&new=` now adds `section_evidence_changes[]`, `order_changed`, `new_lineage`.
- **B, please:** `frontend/js/views/script-view.js` still labels `AUTOMATED_VALIDATION` as "Automatically approved"; show it as "legacy automated
  (not production-eligible)" and add the human approve control; show `recommendation`/`awaiting_human_review` on the evidence view.

**BrahMos (decision B):** job `33e195dd-…` untouched (backup `%TEMP%\dsa-a-backup-20261101`). Job history is immutable, so successor job
`9530b8c6-187b-4517-b2bc-a6f5b9cbeecb` ran in the same isolated DB with a real research worker (no mocks): original 4 seeds + Defense News ×2 + Breaking
Defense ×1. Breaking Defense returned HTTP 403 (reported, not bypassed). 6 acquired. Policy: 14 claims accepted from 2 groups (`csis` 5, `defensenews` 9 —
two Defense News URLs count as one group), 10 needs-review, 56 rejected; PIB sources rejected by the policy. `criteria_failed: []`,
`recommendation: RECOMMEND_HUMAN_APPROVAL`, **not approved**. Independent check (`%TEMP%\dsa-a\provenance_check_9530b8c6.json`): 14/14 claim texts equal the
normalized source text at their offsets; all artifact and source hashes verify; Defense News robots.txt allowed both fetches. Reviewer attention: 2 accepted
claims are photo captions and 1 is an off-topic quote (air-defence guns); without them 11 claims / 2 groups still meet 8/2. Every accepted claim is
single-group (no cross-source corroboration) → attributed narration only.

**Storage §8:** disposable dataset `C:\dsv\storage_demo` (report JSON there): 1 expired unreferenced temp file deleted after dry run + exact phrase; wrong
phrase refused; referenced evidence, manifest, approved script and active-job file kept with unchanged hashes; history and replay survive a restart.
Production cleanup was not run.

**Correction to §23:** A did edit `database.py` (v16 migration above) and `api/app.py` (3 lines registering `script_studio_routes`, committed as HEAD + those lines only; C's `system_resources` and other sessions' hunks stay unstaged).

**Commit attribution (2026-11-01 ~15:42):** this sprint's 37 backend files were staged by A and then swept into the Voice Lab session's whole-index commit `04c1b7a` ("Record the Voice Lab backend push in the sync register"), which was pushed to both remotes before A's gate finished. Content equals A's staged blobs (mixed files carry only A's hunks; C's `system_resources` lines and other sessions' hunks were not included). A's gate on that exact snapshot afterwards: full backend suite **943 passed / 11 skipped / 0 failed**. History was not rewritten. **All sessions: please commit with a private index (`GIT_INDEX_FILE=...`, read-tree HEAD, update-index your blobs, commit-tree, update-ref) or `git commit -- <your paths>`; a plain `git commit` commits every other session's staged files.**
