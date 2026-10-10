# DefenceStudio — read-only discovery

Date: 2026-10-10 (Asia/Calcutta). Sources: W = `D:\VBDOTNET\whisper_transcription`; Q = `E:\QVault`. Destination root: `E:\Jana Soft\DefenceStudio`.


# Integration gaps

## Source-confirmed mismatches

1. W DownloadController.download_video_360/720 calls nonexistent MediaService.download_video. Its download_subtitles omits required output_dir. Current app.py bypasses this controller, so these are reuse failures, not proof every current button fails.
2. W app.py downloads then selects newest MP3 globally, risking the wrong source; downloads return True instead of a path. Fixed transcript/export names overwrite previous outputs.
3. W editor widget keys transcript_area/subtitle_area differ from state read by export. Edits are not explicitly transferred. The final app.py creates a Timeline tab but has no tabs[2] rendering block.
4. W ProjectManager inserts projects but has no schema setup in that file. Database contents were not read, so actual table existence is unknown. ProjectManager/ProjectCache are not wired to current entrypoint.
5. Q WhisperWrapper returns plain text, incompatible with W processor's segments/words dict. A common transcription adapter is mandatory.
6. Both subtitle downloaders select English; Q SubtitleParser strips timing and globally deduplicates repeated lines. It cannot supply accurate timed narration captions.
7. Q KnowledgeExtractionService dispatches only youtube_video and deletes media/VTT/frames by default. Separate education/GK modules are not automatically part of this research pipeline. Retention must change if source assets are needed later.
8. Q current research repository uses SQLAlchemy and consolidated models. Master document's raw-sqlite3/SQLite-only description is stale.
9. Q JobProgress's active test excludes only ready/failed; completed/cancelled remain animated. Adapt terminal statuses.

## Missing product contracts

No shared defence project connecting research approval, paired language scripts and renders was found. Video references quiz source_file/question_ids. API generation queues after permission checks, without research/script revision approval. Execution permission is not content approval.

No translation/review/glossary workflow or dual-output orchestration is present in selected pipeline. Selecting Tamil voice does not translate hardcoded English quiz narration. Preserve shared factual claims/scene IDs, but give each language independent text, voice, durations, captions and exports.

No AI image generation, footage/map narrative scene composer, YouTube upload OAuth/Data API, publishAt scheduling or Analytics integration was found in selected services/integrations/routers and manifests. Existing generated images are cards/backgrounds/captions plus a thumbnail still; LLM uses chat text. yt-dlp acquisition is not publishing. Absence statements are scoped, not exhaustive whole-project assertions.

LLM evidence/confidence and consensus are not independent fact verification. _analyze_document truncates to 24000 characters. Invalid JSON can become {} and still flow as successful analysis. Add strict schema and coverage validation, source-linked claim review and human approval.

## Tamil/English risks

Edge source declares female ta-IN-PallaviNeural and en-IN-NeerjaNeural/NeerjaExpressiveNeural. These are source entries, not live service guarantees. Tamil entry is mislabeled as accented English. Base pitch plus role modulation may alter naturalness substantially. Test neutral settings, approved Tamil text with English military terms, acronyms, numbers and units. Existing plain-text synthesize contract has no structured language spans. Azure escapes all input inside one voice/prosody block, so embedded SSML text does not add per-term control. Azure provides no word timings; fallback timings are estimated.

Use a reviewed glossary with canonical English term, display form and spoken form. Maintain semantic parity across two approved scripts. Do not translate at synthesis time. Q font loading relies on template filenames, and glass_dark references Poppins only. Tamil glyph coverage, shaping, wrapping, combining marks and highlighting remain unverified; inspect actual frames later.

## Configuration, persistence and cache risks

Q settings creates folders on import; core app runs init_db/seed; database engine and schema operations are coupled to global configuration. New paths/lifecycle must precede importing adapted modules. Never copy live config values.

Research executor and acquisition worker use separate process-local pools. Persisted rows alone do not prove restart recovery. AcquisitionQueueService handles source items, not failed render recovery. Render semaphore is process-local.

Video queue records IDs but worker reloads mutable source_file by selection arguments; snapshot approved content. TTS cache includes provider/voice/rate/pitch/text, but excludes provider model/version and changes in base-pitch behavior. No locking/atomic cache write established. Add effective settings/version and concurrency handling. Model singleton concurrency unverified.

Audio is mono PCM with whole-duration RAM allocation, fixed-volume music and peak scaling, without established loudness targets or ducking. FFmpeg duration returns zero on some errors; reject invalid durations before layout. Research frame extraction uses PATH; renderer/probe use configured paths. Unify this contract.

## Scaffolds and unknowns

W download_center is heading-only; dashboard has fixed-zero counters. Q BaseLLMProvider, TTSProvider and KnowledgeExecutor are intentional contracts backed by implementations, not feature stubs. Other provider files remain behavior-unverified, not assumed scaffolds. No bugs were fixed.

## Later targeted validation

Only in the isolated new workspace: check import side effects/path isolation, actual download path, multilingual subtitles, timestamp contract, malformed-AI-schema rejection, revision approval gates, short Tamil-English terminology samples and separate English sample, Tamil frame rendering, caption/A-V synchronization, cache invalidation, worker restart, and paired outputs sharing approved research IDs. Verify package versions, binaries and asset licensing. None ran in discovery.
