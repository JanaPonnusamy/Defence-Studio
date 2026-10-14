# Self-test report - Phase R1

Date: 2026-10-13. Machine: Windows 10 Pro, Intel Core i7-4770 @ 3.4 GHz (4C/8T), 7.9 GB RAM, Python 3.13.5, Windows PowerShell 5.1.19041,
ffmpeg/ffprobe 8.1-essentials_build-www.gyan.dev (`--enable-gpl --enable-version3 --enable-libx264`, GPLv3 build) at `D:\ffmpeg\bin`.
Everything below was actually run; nothing is mocked or estimated.

## 1. pytest

`python -m pytest -q` -> **117 passed, 1 skipped, 0 failed** (~45 s).

| File | Tests | Covers |
|---|---|---|
| `test_package.py` | 37 | schema/version, unknown keys, bad/malformed sha256 (timeline, narration, asset), tampered file, 8 unsafe path forms, traversal to a real outside file, symlink escape, missing asset, READY asset missing, GAP rules, timeline rules in a package, mime/content mismatch, undecodable image, non-WAV narration, default 1920x1080@25, timeline output used, invalid profiles, timeline checksum cross-check, UTF-8 BOM, JSON-schema validity of the manifest |
| `test_motion_layout.py` | 37 | deterministic windows for every supported motion (inside source, aspect preserved), zoom/pan direction, static, unsupported-motion fallback, sanitising bad scales/anchors, letterbox never stretches (6 shapes), transition clamping, crossfade plan + unknown transitions, one-shot-per-frame coverage, first/last holds, trailing-shot drop, duration thresholds (0.4/0.6/5.0/5.1 s etc.), re-implemented timeline rules |
| `test_jobs.py` | 20 | state-machine table + 8 parametrised transitions, terminal states immutable, cancel QUEUED/RUNNING/unknown, FFMPEG_NOT_AVAILABLE fails explicitly with no output, retry rules, idempotency (same/different options/different package/no key), option validation, **restart recovery** (RUNNING -> FAILED/JOB_INTERRUPTED, partial + unverified output deleted, verified output of other jobs untouched, QUEUED re-queued, unrelated live PID not killed) |
| `test_render_real.py` | 10 | **real renders**: all QC checks, frame count 240, sha256 matches file, GAP frame pixels are the flat card (not an image), outputs never overwritten, >5 s mismatch rejected before encoding, 0.5-5 s mismatch warns and video still covers the narration, motion/transition/Tamil-overlay warnings, encoder failure (bad preset) -> ENCODER_FAILED + no output, cancel hook kills its own ffmpeg, **cancel of a running job end-to-end**, job end-to-end |
| `test_api.py` | 7 | health (AVAILABLE and NOT_AVAILABLE), full lifecycle via HTTP (idempotency, output is `video/mp4` with `ftyp`, QC, terminal cancel/retry 409, list), invalid package -> FAILED + retry, request validation / 404s, zip upload render + idempotent upload, zip-slip / garbage / no-manifest uploads |
| `test_crash_recovery.py` | 1 | **real uvicorn server killed (`Process.kill`) while ffmpeg is encoding**, restart, job FAILED/JOB_INTERRUPTED, no `output.mp4`/partial left, orphan ffmpeg gone, `/output` 409, retry creates a new job |
| `test_isolation_and_fetch.py` | 6 | AST import isolation (no Defence Studio / VVM / Audio Module / QVault / Whisper imports; only expected third-party packages), no references to sibling directories, fetch script against a local fake VVM (**GET only**, byte-exact timeline, valid package), corrupted asset rejected + cleaned up, remote host / non-empty output refused, unreachable VVM fails cleanly |

The 1 skip is the symlink-escape test: it needs symlink privilege, which this Windows account lacks (the `..`/absolute-path/resolve checks it complements are tested).

## 2. Real renders (acceptance)

Both rendered through the job system (`scripts/real_render_benchmark.py`), 1920x1080 @ 25 fps, libx264 `veryfast` crf 20 + AAC 48 kHz, one worker.
Wall time = job created -> SUCCEEDED (includes package validation, frame composition, encode, ffprobe + full decode). Memory = peak working set.

| | (a) small | (b) long |
|---|---|---|
| Package | 20 s tone WAV, 4 shots, 2 crossfades (DISSOLVE, FADE) + a CUT, caption/rights/editorial overlays, **1 GAP** shot, 6 images (landscape, portrait, JPEG/PNG) | ~10 min, **140 shots** (0.6-1.4 x mean), 4 GAP shots, 12 images, mixed motions/transitions/overlays, VVM-style synthetic timeline |
| Output | `storage\benchmarks\small\renders\r_d38b8a1fd6b84f\output.mp4` | `storage\benchmarks\long\renders\r_2388654089c64d\output.mp4` |
| ffprobe | mp4; h264 High yuv420p 1920x1080 25/1; aac 48000 Hz mono | same |
| Duration | 20.000 s (WAV 20.000 s) | 600.000 s (WAV 600.000 s) |
| Frames (decoded) | 500 = round(20 x 25) | 15000 = round(600 x 25) |
| Full decode errors | 0 | 0 |
| Size | 3.1 MB | 91.4 MB |
| **Wall time** | **23.1 s** (1.15x realtime) | **680.9 s = 11.3 min** (1.13x realtime) [composition+encode 657 s, validation 22 s] |
| **Peak memory** | renderer process 130.5 MB; ffmpeg 475.2 MB | renderer process 151.2 MB; ffmpeg 513.2 MB |
| QC checks | 11/11 | 11/11 |
| QC warnings | `UNUSED_ASSET` (3 unused images) | `TRANSITION_UNSUPPORTED` x2 codes (23 `FADE_IN` + 23 `WIPE` shots rendered as CUT) |

Re-verified **independently** with a separate `ffprobe` and `ffmpeg -v error -i out.mp4 -f null -` after the job: identical stream facts, clean decode
(full decode of the 10-minute file takes 22 s). (a) rendered twice by different runs produced the **same sha256** (`18252c41...`), i.e. output is deterministic for the same package/ffmpeg.
A frame at t=12.5 s of (a) was decoded and its pixels confirmed as the flat "VISUAL GAP" card colour; frames at 2 s (overlays) and 5.25 s (crossfade between a landscape shot and a pillarboxed portrait shot) were viewed by eye.

Not measured: other CPUs, other output sizes beyond 1920x1080/1280x720/640x360/320x180, very large source images, multiple concurrent jobs.

## 3. PowerShell 5.1 self-test

`powershell.exe -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1` -> **exit 0, PASS=16 FAIL=0 SKIP=0**:
project files; python + packages; ffmpeg + ffprobe (GPLv3 detected); full pytest (117 passed, 1 skipped); synthetic packages; `/health`;
POST `/renders` + idempotency key (202 then 200, same job); render job SUCCEEDED (500/500 frames); `/qc` 11 checks; independent ffprobe of the
downloaded MP4 (h264+aac 640x360 25 fps 20.000 s); full decode zero errors; unknown job 404; invalid package -> FAILED `INVALID_PACKAGE`
(`narration: sha256 mismatch`); cancel mid-render -> CANCELLED with the ffmpeg PID gone; **backend killed mid-render -> restart -> FAILED/JOB_INTERRUPTED,
no mp4 left, no orphan ffmpeg**; interrupted job's `/output` -> 409. The script stops only the processes it started.

## 4. Defects found and fixed during verification
* A UTF-8 BOM (written by PowerShell 5.1 `Set-Content -Encoding UTF8`) made `package.json` unreadable; manifest and timeline now accept a BOM (regression test added). The self-test's invalid-package case had passed for that wrong reason before; it now asserts the sha256-mismatch message.
* `_move(..., "CANCELLED")` could cancel a RUNNING job instantly; cancel of QUEUED jobs is now restricted to the QUEUED source state.
* `UNUSED_ASSET` produced one warning per asset; aggregated.

## 5. Verified vs not verified

Verified (by the runs above): package validation incl. traversal/sha/schema/READY-asset/GAP; motion maths and letterbox; transition clamping; duration rules (warn 0.5 s, reject 5 s); real MP4 output validity (ffprobe + full decode, frame count, +/-0.1 s duration); job states, cancel (kills its own ffmpeg), idempotency, retry, restart recovery after a hard kill; API endpoints; VVM-fetch script against a fake VVM over GET only; no forbidden imports.

Not verified: the fetch script against a **live** Video Visual Module (the VVM was not started; contract taken from its code/docs and a fake server); real VVM-exported timelines (none existed on disk, fixtures are synthetic); playback in players/browsers other than ffmpeg's decoder; audio quality (tone fixture only; mono 24 kHz WAV upsampled to 48 kHz); Linux/macOS; symlink-escape on this machine; overlay rendering with real Tamil/other scripts (explicitly unsupported).
