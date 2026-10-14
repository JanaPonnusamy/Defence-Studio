# Defence Era — Pending Work (audit 2026-10-14)

Priority: **P0** blocks the next phase · **P1** needed for a usable pipeline · **P2** quality/hardening · **P3** optional. IDs are referenced by `DEFENCE_ERA_IMPLEMENTATION_ROADMAP.md`.

## A. Critical defects

No failing test was found. The items below are defects of record or integrity risks.

| ID | Item | Reason | Dependency | Pri | Completion condition |
|---|---|---|---|---|---|
| A1 | `standalone/audio-module` and `standalone/video-renderer` are git-ignored (`.gitignore` `/standalone/*`) | No history or rollback for two modules that hold all voice and render logic | User authorization to commit (never done automatically) | P0 | Both directories tracked via explicit un-ignore; `git ls-files` lists them; runtime `storage/` and `.venv` still ignored |
| A2 | `project_status.md` contradicts reality (F2 "review required", "Phases 5–10 NOT_STARTED", stale test counts, standalone modules unmentioned) | Agents following it will repeat or skip work | None | P0 | Tracker rewritten from this audit; one row per phase matches `DEFENCE_ERA_PHASE_STATUS.md` |
| A3 | Multiple agent sessions edit one tree concurrently (observed in F4, F5 and again this session: the renderer appeared while another session was working) | Overwrites and unreviewed phase completion | Process decision | P1 | Single-writer rule recorded in `CLAUDE.md`; each phase carries an owner and a gate review |
| A4 | Windows `MAX_PATH` failure for deep storage roots (E2E finding) | Artifact path 261 chars fails writes | None | P2 | Test with a 250-char root passes, or an explicit path-length guard returns a clear error |

## B. Missing core features

| ID | Item | Reason | Dependency | Pri | Completion condition |
|---|---|---|---|---|---|
| B1 (PARTIAL: RSS live-verified, hosted search API needs user credential) | Real topic search provider (Phase 1B) | Only RSS keyword match works; search API never run | Provider and credential choice by user | P0 | One real provider returns results for 3 real topics; failures reported; covered by recorded-fixture and opt-in live tests |
| B2 (DONE 2026-10-14, RM-1; tested, UI not browser-verified) | Defence category taxonomy and filters (Indian defence, international, Russia–Ukraine; country/region; source class) | Design §8 Phase 1A/1B | B1 | P0 | Category and source-class stored on candidates; listing filters by them; tests |
| B3 (DONE 2026-10-14, RM-1; history derived from persisted jobs, no new table) | Search history persistence and pagination | Rule 6; users must reopen searches | B1 | P1 | Searches listed and reopenable after restart; pages deterministic |
| B4 | Integrated voice stage (Phase 6) | Audio Module is standalone | P3 approved script, B-contract | P1 | Backend stage calls Audio Module via HTTP adapter; audio artifact hash-linked to scene plan |
| B5 | Integrated render stage (Phase 7) | Renderer is standalone; scene plan → package builder missing | B4, Visual Module integration | P1 | Approved scene plan + audio + assets produce a `video-render-package`; job renders and records `qc_report.json` |
| B6 | Subtitles and Tamil text rendering | Required by design; renderer Latin-only | Tamil font + shaping decision (Pillow/HarfBuzz or ffmpeg libass) | P1 | Tamil caption frame rendered and visually compared; subtitle timing from alignment |
| B7 | Phase 8 QC and final approval | Not started | B5 | P1 | Checks in design Phase 8 implemented; failed QC blocks completion |
| B8 | Branding, thumbnail, metadata package (Phase 9) | Not started | B5, B7 | P2 | Opening/end cards versioned; thumbnail and metadata generated |
| B9 | YouTube publishing YT-01..YT-08 | Spec only | B7, Google OAuth credentials | P2 | Private upload verified by video ID |
| B10 | Previous-video history / duplicate prevention | Design §10 | P3 | P2 | Prior topic flagged on new job |
| B11 | Per-user credential store and settings UI | Design §7; env vars only today | Auth | P2 | Encrypted at rest; never returned by API |
| B12 | Scene, asset, audio, render, QC UI views | Frontend ends at script | Matching backend stages | P1 | Each view works on populated data; mobile widths pass |

## C. Incomplete integrations

| ID | Item | Reason | Dependency | Pri | Completion condition |
|---|---|---|---|---|---|
| C1 | Core ↔ Video Visual Module (timeline v1.1) | Two scene/visual planners exist | Scene plan to VVM input mapping | P1 | One shared contract; scene plan drives VVM; timeline stored as artifact |
| C2 | Core ↔ Audio Module | Standalone only | B4 | P1 | See B4 |
| C3 | Core ↔ Video Renderer | Standalone only | B5 | P1 | See B5 |
| C4 | QVault / Whisper reuse (Knowledge Explorer, word alignment) | Design says reuse; QVault must stay isolated | Interface-only contract | P2 | Documented interface; QVault code unchanged |
| C5 | AI-assisted extraction/analysis seam | Present but unwired | OpenAI credential | P2 | Behind flag; deterministic output unchanged when off |
| C6 | FFmpeg location convention across modules | Audio, VVM, renderer each resolve FFmpeg differently (imageio-ffmpeg 7.1 vs system 8.1) | None | P3 | One documented lookup order |

## D. Unverified behaviour

| ID | Item | Reason | Dependency | Pri | Completion condition |
|---|---|---|---|---|---|
| D1 | Live OpenAI script generation, token/cost records, ₹10 target | Never run: no key, no spend authorized | Credential + budget approval | P1 | One real run recorded with model, tokens, cost, retries |
| D2 | Tamil script quality and faithfulness | Only structural validation exists | D1, Tamil-capable reviewer | P1 | Reviewer sign-off on 3 scripts; defects logged |
| D3 | Live YouTube caption acquisition | `DEFENCE_STUDIO_LIVE_YOUTUBE` skipped; needs `yt-dlp` | Network, yt-dlp | P2 | Opt-in test passes against one public video |
| D4 | Live HTTP acquisition test | `DEFENCE_STUDIO_LIVE_HTTP` skipped | Network | P2 | Opt-in test passes |
| D5 | Browser acceptance: UI-06 and populated pipeline | Claimed, not re-run; views lack automated tests | Phase 1B data | P1 | Sweep re-run at 320–1440 px with real data; screenshots stored |
| D6 | Tamil voice availability and quality in local TTS | Not verified in this audit | Windows voice inventory | P1 | Audio Module lists Tamil voice or documents absence; sample WAV analysed |
| D7 | Real AI image generation | Opt-in paid tests skipped; never made a real request | Credential + budget approval | P2 | One recorded real generation with cost |
| D8 | Renderer behaviour on other CPUs, sizes, concurrent jobs | R1 measured one machine, one worker | None | P3 | Documented benchmark matrix |
| D9 | Symlink-escape test in renderer | Skipped (no symlink privilege) | Privileged shell | P3 | Test passes elevated |
| D10 | GPLv3/patent position for FFmpeg/libx264 use | Technical analysis only, not legal advice | Qualified reviewer | P2 | Written conclusion before any distribution |

## E. Technical debt and optional enhancements

| ID | Item | Pri | Completion condition |
|---|---|---|---|
| E1 | Stale pytest atexit `PermissionError` hides summary on this account | P3 | Documented `--basetemp` in test commands or fixed temp dir |
| E2 | Frontend has no build, lint or view-level tests | P2 | Tests for discovery/sources/claims/evidence/script views |
| E3 | Tracked dev DB will accumulate real research data | P2 | Policy: dev DB resets or moves out of git before real runs |
| E4 | Per-module duplicated job queues (backend, Audio, VVM, renderer) | P3 | Decision recorded whether to unify |
| E5 | `component_registry/REGISTRY.md` lacks standalone modules and UI primitives | P3 | Registry rows for each reusable component |
| E6 | Dev-auth bypass with fixed owner | P3 | Startup warning and docs; disabled in non-dev config |
