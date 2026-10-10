# Defence Studio — Phase 0 repository audit and integration plan

Date: 2026-10-10 (Asia/Calcutta). Status: ACCEPTED after user review on 2026-10-10. F1 is separately authorized and completed; Phase 0 remains closed.
Scope: inspection and isolated baseline verification only. No implementation edits, source replacements, deployment, provider calls, or fabricated production data.

## 1. Authority, repository identity, and current status

The governing document is [DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md](./DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md), especially sections 3, 4, 8, 12, and 15. Read alongside the local project.md/project_status.md and relevant discovery reports, plus E:/QVault/AGENTS.md, CLAUDE.md, Project_Master_Document.md, Project_Todo.md and the Whisper README. The separately named DEFENCE_STUDIO_PROJECT_BRIEF.md was not found in the inspected project locations; its full contents have not been verified.

The older project.md recommendation to implement inside QVault and its old phase numbering are superseded by the new core design. The originals must remain intact. Proposed reuse is through narrow adapters or controlled component copies in DefenceStudio, with provenance and regression tests. This report does not approve a replacement architecture.

| Location | Actual state |
|---|---|
| E:/Jana Soft/DefenceStudio | Core design, planning and discovery Markdown, analysis archive; no frontend/backend/application implementation before this audit. Audit artifacts added under analysis/phase0-baseline. |
| E:/QVault | Existing React/FastAPI application. Branch develop, HEAD 0c8146b53ec32d979d4a630b0c71c08af77fa8af. Both main/develop and remote branches present; recent all-branch history inspected. |
| D:/VBDOTNET/whisper_transcription | Separate Streamlit application and Python transcription/export helpers. Source inspected; application not launched. |

Existing QVault working-tree changes, present before this audit: backend/app/integrations/gk_site_analyzer.py, backend/scripts/import_legacy_gk_json.py, frontend/src/pages/GkScraper.tsx, frontend/vite.config.ts; untracked database/qvault.db-journal. These are not our changes and have not been edited or reverted. Source provenance must include selected-file hashes as well as HEAD, since the checkout is dirty.

Defence Studio Phases 1–10 are NOT_STARTED. QVault features are reusable inputs, not evidence that Defence Studio itself is implemented.

## 2. Actual components and reuse/dependency map

Q paths below are relative to E:/QVault; W paths to D:/VBDOTNET/whisper_transcription. They identify real inspected files or explicitly named dependency boundaries, not proposed files already implemented.

| Component | Actual files and capability | Reuse / gap |
|---|---|---|
| UI | Q frontend/package.json; frontend/src/App.tsx; pages/KnowledgeResearchPage.tsx, KnowledgeResearchHistoryPage.tsx, VideoGeneration.tsx; components/JobProgress.tsx, Modal.tsx; services/knowledgeResearchApi.ts | React 18, TS, Vite, Bootstrap shell; /research, /research/history, /research/:sessionId, /videos and separate /knowledge route. Reuse interaction patterns/styles. Imports depend on auth, API client, types and branding; do not copy pages alone. No Defence news feed, scene editor or approval workflow exists here. |
| API/bootstrap | Q backend/app/core/app.py, api/routers/research.py, api/routers/videos.py, api/deps.py | FastAPI routers and module RBAC. Startup initializes/migrates/seeds DB and seeds Instagram cookies; import is not inherently read-only. Isolated baseline avoids the live store. |
| Store | Q backend/app/database/session.py, models/knowledge_research.py, models/acquisition.py, models/video.py, repositories/knowledge_research_repository.py | Shared SQLAlchemy; SQLite and SQL Server support. Local database/qvault.db exists. Live configured backend was not queried. Use isolated SQLite authoritative metadata for the new workflow, with artifact folders; do not copy the production DB. |
| Jobs | Q backend/app/core/acquisition_worker.py; services/knowledge_executor.py; services/acquisition_queue_service.py; integrations/acquisition/dto.py | Persisted jobs plus process-local executors. AcquisitionDocument/JobSpec and AcquisitionItem provide a useful provider contract, unique provider/source identity, retry counters and recover_stuck for downloads. These are not a durable full video workflow scheduler. |
| Topic research | Q backend/app/services/source_search_service.py; search_providers/youtube_search_provider.py; knowledge_research_service.py | Research search registry contains YouTube only. Topic and URL modes, per-source failure rows, facts/entities/consensus, JSON/Markdown reports. Adapt orchestration and history; add feed/search provider integration, provenance, dedupe and strict evidence contracts. |
| Existing web intake | Q backend/app/integrations/acquisition/providers/education/discovery.py and parser.py; integrations/gk_http.py; services/education_acquisition_service.py | Manual URL, sitemap, RSS, government/crawl, Google/Bing/DDG HTML result adapters exist outside Research. Parser dispatch supports HTML/PDF/DOCX/image/XML/TXT/ZIP, with education-specific fields/classification. Reuse transport/format parsing through adapters; do not adopt education semantics or assume live search reliability. Search failures can silently produce no items. |
| Video extraction | Q backend/app/services/knowledge_extraction_service.py; yt_dlp_wrapper.py; subtitle_parser.py; whisper_wrapper.py; knowledge_frame_extraction_service.py; ocr_service.py | ExtractionResult contains paths, checksum, processing version, metadata. Current flow downloads audio, attempts video/subtitles, transcribes, optionally OCRs, merges text and deletes media. It is not subtitle-first. QVault Knowledge Explorer /knowledge is PDF hierarchy browsing, distinct from the /research video research module. |
| LLM/usage | Q backend/app/services/llm_service.py; llm_providers/openai_provider.py; config/knowledge_config.py; models/knowledge_research.py | Provider abstraction and token/cost/latency records. Invalid JSON can return an empty object; pricing is a fixed table. No proven per-video INR budget enforcement or encrypted per-user provider vault. Global environment configuration is not per-user credential storage. |
| Script/timeline | Q backend/app/services/video_source_service.py, video_script_service.py, video_timeline_service.py | QuestionItem → ScriptSegment → quiz VideoTimeline. English quiz phrases and question/option/reveal semantics. Real clip durations, pauses, caption chunks, TTS cache. Adapt contract boundaries; these do not directly accept approved defence narrative scenes. |
| TTS | Q backend/app/integrations/tts/__init__.py, edge_provider.py | TTSProvider.synthesize(text, voice, out_path, rate, pitch) → SynthesisResult(path, duration, words). Seven registered provider modules. Edge requests WordBoundary and probes audio duration. Installed/configured/working are different states; no live provider verification in this audit. |
| Audio/render/export | Q backend/app/services/video_audio_service.py, video_render_service.py, video_subtitle_service.py, video_generation_service.py; integrations/ffmpeg.py | Mono NumPy mixdown, tick sounds, optional music/fade and peak limiting; Pillow frames streamed to FFmpeg H.264/AAC, portrait/landscape, JPG thumbnail and SRT. Keep renderer; adapt scene input later. No full QC, rights gate, narrative assets or loudness/ducking stage established. |
| Whisper helper | W app.py; app/services/whisper_service.py, transcript_processor.py, export_center.py | openai-whisper with word_timestamps=True; word timeline, SRT/TXT/JSON exports. Reuse helpers behind explicit input/output paths. UI picks newest downloaded MP3 and fixed export filenames, unsafe for concurrent jobs. Transcription timestamps are not automatically forced alignment of an approved script. |

Dependency boundaries: acquisition DTO → provider transport/parser → repository/queue; research source/extraction DTO → analysis/report; approved future scene plan → measured audio/timeline → existing renderer. Isolate adapters from QVault startup, global settings and DB side effects. Both original projects use a top-level app package: importing both blindly in one interpreter risks collisions.

## 3. Current pipelines (source verified)

### Research, separate from rendering

/research UI → POST /api/research/sessions → KnowledgeResearchService → BackgroundTaskExecutor → YouTube topic search or supplied URL → document rows → audio/video/subtitle extraction → Whisper + optional OCR → merged.txt → first 24,000 characters analyzed through LLMService → facts/entities/AI-run metadata → cross-source consensus for multiple successful sources → report.json/report.md → history/results.

Per-source failures are retained; processing continues if at least one source succeeds. Reports include timeline/conflict fields, but these are not a validated claim-to-passage evidence graph or approved immutable research version. Stage percentages use preset weights rather than measured elapsed-work estimates.

### Existing video generation

/videos UI → POST /api/videos/generate → VideoGenerationService.queue → load question JSON → videos row + acquisition_jobs(video_render) → acquisition worker → render semaphore → reload source JSON → VideoScriptService English quiz segments → TTS cached per provider/voice/rate/pitch/text → measured clip durations and words → quiz timeline/captions → WAV mixdown → Pillow frame stream → FFmpeg MP4 + JPG → SRT → video/job completed rows and notification.

No connection from research reports to this input exists. Queueing records question IDs, but rendering reloads the source file; this is not an immutable input snapshot. FFmpeg uses overwrite mode and stable per-video output filenames, so later retries need output version protection. Quiz intro/outro drawing already exists; the missing feature is versioned channel branding with mandatory reviewed cards, not all title-card capability.

### Whisper Studio

URL → MP3 download → choose newest MP3 in shared downloads → Whisper result → transcript and word timeline → segment SRT → fixed transcript.txt/transcript.srt/timeline.json exports. Keep the reusable functions; do not adopt the UI/session state as authoritative workflow state.

## 4. Baseline verification

Commands and raw outputs are retained in analysis/phase0-baseline. Existing tests use their own synthetic/offline fixtures; those were not represented as live sources or product data. No new mock feature or news data was created.

- Backend: existing E:/QVault/backend/.venv/Scripts/python.exe, with PYTHONDONTWRITEBYTECODE=1 and explicit QVAULT_DB_BACKEND=sqlite, QVAULT_DATABASE_URL, QVAULT_STORAGE_DIR, QVAULT_OUTPUT_DIR and QVAULT_BRANDING_CONFIG_PATH pointing into this audit folder. See run-backend.ps1 for the exact command/environment.
- Import: `python -c "from app.core.app import app; print('ROUTE_COUNT', len(app.routes))"` passed, exit 0. Printed ROUTE_COUNT 22; this is the literal route-list length, not a claim about expanded API endpoint count.
- Frontend: from E:/QVault/frontend, `npm.cmd run build -- --outDir 'E:/Jana Soft/DefenceStudio/analysis/phase0-baseline/frontend-dist'` passed, exit 0; TypeScript and Vite passed, 128 modules transformed, Vite build 4.12s. Benign warning that the external output directory would not be emptied. No npm install performed.
- FFmpeg and FFprobe `-version`: both 8.1.1-essentials_build-www.gyan.dev.
- Full pytest baseline: `python -m pytest -q -p no:cacheprovider` completed with exit 1: **138 passed, 1 failed, 1 warning in 141.95s** (includes slow tests). Failure: tests/test_frame_extraction_strategies.py::TestExtractionOptions::test_all_four_strategies_are_registered, line 244. Expected four strategies, actual registry additionally contains all_frames. Existing test/registry mismatch; no fix made. Warning: Starlette TestClient/httpx deprecation. Full output: analysis/phase0-baseline/pytest.log. These frame/worker tests do not prove quiz-generation or narrative rendering end-to-end.
- Not exercised: live source providers, LLM/API billing, actual Tamil TTS, Whisper model inference, quiz-generation end-to-end, narrative rendering, browser/mobile flows, multi-device recovery or SQL Server. No claim of readiness for these behaviors.

## 5. Risks and smallest safe adaptations

1. **Persistent metadata is not durable execution.** Acquisition item retry exists, but top-level research/video jobs use in-memory executors; no complete scheduling, priorities, lease recovery or workflow idempotency is established. Adapt the queue pattern with atomic claims and restart reconciliation before feed jobs.
2. **Source trust/provenance gaps.** Add separate event/publication/update/retrieval timestamps, source category and passage identifiers. Hash equality is not independent corroboration; syndicated grouping requires its own output.
3. **Extraction order conflicts with master.** Add subtitle-first selection in the Defence adapter; use audio/video only as necessary. Preserve original QVault extraction behavior.
4. **Silent analysis failure/truncation.** Validate adapter outputs against schemas; report malformed/empty JSON, truncation and partial source failures explicitly. Do not treat LLM confidence as verification.
5. **Search reuse was understated in older audit.** Web adapters exist, but HTML result scraping is not a configured search API guarantee. Preserve provider failures, validate chosen adapters against real sources at Phase 1 acceptance, and avoid copying education filters.
6. **Audio alignment evidence.** Timeline falls back to proportional word timings when a provider supplies none; cache does not label that origin. Edge extends word ends to following starts and the final audio duration. Add timing provenance and measured alignment later; a last-end/duration match alone is not proof of alignment quality.
7. **Tamil/video limits.** Fonts present are Poppins only; Tamil glyph/shaping support and voice pronunciation are unverified. Existing scene types are quiz-specific. Use a renderer adapter and isolated narrative extension, not a replacement renderer.
8. **Credentials/access.** Current provider settings are global env values; research session model lacks a user ownership field. Module RBAC alone is insufficient evidence of job/user isolation. Per-user secret encryption and job/export ownership need explicit implementation/tests.
9. **Concurrency/versioning.** Shared TTS cache writes, fixed outputs, Whisper newest-file selection, and source reloads need idempotent/versioned artifacts. Never share original runtime folders with Defence workers.
10. **Document drift.** QVault master contains obsolete raw-sqlite3 research claims despite newer SQLAlchemy models and maintenance note. AGENTS specifies backend 8005 while CLAUDE still says 8004. Use actual launcher/proxy settings when running; do not modify existing ports. Old Defence phase numbering is historical.

## 6. Proposed integration contracts — design only

Use an additive versioned envelope: schema_version, job_id, artifact_id/version, producer/component_version, input artifact references and hashes, status, timestamps, warnings/errors and output references. SQLite owns lifecycle/leases/approvals; manifests mirror it. Per-job immutable artifact versions preserve provenance. No new runtime model is implemented by this report.

| Boundary | Proposed input → output | Required semantics |
|---|---|---|
| Foundation | JobCreate.v1 → JobState.v1 + stage records | user ownership, stable identity, idempotency key, atomic claim, transition log, restart handling; explicit validation/conflict errors |
| Feed/search | filters/topic/time range/provider config refs → SourceCollection.v1 | original URL/title/language, source identity/category and dates, provider failures; bounded retries and deduped fetch identity |
| Extraction | source + extraction policy → ExtractedContent.v1 | raw artifact/hash, normalized passages with IDs, extraction method/version, warnings; subtitle-first; no secrets |
| Analysis | normalized collection + duplicate groups → ResearchPackage.v1 | claims and supporting/contradicting/inconclusive passage links, timeline, conflicts/gaps; no approval implied |
| Review | package hash/version + reviewer decisions → Approval.v1 | exact immutable version, reviewer/time, excluded/unverified policy; dependent invalidation only |
| Later stages | approved package → script → scenes → assigned assets → finished audio/alignment → render → QC/export | implement only in master order; each boundary documents validation, retry/idempotency, side effects, dependencies and contract tests |

Preserve existing AcquisitionDocument, ExtractionResult and SynthesisResult through adapters; do not silently change their meaning. Exported schemas get explicit versions rather than relying on unversioned Python dataclasses.

## 7. Ordered backlog and proposed ownership

No agent has been dispatched. Proposed ownership is subject to Phase 0 review; each later task must claim exact files before editing. All implementation paths below are proposed under E:/Jana Soft/DefenceStudio, never the original QVault or Whisper trees. Reuse existing project.md/project_status.md for coordination after review instead of creating a competing task-board system.

| Order | Scope / gate | Proposed owner and boundary |
|---|---|---|
| Foundation, first within Phase 1 | SQLite workflow identity, artifacts, ownership, transitions, durable queue/recovery and settings contracts | Codex: backend/app/{models,repositories,services,core}, contracts and backend tests; no overlapping UI edits |
| 1A | Real feed adapters, coverage/failure reporting, topic selection | Codex adapters/backend; Claude frontend feed/settings components against frozen contracts |
| 1B | Topic search and source collection | Codex provider adapters; Claude search/progress UI |
| 1C | Extraction/validation/normalization/deduplication and versioned package | Codex services/contracts/tests; Claude source inspection UI |
| 1D | Evidence/timeline/conflicts and preliminary human review | Codex evidence/export backend; Claude review presentation; real topic reopen/export/partial-failure gate |
| 2 | Immutable research approval | Codex approval/invalidation/access controls; Claude review actions; exact approved-version gate |
| 3 | Original Tamil script, actual usage/budget, editing and approval | Codex script/provider/history services; Claude script editor; source-reference and budget gate |
| 4 | Ordered scene creation and voice direction | Codex scene contract; Claude scene editor; schema/version/approval gate |
| 5 | Asset discovery, rights and assignment | Codex registry/adapters; Claude assignment/review UI; provenance gate |
| 6 | Voice → audio finishing → alignment | Codex TTS/audio adapters and timing tests; Claude preview UI; actual-audio gate |
| 7 | Existing Python renderer integration | Codex renderer adapter; Claude monitor; valid output and safe retry gate |
| 8 | QC and final approval | Codex validators/export gate; Claude findings/review UI |
| 9 | Branding/publishing package | Codex versioned templates/export assembly; Claude settings/preview |
| 10 | Additional domains/formats | Only after earlier acceptance gates; configurable reuse without core redesign |

Contract edits have one owner (Codex proposed); Claude consumes approved snapshots. Integration gates include component tests, contract tests and real end-to-end verification; a passing build alone never closes a phase.

## 8. Smallest first implementation task after review

**F1: Persist one user-owned workflow job and its versioned stage/artifact references in isolated SQLite.** Establish the foundation before feed/search. Reuse QVault layering, DTO and repository patterns through a narrow documented boundary; no provider execution, renderer changes or broad repository copying in this task.

Proposed files: backend/app/contracts/jobs.py; models/workflow_job.py; repositories/workflow_job_repository.py; services/workflow_job_service.py; api/routers/jobs.py; tests for persistence, idempotency and transitions; minimal configuration/bootstrap wiring. Confirm exact names against the selected controlled-reuse bootstrap before creating files.

Acceptance: create/read job with stable ID and user ownership; same idempotency key and payload returns the same job, mismatched payload conflicts; stage transitions persisted with timestamps/errors; invalid transitions rejected; job and artifact references survive process restart; no secrets in artifacts; concurrent duplicate requests cannot create two jobs; unauthorized read/export fails; component and API/contract tests pass on isolated SQLite; source/version/hash provenance recorded for every reused file. Worker scheduling/recovery follows as the next foundation task before feed execution; F1 alone does not claim a durable queue or Phase 1 completion.

Handoff: review this audit/integration boundary first. Core master section 15 explicitly says to wait for review before implementation edits. No later phase has started.


