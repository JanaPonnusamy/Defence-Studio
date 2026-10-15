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

## Update 2026-10-14 (post RM-0..RM-10)

Closed: A1, A2, A3, A4, B2, B3, B4*, B5, B6, B7, B8, B10, B11, B12 (Production tab), C1, C2, C3, E5(partial). *B4: English only. Open/blocked: B1 hosted vendor, B9 live YouTube, D1/D2 live generation + Tamil quality, D3/D4 live acquisition opt-ins (D4 exercised implicitly by the e2e run), D6 Tamil voice, D7 AI images (deliberately disabled), D10 licensing review, C4 QVault interface doc, C5 AI extraction seam, E2 frontend per-view coverage (production/discovery/script have node tests).

## F. Fact check / media verification and voice (added 2026-10-15)

| ID | Item | State | Prerequisite (no secrets) | Pri | Completion condition |
|---|---|---|---|---|---|
| F1 | Hosted web search | NOT_CONFIGURED | `DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL` and `DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY` both set (vendor chosen by user) | P2 | One bounded authorized live query recorded; RSS remains the working path |
| F2 | Reverse-image search | NOT_CONFIGURED (adapter mock-tested) | https `DEFENCE_STUDIO_REVERSE_IMAGE_URL` speaking the contract in `verification/adapters.py`; optional `..._KEY`; `..._SEND_IMAGE=1` only if image upload is approved | P2 | One real provider response recorded; NO_MATCH never read as original |
| F3 | OCR | DEPENDENCY_MISSING | Tesseract binary on PATH (+ language data for Tamil if needed) | P2 | A real image OCR'd; text stored as untrusted evidence |
| F4 | Object detection | UNSUPPORTED | Local model with verified weights/licence | P3 | Detections labelled as model output, never as confirmed findings |
| F5 | Asset Rights Engine adapter | NOT_STARTED | Contract decision per `docs/asset_rights_integration/COMPATIBILITY.md` | P2 | Clearance only from an actual authorized ARE/AssetService result |
| F6 | Fact Check browser acceptance | PARTIAL — 24/24 scripted headless-Chrome checks passed 2026-10-15 (render at 3 widths, readiness chips match API, claim verify, package build, blank-note rejection, approval, persistence across navigation, media check, no external requests); 2 defects fixed (duplicate actions, detail pane wiped on error). Not tested: human correction, video upload, OCR/hosted-search/real reverse-image paths, real device/touch, second browser, production-like volume | Browser; providers for blocked paths | P1 | Remaining checks recorded, then F6 closed |
| F7 | Re-run opt-in live RSS test | SKIPPED 2026-10-15 | Network; `DEFENCE_STUDIO_LIVE_FACTCHECK=1` | P3 | Test passes and is logged with date |
| F8 | Tamil TTS provider + verification (voice engine) | BLOCKED | Owned/licensed Tamil model; supersedes D6 for the voice engine | P1 | Real Tamil WAV evaluated by a Tamil reviewer |
| F9 | Forced alignment for generated audio | DEPENDENCY_MISSING | torch/torchaudio + a commercially licensed aligner model (MMS-FA is CC-BY-NC) | P2 | Model-derived word timings verified on Tamil |
| F10 | Reference voice source acquisition | NOT_STARTED | Authorized local file or explicit permission; no automatic download | P2 | Analysis run on the authorized file |
| F11 | Voice tab browser acceptance | PARTIAL — 24/24 scripted headless-Chrome checks passed 2026-10-15 (render at 3 widths, models/readiness honesty, Tamil not offered, real local SAPI English generation with playable audio and estimated-alignment labels, analyzer upload + profile, history after refresh, open result, no external requests); 2 defects fixed (duplicate form submission, favicon). Not tested: cancel/retry/discard in the browser, Tamil TTS, forced alignment, real device/touch, second browser, production-like volume | Browser; Tamil provider; aligner | P2 | Remaining checks recorded, then F11 closed |

Test prerequisite: backend tests need `DEFENCE_STUDIO_AUTH_SECRET` (>= 32 bytes) in the environment; E1's pytest atexit PermissionError still appears on this account and does not affect results.
