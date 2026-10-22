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

## G. Content Search → final content pipeline reconciliation (audit 2026-10-22; documentation only — nothing here was run)

Requested workflow: topic → multi-source search (web/news, official releases, independent reporting, social, X trends, YouTube transcripts) → guarded acquisition → normalize/dedup/syndication → evidence-linked claims → automatic assessment → versioned research package (with images + rights) → OpenAI generation (900–1,300 words) → validation, cost record, persistence. Canonical documents: `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` (§8 Phases 1–3, §13), `DEFENCE_ERA_PHASE1_CONTENT_SEARCH_SPEC.md`, `backend/config/source_policy.json` + `backend/config/evidence_policy.json` (the validation policy). No separate `CONTENT_SEARCH_ENGINE_SPEC`, `CONTENT_ACQUISITION_ARCHITECTURE`, `CONTENT_VALIDATION_POLICY` or `DEVELOPMENT_RULES` file exists; development rules are core design §1/§1A/§13 and `CLAUDE.md`. "Implementation" below is from reading source and test names, not from a test run.

| Requirement | Authoritative section | Implementation (code read) | Tests (named, not re-run) | Gap / next action |
|---|---|---|---|---|
| 1–2 Topic entry; web/news/official/independent search | Design Phase 1A/1B; Phase-1 spec §5–7 | `discovery/providers.py`: `rss_feed`, `configured_search_api`, `manual_url`; taxonomy/source class (RM-1) | `test_discovery_f1a`, `test_discovery_manual_url` | Hosted search NOT_CONFIGURED, never live-called (F1). Already covered by spec. |
| 2 X trends / social posts | **Not in any spec** | **No provider exists** (no X/social/trends adapter anywhere in `backend/`) | none | **G1.** Do not claim support. |
| 2 YouTube subtitles/transcripts | Design Phase 1C | `acquisition/providers/youtube.py` (yt-dlp, subtitles only); registered by `default_registry` only when a backend is supplied; discovery does not search YouTube | `test_youtube_rm8` (mocked); live test skipped (D3) | Live path unverified (D3); YouTube discovery unspecified → G1. |
| 3 Guarded acquisition + metadata | Design 1C; F3 | `acquisition/*`, `url_guard.py`, `source_policy.py`, `source_dates.py` | `test_acquisition_*`, `test_url_guard`, `test_source_policy_*` | Live HTTP skipped (D4). Media/image metadata not collected → G3. |
| 4 Normalize; exact/near dedup; syndication | Design 1C; F4 | `analysis/normalize.py`, `dedup.py` (shingle Jaccard groups), `relationships.py` | `test_analysis_f4`, `test_claim_boilerplate` | Cross-publisher syndication is text-similarity only (F4 "unverified on real corpora") → G6. |
| 5 Evidence-linked claims; fact / attributed / analysis / uncertainty | Design 1D; RM-2 | `analysis/claims.py`, `ai_extraction.py` (AI seam), statement kinds | `test_ai_extraction`, `test_script_rm2` | AI extraction live path never run (C5/D1). |
| 6 Automatic assessment + approval | Design Phase 2 (human review kept for disputed/high-impact); the automatic policy is described **only in code/JSON** | `services/evidence_policy.py` + `evidence_policy.json`: score ≥70; strict classes (casualty/operational/range) need ≥2 independent groups; `NEEDS_REVIEW` excluded from usable set; conflicts block; readiness ≥8 claims and ≥2 sources; records `AUTOMATED_POLICY` approval; never overwrites a human decision | `test_evidence_policy`, `test_evidence_policy_scoring` | Implemented but not specified; owner authorization not documented → G2 (no code change proposed). |
| 7 Versioned research package | Design 1C/1D; `contracts/evidence.py` | `EvidencePackageV1`: topic, discovery, sources, claims, duplicate/claim groups, conflicts, timeline, limitations, blockers, approval | `test_evidence_package`, `test_research_pipeline` | **No images/assets/rights field; no explicit research-gaps list** → G3. |
| 8 Send approved package to OpenAI | Design Phase 3 | `services/script.py`, `script_prompt.py`, `providers/openai_text.py`; gated on approved package; key via credentials service | `test_script_phase3`, `test_script_auto_approval`, `test_openai_structured_output` | Not LIVE-VERIFIED (D1); a prior revision is `VALIDATION_FAILED` and stays unchanged. |
| 9 Original 900–1,300-word content with opening/sections/conclusion | Design Phase 3 ("about 1,300 words") | `ScriptConfig.target_words` (default 1300, range 100–5000) | `test_script_phase3` | **No 900–1,300 range** → G4. |
| 10 Word-count + schema validation | Phase 3 acceptance | Count computed (`narration_word_count`); `script_integrity.py` emits `TARGET_LENGTH_MISSED` only as a **WARNING** at ±25% of target | `test_script_integrity_corroboration`, `test_script_final_validation` | Length is not a hard gate → G4. |
| 10 Claims vs cited evidence | Phase 3 acceptance | `script_integrity.py` (`FACT_NOT_CORROBORATED`, source dedup, stale-date warnings) | `test_script_integrity_corroboration` | Covered. |
| 10 Model/token/cost record + budget | Design Phase 3 cost | pre-call `_budget_guard`, `max_text_cost_inr`, measured cost + pricing source; Inspector | `test_script_inspection`, `test_script_phase3` | Covered in code; ₹10 cap not promised without live measurement (D1). |
| 10 Persist / reopen | Phase 3 acceptance | Revisions persisted | `test_revisions_and_approval_persist_across_a_fresh_service_instance` | Covered by test; browser reopen pass owed (D5). |

| ID | Registered gap | Prerequisite | Pri | Acceptance criteria |
|---|---|---|---|---|
| G1 | X trends, public social posts and YouTube search discovery are unspecified and have no provider. Approach if wanted: a provider-neutral `DiscoveryProvider` adapter, enabled only when the owner supplies a permitted API/credential and accepts its terms and cost. Social posts are leads/attributed claims only — never `AUTO_ACCEPTED` evidence nor an independent source group unless owner policy says so. | Owner decision on provider, terms, cost ceiling (as spec D1/D3) | P2 | Descriptor reports NOT_CONFIGURED when absent; with a provider, one bounded live query recorded; `source_class=SOCIAL`; guarded fetcher and platform terms respected; no fabricated results. |
| G2 | Automatic evidence-approval policy is undocumented in the specs. Document the existing policy (thresholds, strict claim classes, what stays human: disputed/conflicting, high-impact, quantitative claims lacking 2 independent groups) in core design Phase 2 and record explicit owner authorization with date. Weakens no gate. | Owner authorization | P1 | Spec states criteria and exclusions; threshold/class changes need owner sign-off; restricted claims proven to stay out of the usable set by tests. |
| G3 | Research package has no images/assets with provenance and rights, and no explicit `research_gaps`. | Phase 5 asset service; Asset Rights Engine adapter (F5); package schema bump | P2 | Package v2 lists image candidates (source URL, retrieval time, rights status `RIGHTS_REVIEW_REQUIRED` unless an actual ARE/AssetService result) and `research_gaps[]`; v1 packages stay readable with unchanged digests. |
| G4 | Configurable 900–1,300-word range not implemented. Add `min_words`/`max_words` (defaults 900/1300) to script config; out-of-range fails validation (not a warning); `target_words` stays the prompt target and the range is stated in the prompt. | None (offline) | P1 | <900 or >1,300 narration words fails with a stable code; config validated (min ≤ target ≤ max); boundary tests 899/900/1300/1301; existing failed revision unchanged. |
| G5 | Live evidence path unproven end to end (hosted search, live HTTP/YouTube, live OpenAI). | Credentials + budget (D1, D3, D4, F1) | P1 | Existing D/F items closed with recorded runs. |
| G6 | Syndication/press-release republication across publishers is detected only by text similarity; independence groups come from the registry, not verified origin. | Real-corpus evaluation | P2 | Fixture set of wire/PIB republications groups as one origin and does not satisfy the 2-group gate; false-merge rate recorded. |

Distinction kept: dedup (deterministic grouping) ≠ automated assessment (policy score/gates) ≠ approval (`AUTOMATED_POLICY` record or human review). A duplicate-free package is not verified.
