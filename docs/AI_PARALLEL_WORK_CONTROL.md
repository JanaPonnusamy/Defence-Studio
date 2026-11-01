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
