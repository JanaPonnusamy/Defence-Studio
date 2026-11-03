# Defence Studio — Component Checklist (live)

- Created: 2026-10-29 · Last updated: 2026-10-29 (session start; see Log)
- Repo: `E:\Jana Soft\DefenceStudio` · branch `main` · HEAD `7aacc18`
- Working tree: DIRTY before this session — 24 entries (staged changes to `discovery/providers.py`, `services/research_monitor.py`, `services/script.py`, `services/script_prompt.py`, `research-view.js`, two new test files, `AGENT_WORK/source_material/*`; unstaged `backend/config/source_policy.json`, `data/defence_studio.sqlite3`). **Not made by this session — preserved, not touched.**
- Authority: per-component status lives in `component_registry/REGISTRY.md` and `DEFENCE_ERA_PHASE_STATUS.md`. This file tracks *this task's* verification evidence only; it does not replace those.
- Constraints for this task (owner): no commit/push, no publish, no live-DB changes, isolated DBs only, cost limit before any paid call.

## UI launch

| Item | Result |
|---|---|
| Command | `python -m uvicorn defence_studio.api.app:app --app-dir backend --host 127.0.0.1 --port 8011` with `DEFENCE_STUDIO_DB_PATH`/`STORAGE_PATH` → session scratchpad `ui/` (isolated, NOT `data/`), dev-mode + dev-auth bypass, `RESEARCH_WORKER=0`, no OpenAI key in env |
| Pre-check | 8010/8011 free; port 8000 held by unrelated `python.exe` (PID 8800, Services session) — left alone |
| URL | http://127.0.0.1:8011/ — `GET /` 200, `GET /api/dev/session` 200, "Application startup complete" |
| Observed render | Headless Edge screenshot `scratchpad/ui/initial.png`: login card "Defence Studio — Content Intelligence Workspace", "Enter development mode" + bearer-token form. Also opened in the default desktop browser (not observed by agent). |
| Console/server errors | none in server log at startup |
| Interactive pass (2026-10-29) | Playwright+Edge headless (`scratchpad/uidrive/tour.py`): dev-mode login OK, all 12 tabs (Dashboard, Search & Evidence, Discovery, Sources, Claims, Evidence, Fact check, Script, Scene, Production, Voice, Inspector) navigate; **0 console errors/page errors**; correct empty states on the isolated DB. Screenshots `scratchpad/uidrive/*.png` |
| Status | **PASS (renders + navigates, empty data)** — populated-data pass not done |

## Inventory summary (from `component_registry/REGISTRY.md`, re-tested where noted)

| Group | Components | Entry point | Prior registry status | This-session evidence | Status |
|---|---|---|---|---|---|
| Core backend platform | BE-01..BE-08 (auth, config, DB, jobs, worker, app) | `backend/defence_studio/` | VERIFIED | backend suite (pending) | TESTING |
| Artifact store / URL guard / fetcher | BE-09..BE-11 | `storage/`, `acquisition/` | VERIFIED | backend suite | TESTING |
| Acquisition (incl. YouTube **captions-only** provider) | BE-12..BE-15 | `acquisition/providers/youtube.py` | VERIFIED (offline) | backend suite; live YouTube never run | TESTING |
| Analysis / claims / dedup | BE-16..BE-22 | `analysis/` | VERIFIED | backend suite | TESTING |
| Review (F5) | BE-23..BE-25 | `services/review.py` | VERIFIED | backend suite | TESTING |
| Discovery (RSS/search) | BE-26..BE-31 | `discovery/` | VERIFIED; hosted search no credential | backend suite | TESTING |
| Evidence package / research worker | BE-32..BE-34 | `services/evidence.py` | VERIFIED | backend suite | TESTING |
| Script (OpenAI provider, prompt, validation, service) | BE-35..BE-39 | `services/script*.py`, `providers/openai_text.py` | VERIFIED offline; live OpenAI not verified | backend suite | TESTING |
| Scene plan / assets | BE-40..BE-43 | `services/scene.py`, `services/assets.py` | VERIFIED | backend suite | TESTING |
| Fact-check / media context | FC-01 | `verification/` | PARTIAL | backend suite | TESTING |
| UI primitives | UI-01, UI-03..UI-06 (UI-02 date picker PLANNED) | `frontend/js/components/` | VERIFIED | frontend 78/78 | PASS (unit) |
| UI views | F5-V1..V3, F1A-V1, P2-V1, P3-V1, P4-V1, SE-1, SE-2, voice/production/factcheck/inspector/dashboard/login | `frontend/js/views/` | mixed | frontend 78/78; all 12 tabs render, 0 console errors (empty data) | PARTIAL (populated-data pass owed) |
| Audio Module | standalone | `standalone/audio-module` | TESTED standalone | module suite (pending) | TESTING |
| Video Renderer | standalone | `standalone/video-renderer` | TESTED standalone | module suite (pending) | TESTING |
| Video Visual Module | standalone | `standalone/video-visual-module` | TESTED standalone | module suite (pending) | TESTING |
| Voice engine | EXT-03 | `components/voice-engine` | TESTED | module suite (pending) | TESTING |
| Asset-Rights Engine | EXT-01 | `components/asset-rights-engine` | TESTED | module suite (pending) | TESTING |
| Alignment engine | EXT-02 | `components/defence-align` | TESTED | module suite (pending) | TESTING |
| YouTube publishing | YT-P1..P4 | — | PLANNED | out of scope (no publishing) | NOT_STARTED (intentionally) |
| Date/time picker | UI-02 | — | PLANNED, not needed | — | NOT_STARTED |

## Tests (actual results, 2026-10-29)

| Suite | Command | Result | Evidence |
|---|---|---|---|
| Core frontend | `cd frontend; npm test` | **78 pass / 0 fail / 0 skip** | terminal |
| Core backend | `cd backend; python -m pytest -q --basetemp <scratch>` (temp DB) | pending | `scratchpad/backend_tests.log` |
| Module suites | per-module `pytest -q` | pending | `scratchpad/mod_*.log` |

## Source-video workflow (task Phase 5)

| Item | Status |
|---|---|
| Source URL | **OWNER_DECISION_REQUIRED** — prompt contains placeholder `[PASTE THE CHOSEN PUBLIC VIDEO URL HERE]` |
| Caption extraction | Existing BE-14 (captions-only, host-restricted) imports the **`yt_dlp` Python module** lazily. Server interpreter is repo-local `.audit-venv` (first `python` on PATH) — `yt_dlp` NOT installed there; only a standalone `yt-dlp.exe` exists in `C:\Program Files\Python312\Scripts` (no python.exe beside it). → **BLOCKED**: live caption fetch would fail "yt-dlp is not installed". Repair proposal (needs OK, env ownership unknown): `.audit-venv\Scripts\python -m pip install yt-dlp` (additive) or install backend `youtube` extra into `backend/.venv`. Entry path in UI: Discovery tab → manual http(s) source URLs |
| OpenAI | key configured in `backend/.env.local` (value not read); pre-call INR budget guard exists (`services/script.py:318`). Cost limit not yet set for this task |
| Tamil TTS | BLOCKED per prior status (no Tamil voice/provider) |
| ffmpeg | present `D:\ffmpeg\bin\ffmpeg` |

## Cross-Project Component Discovery

Phase 0 task, 2026-10-29. Read-only on all eight sources; nothing moved; originals hash-verified unchanged after copy.
Authoritative statuses: `component_registry/components.json` (XP-01..16). Catalog: `AGENT_WORK/COMPONENT_CATALOG.md`.

### Source projects

| Project | Accessible | What it actually is | Reuse outcome |
|---|---|---|---|
| `E:\QVault` (git 0c8146b; 5 pre-existing uncommitted changes, untouched) | yes | Mature FastAPI app: multi-provider TTS, ffmpeg, yt-dlp, subtitle parser, quiz renderer | **XP-01 Edge TTS copied**; XP-03..07 reviewed |
| `D:\VBDOTNET\whisper_transcription` (no VCS) | yes | Streamlit app; own docs say "mostly scaffolding"; thin real wrappers; no tests | **XP-02 Whisper ASR copied**; XP-08 not reused |
| `D:\VBDOTNET\NexusYTSync` | yes | ~755 small files, many stubs | XP-09 alt ASR / XP-10 chunker deferred; XP-11 not reused |
| `D:\VBDOTNET\TTVC` | yes | Math-tutor text→voice→video (Groq, edge-tts, moviepy), hard-coded paths | XP-12/13 not reused |
| `D:\VBDOTNET\YT_DOWN` | yes | One-off scripts, hard-coded URLs | XP-14 not reused |
| `D:\VBDOTNET\Transcription` | yes | Single Streamlit file | XP-15 not reused |
| `D:\VBDOTNET\tnpsc` | yes | Exam scrapers + one audio script | XP-16 not reused |
| `D:\VBDOTNET\AgriEye` | yes | TS WhatsApp group-member collector | none (unrelated; personal data) |

### Selection rationale
- Acquisition / captions / subtitle parsing: **keep BE-14 + BE-17** — only SSRF-guarded, tested implementations; all source variants are unguarded yt-dlp calls; media download is outside current policy.
- TTS: **XP-01** — only candidate with measured word timings + a Tamil voice; fills EXT-03's "no Tamil provider" gap. TTVC edge-tts is weaker (no timings, defective normalizer).
- ASR fallback: **XP-02** (word timestamps) over XP-09 (faster, drops timestamps) and the YT_DOWN script.
- Rendering / script generation: canonical in-repo components exist; QVault/TTVC versions are domain-specific.

### Copies (SHA-256 source = copy; see each PROVENANCE.md)
| Source | Destination |
|---|---|
| QVault `integrations/tts/__init__.py`, `tts/edge_provider.py`, `integrations/ffmpeg.py`, `services/video_subtitle_service.py` | `components/edge-tts-provider/source/qvault/` (reference, not imported) |
| whisper_transcription `services/whisper_service.py`, `transcript_processor.py` | `components/whisper-asr/source/whisper_transcription/` (reference, not imported) |

Adapters (deviations documented): `components/edge-tts-provider/edge_tts_provider.py`, `components/whisper-asr/whisper_asr.py`. No secrets, DBs, venvs or media copied.

### Tests (actual)
| Suite | Result |
|---|---|
| `components/edge-tts-provider` pytest | 11 passed (fake edge_tts: timings, pitch offset, invalid input, no partial file, missing dep, missing ffprobe, copy hashes) |
| `components/whisper-asr` pytest | 5 passed (fake model: mapping, cache, SRT rounding, missing media, missing dep, copy hashes); 1 initial failure was a bad float fixture, fixed |
| `backend/tests/test_component_registry_json.py` | 3 passed (fields, unique IDs, stage/evidence rules, no secrets) |
| Live smoke (real synthesis / transcription) | **NOT RUN** — `edge_tts`, `whisper` not installed in `.audit-venv` |

### Blockers / owner decisions
- B-XP1: install `edge-tts` (optionally `openai-whisper`) into the server venv — needs OK.
- B-XP2: Edge TTS service terms for monetised YouTube narration — owner/legal decision.
- B-XP3: ASR fallback needs audio, which current captions-only policy does not acquire — policy decision.

### Next action
After B-XP1: one live 1-sentence Tamil synthesis smoke via XP-01 (free, no key); then propose a `VoiceModel` adapter for EXT-03 (MP3→PCM via ffmpeg) under the normal phase workflow.

## Defects / risks
- D-01 (low, cosmetic): empty white bordered box rendered with no content — bottom-left on Dashboard, right panel on Script (no-job state). Screens `uidrive/01_after_login.png`, `uidrive/v07_Script.png`. Not yet diagnosed.
- D-02 (high for Phase 5): `yt_dlp` module missing from the server interpreter → caption extraction cannot run live (see Source-video table).

## Owner decisions
1. Source video URL (placeholder in instructions).
3. Permission to `pip install yt-dlp` into `.audit-venv` (or name the intended runtime venv).
2. Per-test OpenAI cost cap (proposed: ≤ ₹25 per live call, single smoke call only).

## Next action
Record backend + module suite results; then interactive UI pass via dev mode.

## Log
- 2026-10-29 — Voice analysis progress UI (EXT-03 Voice tab, Analyzer panel): IMPLEMENTED + TESTED + live UI verified on isolated DB. Files: `frontend/js/views/voice-view.js`, `frontend/styles/main.css`, new `frontend/tests/voice-progress.test.mjs`. Data source: existing `GET /api/voice/jobs/{id}` (status, stage attempt, progress_note, updated_at, error, can_cancel/can_retry) polled every 2 s while QUEUED/RUNNING; restore via existing `GET /api/voice/jobs?kind=ANALYSIS`. No backend percentage exists → indeterminate bar (no aria-valuenow); full bar only on server COMPLETED. Tests: frontend 86/86, backend voice 28 passed. Live: QUEUED→RUNNING→QUEUED(retry)→FAILED(NoSpeechError, sine fixture) observed, restore after reload and after server restart, 390px no h-scroll, 0 console errors. COMPLETED observed live 2026-10-29 with owner-supplied `Downloadsoice_reference.wav` (33 s, 48 kHz stereo; copied to scratchpad `ref_in/`): RUNNING→COMPLETED in ~3 s, bar aria-valuenow=100, profile VSP-22c41234b7c90fae rendered, confidence 0.857, state + profile restored after reload, 0 console errors (isolated DB only). Screens `scratchpad/uidrive/vp_*.png`.
- 2026-10-29 — Cross-project Phase 0: 8 projects inspected, 16 candidates registered (XP-01..16), XP-01/XP-02 copied + adapted + offline-tested (11+5+3 pass).
- 2026-10-29 — UI interactive tour PASS (12 tabs, 0 console errors); found D-01, D-02.
- 2026-10-29 — inspected repo/status docs; launched UI on isolated DB (8011), observed login render; frontend 78/78; started backend + module suites.

## Tamil TTS acceptance (2026-10-29, branch `feature/tamil-tts-acceptance` from `develop` f78c23f)

Recovered unchanged from backup `wip/pc2-2026-10-29` (a012c84) and extended. Work done in a separate git worktree so the main checkout (another session's uncommitted research-handoff work) was not touched.

| Item | Status | Evidence |
|---|---|---|
| Canonical Tamil TTS provider | VERIFIED (single path) | `services/audio.py` → audio module `/tts/synthesize` → `app/tts/registry.py` `edge-neural` → `EdgeNeuralAdapter`. Only one Edge provider registered. Backup's second Edge voice in `components/voice-engine`/`services/voice_engine.py` NOT ported (would duplicate). `components/edge-tts-provider` (XP-01) stays on the backup branch only. |
| Tamil voice selection | IMPLEMENTED + TESTED (unit/mock); browser pass not done | Production → Narration audio: provider + voice selects built from server status (`default_provider`, `voices` from module `/tts/providers`, `suggested_voice` = `ta-IN-PallaviNeural`, `provider_terms`). Undeclared voice → 422 `VOICE_UNSUPPORTED` before synthesis. A request naming no voice keeps the module default (Valluvar). Valluvar/Saranya selectable. |
| Analysis vs synthesis profiles | UNCHANGED on develop | Voice-tab analysis profiles (e.g. Toshila v1) are not voices; no profile is saved from generated audio (the model has no such concept). Backup's profile-labelling UI changes remain on a012c84, not ported in this task. |
| Military-designation pronunciation | TESTED; S-400 choice UNRESOLVED | Order: suffix rewrites → designation table → lexicon. `S-400` designation "S four hundred" (unchecked) shadows lexicon "எஸ்-400" (ASR-checked 2026-10-28). Kept as committed (f78c23f, deliberate); pinned by `test_tamil_voice_selection.py`. Su-57-ல் rewrite, MiG-29, DRDO, numbers/units tested with real configs; unreviewed suffix (MiG-29-இல்) reported separately. A/B samples of both S-400 forms produced for a listening decision. |
| Live synthesis (canonical adapter) | LIVE-VERIFIED technically; NOT listening-accepted | 2026-10-29, edge-tts 7.3.1, `standalone/audio-module/.venv` (Py 3.12.10, git-ignored). Same prepared text, 3 voices: Pallavi 20.02 s, Valluvar 19.66 s, Saranya 21.07 s; all 24 kHz mono pcm_s16le, 5/5 sentence boundaries, full ffmpeg decode 0 errors; + 6 S-400 A/B clips. Wrong-locale voice and empty text raise clear ValueErrors; no file or temp dir left. Samples in session scratchpad only (not committed). Cost: 0. |
| Acoustic analysis of samples (heuristic) | RECORDED | Analyzer (`voice_analyzer.analyze_audio`), earlier 48 kHz decode of identical text: pitch median Pallavi 246.2 / Saranya 231.9 / Valluvar 149.5 Hz; syllables/s 4.95 / 4.81 / 5.16; quality score 0.910 / 0.909 / 0.887 (analysis heuristic, not similarity). Reference Toshila v1 (219.18 Hz) unchanged. No voice match claimed. |
| Human listening | NOT DONE | Owner must listen: naturalness, ழ/ள/ற, designations, numbers, S-400 A/B. |
| Whisper ASR | NOT INTEGRATED (by design) | XP-02 copy remains on backup branch only; no model installed. |
| Provider licensing | BLOCKED for monetized production | edge-tts library ≠ service terms. Edge read-aloud endpoint has no published commercial/YouTube terms; status reports `EVALUATION_ONLY`. Licensed route: Azure AI Speech (same voices listed: ta-IN Pallavi/Valluvar, ta-LK Saranya/Kumar, ta-MY Kani/Surya, ta-SG Venba/Anbu) — needs Azure subscription + key, an `azure-speech` adapter in the audio module, and owner acceptance of Microsoft Product Terms. No paid call made. |
| Regression tests | see Tests below | |
| GitHub delivery | see Log | feature branch to `origin`; `axythic` remote not configured on this PC. |

### Tests (feature branch)
- `backend: pytest tests/test_tamil_tts_quality.py tests/test_tts_designations.py tests/test_stage_sync.py tests/test_audio_rm4.py tests/test_tamil_voice_selection.py` → 36 passed, 1 skipped (real-module English test; module not running).
- `standalone/audio-module: .venv pytest` → 65 passed.
- `frontend: node --test tests/*.test.mjs` → 82 passed (incl. new `voice-picker.test.mjs` 4).
- `test_voice_tamil_generation` (backup) not applicable: it tests the non-ported voice_engine Edge adapter.

### Next
1. Owner listening check of the 3 voices + S-400 A/B; then set the designation/lexicon precedence and `verified` flags.
2. Decide production TTS: Azure AI Speech adapter (paid) or another licensed Tamil voice.
3. Browser pass of Production → Narration audio voice picker on a populated job.
