# Defence Era / Defence Studio — Current State Audit

**Audit date:** 2026-10-14. **Method:** document review, targeted source inspection, and test suites re-run on this machine (Windows 10, Python 3.13.5, Node 24.14.1, FFmpeg 8.1). No production code was modified. No credentials were read or are reproduced here.

## 0. How this audit was scoped

- `Project_Master_Document.md` **does not exist** in the repository. The governing documents are `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` (plan, phases, acceptance) and `project_status.md` (state), per `CLAUDE.md`. These were treated as the master documents. The official phase numbers are the core design's Phase 0–10; the F1–F5 foundation items from `project_status.md` are mapped to them, not renumbered.
- The R1 "Voice and Video standalone systems status audit dated 2026-10-13" was not found on disk. Its content was re-derived from the standalone modules' own docs and test runs.
- Prior completion claims were treated as hypotheses. Each is marked below as **re-verified** (I ran it) or **claimed** (documented, not re-run).

## 1. Architecture and actual repository structure

| Area | Path | Reality |
|---|---|---|
| Core backend | `backend/defence_studio/` (~11k LOC, 20 test files) | FastAPI app (`api/app.py`), SQLite schema v7, durable worker, contracts, services. Standalone: does not import QVault/Whisper. |
| Core frontend | `frontend/` (static HTML/JS, no build step) | Views: login, dashboard, discovery, sources, claims, evidence, script. **No** scene, asset, audio, render, QC or publish views. |
| Database | `data/defence_studio.sqlite3` (303 KB, tracked in git on purpose) | 18 tables; 1 job, 13 stages, 5 artifacts; all scene/asset/script/evidence-package tables are empty. |
| Standalone: Audio Module | `standalone/audio-module/` | Own FastAPI/SQLite/UI. Alignment, analysis, provider-neutral TTS. Not wired to the backend. |
| Standalone: Video Visual Module | `standalone/video-visual-module/` (v1.1.0, tracked in git) | Own FastAPI/SQLite/UI. Produces the renderer-neutral timeline v1.1. Not wired to the backend. |
| Standalone: Video Renderer | `standalone/video-renderer/` (git-ignored) | Own FastAPI/SQLite/worker. Renders package to MP4. See §3. Not wired to the backend. |
| Automation | `automation/` (NDF framework) | Project tooling, not part of the content pipeline. |
| Registry | `component_registry/` | README + REGISTRY.md only. |

**Key structural fact:** the core backend and the three standalone modules are four independent islands. No code path carries a job from research → script → scene → voice → render. Integration is documented intent only.

## 2. Backend API surface (verified by reading `api/app.py`)

Implemented route groups under `/api/jobs/{job_id}`: jobs (create/list/get/queue/stage transitions/artifacts); `review/sources`, `review/claims` (+decisions, history); `discovery/requests|candidates|selection`; `timeline`; `package` (+readiness, approve, export); `script/revisions|generate|request-changes|approve|export`; `scene/plans|generate|revise|request-changes|approve|scene-digests`; `assets` (+rights, review, history), `scenes/{id}/assets`, `assignments` (+readiness).

**Not present anywhere in the core backend:** voice/TTS/audio, alignment, render, QC, thumbnail, branding, metadata, YouTube/OAuth, scheduling, previous-video history/duplicate prevention, cost/budget reporting endpoint, user credential settings (core design §7).

## 3. Test and build results (executed 2026-10-14)

| Suite | Command | Result |
|---|---|---|
| Core backend | `python -m pytest -q` in `backend/` | **287 passed, 2 skipped, 0 failed** (49 s). Skips: `DEFENCE_STUDIO_LIVE_HTTP`, `DEFENCE_STUDIO_LIVE_YOUTUBE` opt-ins — the live HTTP and live YouTube checks did **not** run. |
| Core frontend | `node --test tests/*.test.mjs` | **7 passed, 0 failed**. Covers `api.js` and the dashboard only; discovery, sources, claims, evidence and script views have no automated tests. |
| Audio Module | `.venv` pytest | **31 passed, 0 failed** (8 s). |
| Video Visual Module | `.venv` pytest | **206 passed, 2 skipped** (32 s). Skips: paid-provider tests (opt-in, need `OPENAI_API_KEY`) — no real AI image has ever been generated. |
| Video Renderer | `python -m pytest -q` | **117 passed, 1 skipped** (48 s). Skip: symlink privilege unavailable. |
| Renderer long output | independent `ffprobe` + `ffmpeg -v error -f null` on `storage/benchmarks/long/.../output.mp4` | h264 1920x1080 25 fps + aac, 600.000 s, **0 decode errors**. |

Invocation notes: the global interpreter lacks `librosa` and `alembic`, so the Audio and Visual modules must use their own `.venv`. pytest's atexit cleanup prints a harmless `PermissionError` on `pytest-current` in this account; it hides the summary line unless `--basetemp` or `--junitxml` is used.

Not run: the self-test PowerShell scripts, the UI browser sweeps (Chrome/Edge), live provider calls, QVault baseline (`analysis/phase0-baseline/`). Those remain **claimed**.

## 4. Pipeline stage check (Rule 5)

| # | Stage | Works independently? | Verified end to end? |
|---|---|---|---|
| 1 | Select/discover topic | Yes: RSS provider works when feeds are configured | Claimed live E2E 2026-10-11 (3 real feeds, 9 candidates); not re-run. |
| 2 | Search current news | **Partly:** RSS keyword match only. The `configured_search_api` provider has never run against a real service. | No |
| 3 | Preserve source evidence | Yes (artifact store, SSRF-guarded fetch) | Claimed live E2E; tests pass |
| 4 | Normalize/deduplicate | Yes, deterministic; AI-assisted extraction is a seam only | Claimed live E2E; tests pass |
| 5 | Validate claims, dates, conflicts | Partly: deterministic claim extraction and relationships; reliability scoring unverified | Tests pass |
| 6 | Structured research package | Yes (`EvidenceService`, schema v4) | Tests pass; no package exists in the dev DB |
| 7 | Fact-checked script | Backend contract and validation done; **live generation never run** (no key configured) | No |
| 8 | Tamil + English review | Structural only; no semantic/faithfulness check, no Tamil quality review | No |
| 9 | Voice audio | Audio Module (standalone): local pyttsx3 + mock TTS. No integration. | Standalone tests only |
| 10 | Scene and media plan | Scene plan (core) + Visual Module (standalone) both exist; not connected | Tests only |
| 11 | Render + subtitles | Renderer (standalone) renders genuine MP4; **subtitles, Tamil text and alignment are excluded** | Standalone only |
| 12 | Thumbnail/branding/metadata | **Not started** | — |
| 13 | YouTube upload/schedule | **Not started** (spec only) | — |

**No stage chain from 6 onward has been executed end to end. Nothing after stage 7 is connected to the core job.**

## 5. Security and configuration

- **Auth:** HS256 bearer tokens; the app refuses to run without `DEFENCE_STUDIO_AUTH_SECRET` (≥32 bytes). A dev bypass exists (`dev_auth.py`); it needs two env flags, loopback-only, no forwarded headers, single fixed owner. It is well guarded, but the default dev owner `e2e-operator` is fixed and the bypass must never be enabled outside a dev box.
- **Secrets:** no key-shaped strings found in the tree. `.env.template` and `.env.example` (VVM) are templates. No OpenAI/search/YouTube credentials are configured in this environment.
- **Tracked database:** `data/defence_studio.sqlite3` is deliberately committed (second-PC portability). It currently holds one job and no secrets, but any future real research or owner data would be committed with it.
- **Source control gap:** `.gitignore` line `/standalone/*` ignores `audio-module` and `video-renderer`. Only `video-visual-module` is tracked. Two of three production-bound modules have no version control.
- **Network safety:** SSRF guard (`acquisition/url_guard.py`) with tests; redirect/size/timeouts bounded.
- **YouTube OAuth:** not implemented; token storage design exists only in the spec.
- **FFmpeg licence:** the installed build is GPLv3 (libx264). The renderer calls it as a subprocess and does not bundle it; the Visual Module's `LICENSING_AUDIT.md` recommends users supply their own. Legal confirmation is outstanding.
- **Windows MAX_PATH:** artifact paths of 260+ characters fail with deep storage roots (found in E2E; not fixed).

## 6. Differences between documents and implementation

1. `project_status.md` header and tracker are stale (last body edit 2026-10-10/11): F2 still reads "REVIEW REQUIRED"; "Phases 5–10 NOT_STARTED" contradicts the existing Phase 5 M2.x rows and the three standalone modules; test counts quoted (108/134/165) are far below the 287 now passing.
2. The core design says the renderer should be "the existing Python/FFmpeg video builder through a stable adapter". The renderer built is a new standalone application; no such adapter exists. Same for TTS (Phase 6 says reuse QVault/Edge TTS; the Audio Module is a new build).
3. "Phase 1: IMPLEMENTED (1A–1D)" overstates: 1B (automatic topic search) is explicitly noted PARTIAL in the same tracker. Phase 1's acceptance (search, extract, analyse, **review, persist, reopen, export** a real topic) has not been shown in a browser.
4. Core design Phase 1A requires country/region, category and source-class filtering. The candidate listing API filters only by language, source type, provider, min score and text query; `date_from`/`date_to` exist on the request but not on the listing. There is no category taxonomy (Indian defence / international / Russia–Ukraine) anywhere in code.
5. The renderer's R1 build is documented as complete by its own self-test report; this audit **re-verified** its pytest and the long output's decode, but it was produced by a separate agent session, not by a reviewed phase gate.

## 7. Reusable capabilities

Durable worker with leases/recovery (`services/worker.py`), artifact store with hashes, SSRF-guarded fetcher, discovery provider registry, deterministic dedup/claims, revision-bound review ledgers, versioned contracts, scene plan, asset/rights ledger, standalone alignment/analysis, timeline contract, deterministic Pillow+FFmpeg renderer with QC and crash recovery.

## 8. Risks and blockers

| Risk | Effect |
|---|---|
| No search API credential/provider chosen | Topic search cannot be demonstrated beyond curated RSS feeds |
| No `DEFENCE_STUDIO_OPENAI_API_KEY` | Script generation unverified; ₹10/video cost cap unmeasured |
| Tamil TTS/subtitle/overlay unproven | Language goal at risk: renderer is Latin-only; local TTS voice support for Tamil unverified |
| Four disconnected islands | Largest schedule risk; each integration is a contract-mapping task |
| Untracked standalone modules | Loss/rollback risk |
| Multiple agent sessions editing the same tree | Observed again in this session (the renderer was written by another session while I was preparing to) |
| Manual browser acceptance outstanding | Phases 1–3 remain "awaiting manual verification" |
