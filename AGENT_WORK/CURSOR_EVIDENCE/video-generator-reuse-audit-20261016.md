# Video-generator reuse audit — 2026-10-16

Read-only comparison of QVault (`E:\QVault`), TTVC (`D:\VBDOTNET\TTVC`), and Defence Studio (`E:\Jana Soft\DefenceStudio`). No source edits, installs, downloads, renders, paid API calls, git mutations, `.env` reads, or DB/job writes were performed on QVault or TTVC.

**Tamil narration for Defence Studio remains BLOCKED.** This audit does not provision a Tamil provider, does not declare `DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES:ta`, and does not treat Edge/Azure Pallavi IDs as verified Defence Studio narration.

Legend: **IMPLEMENTED** (code exists) · **TESTED** (automated tests exist; this pass did not re-run foreign suites) · **REAL-OUTPUT-VERIFIED** (existing on-disk media independently probed this pass) · **PARTIAL** · **UNKNOWN**.

YouTube `https://www.youtube.com/watch?v=kwG9uCDeSk4&t=167s`: **WebFetch returned HTTP 403**. No audio/video was downloaded. **No delivery-characteristic claims** (pace/emphasis/rhythm/pauses/pitch/energy) are made for that reference.

---

## 1. Preflight (preserve work)

| Project | Git | Status this pass | Instruction files read |
|---|---|---|---|
| QVault | Yes | Uncommitted: `backend/app/integrations/gk_site_analyzer.py`, `backend/scripts/import_legacy_gk_json.py`, `frontend/src/pages/GkScraper.tsx`, `frontend/vite.config.ts`; untracked `database/qvault.db-journal`. **Not touched.** | `CLAUDE.md`, `AGENTS.md`, `PROJECT_STATUS.md` (dashboard 2026-07-27; **stale vs video engine**), `config/.env.example` keys only |
| TTVC | **Not a git repo** | Python/Tkinter tree under `D:\VBDOTNET\TTVC`. `.env` present — **not opened.** | No CLAUDE.md. Read `main.py`, `app/*` |
| Defence Studio | Yes | Unrelated untracked `AGENT_WORK/indic-parler-trial/`, Codex log files. **This pass only writes AGENT_WORK Cursor evidence + append log.** | `project_status.md`, `project.md`, audio-module / video-renderer / voice-engine READMEs, `AGENT_WORK/CURSOR.md` TTS/render evidence |

QVault `PROJECT_STATUS.md` still describes an exam-intelligence platform without a video-generation module. The **Video Generation Engine is present in source** (`video_generation_service.py`, `/api/videos`). Treat `PROJECT_STATUS.md` as outdated for this capability.

---

## 2. QVault architecture and entry points

**Stack:** Python 3.11+ FastAPI + SQLAlchemy 2 + React/Vite. Launch: `E:\QVault\launch_qvault.bat` (API :8005 per settings).

**Video generation is a quiz compositor**, not a defence-news scene pipeline. Input is question JSON (`VideoSourceService`), not an approved narrative script.

| Role | Path |
|---|---|
| HTTP generate | `E:\QVault\backend\app\api\routers\videos.py` — `POST /api/videos/generate`, `/batch`, `/preview`, `GET /tts-providers` |
| Orchestrator | `E:\QVault\backend\app\services\video_generation_service.py` — `queue` → `acquisition_jobs.job_type=video_render` → `run_render` |
| Worker | `E:\QVault\backend\app\core\acquisition_worker.py` (`video_render` branch) |
| Script (quiz English) | `E:\QVault\backend\app\services\video_script_service.py` |
| Timeline + TTS cache | `E:\QVault\backend\app\services\video_timeline_service.py` (`output/.work/tts_cache`) |
| TTS registry | `E:\QVault\backend\app\integrations\tts\__init__.py` |
| Mix | `E:\QVault\backend\app\services\video_audio_service.py` |
| Pillow→ffmpeg render | `E:\QVault\backend\app\services\video_render_service.py` |
| SRT | `E:\QVault\backend\app\services\video_subtitle_service.py` |
| ffmpeg helpers | `E:\QVault\backend\app\integrations\ffmpeg.py` |
| Templates | `E:\QVault\assets\templates\*.json` (6; default `glass_dark`) |
| UI | `frontend/src/pages/VideoGeneration.tsx` (not re-opened this pass; listed in `project.md`) |

Pipeline (`run_render`): load questions → `VideoTimelineService.build` (synthesize each script segment) → mix WAV → stream frames into ffmpeg with muxed audio → `write_srt` → thumbnail. Progress via `AcquisitionJob.progress` / `stage`. Concurrent renders gated by `video_concurrent_renders` (default 1).

**TTS providers (self-registering):**

| Provider | File | available() | Prosody | Word timings |
|---|---|---|---|---|
| **edge** (default `settings.tts_provider`) | `integrations/tts/edge_provider.py` | `import edge_tts` | rate + pitch (Edge Communicate) | **WordBoundary events** (real) |
| azure | `azure_provider.py` | key+region | SSML rate/pitch | none (empty list) |
| openai | `openai_provider.py` | `QVAULT_TTS_OPENAI_API_KEY` | rate only | none |
| elevenlabs | `elevenlabs_provider.py` | API key | none | none |
| google | `google_provider.py` | API key | rate/pitch | none |
| kokoro | `kokoro_provider.py` | `kokoro`+`soundfile` packages | ignored | none |
| piper | `piper_provider.py` | exe + model paths exist | ignored | none |

Default voice in **code** (`settings.py`): `ta-IN-PallaviNeural` with comment “Pallavi speaking the English script gives Tamil Nadu accented English”. **`.env.example`**: `QVAULT_TTS_VOICE=en-IN-NeerjaNeural`. Pallavi is a **Tamil voice ID**; using it for English quiz copy is a labeling/use-case mismatch, not proof of Tamil defence narration.

Edge curated voices include Pallavi, Neerja Expressive/Neural, Prabhat, Aria. Pallavi gets `base_pitch +12Hz`. Timeline `ROLE_MODULATION` maps intro/question/option/think/reveal/explanation/outro to rate/pitch pairs — **not an emotion API**.

**Audio:** decode via ffmpeg to mono 44.1 kHz int16; mix narration + synthesized countdown ticks + optional template BGM at `music_volume` (default 0.07); peak-normalize if `|mix|>32000`. **No loudness target (LUFS), no ducking.**

**Subtitles:** caption chunks from measured TTS word timings (or length estimates if provider returns none); on-canvas word highlight in renderer; sidecar SRT. Sync is **timeline-driven from TTS**, not forced alignment.

**Jobs:** SQLite `acquisition_jobs` + in-process `ThreadPoolExecutor`. Failed jobs store `error`. **No cancel/retry endpoints observed** on `videos.py`. Jobs **do not survive process restart** (documented in Defence Studio `project.md` from a prior file-level read; not re-proven here).

**Deps (`backend/requirements.txt`):** `edge-tts`, Pillow, numpy, fastapi, yt-dlp, openai, faster-whisper, RapidOCR, etc. **kokoro/piper/pydub/moviepy are not declared.** ffmpeg/ffprobe expected on PATH (`QVAULT_FFMPEG_PATH`).

**Tests vs real video:** tests present are ingestion/frame/auth (`test_video_providers.py` is **YouTube/Instagram VideoProvider**, not quiz TTS/render). **No TTS/timeline/render tests.** `video_fixtures.py` builds **synthetic lavfi MP4s for frame extraction**, not generated quiz videos.

**Real output this pass:** `E:\QVault\output\videos|shorts|reels` **empty**; `output/.work/tts_cache` **0 files**. **No REAL-OUTPUT-VERIFIED QVault quiz MP4.**

---

## 3. TTVC architecture and entry points

**Actual stack: Python 3 + Tkinter + Groq LLM + edge-tts + pydub + MoviePy.** Path `D:\VBDOTNET\TTVC` is historical; **no `.vbproj`/`.sln`.** Not VB.NET.

| Role | Path |
|---|---|
| Entry | `D:\VBDOTNET\TTVC\main.py` — `ensure()` then `app.ui.main_ui` |
| UI | `app/ui/main_ui.py` — Generate / Audio / Video buttons |
| LLM | `app/services/ai_service.py` (Groq; key from env loader in `app/config.py`, **`.env` not read**) |
| TTS+concat | `app/services/audio_service.py` |
| Video | `app/services/video_service.py` |
| Installer | `app/utils/installer.py` — pip install groq/edge-tts/pydub/moviepy **if the app is launched** (not launched this pass) |
| Scaffold (stale) | `setup_project.py` / `setup_project1.py` — templates that **overwrite** the tree if run; **not run** |
| Sidecar | `grok.py` (Groq math JSON UI), `raw_text_processor.py` (Ollama) — not the A/V pipeline |
| Font | `assets/fonts/Noto Sans Tamil Regular.ttf` (file exists) |

**TTS:** `VOICE_MAP` `en` → `en-IN-NeerjaNeural`, `ta` → `ta-IN-PallaviNeural`. `edge_tts.Communicate(text, voice=voice).save` — **no rate/pitch/emotion**. Tamil/English **unit dictionaries** expand km/hr etc. before TTS. Concatenate step MP3s + final with pydub → `Output/final_output.mp3`.

**Video:** MoviePy `TextClip` 1280×720, 4 s per step, Noto Sans Tamil font, `concatenate_videoclips`, `write_videofile` fps=24. **No BGM, no images/clips, no subtitles, no A/V sync to narration duration** (fixed 4 s vs real speech length).

**Jobs:** none. Threads + `IS_PROCESSING` flag only. No persistence, cancel, retry, or hashed artifacts.

**Tests:** none found.

**Real output this pass (ffprobe, no regeneration):**

| File | Evidence |
|---|---|
| `D:\VBDOTNET\TTVC\Output\final_output.mp3` | **REAL-OUTPUT-VERIFIED** container: mp3, **132.336 s**, 2 648 205 bytes |
| `step_0.mp3` … `step_5.mp3`, `final.mp3` | present; `step_0` duration **20.832 s** |
| `final_video.mp4` | **absent** — video assembly **not REAL-OUTPUT-VERIFIED** |

Speech **content/language/quality were not listened to** (no playback). Presence of MP3 ≠ Tamil-defence narration quality.

**Licensing:** edge-tts talks to Microsoft Edge neural endpoints (network, ToS not re-read). Groq is a paid/cloud LLM. MoviePy typically shells ffmpeg. Noto Sans Tamil is typically SIL OFL (file present; license file not separately opened).

---

## 4. Defence Studio equivalents (minimal read + reused AGENT_WORK)

| Layer | Path / evidence |
|---|---|
| Audio module | `standalone/audio-module/` — FastAPI, `TTSAdapter`, `local-pyttsx3` + `mock-tts`. Cursor 2026-10-15: real English WAV Hazel/Zira, 5.518 s, `is_mock` false. Pitch/emotion **not** implemented on SAPI. **Tamil BLOCKED.** |
| Voice engine | `components/voice-engine/` — `SapiModel` tempo+energy; `DEFENCE_ERA_FEMALE_001` **NOT_PROVISIONED**. Analyzer profiles pitch/rate/pauses/energy/emotion-as-INFERENCE. |
| Core RM-4 | `backend/defence_studio/services/audio.py` — `check_provider_policy`; Tamil on English-only fails closed. |
| Visual | `standalone/video-visual-module/` — timeline/assets; R1 renderer does not burn Tamil. |
| Renderer | `standalone/video-renderer/` — Pillow+ffmpeg H.264/AAC; cancel/retry/QC. Cursor: real MP4 from English WAV, QC pass. **R1: no subtitles/BGM.** Core RM-5/7: libass burn-in + optional ducked music (English chain TESTED; Tamil **font** re-verified separately; Tamil **narration BLOCKED**). |
| Alignment | `components/defence-align/` — MMS-FA CC-BY-NC; not commercial-cleared. |

Do **not** copy QVault’s quiz glassmorphism renderer over Defence Studio’s rights-aware timeline renderer.

---

## 5. Comparison matrix

| Capability | QVault impl+evidence | TTVC impl+evidence | Defence Studio equivalent | Recommended reuse | Deps/licensing/compatibility risks |
|---|---|---|---|---|---|
| Architecture | FastAPI quiz video engine **IMPLEMENTED** | Python Tkinter tutor **IMPLEMENTED** (not VB.NET) | Modular FastAPI services + standalone modules **IMPLEMENTED** | Keep DS architecture; **adapter** QVault TTS, do not merge apps | QVault RBAC/SQL vs DS owner-scoped jobs |
| Video entry | `POST /api/videos/generate` **IMPLEMENTED** | UI Video button → MoviePy **IMPLEMENTED** | RM-5 `start_render` + renderer API **IMPLEMENTED+TESTED** | None from TTVC; none of QVault quiz orchestrator | Different inputs (questions vs approved scenes) |
| Natural female TTS | Edge Pallavi/Neerja **IMPLEMENTED**; no local WAV in `output/` → **not REAL-OUTPUT-VERIFIED** | Edge Neerja/Pallavi; **MP3 REAL-OUTPUT-VERIFIED** (duration only) | SAPI Zira/Hazel English **REAL-OUTPUT-VERIFIED**; neural Tamil **BLOCKED** | **Adapter:** QVault `TTSProvider` + Edge `WordBoundary` behind DS `TTSAdapter`/`VoiceModel` | Edge is **network Microsoft**; ToS/privacy for defence text; not offline; **Tamil DS gate stays fail-closed until declared+reviewed** |
| Faster local inference | Kokoro/Piper **IMPLEMENTED** optional; **not in requirements**; availability UNKNOWN here | None local | SAPI local (English). Indic-parler trial is a **separate untracked DS AGENT_WORK path — out of this comparison’s reuse rec** | Prefer SAPI for English speed; Piper **only if** exe+model already on disk (not verified) | Kokoro/Piper extra deps; GPU UNKNOWN |
| Pitch / rate / pauses / emotion | Rate+pitch + `ROLE_MODULATION` **IMPLEMENTED**; no pause/emotion API **PARTIAL** | No modulation **IMPLEMENTED** (fixed voice) | SAPI tempo+energy **TESTED**; pitch/emotion unsupported **PARTIAL** | Port **rate/pitch mapping** into DS `controls`; keep DS pause list as post/SSML if provider supports | Edge `%`/`Hz` strings ≠ DS 0.5–2.0 tempo floats — needs a mapper |
| Mix / normalize / validate | Peak-normalize mix **IMPLEMENTED**; no LUFS **PARTIAL** | pydub concat 192k **IMPLEMENTED** | DS QC LUFS/silence **TESTED**; audio-module validate **TESTED** | Reuse DS QC; optionally QVault mix-buffer idea for BGM+ticks | numpy mix is fine; don’t import QVault settings |
| Subtitles + timestamps | WordBoundary → highlight + SRT **IMPLEMENTED**; no generated sample **not REAL-OUTPUT-VERIFIED** | None | Core libass SRT/VTT/ASS **TESTED**; renderer R1 no captions; aligner license BLOCKED | Adapter Edge timings into DS subtitle cues; keep measured-chunk fallback | Poppins in QVault templates is **not Tamil-capable**; DS already uses Nirmala.ttc |
| Assembly / transitions / BGM | Pillow glass UI + ffmpeg + optional BGM **IMPLEMENTED**; empty `output/videos` | TextClip concat **IMPLEMENTED**; **no MP4 on disk** | Renderer shots/GAP/xfade **REAL-OUTPUT-VERIFIED** (English); RM-7 ducked music **TESTED** | **Do not copy** quiz renderer or MoviePy | MoviePy vs DS ffmpeg contract; GPL ffmpeg already in DS |
| Jobs / cancel / retry | Queue+progress+fail **IMPLEMENTED**; cancel **UNKNOWN/absent** | Threads only **PARTIAL** | DS cancel/retry/INTERRUPTED **TESTED** | Keep DS workers | QVault in-process jobs lost on restart |
| Tests | Ingestion tests only **PARTIAL** | None | Broad suites + real English media **TESTED / REAL-OUTPUT-VERIFIED** | Don’t import QVault tests | — |
| Complete real video | **No MP4** | **No MP4**; MP3 only | English module-level MP4 **REAL-OUTPUT-VERIFIED**; full episode package **NOT this pass** | — | Unit tests ≠ complete episode |

---

## 6. Focus findings (operator questions)

1. **Most natural available female TTS in these two repos:** Microsoft **Edge neural** (`en-IN-NeerjaNeural` / `ta-IN-PallaviNeural`), already used by both QVault (code) and TTVC (on-disk MP3). That is **not** a new model recommendation. Quality vs SAPI Zira/Hazel is **UNKNOWN** until a human listens. **Tamil remains BLOCKED in Defence Studio** until provider declaration, fail-closed policy, a retained WAV, and Tamil-speaker review.
2. **Faster local render:** Defence Studio renderer (`veryfast` x264) + SAPI is the proven local English path. QVault streams Pillow frames at 30 fps — likely **slower** than DS for 1080p. TTVC MoviePy text clips are simpler but unproven (no MP4). Kokoro/Piper were **not** shown installed.
3. **Adapter vs copy:** Adapter only. Copying QVault quiz engine or TTVC MoviePy would duplicate workers, fight DS evidence gates, and drag Groq/edge installer side effects.
4. **Voice/audio controls worth lifting:** Edge `rate`/`pitch` + QVault `ROLE_MODULATION`; WordBoundary timings; TTVC **abbreviation expansion** maps (narrow, useful). Not: quiz countdown ticks, glassmorphism, auto-pip install.
5. **Reuse without extra deps:** `edge-tts` is the incremental dependency if Edge is authorized. ffmpeg/numpy/Pillow already in DS. Skip kokoro, piper, moviepy, pydub unless a specific gap remains after Edge adapter.

**No additional TTS model or expensive generation trial is recommended until this comparison is accepted.**

---

## 7. Best reuse candidate

**QVault Edge `TTSProvider` (plus WordBoundary + rate/pitch mapper), adapted onto Defence Studio `TTSAdapter` / `VoiceModel`, without copying the quiz renderer.**

Reasoning: it is the only **provider-neutral** TTS surface among the three with neural female voices and **measured word timings** from the synthesizer. TTVC proves Edge can emit a long MP3 on this machine (132 s) but has no jobs, no tests, auto-pip, and no video artifact. Defence Studio already has stronger jobs, QC, and English SAPI evidence; it lacks neural Indian-English/Tamil **adapters** and honest language gating must stay.

---

## 8. Unresolved questions

- Is Edge TTS ToS acceptable for defence-topic text leaving the machine?
- Live `.env` in QVault (unread) vs code default Pallavi vs example Neerja — which voice operators actually use?
- Are Kokoro/Piper binaries/models already installed outside these trees?
- What language is `TTVC\Output\final_output.mp3` (human listen)?
- QVault acquisition-job cancel/restart semantics (not fully traced beyond missing router endpoints).

---

## 9. Smallest next verification (no new model, no paid trial)

**Human listen** to existing `D:\VBDOTNET\TTVC\Output\final_output.mp3` (already on disk) and note language, naturalness, and English loanword handling. Optionally, if Edge is authorized later, one **English** Edge sentence through a **new DS adapter** with WordBoundary logged — not a Tamil production claim.

Do not run TTVC `main.py` (installer mutates the environment). Do not run QVault generate (would write output/DB).

---

## 10. Handoff for Claude (independent verify)

Scope: read-only. **Do not modify QVault or TTVC.** Do not install, download, synthesize, or declare Tamil ready.

Check: (1) `E:\QVault\output\videos` still empty and tts_cache empty; (2) TTVC `final_output.mp3` still ~132 s mp3; (3) `edge_provider.py` WordBoundary + Pallavi `base_pitch`; (4) TTVC is Python not VB.NET; (5) DS Tamil narration still BLOCKED in `project_status.md` RM-4; (6) this report’s matrix classifications; (7) YouTube reference still unevaluable unless a permitted fetch actually returns player/audio metadata — do not invent delivery stats.

Preserve QVault uncommitted GK-scraper files. Do not open `.env`. Do not overwrite this evidence file; append your log to `AGENT_WORK/CLAUDE.md` only.
