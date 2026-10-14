# Standalone Video Renderer - Phase R1 (MVP)

Renders a Video Visual timeline plus a narration WAV into a **validated MP4** (H.264 + AAC 48 kHz). Standalone: its own
source tree, SQLite DB, `storage/`, job queue, API, tests and docs. It does not import from, and does not modify, the
Video Visual Module, Audio Module, QVault, Whisper Studio or Defence Studio.

**Not supported in R1:** subtitles/captions, Tamil or other non-Latin text, music, branding, intro/outro, thumbnails,
video clips, a UI. See [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md).

## Requirements
* Windows 10+ (verified), Python 3.13, `pip install -r requirements-dev.txt` (FastAPI, SQLAlchemy, Pillow, uvicorn, python-multipart; pytest/httpx/jsonschema for tests)
* **ffmpeg + ffprobe on PATH** (or `RENDERER_FFMPEG` / `RENDERER_FFPROBE`). You supply it; it is never bundled. Verified with ffmpeg 8.1 (GPLv3 build).

## Quick start
```powershell
cd "D:\Jana Soft\defenceStudio\standalone\video-renderer"
python -m uvicorn renderer.main:app --host 127.0.0.1 --port 8780
# in another shell
curl http://127.0.0.1:8780/health
curl -X POST http://127.0.0.1:8780/renders -H "Content-Type: application/json" -d "{\"package_path\": \"D:\\\\path\\\\to\\\\package\"}"
curl http://127.0.0.1:8780/renders/<job_id>            # progress by frame
curl -o out.mp4 http://127.0.0.1:8780/renders/<job_id>/output
curl http://127.0.0.1:8780/renders/<job_id>/qc
```

### Build a package from a running Video Visual Module
```powershell
python scripts\fetch_from_vvm.py --vvm http://127.0.0.1:8765 --plan <visual_plan_id> `
    --narration D:\path\narration.wav --out storage\packages\episode1 --width 1920 --height 1080 --fps 25
```
Uses only the VVM's public GET endpoints (`/visual-plans/{id}/timeline`, `/assets/{id}/file`), checks each asset's
sha256 against the timeline, and validates the finished package. Localhost only unless `--allow-remote`.

### Make a synthetic package (no downloads) and render it
```powershell
python -c "from pathlib import Path; from renderer.synthetic import small_package; small_package(Path('storage/packages/demo'))"
python scripts\real_render_benchmark.py small     # 20 s, 1080p25, 4 shots, 2 transitions, overlays, 1 GAP
python scripts\real_render_benchmark.py long      # ~10 min, ~140 shots
```

## Package, outputs, API
All specified in [docs/CONTRACTS.md](docs/CONTRACTS.md) (package schema in [schemas/](schemas/)). In short: a folder with
`package.json` (+ sha256 of every file), `timeline.json`, `narration.wav`, `assets/`. Output goes to
`storage/renders/<job_id>/output.mp4` (never overwritten) with `qc_report.json`. A job succeeds only after ffprobe and a
full decode pass.

## Configuration (env)
`RENDERER_STORAGE_ROOT`, `RENDERER_FFMPEG`, `RENDERER_FFPROBE`, `RENDERER_FONT_PATH`, `RENDERER_TRANSITION_SECONDS` (0.5),
`RENDERER_X264_PRESET` (veryfast), `RENDERER_X264_CRF` (20), `RENDERER_AUDIO_BITRATE` (192k), `RENDERER_WORKERS` (1).

## Tests
```powershell
python -m pytest -q                                              # full suite (real ffmpeg renders; skips only if ffmpeg is absent)
python -m pytest -q -m "not ffmpeg"                              # logic-only subset
powershell -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1   # PowerShell 5.1 end-to-end self-test
```
Real results: [docs/SELF_TEST_REPORT.md](docs/SELF_TEST_REPORT.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Layout
```
renderer/   package, timeline_rules, layout, motion, compose, render, media, jobs, db, main, upload, synthetic
scripts/    fetch_from_vvm.py, real_render_benchmark.py
schemas/    video-render-package.v1.schema.json
tests/      pytest suite     docs/   contracts, architecture, limitations, self-test
storage/    runtime data (git-ignored): renderer.db, renders/, packages/, logs/
```
