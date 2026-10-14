# Defence Studio — Setup, Run, Test, Troubleshooting

Facts here were verified on Windows 10, Python 3.13, FFmpeg 8.1 (2026-10-14). Anything marked **unverified** has not been exercised against the real external service.

## 1. Components and ports

| Component | Location | Default port | Role |
|---|---|---|---|
| Core API + UI | `backend/`, `frontend/` | 8010 (`uvicorn defence_studio.api.app:app --app-dir backend`) | jobs, research, script, scenes, assets, QC, package, YouTube, UI |
| Video Visual Module | `standalone/video-visual-module` | 8765 | visual plan / renderer-neutral timeline |
| Audio Module | `standalone/audio-module` | 8001 | TTS + alignment |
| Video Renderer | `standalone/video-renderer` | 8780 | MP4 render (needs FFmpeg) |

The core talks to the modules **only over their public HTTP APIs** (`backend/defence_studio/integrations/`). URLs: `DEFENCE_STUDIO_VISUAL_MODULE_URL`, `DEFENCE_STUDIO_AUDIO_MODULE_URL`, `DEFENCE_STUDIO_RENDERER_URL`.

## 2. Install and run

```powershell
# core
cd backend; pip install -e ".[test]"; pip install cryptography
$env:DEFENCE_STUDIO_AUTH_SECRET = "<32+ random bytes>"
uvicorn defence_studio.api.app:app --app-dir . --host 127.0.0.1 --port 8010

# each module has its own venv/requirements (see its README); example
cd standalone\video-visual-module; pip install -r requirements.txt
$env:VIDEO_MODULE_STORAGE_ROOT = "D:\data\vvm"; python -m uvicorn app.main:app --port 8765
cd standalone\audio-module; .venv\Scripts\python -m uvicorn app.main:app --port 8001
cd standalone\video-renderer; python -m uvicorn renderer.main:app --port 8780
```

Gotcha (verified): the Audio Module's SQLite URL defaults to its own `storage/`; set both `AUDIO_MODULE_STORAGE_ROOT` and `AUDIO_MODULE_DATABASE_URL` when pointing it elsewhere.

## 3. Configuration (environment; secrets never in source control)

| Variable | Purpose |
|---|---|
| `DEFENCE_STUDIO_AUTH_SECRET` | HS256 signing secret (≥32 bytes). Required. |
| `DEFENCE_STUDIO_DB_PATH`, `DEFENCE_STUDIO_STORAGE_PATH` | SQLite file / artifact root. **Keep the storage root short on Windows** (writes beyond 240 chars fail with a clear error). |
| `DEFENCE_STUDIO_DISCOVERY_CONFIG` | feed catalogue JSON (default `backend/config/discovery_feeds.json`) |
| `DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL` / `_KEY` | hosted search provider (unconfigured by default; never live-called) |
| `DEFENCE_STUDIO_OPENAI_API_KEY` or per-owner key via `PUT /api/settings/credentials/openai` | script generation |
| `DEFENCE_STUDIO_SCRIPT_PROVIDER=offline` | explicit opt-in extractive (no-LLM) script provider |
| `DEFENCE_STUDIO_SECRET_KEY` | Fernet key encrypting stored credentials (`python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`) |
| `DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES` | declare provider→languages, e.g. `local-pyttsx3:en` (default; **no Tamil voice installed**) |
| `DEFENCE_STUDIO_SUBTITLE_FONT_FILE` / `_FAMILY` | override subtitle font (default Nirmala UI for Tamil) |
| `RENDERER_FFMPEG` / `RENDERER_FFPROBE` | FFmpeg binaries (else PATH) |
| `DEFENCE_STUDIO_YT_CLIENT_ID`, `_CLIENT_SECRET`, `_REDIRECT_URI`, `_TOKEN_KEY`, `_EXPECTED_CHANNEL_ID`, `_DAILY_QUOTA`, `_WORKER=1` | YouTube (see §6) |

## 4. Tests

```powershell
cd backend
$env:DEFENCE_STUDIO_AUTH_SECRET = "phase-one-test-signing-secret-32-bytes-minimum"
python -m pytest -q --basetemp C:\dstmp\pt     # --basetemp avoids a Windows temp-dir PermissionError
cd ..\frontend; npm test
```

Tests marked INTEGRATED skip with a printed reason unless the real modules are running (ports above). FFmpeg-dependent tests skip without FFmpeg. **Mocked-Google YouTube tests are labelled mocked and do not verify Google.**

End-to-end acceptance (real feeds + real modules, throw-away DB):
`python backend/scripts/e2e_acceptance.py Ukraine --out analysis/e2e-acceptance.json`

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `LANGUAGE_UNSUPPORTED` on audio | No voice declared for that language. Tamil needs an installed Tamil voice/provider declared via `DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES`. |
| QC `VISUAL_COVERAGE` fails | Shots have no rights-cleared asset. Upload (`POST /assets/upload`), review rights, assign to scenes, rebuild the visual timeline. |
| Render FAILED `FFMPEG_NOT_AVAILABLE` | Set `RENDERER_FFMPEG` or put FFmpeg on PATH. |
| Tamil subtitle boxes | Font lacks glyphs: QC `SUBTITLE_GLYPHS` reports the missing characters; set `DEFENCE_STUDIO_SUBTITLE_FONT_FILE`. |
| Integration rows stuck | A restart marks QUEUED/RUNNING rows `INTERRUPTED`; retry from the UI/API. |
| `alembic` ImportError starting a module | Run it from its own venv; a local `alembic/` folder shadows the package otherwise. |

## 6. YouTube (implemented, MOCK-tested, **not live-verified**)

Required one-time steps: Google Cloud project → enable YouTube Data API v3 → OAuth consent screen (+ test user `defenceerahq@gmail.com`) → Web-application OAuth client with the exact redirect URI `https://<host>/api/youtube/oauth/callback` → set the env vars above. Then in the UI connect, confirm the channel, and run one **private** test upload. Uploads are always created private; any other visibility or a schedule needs a digest-bound approval plus a typed confirmation.

## 7. Licensing and dependency notes (engineering notes, not legal advice)

* FFmpeg on the verified machine is a GPLv3 build with libx264; the system invokes it, never bundles it. Redistribution has GPL obligations.
* Nirmala UI (Windows font) is used for local Tamil rendering; do not redistribute it. Use a freely licensed Tamil font (e.g. Noto Sans Tamil) for distributed builds.
* Python deps: FastAPI, httpx, pydantic, Pillow, SQLAlchemy/alembic (modules), cryptography (Fernet). Audio module uses pyttsx3/librosa.
* Music/images: only assets with a recorded licence are accepted; unknown licence is refused.

## 8. Remaining live-verification steps (all require the owner; none can be done by the agent)

**YouTube.** Google Cloud console → project → enable *YouTube Data API v3* → OAuth consent screen (External, Testing, add `defenceerahq@gmail.com` as test user) → Credentials → *OAuth client ID* type **Web application** → Authorized redirect URI exactly `http://127.0.0.1:8010/api/youtube/oauth/callback` (local) or your HTTPS host. Set in the shell that starts the core: `DEFENCE_STUDIO_YT_CLIENT_ID`, `DEFENCE_STUDIO_YT_CLIENT_SECRET`, `DEFENCE_STUDIO_YT_REDIRECT_URI` (same value as above), `DEFENCE_STUDIO_SECRET_KEY` (Fernet key, §3), optional `DEFENCE_STUDIO_YT_EXPECTED_CHANNEL_ID`. UI: YouTube tab → Connect → confirm channel → private test upload. Testing-mode refresh tokens expire after 7 days. Scheduling and any non-private visibility require the digest-bound approval; do not approve public for the test.

**OpenAI.** Either `DEFENCE_STUDIO_OPENAI_API_KEY` or UI → Provider credentials (needs `DEFENCE_STUDIO_SECRET_KEY`). Then one script generation; the Usage section reports measured tokens/cost (provider-confirmed cost must be read from the OpenAI dashboard; request IDs are not currently persisted).

**Tamil voice.** Installed voices: en-GB, en-US only. Options: (a) Windows Settings → Time & language → Speech → add a Tamil (India) voice, if offered for your Windows build, then `DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES=local-pyttsx3:en,ta`; (b) a Tamil provider you approve (e.g. `edge-tts` is already pip-installed but sends script text to a Microsoft online service and is not integrated or approved). Tamil narration is unverified until a real voice is used and a Tamil speaker reviews it.

## 9. Gate audit notes (2026-10-14)

Provider error text is scrubbed of the configured key and key-shaped tokens. Not covered: concurrent-render isolation test, load test, redaction of logs written by the standalone modules. FFmpeg GPL/libx264 and Nirmala UI redistribution remain items for qualified legal review (§7).
