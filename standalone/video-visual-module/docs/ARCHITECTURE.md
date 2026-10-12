# Architecture

## 1. Shape

```
            HTTP (FastAPI, app/main.py)                     UI (ui/*, served at /ui)
                     |
  app/api/*  projects | assets | scenes | visual_plans/shots | jobs | costs | system
                     |
     +---------------+------------------------------+
     |  synchronous: import, maps/stats, scenes     |  asynchronous (persisted jobs): plan build, generation
     v                                              v
 app/pipeline/*                               app/jobs/queue.py (ThreadPoolExecutor, DB-backed state)
                                              app/jobs/handlers.py
                                                    |
   scene_parser -> intent -> selector -> shot_planner (+ motion) -> timeline (renderer contract)
                                  ^                 |
                                  |                 +--> generation/service.py --> AIImageProvider
                                  |                                               (local-procedural | mock-local)
                       SQLite (app/db, Alembic)  +  storage/ (files)
```

Stack: Python 3.10+ (tested on 3.13.5), FastAPI, SQLAlchemy 2 + SQLite (WAL), Alembic, Pillow, pydantic-settings, and a plain JS UI. No external services, no network calls and no FFmpeg.

## 2. Pipeline (spec §3)

| Stage | Module | What it does |
|---|---|---|
| Import | `pipeline/import_assets.py` | Content validation (Pillow full decode, PDF header/trailer, video container magic), size, dimension and decompression-bomb limits, sha256 plus per-project dedup, metadata sanitisation, URL validation, rights rules and editorial category. Writes a `source_metadata/{asset_id}.json` provenance sidecar. |
| Data graphics | `pipeline/graphics.py` | STAT_CARD / NUMBER_CALLOUT / BAR / LINE / COMPARISON / TIMELINE rendered only from structured data points (value, unit, source, as_of, confidence). Schematic maps plotted only from supplied coordinates (markers, route, region polygon, labels, ordered movements), with a lat/lon grid and no basemap or borders. Map marker and region boxes become highlight regions. |
| Scene parser | `pipeline/scene_parser.py` | Narration: paragraphs become scenes (max 8 sentences each) and sentences become segments, with timing estimated at the configured WPM (`timing_source=ESTIMATED`). Timed segments `{text,start,end[,scene]}` from an external timing contract are validated for order and overlap (`timing_source=PROVIDED`). |
| Visual intent | `pipeline/intent.py` | Deterministic keyword classifier per sentence covering the 12 spec intents, plus entity and number extraction. Figures are unit-aware (`18%` ≠ `18`). Model designations (Talon-9) and years are not statistics. Each intent maps to ranked preferred asset types and an `ai_allowed` flag. AI is allowed only if every matched intent allows it. |
| Beats | `pipeline/shot_planner.py::build_beats` | Narration-driven visual changes (spec §12). A new beat starts on an intent change, a new entity, a new figure or claim, a document or evidence reference, a map need, escalation or conclusion, or a major sentence (≥ 8 words). Short cue-less sentences stay on the current visual. |
| Selector | `pipeline/selector.py` | Priority 1 is a relevant source asset with usable rights (OPEN/LICENSED/PERMITTED; UNKNOWN only by explicit option; RESTRICTED never). Priority 2 is an existing AI illustration, only where `ai_allowed`. Otherwise it returns an explicit GAP (`SOURCE_UNAVAILABLE`/`RIGHTS_UNKNOWN`) or a generation request. Strict-relevance intents (evidence, statistic, map/location, timeline, comparison) require a specific lexical match. A statistic graphic is used only if its figure equals the narrated figure. Secondary-intent fallback (spec §14) is allowed only to specifically relevant assets. |
| AI pass | `shot_planner.plan_project` | If `generate_ai`, the cap is `max_ai_images` (default 4, hard cap 12). The hook is the first AI-allowed beat of the opening scene. Next come AI-allowed beats with no source, then beats with only non-specific b-roll, one per scene, HIGH importance first. Other illustrative beats reuse the generated images with new framing. Failures become plan warnings, never substitutions. |
| Shots + motion | `shot_planner` + `pipeline/motion.py` | A beat longer than `max_shot_seconds` (fallback only) is split into several shots of the same asset. Split points snap to sentence boundaries. Each shot takes the next entry in the asset's framing ladder (wide → slow zoom → close crop → pan → highlight region …), and consecutive shots are never identical. Crops are normalised (x,y,w,h), square in source space (aspect preserved, never stretched), and widened to include protected regions. Motions that would push a protected region out of frame are downgraded to STATIC with a recorded reason. Documents and stat graphics only get non-distorting motion. Video clips get sequential in/out segments. |
| Timeline | `pipeline/timeline.py` | Renderer-neutral JSON (schema `video-visual-timeline` v1.0) with shots, asset references, attributions and summary. `validate_timeline` must pass and the file is written and re-read from disk before the job may succeed. |
| Preview | `pipeline/preview.py` | Optional real PNG stills of a shot at t=0/1 and a contact sheet. PDFs and clips render an explicit "preview unavailable" tile. |

## 3. Generation adapter (spec §22–§24)

- `generation/base.py`: `ImageGenerationRequest`, `ImageGenerationResult`, `AIImageProvider` (`name`, `model`, `is_mock`, `estimate_cost`, `generate`) and `ProviderError` (GENERATION_FAILED / PROVIDER_TIMEOUT / PROVIDER_QUOTA).
- `generation/service.py` is the only caller of providers. It handles request validation (credential-looking prompts are rejected), the per-project budget, bounded retry for non-billable providers (`generation_max_retries`=2); billable providers are not retried by default (`billable_generation_max_retries`=0), timeouts after sending are recorded as provider_state UNCERTAIN with an UNKNOWN-cost record and are never retried or replaced by a placeholder and output verification (inside `generated/`, non-empty, decodes, claimed dimensions match). It then creates an Asset with `source_type=AI_GENERATED` and `editorial_category=AI_GENERATED`, a `GenerationRecord` and a `CostRecord`. `is_mock` propagates to all three.
- Providers: `local-procedural` is a REAL local renderer (new PNG, procedural composition, "ILLUSTRATIVE AI VISUAL - NOT EVIDENCE" band, documented synthetic cost of USD 0.01 per 1280×720 standard image). `mock-local` is a MOCK for tests (64×36 grey "MOCK" image, zero cost, `[MOCK]` title prefix). Pipeline code contains no provider-specific calls (enforced by a test).

## 4. Jobs (spec §25)

`jobs` rows hold `job_id, job_type, project_id, status, progress, stage, payload, idempotency_key, retry_of, cancel_requested, created/started/completed_at, error_code, error_message, result_id`. The statuses are QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED.

- Workers: `ThreadPoolExecutor(job_worker_threads)`. Handlers are registered by `job_type` (`build_visual_plan`, `generate_image`).
- SUCCEEDED is set only after the handler returns, and handlers return only after verifying their artefacts.
- Cancel: QUEUED becomes CANCELLED immediately. RUNNING sets `cancel_requested`, which is honoured at the next progress checkpoint.
- Startup recovery (`recover_jobs`): RUNNING jobs become FAILED/`JOB_INTERRUPTED`, their BUILDING plans become FAILED and PENDING generations become FAILED. QUEUED jobs are re-submitted.
- Terminal snapshots are written to `storage/jobs/{job_id}.json`.

## 5. Data model (SQLite, `alembic/versions/0001_initial_initial_schema.py`)

`projects`, `assets` (spec §6 fields plus mime, size, is_mock, editorial_category, extra_metadata, reuse_count), `scenes` (spec §13 plus narration_segments, timing_source, order_index), `visual_plans` (status, options, stats, warnings, timeline_json, timeline_path, error), `shots` (spec §15 plus editorial_label, intent, status, gap_code, change_reason, framing, reuse_index, overlays), `jobs`, `generation_records`, `cost_records`. Files are referenced by POSIX paths relative to `storage_root`, and every resolution is checked against the storage root.

## 6. Storage layout (spec §26), under `storage_root` (default `<module>/storage`)

```
video_visual_module.db   assets/<project>/   source_metadata/<asset>.json   generated/<project>/
maps/<project>/          graphics/<project>/ documents/<project>/            clips/<project>/
scenes/<scene>.json      plans/<plan>.json (+ contact sheets, previews/)   jobs/<job>.json
attribution/<plan>.json  logs/video_module.log (rotating, redacted)
```

## 7. Editorial safety (spec §8, §31, §34)

- Every asset and shot carries `editorial_category` / `editorial_label` ∈ {FACTUAL_EVIDENCE, ILLUSTRATIVE_VISUAL, AI_GENERATED, UNKNOWN}.
- AI imagery is always `AI_GENERATED`. An `AI_IMAGE` cannot be imported as an external source. AI shots carry a required `editorial_label` overlay, and the validator rejects any AI shot labelled evidence.
- Missing factual material yields an explicit GAP shot plus a plan warning. AI is never substituted.
- Statistics come only from structured data. A stat graphic is shown only for the same figure. Maps plot only supplied coordinates and draw no borders.
- UNKNOWN rights are never shown as open (`rights_label` text), are excluded from plans unless explicitly allowed, and are then flagged on the shot and in warnings.

## 8. Security (spec §32)

The design covers:
- **Credentials**: an optional `X-API-Key` (constant-time compare) protects every router except `/health`, `/docs` and `/ui`. Keys are read from env only. The log redaction filter covers keys, bearer tokens, `password=` and `sk-…`. Prompts containing credentials are rejected.
- **Input handling**: upload size limits are enforced while streaming. Filenames are sanitised, and storage paths are generated and verified under the storage root. URLs must be http(s) without userinfo. Metadata is size- and depth-limited and stored inert. There is no shell, eval or subprocess in `app/` (enforced by a test).
- **Database**: ORM-only queries.
- **Error responses**: these use the generic envelope. Stack traces stay in server logs and are never returned. Validation errors never echo input values.
- **HTTP headers**: `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.
- **UI**: all server data is HTML-escaped, and images are fetched as blobs so the API key never appears in URLs.
