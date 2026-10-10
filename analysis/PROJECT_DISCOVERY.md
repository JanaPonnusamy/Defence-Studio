# DefenceStudio — read-only discovery

Date: 2026-10-10 (Asia/Calcutta). Sources: W = `D:\VBDOTNET\whisper_transcription`; Q = `E:\QVault`. Destination root: `E:\Jana Soft\DefenceStudio`.


# Project discovery

Exactly two existing projects were inspected. No broader drive-wide project search was performed.

| Project | Entrypoints | Relevant capabilities | Constraint |
|---|---|---|---|
| `D:\VBDOTNET\whisper_transcription` | source-inspected `app.py`, `requirements.txt` | Streamlit transcription, word timestamps, subtitles and exports | Current entrypoint bypasses older controllers/history/panels |
| `E:\QVault` | documented `backend/app/main.py`; inspected `backend/app/core/app.py`, `frontend/src/App.tsx` | Research provenance, LLM adapters, TTS, timed rendering, jobs/history, React | Education domain and infrastructure coupling |

## Method

Shallow listings, then Q master/todo and manifests, selected services/integrations/models/repositories, API and UI call sites. Source overrides documentation. Inventory signatures/line numbers were extracted by AST without importing application code. Historical documented runtime successes were not treated as current verification.

Q AGENTS.md was read. Its implementation workflow requires changing tracking documents and executing app/build checks; the user's explicit read-only discovery scope takes precedence. No originals or tracking documents were edited.

Excluded: .git contents, virtual environments, node_modules, build/dist, logs, media/model libraries, caches, database contents and live .env/cookies. Asset inspection was shallow template/font names plus glass_dark.json. NCERT, syllabus, education/GK business logic, Instagram and automation were excluded from implementation review. Generic acquisition queue/storage received targeted inspection for future source tracking. Other TTS provider filenames were discovered but not behavior-verified or recommended first.

## Manifests

W requirements: streamlit, openai-whisper, yt-dlp, ffmpeg-python, pandas, plotly, python-dotenv; unpinned. Selected media code uses yt-dlp's FFmpeg postprocessor, not ffmpeg-python directly.

Q backend requirements: fastapi, uvicorn[standard], pydantic>=2, pydantic-settings, SQLAlchemy>=2, pyodbc, python-multipart, PyJWT, bcrypt, yt-dlp, pycryptodomex, rapidocr-onnxruntime, curl_cffi, beautifulsoup4, PyMuPDF, pytest, httpx, openai, faster-whisper, edge-tts, Pillow, numpy, ijson. Most are unpinned; pyproject requires Python >=3.11. Declarations are not proof of installation.

Q frontend: React/ReactDOM ^18.3.1, React Router ^6.28.0, axios ^1.7.7, Bootstrap ^5.3.3, Bootstrap Icons ^1.11.3; TypeScript ^5.6.3, Vite ^5.4.11, @vitejs/plugin-react ^4.3.4. Tailwind/PostCSS also declared; inspected widgets use Bootstrap classes.

FFmpeg is an external requirement; Q also uses FFprobe. Availability/version was not checked. openai-whisper and faster-whisper have different result contracts and footprints, not a proven package conflict. Q Edge passes boundary to edge_tts.Communicate; a compatible dependency version must later be pinned/verified. No definitive version-resolution failure can be established from manifests alone.

## Source wiring

W app.py -> MetadataController/YouTubeService; direct MediaService/SubtitleService; WhisperService -> TranscriptProcessor -> ExportCenter. ProjectManager/ProjectCache/DownloadController and separate UI panels are not used by this entrypoint.

Q frontend App.tsx:59-62 registers research/video pages. Research page -> knowledgeResearchApi -> research router -> KnowledgeResearchService -> search/extraction/LLM/repository. Video page -> videos.generate -> VideoGenerationService.queue -> acquisition_worker.submit_job -> video_render branch -> run_render -> TTS/timeline -> mixdown/render/SRT/history. TTS __init__ self-imports seven providers; selecting only Edge requires adapting registration.

No applications, tests, API calls, downloads, renders, installs or migrations ran. Test filenames test_video_providers.py and test_acquisition_framework.py were located, not executed. Absence findings concern selected modules/manifests, not every file. Asset licensing and voice quality remain unverified.
