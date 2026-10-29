# Defence Studio / Defence Era — Project Master Document

Single entry point and documentation map. Audited 2026-10-15 from code, migrations and test runs on the uncommitted working tree.
Detail lives in the linked documents; this file does not repeat it. Rule precedence: `CLAUDE.md` → `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` (plan, §1/§1A work rules) → `project_status.md` (state).

## 1. Purpose

Solo-creator workflow for source-backed defence / military / geopolitics videos (Tamil first, language selectable):
`topic → research → source register → claims & evidence → fact check → human evidence review → script → script approval → scene plan → assets/rights → voice → render + subtitles → QC → package → (private) YouTube upload`.
Hard rules: human approval at factual/editorial gates, no mock production data, real progress only, rights tracked per asset.

## 2. Architecture (summary)

| Part | Location | Notes |
|---|---|---|
| Core API + worker | `backend/defence_studio/` (FastAPI, SQLite, schema **v13**, migrations in `database.py`) | `api/app.py` + `*_routes.py`; services, repositories, contracts; `research_worker.py` runs queue handlers |
| Core UI | `frontend/` (static ES modules, no build step) | views incl. discovery, sources, evidence, script, fact check, voice |
| Job queue | `workflow_jobs` + `services/worker.py` | leases, retries, restart recovery; research, fact-check and voice jobs share it |
| Fact check / media verification | `backend/defence_studio/verification/` | deterministic engines, stage `factcheck_run`; see its README |
| Voice analyzer + generation | `components/voice-engine/` (+ `docs/voice_engine/INTEGRATION.md`) | integrated via schema v12, `/api/voice/*` |
| Standalone modules (HTTP only) | `standalone/audio-module` :8001, `standalone/video-visual-module` :8765, `standalone/video-renderer` :8780 | core adapters in `backend/defence_studio/integrations/` |
| Asset Rights Engine, Alignment | `components/asset-rights-engine`, `components/defence-align` | standalone; **not imported by core** — see `docs/asset_rights_integration/` |
| Registry | `component_registry/REGISTRY.md` | one canonical copy per component |

Contracts and boundaries: `backend/CONTRACTS.md`, `backend/WORKER_CONTRACT.md`, `backend/SOURCE_PROVENANCE.md`. Setup/run/test/config/troubleshooting: `OPERATIONS.md`; first-time setup and the two-PC database workflow: `README.md`. These two files serve as the development guide; no separate architecture/development files are kept to avoid duplication.

## 3. Verified status (this audit)

Tests run 2026-10-15 (`DEFENCE_STUDIO_AUTH_SECRET` set, short `--basetemp` — a long basetemp fails on the Windows 240-char storage-path guard):
core backend **500 passed / 8 skipped** · core frontend **42 passed** · voice engine **59 passed**. Audio / Visual / Renderer suites were not re-run (last recorded 31 / 206+2 / 117+1).

| Area | Status | Basis |
|---|---|---|
| DB schema + migrations v1–v13, job queue, worker recovery | COMPLETE_VERIFIED | full suite incl. spawned-worker crash test; migration tests |
| Research / discovery / acquisition / analysis / review | PARTIALLY_VERIFIED | tests pass; RSS live on 4 feeds (recorded); no hosted search provider; live HTTP/YouTube tests skipped |
| Fact Check Engine | PARTIALLY_VERIFIED | deterministic verdicts + gates tested; no live web search |
| Media Context Verification | PARTIALLY_VERIFIED | local hashing/EXIF tested; reverse-image search, OCR, object detection unavailable (reported, never faked) |
| Script generation + approval gates | PARTIALLY_VERIFIED | gates/validation tested; live OpenAI generation never run (no key); Tamil quality unreviewed |
| Scene plan / asset service | PARTIALLY_VERIFIED | contract + service tested |
| Voice Analyzer / Generation | PARTIALLY_VERIFIED | tests pass, English SAPI only; **no Tamil voice**; licensed narrator not provisioned |
| Audio / Visual / Renderer integration (English) | PARTIALLY_VERIFIED | integration tests + recorded e2e `analysis/e2e-acceptance-20261014.json`; Tamil narration BLOCKED |
| QC, branding/thumbnail/package | PARTIALLY_VERIFIED | tested with real FFmpeg; not published anywhere |
| Asset Rights Engine integration | IMPLEMENTED_UNVERIFIED | engine tests pass standalone; no code path in core; integration spec frozen, readiness defect F5D-01 recorded |
| YouTube upload | IMPLEMENTED_UNVERIFIED | mocked Google only; BLOCKED on OAuth credentials + channel |
| Hosted search provider, per-owner paid APIs live | BLOCKED | credentials not configured |
| Browser pass for newest views (fact check, voice) | PARTIAL | 24/24 + 24/24 scripted headless-Chrome checks 2026-10-15 (`frontend/tests/browser/`); outstanding: OCR, hosted-search and real reverse-image provider paths; Tamil TTS; forced alignment; Voice cancel/retry/discard in the browser; Fact Check human correction; video upload; real-device/touch; a second browser; production-like data volume |

## 4. Constraints and risks still in force

- Single-writer rule; no commit/push without authorization (`CLAUDE.md`). The tree carries uncommitted fact-check/voice work and a concurrent session was editing status files during this audit.
- Dev auth bypass (`DEFENCE_STUDIO_DEV_MODE` + `DEV_AUTH_BYPASS`) is loopback-only and must never be enabled outside a dev box.
- `data/defence_studio.sqlite3` is tracked on purpose; real owner data would be committed with it. Two-PC rule: snapshot, never text-merge.
- FFmpeg build in use is GPLv3 (user-supplied, called as subprocess); legal and font-redistribution review outstanding. OS (SAPI) voice commercial/YouTube terms unverified.
- Windows paths: keep storage roots short (guard fails writes > 240 chars). A root longer than 118 characters fails `POST /api/jobs` up front with `STORAGE_PATH_TOO_LONG` (a preflight added 2026-10-29), so no orphan DRAFT job is left. Test servers must not use a session scratchpad as `DEFENCE_STUDIO_STORAGE_ROOT`; use a short path such as `C:\t\...`.
- API error contract: every `/api` failure answers in JSON with `detail`, and server faults also carry `error_code` and `error_id`. Unexpected exceptions return a generic message and are logged with the same `error_id`, never with internals in the response. The UI reports an unreadable (non-JSON) reply with its HTTP status, and reports network failures separately.
- Tamil is the primary language goal but Tamil narration is unproven end to end (subtitle shaping verified visually).
- "Mocked" and "live-verified" are not interchangeable; trackers must keep the distinction.

## 5. Design intent carried from the 2026-10-10 discovery (not yet implemented, unresolved)

Preserved from the retired `analysis/RECOMMENDED_BUILD_ORDER.md`; not in the core design:
- Paired Tamil and English scripts generated from the same approved claims and **shared scene IDs**, each with independent text, voice, durations, captions and exports; synthesis receives approved text, never a translation request; a glossary protects canonical English terms/acronyms/numbers; semantic parity reviewed by a person.
- Per-language outputs kept separate (script / audio / timeline / MP4 / SRT / thumbnail / provenance) under a common approved research revision and scene plan.
- Analytics/publishing-history sync joining channel/video IDs to project/language/render revision (after YouTube is live-verified).

## 6. Documentation map

| Purpose | File |
|---|---|
| Agent rules, token-saver | `CLAUDE.md` |
| Plan and phase contracts | `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` |
| Current state | `project_status.md` (historical evidence log: `docs/history/project_status_history.md`) |
| Per-phase status table, pending work, roadmap/ledger | `DEFENCE_ERA_PHASE_STATUS.md`, `DEFENCE_ERA_PENDING_WORK.md`, `DEFENCE_ERA_IMPLEMENTATION_ROADMAP.md`, `DEFENCE_ERA_PHASE_LEDGER.json` |
| Setup / run / test / config | `README.md`, `OPERATIONS.md`, `.env.template` |
| Contracts | `backend/CONTRACTS.md`, `backend/WORKER_CONTRACT.md`, `backend/SOURCE_PROVENANCE.md` |
| Module specs | `DEFENCE_STUDIO_AUDIO_MODULE_FULL_BUILD_SPEC.md`, `..._VIDEO_VISUAL_MODULE_FULL_BUILD_SPEC.md`, `..._YOUTUBE_INTEGRATION_SPECIFICATION.md`, `DEFENCE_ERA_PHASE1_CONTENT_SEARCH_SPEC.md` |
| Integration docs | `docs/asset_rights_integration/`, `docs/voice_engine/INTEGRATION.md`, `backend/defence_studio/verification/README.md` |
| Phase 0 baseline (historical, accepted) | `REPOSITORY_AUDIT.md`, `project.md` (referenced by `ndf.config.toml`) |
| QVault/Whisper reuse catalogue (historical) | `analysis/COMPONENT_INVENTORY.md`, `COMPONENT_REUSE_MATRIX.md`, `INTEGRATION_GAPS.md`, `PROJECT_DISCOVERY.md` |
