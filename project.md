# Defence Studio — Project Plan & Integration Map

> Living document. Source of truth for *what* we are building and *how it maps onto existing code*.
> Progress, decisions and change log live in `project_status.md`.
> Planning input: `DEFENCE_STUDIO_PROJECT_BRIEF.md` (transferred from ChatGPT "Money Earning").
> Last updated: 2026-10-10 — Phase 0 audit (Claude).

---

## 1. Goal (from the brief)

A solo-creator workflow for source-backed defence / military / geopolitics YouTube videos (Tamil first, language selectable):

`Topic → research → source register → claims & evidence → human evidence review → script (OpenAI) → script approval → scene plan → scene approval → TTS → word alignment from real audio → existing Python/FFmpeg renderer → QC → export`

Hard rules carried from the brief: reuse existing code, no mock data, no redesign, human approval at factual/editorial gates, ₹10/video OpenAI text-cost target (measured, not promised), media-rights tracking, real progress (no fake percentages).

---

## 2. Evidence labels used in this document

- **[V] Verified** — I read the actual source/config file on your computer on 2026-10-10.
- **[D] Documented** — stated in QVault's own docs (`Project_Master_Document.md`) or the earlier `analysis/` reports; not re-checked in code.
- **[A] Assumption** — my inference; needs your confirmation.
- **Not run** — nothing was executed: no app start, no tests, no TTS call, no render. This session has no shell on your computer.

---

## 3. What actually exists (repositories inspected)

| Path | What it is | Status |
|---|---|---|
| `E:\QVault` | FastAPI + React "Exam Intelligence Platform". Contains the **Video Generation Engine**, **Knowledge Research Engine**, TTS layer, FFmpeg renderer, jobs, DB, auth/RBAC, UI. Git branch checked out: `develop` [V]. | **Primary reuse base** |
| `D:\VBDOTNET\whisper_transcription` | "Whisper Studio Pro" — Streamlit app: YouTube → MP3 → openai-whisper → word timeline/SRT/TXT export [V]. | Small helpers reusable; app itself partly broken (see §6) |
| `E:\Jana Soft\DefenceStudio` | Destination folder. Contains only `analysis/` (a read-only discovery written earlier today by another tool) + this file + `project_status.md`. **No source code yet** [V]. | Target |

**[A] Name mapping — please confirm:**
- Brief's "existing **Python video generator**" = QVault **Video Generation Engine** (module #21). I found no other video generator.
- Brief's "**QVault Knowledge Explorer** that extracts subtitles/text from videos" = QVault **Knowledge Research Engine** (`/research`, module #20). Note: QVault also has a different module literally called "Knowledge Explorer" (module #13, PDF→knowledge tree), which does **not** process video.

---

## 4. File-level integration map (QVault, paths relative to `E:\QVault\backend\app`)

### 4.1 Backend platform
| Concern | Files | Facts |
|---|---|---|
| Entry point | `main.py` → `core/app.py` | [V] `create_app()` runs `init_db()`, `seed()`, cookie/Instagram setup at import; registers 17 routers incl. `research` and `videos`. |
| Run | `E:\QVault\launch_qvault.bat` | [V] backend :8005, frontend :5174, uses `backend\.venv` if present. ⚠ `CLAUDE.md` says :8004, `AGENTS.md` says :8005 — docs disagree; the .bat uses 8005. |
| Config | `config/settings.py`, `config/knowledge_config.py`, `E:\QVault\config\.env` | [V] `.env.example`: `QVAULT_DB_BACKEND=sqlite`, `LLM_PROVIDER=openrouter`, `QVAULT_TTS_PROVIDER=edge`, `QVAULT_TTS_VOICE=en-IN-NeerjaNeural`. (Live `.env` deliberately not read.) |
| Database | `database/session.py`, `database/migrations/`, `E:\QVault\database\qvault.db` (~203 MB) | [V] SQLAlchemy 2; SQLite default, SQL Server optional. [D] additive-only migrations. |
| Jobs / worker | `core/acquisition_worker.py`, `models/acquisition.py` (`acquisition_jobs`) | [D/V] in-process `ThreadPoolExecutor`, `job_type="video_render"` branch; jobs don't survive restart [D]. |
| Auth / RBAC | `api/deps.py`, `models/rbac.py` | [D] `module:action` permissions, every endpoint guarded. |
| Project rules | `E:\QVault\CLAUDE.md`, `AGENTS.md`, `Project_Master_Document.md`, `Project_Todo.md` | [V] Reuse-first, no parallel package tree, no mocks, update master/todo per sprint, verify via import + `npm run build`. |

### 4.2 Research ("Knowledge Explorer" per brief)
| Capability | Files | Facts |
|---|---|---|
| Orchestrator | `services/knowledge_research_service.py` | [V] Mode A (one YouTube URL) / Mode B (topic → top-N YouTube). `MAX_ANALYSIS_CHARS = 24000` truncation. Own thread pool (`knowledge_executor.py`). |
| Source search | `services/source_search_service.py`, `search_providers/youtube_search_provider.py` | [V] **YouTube only** (`ytsearchN:`). No web/news search. |
| Extraction | `services/knowledge_extraction_service.py`, `yt_dlp_wrapper.py`, `subtitle_parser.py`, `whisper_wrapper.py` (faster-whisper), `ocr_service.py`, `knowledge_frame_extraction_service.py` | [V/D] transcript + subtitles + frame OCR per video; media deleted after extraction [D]. `whisper_wrapper` returns plain text (no timestamps) [D]. |
| LLM layer | `services/llm_service.py`, `llm_providers/openai_provider.py` | [V] OpenAI-SDK-compatible (OpenAI/OpenRouter/etc. by base URL). Records input/output tokens + `estimated_cost` (USD) per call. ⚠ Pricing is a **hard-coded table** (gpt-4o-mini, gpt-4o, gpt-4.1-mini). ⚠ `_parse_json` returns `{}` on invalid JSON — silent failure. |
| Persistence | `models/knowledge_research.py`, `repositories/knowledge_research_repository.py` | [V] SQLAlchemy tables `knowledge_sessions`, `knowledge_documents` (url, title, language, checksum, paths), `knowledge_facts` (value, confidence, evidence text), `knowledge_entities`, `knowledge_consensus`, `knowledge_ai_runs` (tokens, cost). Master doc's "raw sqlite3" note is stale (it has a 2026-08-04 correction). |
| API / UI | `api/routers/research.py`; `frontend/src/pages/KnowledgeResearchPage.tsx`, `KnowledgeResearchHistoryPage.tsx`, `components/knowledge-research/*` | [V] routes `/research`, `/research/history`, `/research/:sessionId`. |

### 4.3 Video generator, TTS, renderer
| Capability | Files | Facts |
|---|---|---|
| TTS contract | `integrations/tts/__init__.py` | [V] `TTSProvider.synthesize(text, voice, out_path, rate, pitch) → SynthesisResult(path, duration, words[WordTiming])`. Registry imports 7 providers (azure, edge, elevenlabs, google, kokoro, openai, piper). Per-segment rate/pitch already supported — good fit for a Voice Direction Engine. |
| Edge TTS | `integrations/tts/edge_provider.py` | [V] Requests `WordBoundary` events → **real word timings from the generated audio**; duration via ffprobe. Voices incl. `ta-IN-PallaviNeural` (⚠ labelled "Tamil Nadu accented English" — it is a Tamil voice id; mislabel). Adds a +12Hz base pitch to Pallavi. Uses `asyncio.run` (sync worker assumption). |
| Script | `services/video_script_service.py`, `services/video_source_service.py` | [V] `ScriptSegment`; builds **quiz** narration from `QuestionItem`s loaded from `storage/**/*.json`. English phrasing hard-coded. |
| Timeline | `services/video_timeline_service.py` | [V] Synthesises each segment first, lays out visuals from **measured** audio durations; TTS cache in `output/.work/tts_cache`; falls back to estimated word timings only if provider gives none; `estimate()` preview ≈2.5 words/s (preview only). |
| Audio mix | `services/video_audio_service.py` | [D] NumPy mixdown, countdown ticks, optional music at fixed volume — no ducking/loudness target. |
| Renderer | `services/video_render_service.py` (~40 KB), `integrations/ffmpeg.py` | [D] streaming Pillow frames → FFmpeg libx264/aac; glassmorphism quiz layout; landscape + portrait; templates `E:\QVault\assets\templates\*.json` (6) [V]; fonts **Poppins only** [V] (⚠ Tamil glyph support must be verified — Poppins is not a Tamil font). |
| Subtitles | `services/video_subtitle_service.py` | [D] `write_srt` from timeline. |
| Orchestrator | `services/video_generation_service.py`, `models/video.py`, `repositories/video_repository.py`, `api/routers/videos.py` | [V] `queue()` loads questions → creates `videos` row → `acquisition_jobs` (`video_render`) → worker runs `run_render` (load → script → TTS → timeline → mix → render → SRT/thumbnail). Render semaphore. Reloads `source_file` at render time (not a snapshot). |
| UI | `frontend/src/pages/VideoGeneration.tsx` | [V] route `/videos`. |
| Outputs | `E:\QVault\output\videos|shorts|reels` | [V] `output\videos` is currently **empty** — no rendered sample on disk to inspect. |

### 4.4 Tests
[V] `E:\QVault\backend\tests\`: `test_video_providers.py`, `test_acquisition_framework.py`, `test_frame_extraction_strategies.py`, `test_frame_sampling.py`, `test_worker_local_mp4.py`, `test_ytdlp_cookies.py`, `test_auth_login.py`, `test_branding_service.py`, `test_education_acquisition.py`, `test_instagram_module.py` (+ `video_fixtures.py`). **No tests for TTS, timeline, script, render, research service or LLM parsing.** No frontend tests. **None were run.**

### 4.5 Whisper Studio Pro (`D:\VBDOTNET\whisper_transcription`)
[V] `app.py` (Streamlit) → `MediaService.download_mp3` → picks the **newest MP3 in the folder** (not the one just downloaded) → `WhisperService.transcribe` → `TranscriptProcessor.extract_word_timeline / generate_srt` → `ExportCenter` writes fixed names (`exports/transcript.*`, overwritten each run). "Timeline" tab has no content block. Reusable: `transcript_processor.py` (word-timeline/SRT from whisper output) as a possible **forced-alignment fallback** for TTS providers without word events.

---

## 5. Capability map — brief vs. reality

| # | Brief stage | Exists today | Reuse | Gap to build |
|---|---|---|---|---|
| 1 | Create job (topic, duration, language, budget) | Research session (topic/URL), video job rows, `acquisition_jobs`, `JobProgress` UI [V] | Job/worker/progress pattern | A **defence project** record tying research→script→scenes→render; duration/language/budget fields; stage states. |
| 2 | Research | YouTube topic search + transcript/subtitle/OCR extraction [V] | KnowledgeResearchService, extraction, LLM layer | **Web/news/official-source adapters** (none exist); search API choice + cost. |
| 3 | Source records | url, title, language, checksum, created_at [V] | `knowledge_documents` | publisher/author, **published / updated / retrieved / event** times, source category, rights. |
| 4 | Normalize & dedupe / syndication | checksum per doc only [V] | — | Near-duplicate + syndication grouping; "not independent corroboration" rule. |
| 5 | Claims & evidence | `knowledge_facts` (value, confidence, free-text evidence), consensus/conflicts JSON [V] | As input | Claim ↔ source links with **supports / contradicts / inconclusive**; strict JSON schema (no silent `{}`). |
| 6 | Human evidence review | none | — | Review UI + statuses supported/disputed/unverified/excluded + approval events. |
| 7 | Script generation (OpenAI, Tamil) | LLMService generic `generate/generate_json` [V]; quiz script builder (English) [V] | LLMService | Source-referenced narrative script prompt; Tamil; target duration. |
| 8 | Script approval & invalidation | none | — | Script versions, approval tied to exact version, downstream invalidation. |
| 9–10 | Scene plan + approval | quiz scene timeline only [V] | Timeline layout engine, templates | Narrative scene DTO (media, caption, motion, transition, voice direction), editor, approval. |
| 11 | Narration + word alignment | Edge TTS with **real WordBoundary timings**; timeline from measured audio [V] | Directly | Voice Direction Engine mapping → rate/pitch/pauses; Tamil voice quality check; mark estimated timings. |
| 12 | Render | Streaming Pillow+FFmpeg quiz renderer [V/D] | Compositor, FFmpeg, templates, SRT | Narrative layouts (image/footage/map + captions), **Tamil font**, rights-checked assets. |
| 13 | QC | none | `probe_duration` | Duration/A-V sync, missing media, black gaps, caption overflow, loudness. |
| 14 | Export | MP4 + SRT + thumbnail [D] | Directly | Description, source register, QC report. |
| — | Cost ledger / ₹10 cap | per-call tokens + USD estimate in `knowledge_ai_runs` [V] | Directly | Per-project attribution, INR, configurable current pricing, **pre-call budget gate**, separate TTS/search costs. |
| — | Branding / music / end card | 6 JSON templates, no music, no intro/outro [V/D] | Templates | Versioned intro/outro/end-card, licensed music cues, ducking. |

---

## 6. Current end-to-end execution paths (as they exist)

**Research** [V]: `/research` page → `POST /api/research/sessions` → `KnowledgeResearchService.start_session` → own thread pool → YouTube search (Mode B) → per video: yt-dlp download → faster-whisper transcript + subtitles + frame OCR → merged text (truncated 24k chars) → LLM analysis JSON → facts/entities rows + `knowledge_ai_runs` → consensus → `report.json/md` in `storage/knowledge/<id>/`.

**Video** [V]: `/videos` page → `POST /api/videos/generate` → `VideoGenerationService.queue` (load quiz questions from JSON file) → `videos` row + `acquisition_jobs(video_render)` → worker → `run_render`: questions → `VideoScriptService.build` → `VideoTimelineService.build` (TTS per segment, cached, measured durations) → `VideoAudioService` mix → `VideoRenderService` (FFmpeg) → SRT + thumbnail → `output/videos/`.

**There is no path today from research output to video input.** The video engine only consumes quiz-question JSON.

---

## 7. Proposed integration architecture

**Recommended (pending your decision D1): build Defence Studio as a new module inside QVault on a feature branch** (e.g. `feature/defence-studio` off `develop`).
Why: QVault's own rules forbid parallel package trees and duplicated workers/tables; every needed building block (auth, jobs, worker, LLM + cost rows, TTS with word timings, renderer, React shell) already lives there; a git branch is fully reversible. Quiz video and existing research keep working unchanged — Defence Studio adds new files and *extends* contracts, it does not edit quiz behaviour.

Alternative (what the earlier `analysis/` reports proposed): copy/adapt selected files into a separate app at `E:\Jana Soft\DefenceStudio`. Cleaner domain separation, but duplicates ~25 files that will drift from QVault and needs new auth/DB/config bootstrap first.

Conceptual flow inside QVault:
```
React /defence pages ─► api/routers/defence.py ─► services/defence/*
   project_service (stages, approvals, invalidation)
   research  ─► reuse KnowledgeResearchService + NEW web/news source adapters
   evidence  ─► NEW claims/evidence/dedupe on top of knowledge_* rows
   script    ─► reuse LLMService + NEW budget gate/ledger
   scenes    ─► NEW narrative scene DTO + voice direction
   render    ─► reuse TTS registry, timeline layout, audio mix, FFmpeg, write_srt
                + NEW narrative layout in renderer, Tamil font, QC
jobs        ─► reuse acquisition_jobs + acquisition_worker (new job_types)
```
Candidate new tables (names adapted to QVault style, created only when their phase starts): `defence_projects`, `defence_sources` (or columns added to `knowledge_documents`), `defence_claims`, `defence_claim_evidence`, `defence_script_versions`, `defence_scene_plans`, `defence_approval_events`, `defence_media_assets`, plus project linkage on `knowledge_ai_runs` and `videos`.

---

## 8. Phased plan (small, independently testable deliverables)

**Phase 0.5 — Baseline verification (no code changes)**
Confirm what actually runs today: backend import, existing pytest suite, frontend build, one Edge TTS **Tamil** sample with word timings, one existing quiz render. Gives a known-good baseline before any change.

**Phase 1 — Project/job foundation**
1.1 `defence_projects` model + repository + migration (topic, language, target duration, budget INR, stage, status).
1.2 Stage state machine (QUEUED/RUNNING/REVIEW_REQUIRED/APPROVED/BLOCKED/FAILED/CANCELLED/COMPLETED) + `defence_approval_events` referencing exact artifact version.
1.3 API `/api/defence/projects` (RBAC `defence:view/execute/approve`) + `/defence` dashboard page in the Studio group using real rows.

**Phase 2 — Research & evidence package**
2.1 Link a defence project to a Knowledge Research session (reuse Mode A/B as-is).
2.2 Source provenance fields (publisher, published/updated/retrieved times, category).
2.3 One web/news source adapter (provider chosen in D4) behind the existing `search_providers` interface.
2.4 Dedupe/syndication grouping (deterministic: normalised text hashing + similarity).
2.5 Claims + evidence links (supports/contradicts/inconclusive) with strict JSON schema validation (fix silent `{}` for this path).
2.6 Evidence review UI.

**Phase 3 — Script & budget**
3.1 Cost ledger per project (link `knowledge_ai_runs`), INR conversion, configurable pricing.
3.2 Pre-call budget gate (stop / require approval when projected cost exceeds budget).
3.3 Script generation from approved claims only, with source references; versioning; approval; invalidation.

**Phase 4 — Scene plan**
4.1 Narrative scene DTO + planner (LLM or deterministic split) with voice direction separate from text.
4.2 Media asset registry with rights status; block/warn on unknown rights.
4.3 Scene editor + approval.

**Phase 5 — Voice, render, QC**
5.1 Voice Direction Engine → Edge rate/pitch/pauses; Tamil voice evaluation.
5.2 Narrative render layout + Tamil font in existing renderer (new layout, quiz untouched).
5.3 QC checks + report; stage-level real progress; cancel/resume where safe.

**Phase 6 — Channel package**
Versioned intro/outro/end card, licensed music cues with ducking, thumbnail/description/source-register export.

First milestone (per brief): **Topic → traceable evidence package → approved script** (Phases 1–3).

---

## 9. Risks & dependencies

1. **No shell on your PC from this session** — I can read/write files but cannot run Python, tests, or FFmpeg. Verification needs either you running commands, or a session with the device shell enabled.
2. **Web research source** — nothing exists beyond YouTube. A news/web search API may cost money (counts outside the ₹10 OpenAI target) or needs scraping with ToS care.
3. **Tamil rendering** — Poppins fonts only; Tamil glyph shaping in Pillow needs a Tamil font + raqm/libraqm check.
4. **Tamil voice quality** — `ta-IN-PallaviNeural` declared but untested; English military terms/acronyms/numbers inside Tamil text untested.
5. **LLM JSON silent failure** — `_parse_json → {}` can let empty analyses pass as success.
6. **Stale pricing table** — USD prices hard-coded; ₹10 budget needs current pricing + INR rate.
7. **Default LLM provider is OpenRouter**, brief says OpenAI — config choice (D3).
8. **Worker durability** — in-process thread pools; jobs lost on restart [D].
9. **Doc drift in QVault** — port 8004 vs 8005; master doc partially stale; `VERSION.md` says branch `main` while repo is on `develop`.
10. **Media rights** — no rights tracking exists anywhere today.
11. **QVault startup side effects** — `create_app()` seeds DB, Instagram session etc.; tests must use a separate DB path.

## 10. Tests to add (by phase)
Project state transitions & approval-version invalidation (P1); provenance persistence, dedupe/syndication, conflicting-claim handling, strict-schema rejection (P2); budget gate & ledger arithmetic, retry-without-duplication (P3); scene ordering/rights flags (P4); TTS word-timing vs audio duration, SRT alignment, QC detectors (P5).
