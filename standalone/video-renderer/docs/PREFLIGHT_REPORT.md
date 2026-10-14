# Phase R1 Preflight Report

Date: 2026-10-13. Root: `D:\Jana Soft\defenceStudio\standalone\video-renderer` (authorized: build on the existing scaffold).

## Baseline (verified)

| Item | Finding |
|---|---|
| Existing root | Present, 9 files, created 2026-10-13 09:08, git-ignored (`standalone/video-renderer/`), no commits. Nothing else in the tree except empty `docs/ schemas/ storage/ scripts/ tests/`. |
| Scaffold files (kept) | `.gitignore`, `pytest.ini`, `requirements.txt`, `requirements-dev.txt`, `renderer/__init__.py`, `renderer/config.py`, `renderer/errors.py`, `scripts/__init__.py`, `tests/__init__.py` |
| Python | 3.13.5 (global; no `.venv` exists) |
| FFmpeg / ffprobe | 8.1-essentials_build-www.gyan.dev, on PATH, built `--enable-gpl --enable-version3 --enable-libx264`, no `--enable-nonfree` |
| Dependencies already installed (no install needed) | fastapi 0.139.0, uvicorn 0.48.0, sqlalchemy 2.0.51, pillow 12.2.0, python-multipart 0.0.29, pytest 9.0.3, httpx 0.28.1, jsonschema 4.26.0. `psutil` is absent and will not be installed: peak memory is read with `ctypes` (Win32 `GetProcessMemoryInfo`). |
| Environment changes permitted/required | None. No venv, no pip installs, no downloads. Tests run on the global interpreter. |
| Status audit dated 2026-10-13 | Not found on disk; the user's R1 specification and message are the requirements baseline. |

## Inputs read (Video Visual Module, read-only)

`docs/API_CONTRACTS.md` (renderer contract, timeline v1.1, motion parameters, "future renderer responsibilities"), `docs/KNOWN_LIMITATIONS.md`, `docs/LICENSING_AUDIT.md` (FFmpeg is GPLv3 incl. libx264; users should supply their own), `app/pipeline/timeline.py` and `app/pipeline/motion.py` for the exact field shapes. Nothing was imported or modified; the renderer carries its own JSON Schemas.

Contract points the renderer must honour: shots contiguous, in `order`, in seconds on the narration timeline; normalised crops (w == h, aspect preserved); motion `STATIC, SLOW_ZOOM_IN/OUT, PAN_*, CROP_FOCUS, PARALLAX (use fallback), HIGHLIGHT_REGION`; transition applied at each shot's start (CUT, DISSOLVE, FADE, FADE_IN, FADE_OUT, WIPE; duration is a renderer default); letterbox, never stretch; burn in overlays (`editorial_label`, `source_caption`, `rights_warning`); never fill `GAP` shots with imagery; verify `checksum_sha256`.

## Planned files

New (all under the root):

| Path | Scope |
|---|---|
| `schemas/video-render-package.v1.schema.json` | JSON Schema for `package.json` |
| `schemas/video-visual-timeline.v1.schema.json` | Renderer-owned structural schema of timeline v1.0/1.1 (the source module is untouched) |
| `renderer/hashing.py` | SHA-256 helpers |
| `renderer/ffmpeg_tools.py` | locate/verify ffmpeg+ffprobe, version, licence flags, ffprobe wrapper |
| `renderer/package.py` | package validation (schema, hashes, path safety, media refs, narration WAV) |
| `renderer/timeline_check.py` | timeline integrity rules |
| `renderer/plan.py` | timeline to frame plan (frames per shot, transitions, warnings) |
| `renderer/text.py` | font loading, Latin sanitiser, overlay/gap-card drawing |
| `renderer/compose.py` | deterministic Pillow frame composition (motion, letterbox, transitions) |
| `renderer/render.py` | FFmpeg pipe encoder, cancellation, progress |
| `renderer/qc.py` | ffprobe/full-decode/frame-count QC and `qc_report.json` |
| `renderer/procmem.py` | peak memory via ctypes |
| `renderer/db.py` | SQLAlchemy models + SQLite setup |
| `renderer/jobs.py` | durable job service: queue, workers, cancel, retry, idempotency, recovery |
| `renderer/api.py`, `renderer/main.py`, `renderer/__main__.py` | FastAPI app and server entry |
| `scripts/make_fixtures.py` | deterministic offline fixture generator (short + long packages) |
| `scripts/run_acceptance.py` | real renders via a real server process; records time/memory |
| `tests/*` | unit + ffmpeg-marked integration tests |
| `README.md`, `docs/CONTRACTS.md`, `docs/ARCHITECTURE.md`, `docs/KNOWN_LIMITATIONS.md`, `docs/SELF_TEST_COMMANDS.ps1`, `docs/SELF_TEST_REPORT.md` | documentation |

Modified (scaffold): `renderer/config.py` (add package roots, size limits, host), `renderer/errors.py` (extra HTTP codes). Preserved unchanged: the other seven scaffold files.

## Key design decisions

- Package is a directory; narration time == video time; mismatch measured as `|last shot end - narration duration|` (warn > 0.5 s, reject > 5 s).
- Package asset map is keyed by timeline `asset_id`; package SHA must equal the timeline asset's `checksum_sha256` when present.
- Output at `renders/<job_id>/v<N>/video.mp4`, written as `.partial` then atomically renamed after QC passes; never overwritten.
- Worker threads + SQLite as source of truth; per-job FFmpeg process handle used for cancellation (never a name-based kill).
- Unsupported media (VIDEO_CLIP, PDF) in R1 produce an explicit VISUAL GAP card plus a warning; nothing is fabricated.
