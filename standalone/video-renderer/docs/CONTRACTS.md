# Contracts

Two contracts: the **input** package (`video-render-package` v1) and the **outputs** (MP4 + `qc_report.json`),
plus the HTTP API. The Video Visual timeline (`video-visual-timeline` 1.0 / 1.1) is a *consumed* contract owned by
the Video Visual Module; this renderer re-implements its validation rules (`renderer/timeline_rules.py`) and never
imports the producer's code. Machine-readable manifest schema: [`schemas/video-render-package.v1.schema.json`](../schemas/video-render-package.v1.schema.json).

## 1. `video-render-package` v1

A folder:

```
<package>/
  package.json     manifest (below)
  timeline.json    video-visual-timeline 1.0/1.1, stored byte-for-byte as exported
  narration.wav    PCM or IEEE-float WAV; its duration IS the video duration
  assets/<file>    one still image per asset_id used by READY shots (PNG / JPEG / WebP)
```

`package.json`:

```json
{
  "schema": "video-render-package",
  "version": "1",
  "timeline": "timeline.json",
  "timeline_sha256": "<64 lowercase hex>",
  "narration": { "path": "narration.wav", "sha256": "<64 hex>" },
  "assets": {
    "<asset_id>": { "path": "assets/<file>.png", "sha256": "<64 hex>", "mime_type": "image/png" }
  },
  "output_profile": { "width": 1920, "height": 1080, "fps": 25 },
  "source": { "...": "optional free-form provenance" }
}
```

| Field | Rule |
|---|---|
| `schema`, `version` | exactly `video-render-package` / `1` |
| unknown keys | rejected (allowed extras: `created_at`, `source`, `notes`) |
| paths | relative, `/` separators, no `..`, no absolute/drive paths, no `:` or NUL, must resolve (after symlinks) **inside** the package folder and exist |
| `sha256` | 64 lowercase hex, recomputed for the timeline, the narration and **every** asset; any mismatch rejects the package |
| `assets[*].mime_type` | `image/png`, `image/jpeg`, `image/webp` only (R1: still images; video clips unsupported). Pillow must decode the file and its real format must equal the declared one |
| timeline asset checksum | if the timeline's `assets[id].checksum_sha256` is present it must equal the file's sha256 |
| `output_profile` | `{width, height, fps}` or `null`. Even integers 16..7680 x 16..4320, fps 1..60. Resolution order: package profile -> timeline `output` (when width, height and frame_rate are all set) -> **default 1920x1080 @ 25 fps** |
| timeline | must pass the consumer-side rules: required fields, `schema`/`version` in {1.0, 1.1}, positive consistent durations, contiguity (10 ms), sequential `order`, known labels/motions, crop within 0..1, READY shots resolve to `assets`, GAP shots have null asset + `gap_code`, AI/placeholder labelling rules, no RESTRICTED assets |
| READY shots | each `asset_id` must be present in the manifest **and** have passed validation |
| narration | valid RIFF/WAVE, > 0.1 s |

All problems are collected and returned together (`INVALID_PACKAGE`, with `details[]`).

### How each timeline field is used

| Timeline field | R1 behaviour |
|---|---|
| `shots[].start/end` | place shots on the narration timeline; boundary frame = `round(start * fps)` (same convention as the producer's `frame_start`). First shot is held from t=0; last shot is held to the end of the narration |
| `crop {x,y,w,h}` | the base visible window (normalised to the source) |
| `motion` | `STATIC`, `SLOW_ZOOM_IN/OUT`, `PAN_LEFT/RIGHT/UP/DOWN`, `CROP_FOCUS` are rendered, linear easing, deterministic from the shot's real time. `PARALLAX`, `HIGHLIGHT_REGION` and unknown types fall back to `STATIC` with a `MOTION_FALLBACK` warning |
| `transition` | `CUT`; `FADE` and `DISSOLVE` = crossfade into the shot over a fixed duration (default 0.5 s, `RENDERER_TRANSITION_SECONDS` or per-job `options.transition_seconds`), clamped to half the shorter of the two shots. `FADE_IN`, `FADE_OUT`, `WIPE`, unknown -> `CUT` + `TRANSITION_UNSUPPORTED` warning |
| `overlays[]` | `editorial_label` (top-left, red), `source_caption` (bottom-left), `rights_warning` (above the caption, amber), text from `text`. Latin script only (see below). Other overlay types: `OVERLAY_UNSUPPORTED` warning |
| `status: GAP` | an explicit **"VISUAL GAP"** card (shot number, time range, `gap_code`). Never an image, never AI/placeholder imagery |
| `assets[].url/uri` | not used (the package carries the bytes). `checksum_sha256` is verified |
| `attributions[]` | not rendered in R1 (no credits card). Kept in the package; see KNOWN_LIMITATIONS |
| letterboxing | the visible window is scaled uniformly to fit, centred on black; never stretched |

### Narration vs timeline duration

The video always covers `[0, narration duration]`: `frames = round(duration * fps)`. With `D = |timeline end - narration|`:

| D | Result |
|---|---|
| <= 0.5 s | silent |
| > 0.5 s and <= 5 s | render, `DURATION_MISMATCH_WARNING` in the QC report (trailing shots cut, or last shot held) |
| > 5 s | job FAILED `DURATION_MISMATCH` before anything is encoded |

### Tamil / non-Latin text

**Unsupported in R1.** No font is bundled or downloaded. Overlay text whose letters are not all Latin script is *not*
drawn; the box shows `[text not rendered: non-Latin script unsupported in R1]` and the QC report carries an
`OVERLAY_NON_LATIN` warning. (Mixed text such as "Source: ஆதாரம்" is treated as non-Latin.)

## 2. Outputs

Per job: `storage/renders/<job_id>/`

| File | Meaning |
|---|---|
| `output.mp4` | exists **only** after every check passed; created by atomic rename from `output.partial.mp4`; never overwritten (a second render to the same folder is refused) |
| `qc_report.json` | schema `video-render-qc` v1 (also written, with `passed: false`, when output validation fails) |
| `ffmpeg.log` | ffmpeg stderr |

Encoding: `libx264 -preset veryfast -crf 20 -pix_fmt yuv420p` (BT.709, tv range) + `aac -ar 48000 -b:a 192k`,
`-movflags +faststart`. Configurable via `RENDERER_X264_PRESET`, `RENDERER_X264_CRF`, `RENDERER_AUDIO_BITRATE`.

### Output validation (before SUCCEEDED)

ffprobe: container is mp4, video stream h264 / yuv420p, audio stream aac @ 48 kHz, width x height as requested,
avg frame rate = fps, container duration within **+/-0.1 s** of the narration. Then a full decode
(`ffmpeg -v error -i out.mp4 -f null -`) must print **zero** errors, and the decoded frame count must equal
`round(duration * fps)` +/-1. Any failed check -> job FAILED `OUTPUT_INVALID`, partial file deleted.

### `qc_report.json`

`schema, version, renderer_version, job_id, generated_at, passed, checks[{name, ok, detail}], warnings[{code, message, count, shots[]}],
output{path, sha256, size_bytes}, requested{width, height, fps, source, frames, audio_duration, timeline_start, timeline_end},
probe{format, video, audio}, decode{frames, error_count}, package{path, manifest_sha256, shots, assets, gap_shots, visual_plan_id},
ffmpeg{ffmpeg, ffprobe, version, license_flags{gpl, version3, nonfree, libx264, libfdk_aac, summary}, configuration}, encoder{preset, crf, audio_bitrate},
timings_s{frame_composition_and_encode, output_validation, total}, memory_peak_mb{renderer_process, ffmpeg_process}`.

Warning codes: `UNUSED_ASSET`, `MOTION_FALLBACK`, `TRANSITION_UNSUPPORTED`, `TRANSITION_CLAMPED`, `DURATION_MISMATCH_WARNING`,
`SHOT_TOO_SHORT`, `OVERLAY_NON_LATIN`, `OVERLAY_UNSUPPORTED`, `FONT_FALLBACK`.

## 3. HTTP API (no UI in R1)

Errors: `{"error": {"code", "message", "details"?}}`.

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `ffmpeg: {status: AVAILABLE, ffmpeg, ffprobe, version, license_flags}` or `{status: NOT_AVAILABLE}`; startup `recovery` summary |
| POST | `/renders` | JSON `{package_path, idempotency_key?, options?{transition_seconds}}` **or** multipart `file` (package `.zip`, optional `idempotency_key` field). 202 + job (`created: true`); 200 + the existing job when the idempotency key was already used with an identical request; 409 when reused with a different request |
| GET | `/renders` | newest first (`limit`, `status`) |
| GET | `/renders/{id}` | `status, progress, stage, frames_done, frames_total, error_code, error_message, output_sha256, retry_of, cancel_requested, timestamps` (+ `output_url`, `qc_url` when SUCCEEDED) |
| POST | `/renders/{id}/cancel` | QUEUED -> CANCELLED at once; RUNNING -> `cancel_requested`, becomes CANCELLED within ~8 frames; terminal -> 409 |
| POST | `/renders/{id}/retry` | FAILED/CANCELLED only (else 409); creates a **new** job (`retry_of`) with a new output path |
| GET | `/renders/{id}/output` | `video/mp4`; 409 unless SUCCEEDED |
| GET | `/renders/{id}/qc` | the QC report; 409 if none |

Job states: `QUEUED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED` (QUEUED may also go straight to CANCELLED/FAILED);
terminal states never change. Error codes: `INVALID_PACKAGE, DURATION_MISMATCH, FFMPEG_NOT_AVAILABLE, ENCODER_FAILED,
OUTPUT_INVALID, JOB_CANCELLED, JOB_INTERRUPTED, INTERNAL_ERROR, NOT_FOUND, CONFLICT, INVALID_REQUEST`.

Security notes: `package_path` is a local path on the machine running the renderer (it is only read after the manifest
validates; nothing outside the package folder is ever opened). Uploaded zips are extracted with zip-slip, file-count
and size limits. The server is intended for localhost use; there is no authentication.
