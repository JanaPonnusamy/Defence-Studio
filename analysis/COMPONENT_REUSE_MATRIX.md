# DefenceStudio — read-only discovery

Date: 2026-10-10 (Asia/Calcutta). Sources: W = `D:\VBDOTNET\whisper_transcription`; Q = `E:\QVault`. Destination root: `E:\Jana Soft\DefenceStudio`.

# Component reuse matrix

Destinations are proposed relative to `E:\Jana Soft\DefenceStudio`. No source was copied. Bundle definitions below specify dependent files and replacement boundaries; these are adaptation bundles, not unchanged runnable copies.

| Component | Project / original path | Suitability | Required dependent files | Risks | Destination |
|---|---|---|---|---|---|
| W01 WhisperService | `D:\VBDOTNET\whisper_transcription\app\services\whisper_service.py` | Adapt | B1; direct imports in inventory | UI does not pass language; first model use may download weights; no concurrency control. | `backend/app/integrations/transcription/whisper_service.py` |
| W02 TranscriptProcessor | `D:\VBDOTNET\whisper_transcription\app\services\transcript_processor.py` | Reuse core logic | B1; direct imports in inventory | Consumes openai-whisper dict, not QVault plain text; timestamp edge cases untested. | `backend/app/services/transcript_processor.py` |
| W03 ExportCenter | `D:\VBDOTNET\whisper_transcription\app\services\export_center.py` | Adapt | B1; direct imports in inventory | Overwrites supplied path; no parent creation or project/version partitioning. | `backend/app/services/export_center.py` |
| W04 YouTubeService + MetadataController | `D:\VBDOTNET\whisper_transcription\app\services\youtube_service.py`<br>`D:\VBDOTNET\whisper_transcription\app\services\metadata_controller.py` | Alternative only | B2; direct imports in inventory | Duplicates QVault downloader; no source snapshot or retry policy. | `Not selected; prefer B2` |
| W05 MediaService + SubtitleService | `D:\VBDOTNET\whisper_transcription\app\services\media_service.py`<br>`D:\VBDOTNET\whisper_transcription\app\services\subtitle_service.py` | Adapt only if needed | B2; direct imports in inventory | Returns True, not artifact path; English-only subtitles; title-based names. | `Not selected; prefer B2` |
| W06 DownloadController | `D:\VBDOTNET\whisper_transcription\app\services\download_controller.py` | Do not copy | None; direct imports in inventory | Calls nonexistent MediaService.download_video; subtitles call omits required output_dir. | `None` |
| W07 ProjectCache + ProjectManager | `D:\VBDOTNET\whisper_transcription\app\services\project_cache.py`<br>`D:\VBDOTNET\whisper_transcription\app\services\project_manager.py` | Reference only | B4; direct imports in inventory | No schema initialization in ProjectManager; no content/version hash; current app does not wire either class. | `None; use new project model with B4` |
| W08 TimelineEngine | `D:\VBDOTNET\whisper_transcription\app\services\timeline_engine.py` | Optional reuse | B1; direct imports in inventory | Linear search; assumes ordered timeline; does not compose video scenes. | `backend/app/services/transcript_timeline.py` |
| W09 Streamlit entrypoint + state | `D:\VBDOTNET\whisper_transcription\app.py`<br>`D:\VBDOTNET\whisper_transcription\app\services\session_state_manager.py` | Reference only | B6; direct imports in inventory | Newest global MP3 selected instead of request result; editor keys differ from export state; timeline tab has no rendering block. | `None; prefer React UI` |
| W10 Streamlit UI panels | `D:\VBDOTNET\whisper_transcription\app\ui\download_center.py`<br>`D:\VBDOTNET\whisper_transcription\app\ui\dashboard.py`<br>`D:\VBDOTNET\whisper_transcription\app\ui\timeline_panel.py` | Do not copy | None; direct imports in inventory | Separate UI files do not establish working callbacks. | `None` |
| Q01 YTDLPWrapper / CookieSource | `E:\QVault\backend\app\services\yt_dlp_wrapper.py`<br>`E:\QVault\backend\app\integrations\ytdlp.py` | Adapt selected classes | B2; direct imports in inventory | English subtitle list; extraction flat flag; wrapper has no returned artifact path; integration file also contains unrelated Instagram code. | `backend/app/integrations/youtube/` |
| Q02 WhisperWrapper | `E:\QVault\backend\app\services\whisper_wrapper.py` | Adapt or replace behind B1 interface | B2; direct imports in inventory | Returns text only, discards timestamps and language info; fixed model. | `backend/app/integrations/transcription/faster_whisper_adapter.py` |
| Q03 SubtitleParser | `E:\QVault\backend\app\services\subtitle_parser.py` | Adapt | B2; direct imports in inventory | Global line dedup may erase legitimate repetition; destroys timing; not a general SRT parser. | `backend/app/services/subtitle_parser.py` |
| Q04 YouTube research source search | `E:\QVault\backend\app\services\source_search_service.py`<br>`E:\QVault\backend\app\services\search_providers\youtube_search_provider.py`<br>`E:\QVault\backend\app\models\search_source.py` | Adapt | B2; direct imports in inventory | Search results do not establish source authority; only YouTube provider in this service. | `backend/app/services/research/search/` |
| Q05 KnowledgeExtractionService + frame/OCR helpers | `E:\QVault\backend\app\services\knowledge_extraction_service.py`<br>`E:\QVault\backend\app\services\knowledge_frame_extraction_service.py`<br>`E:\QVault\backend\app\services\ocr_service.py`<br>`E:\QVault\backend\app\models\extraction_result.py` | Adapt | B2; direct imports in inventory | Only youtube_video dispatch; default cleanup deletes media/VTT/frames; constructor requires FFmpeg on PATH; OCR Tamil unverified. | `backend/app/services/research/extraction/` |
| Q06 LLMService + provider adapter | `E:\QVault\backend\app\services\llm_service.py`<br>`E:\QVault\backend\app\services\llm_providers\base_provider.py`<br>`E:\QVault\backend\app\services\llm_providers\openai_provider.py`<br>`E:\QVault\backend\app\models\llm_result.py` | Adapt | B3; direct imports in inventory | Invalid JSON becomes {}; no schema validation; provider classes share compatible API assumption; price table is historical source data. | `backend/app/services/ai/` |
| Q07 KnowledgeResearchService + prompts | `E:\QVault\backend\app\services\knowledge_research_service.py`<br>`E:\QVault\backend\app\services\knowledge_prompts.py`<br>`E:\QVault\backend\app\services\knowledge_executor.py` | Adapt | B3; direct imports in inventory | Not independent fact verification; analysis truncates to 24000 chars; no approval gate; in-process executor. | `backend/app/services/research/` |
| Q08 Research repository + models | `E:\QVault\backend\app\repositories\knowledge_research_repository.py`<br>`E:\QVault\backend\app\models\knowledge_research.py` | Adapt | B3+B4; direct imports in inventory | Master document raw-SQLite claim is stale; source uses SessionLocal and dialect-aware models; new project/revision relationships needed. | `backend/app/repositories/research.py and backend/app/models/research.py` |
| Q09 TTS protocol/registry + Edge | `E:\QVault\backend\app\integrations\tts\__init__.py`<br>`E:\QVault\backend\app\integrations\tts\edge_provider.py` | Adapt | B5; direct imports in inventory | Listed ta-IN female voice is mislabeled as accented English; availability only checks import; voice quality and service availability untested; asyncio.run assumes sync worker. | `backend/app/integrations/tts/` |
| Q10 AzureProvider | `E:\QVault\backend\app\integrations\tts\azure_provider.py` | Adapt later | B5; direct imports in inventory | No word timings; entire text escaped; no per-term language/phoneme control; configured keys not checked. | `backend/app/integrations/tts/azure_provider.py` |
| Q11 PiperProvider | `E:\QVault\backend\app\integrations\tts\piper_provider.py` | Defer | B5; direct imports in inventory | Language/gender unspecified; ignores prosody; WAV can be written to cache .mp3 path; models/binary unverified. | `Optional backend/app/integrations/tts/piper_provider.py` |
| Q12 VideoScriptService + QuestionItem | `E:\QVault\backend\app\services\video_script_service.py`<br>`E:\QVault\backend\app\services\video_source_service.py` | Adapt substantially | B5; direct imports in inventory | Quiz-specific roles/options; English hardcoded; source scanning is unsuitable for explicit approved project revisions. | `backend/app/services/script_service.py and scene_source.py` |
| Q13 VideoTimelineService / VideoTimeline | `E:\QVault\backend\app\services\video_timeline_service.py` | Adapt substantially | B5; direct imports in inventory | Quiz-specific scene events; cache omits provider model/version and has no locking; fallback word timings are estimates. | `backend/app/services/video_timeline_service.py` |
| Q14 VideoAudioService | `E:\QVault\backend\app\services\video_audio_service.py` | Adapt | B5; direct imports in inventory | Mono 44.1kHz; full-length RAM buffer; quiz ticks; no loudness target or music ducking. | `backend/app/services/video_audio_service.py` |
| Q15 VideoRenderService / template helpers | `E:\QVault\backend\app\services\video_render_service.py` | Adapt substantially | B5; direct imports in inventory | Quiz layout, two fixed orientations; no footage/map/image-generation pipeline; Tamil font coverage/shaping unverified. | `backend/app/services/video_render_service.py` |
| Q16 write_srt | `E:\QVault\backend\app\services\video_subtitle_service.py` | Reuse after timeline adaptation | B5; direct imports in inventory | Needs VideoTimeline contract; generated timing precision depends on provider/fallback. | `backend/app/services/video_subtitle_service.py` |
| Q17 FFmpeg | `E:\QVault\backend\app\integrations\ffmpeg.py` | Adapt selected methods | B2+B5; direct imports in inventory | Probe errors may become 0 duration; missing binary not covered by same catch; configured paths differ from research PATH lookup. | `backend/app/integrations/ffmpeg.py` |
| Q18 VideoGenerationService + video history | `E:\QVault\backend\app\services\video_generation_service.py`<br>`E:\QVault\backend\app\models\video.py`<br>`E:\QVault\backend\app\repositories\video_repository.py` | Adapt substantially | B4+B5; direct imports in inventory | No research/approval/language revision linkage; rereads mutable source on worker; per-process concurrency only. | `backend/app/services/video_generation_service.py plus models/repositories/video.py` |
| Q19 Acquisition worker + jobs | `E:\QVault\backend\app\core\acquisition_worker.py`<br>`E:\QVault\backend\app\models\acquisition.py`<br>`E:\QVault\backend\app\services\notification_service.py` | Adapt selected job code | B4; direct imports in inventory | Whole files include unrelated NCERT/GK/education; jobs are not a durable broker; users FK; isolate dispatch and models. | `backend/app/core/production_worker.py plus models/jobs.py` |
| Q20 AcquisitionQueueService + storage DTO | `E:\QVault\backend\app\services\acquisition_queue_service.py`<br>`E:\QVault\backend\app\repositories\acquisition_item_repository.py`<br>`E:\QVault\backend\app\integrations\acquisition\dto.py`<br>`E:\QVault\backend\app\integrations\acquisition\storage.py` | Adapt later | B4; direct imports in inventory | Not video-job recovery; exam/year paths need project/source mapping; no autonomous scheduler established. | `backend/app/services/source_queue.py plus integrations/acquisition/` |
| Q21 Research/video API routers | `E:\QVault\backend\app\api\routers\research.py`<br>`E:\QVault\backend\app\api\routers\videos.py` | Adapt | B6; direct imports in inventory | Whole app factory starts DB setup/seed; routers depend on shared schemas/auth/repos; no approval state validation. | `backend/app/api/routers/research.py and videos.py` |
| Q22 React research/video pages | `E:\QVault\frontend\src\pages\KnowledgeResearchPage.tsx`<br>`E:\QVault\frontend\src\pages\VideoGeneration.tsx` | Adapt | B6; direct imports in inventory | React auth/branding/API/types coupling; video preview is estimated timeline; no dual-language approval editor. | `frontend/src/pages/Research.tsx and VideoProject.tsx` |
| Q23 JobProgress + Modal | `E:\QVault\frontend\src\components\JobProgress.tsx`<br>`E:\QVault\frontend\src\components\Modal.tsx` | Reuse with types/CSS | B6; direct imports in inventory | Requires Bootstrap CSS and Job type; polling is owned by pages. | `frontend/src/components/` |

# Exact future adaptation bundles

Q paths below are relative to `E:\QVault`; W paths to `D:\VBDOTNET\whisper_transcription`. Targets are relative to `E:\Jana Soft\DefenceStudio`. No folder/module wildcard is permission to copy an entire project.

## B0 — new isolated configuration before Q reuse

Use Q `backend/app/config/settings.py`, `backend/app/config/knowledge_config.py`, `backend/app/shared/logging.py` as references for a new minimal settings/logger layer. Retain needed provider setting names only; point all database/storage/output/assets paths into DefenceStudio. Do not copy live .env, secrets, cookies, old connection defaults or import-time directory creation.

Extract Base/SessionLocal/session lifecycle concepts from Q `backend/app/database/session.py`, excluding migrations, unrelated model registration and original database access. Do not copy core/app.py or seed.py. Keep one new backend app package; never overlay both source app folders. These replacement interfaces are future work, not existing components.

## B1 — first timestamp/transcription/export bundle

W `app/services/whisper_service.py`, `app/services/transcript_processor.py`, `app/services/export_center.py`; optional `app/services/timeline_engine.py`. Destinations: matrix W01-W03/W08. Processor/exporter use standard library; Whisper needs openai-whisper/model/FFmpeg. Add project/revision/language-scoped output paths.

First choice: expose one word-timestamp transcription adapter based on W, and let research consume its text field. Q `backend/app/services/whisper_wrapper.py` is an alternative only after adaptation to preserve segments/words/language. Its current string cannot feed W generate_srt. Avoid loading both engines by default. Do not copy W entrypoint/controllers/state/history/UI.

## B2 — source intake/extraction

Take these Q adaptation inputs together:

- `backend/app/services/yt_dlp_wrapper.py`
- `backend/app/integrations/ytdlp.py`: CookieSource only; leave Instagram code behind.
- `backend/app/services/subtitle_parser.py`
- `backend/app/services/source_search_service.py`
- `backend/app/services/search_providers/base_search_provider.py`
- `backend/app/services/search_providers/youtube_search_provider.py`
- `backend/app/models/search_source.py`
- `backend/app/services/knowledge_extraction_service.py`
- `backend/app/services/knowledge_frame_extraction_service.py`
- `backend/app/services/ocr_service.py`
- `backend/app/models/extraction_result.py`

Requires B0 KnowledgeConfig/PIPELINE_VERSION/settings/logger, B1 transcription adapter replacing WhisperWrapper import, yt-dlp/FFmpeg and RapidOCR if OCR retained. An unchanged extraction copy would additionally require Q whisper_wrapper.py and faster-whisper; that is not the preferred first set. Destinations: backend/app/integrations/youtube, services/research, models DTOs. Return actual artifact paths, add language-aware captions, decide media retention and retain timed source evidence.

## B3 — research/AI/provenance

Q inputs:

- `backend/app/services/llm_service.py`
- `backend/app/services/llm_providers/base_provider.py`
- `backend/app/services/llm_providers/openai_provider.py`
- `backend/app/models/llm_result.py`
- `backend/app/services/knowledge_research_service.py`
- `backend/app/services/knowledge_prompts.py`
- `backend/app/services/knowledge_executor.py`
- `backend/app/repositories/knowledge_research_repository.py`
- `backend/app/models/knowledge_research.py`

Requires B0 config/database, B2 search/extraction/SearchSource, openai SDK and SQLAlchemy. Destination: services/ai, services/research, repositories/research.py, models/research.py. Add schema validation and approval revisions. Do not copy older singular knowledge_session/fact/etc model files: active repository uses consolidated knowledge_research.py. BaseLLMProvider/KnowledgeExecutor are intentional interfaces with concrete implementations, not missing features.

## B4 — jobs/history

Adapt selected definitions from Q:

- `backend/app/core/acquisition_worker.py`: submit/run/status/error logic; remove unrelated dispatch branches.
- `backend/app/models/acquisition.py`: AcquisitionJob, optionally Notification; omit NcertBook/GkSite.
- `backend/app/services/notification_service.py` plus NotificationRepository from `backend/app/repositories/acquisition_repository.py` if notifications retained.
- `backend/app/models/video.py` and `backend/app/repositories/video_repository.py` for output history.

Requires B0 Base/SessionLocal/settings/logger and B5 render dispatch. Resolve users.id FK against the new studio user/auth decision; do not silently copy all QVault auth/seed behavior. Extend output registry with project, revision and language. Destinations: core/production_worker.py, models/jobs.py, models/video.py, matching repositories.

Later optional source retry bundle: `backend/app/services/acquisition_queue_service.py`, `repositories/acquisition_item_repository.py`, `integrations/acquisition/dto.py`, `integrations/acquisition/storage.py`, and AcquisitionItem/ACQUISITION_ITEM_STATUSES from models/acquisition.py. Needs B0/Base and job FK. Replace exam/year paths with project/source layout and containment checks. Item retry bookkeeping is not render recovery or scheduling.

## B5 — coupled production bundle

Q inputs:

- `backend/app/integrations/tts/__init__.py`: contract/registry; adapt self-registration to Edge only.
- `backend/app/integrations/tts/edge_provider.py`
- `backend/app/integrations/ffmpeg.py`: probe_duration and needed helpers.
- `backend/app/services/video_script_service.py`: ScriptSegment concept; replace English quiz assembly.
- `backend/app/services/video_source_service.py`: replace QuestionItem with narrative scene DTO; do not copy recursive source scanning.
- `backend/app/services/video_timeline_service.py`
- `backend/app/services/video_audio_service.py`
- `backend/app/services/video_render_service.py`
- `backend/app/services/video_subtitle_service.py`
- `backend/app/services/video_generation_service.py`

Requires B0 config/logger, B4 job/output/notification interfaces, edge-tts, Pillow, NumPy, FFmpeg/FFprobe. Script/timeline/audio/render/SRT share DTOs: adapt together. Destinations: target integrations/tts, integrations/ffmpeg.py and services counterparts in matrix.

Asset input: Q `assets/templates/glass_dark.json` and exact dependencies `assets/fonts/Poppins-Regular.ttf`, `Poppins-Medium.ttf`, `Poppins-SemiBold.ttf`, `Poppins-Bold.ttf`; target assets equivalents. Music is null, so none required. Verify font licensing and Tamil shaping/coverage; add a suitable Tamil font later rather than assume Poppins suffices. No assets copied now.

Azure optional later: `backend/app/integrations/tts/azure_provider.py`, httpx, credential/region config and explicit registration; it lacks word events. Exclude Piper and uninspected OpenAI/Google/ElevenLabs/Kokoro initially. Copying registry unchanged would require all seven provider files; adapt registration instead.

## B6 — selected UI/API, after DTOs stabilize

First small extracts: Q `frontend/src/components/Modal.tsx`, `JobProgress.tsx`, necessary Job type from `frontend/src/types.ts`, React and Bootstrap CSS/icons.

Research page bundle: `frontend/src/pages/KnowledgeResearchPage.tsx`, `KnowledgeResearchHistoryPage.tsx`, `frontend/src/services/knowledgeResearchApi.ts`, `frontend/src/types/knowledgeResearch.ts`; named files under `frontend/src/components/knowledge-research/`: HistoryDrawer.tsx, ProgressTracker.tsx, ResearchForm.tsx, ResultsView.tsx, SettingsDrawer.tsx, FactsTable.tsx, EntitiesTable.tsx, DocumentSummaries.tsx, ConsensusPanel.tsx, format.ts. Detailed nested visual behavior is not browser-verified; keep dependencies together for later adaptation.

Video page input: `frontend/src/pages/VideoGeneration.tsx` and only needed video/job/TTS types from types.ts. Pages require interfaces from api/client.ts, auth/AuthContext.tsx, branding/BrandingContext.tsx, modules.ts, axios and React Router. Adapt those interfaces in a new shell; do not copy all app routes/modules. Destinations: frontend/src/pages/Research.tsx, VideoProject.tsx, components/services/types.

API inputs: `backend/app/api/routers/research.py`, `videos.py`, relevant DTOs in api/schemas.py and permission/session interfaces in api/deps.py. videos.py also uses repositories/user_repository.py, models/rbac.py, repositories/acquisition_repository.py and shared/security.py. Replace with the new auth contract or extract a separately reviewed minimal auth bundle; do not drop authorization or leave unresolved imports. No full core app copy.

## Order and exclusions

B0 first; B2 uses B1; B3 uses B2 and B0 persistence; B5 uses B4 job/output interfaces; B6 wraps stabilized APIs. B4 dispatch -> B5 and B5 queue -> B4 submit is current runtime coupling to handle explicitly.

Exclude databases, storage/downloads/projects/output/caches, cookies/.env, model weights, venv/node_modules/.git, unrelated Q business modules/automation, W broken controllers/scaffold/history/global-file-selection entrypoint. Do not merge requirements blindly. This is an exact adaptation-input list with deliberate new-interface boundaries, not a claim of an unchanged dependency-closed application.
