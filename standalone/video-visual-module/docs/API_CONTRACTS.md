# API Contracts — Video Visual Module v1.1.0

> **Phase 2 (v1.1.0) changes at a glance.**
> - Honest generation classification: `generation_kind` / `provenance_class`, and a new `PLACEHOLDER` category.
> - Explicit cost status: `KNOWN | UNKNOWN | NOT_APPLICABLE`.
> - An optional real AI provider, `openai-images`, with provider states.
> - Provenance and rights fields.
> - Real video and PDF previews.
> - Timing labels `ESTIMATED | SUPPLIED` with stricter validation.
> - Renderer timeline **v1.1**.
>
> All v1.0 fields remain. The changes in meaning are listed under "Compatibility notes" at the end.

Base URL: `http://127.0.0.1:8765` by default. The machine-readable contract is at `GET /openapi.json`, with interactive docs at `/docs`. This file lists only what is implemented.

## Conventions

| Topic | Contract |
|---|---|
| Versioning | API and module version `1.1.0` (`/health`, OpenAPI `info.version`). The renderer timeline carries `schema: "video-visual-timeline"` and `version: "1.1"`, and its JSON Schema is published at `GET /schemas/visual-timeline`. Image adapter contract `2.0` (`/providers`). Cost report `cost_report_version: "2.0"`. |
| IDs | UUID4 strings: `project_id`, `asset_id`, `scene_id`, `visual_plan_id`, `shot_id`, `job_id`, `generation_id`. Narration `segment_id`s are `seg-0001`… or caller-supplied (≤64 chars). |
| Time | Seconds as floats (3-decimal precision) on the narration timeline. Timestamps are ISO-8601 UTC (`…Z`). |
| Content types | JSON everywhere except `POST /assets/import` (multipart/form-data) and file and PNG responses. |
| Auth | None by default (bind to 127.0.0.1). If `VIDEO_MODULE_API_KEY` is set, every endpoint except `/health`, `/docs`, `/openapi.json` and `/ui/*` requires the header `X-API-Key: <key>`. A missing or invalid key returns `401 UNAUTHORIZED`. |
| Idempotency | `POST /visual-plans/build` and `POST /assets/generate` accept the header `Idempotency-Key`. The same key and job type returns the original job with `idempotent_replay: true` and does not start new work. `POST /assets/import` is naturally idempotent per project via sha256: the same bytes return the existing asset with `200` and `deduplicated: true`. |
| Retry | Server side, **billable providers** (`openai-images`): no automatic retries by default (`billable_generation_max_retries=0`). If raised, only "connection never established" and explicit 429 rate limits are resent; timeouts, connections dropped after sending and 5xx are never resent. A timeout is reported with `provider_state: "UNCERTAIN"` (may have been billed; cost `UNKNOWN`) and is never replaced by a placeholder. Non-billable providers (placeholder, mock): transient errors retried up to 2 more times. `PROVIDER_QUOTA` is never retried. Client side: `POST /jobs/{id}/retry` re-runs a FAILED or CANCELLED job as a new job (`retry_of` links them). |
| Headers | Every response has `X-Request-ID`. Error bodies repeat it as `request_id`. |

### Error envelope

```json
{"error_code": "ASSET_INVALID", "error_message": "File is not a valid, complete image (corrupt or unsupported).", "request_id": "3f9a1c2b7d10"}
```

Validation errors add `details: [{loc: [...], msg}]` and never echo submitted values. Stack traces are never returned.

| Code | HTTP | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 422 | Bad field or enum, or bad JSON |
| `NOT_FOUND` | 404 | Unknown id |
| `ASSET_INVALID` | 422 | Corrupt or unsupported file, bad dimensions, missing file |
| `PAYLOAD_TOO_LARGE` | 413 | Upload or metadata over the limit |
| `INVALID_URL` | 422 | `source_url` not http(s), or has embedded credentials |
| `RIGHTS_UNKNOWN` | 422 | OPEN without a licence or with an unknown source, or an open-licence claim on UNKNOWN rights. Also used as a shot `gap_code` |
| `EDITORIAL_POLICY` | 422 | AI image labelled as a source or evidence, or a variant crop cutting a protected region |
| `INVALID_STAT_DATA` | 422 | Statistic missing value/unit/source/as_of, non-numeric value, mixed units |
| `MAP_RENDER_FAILED` | 422 | Map without `data_source`, bad coordinates, point outside bbox |
| `MISSING_NARRATION_ANCHOR` | 422 | No narration, segment without text/start/end, project without scenes |
| `INVALID_TIMING` | 422 | Supplied timing malformed: non-finite, negative, zero or under 50 ms, over 600 s, more than 15 words/s, out of order, duplicate `segment_id`, or overlap over 10 ms |
| `PREVIEW_UNAVAILABLE` | 409 | No decoder available, or the file cannot be decoded (the exact reason is in the message). Never a fake image. |
| `PROVIDER_NOT_CONFIGURED` | 409 / job error | A real AI provider has no credentials, so no request was made and there was no fallback opt-in |
| `PROVIDER_AUTH_FAILED`, `PROVIDER_RESPONSE_INVALID`, `PROVIDER_CONTENT_REJECTED` | job error | Provider answered with an auth error, a malformed response, or a moderation block |
| `PROVIDER_UNAVAILABLE`, `PROVIDER_RATE_LIMITED` | job error | Network error, 5xx or 429 rate limit. Billable providers: not retried by default (see Retry). If the connection dropped after the request was sent, `provider_state` is `UNCERTAIN`. |
| `PATH_TRAVERSAL_REJECTED` | 400 | A stored path escapes module storage |
| `BUDGET_EXCEEDED` | 409 (or job error) | AI budget would be exceeded |
| `CONFLICT` | 409 | Invalid state transition (e.g. cancelling a finished job, timeline of an unbuilt plan) |
| `UNAUTHORIZED` | 401 | API key required |
| `SOURCE_UNAVAILABLE` | 410 / gap code | Stored file missing, or no usable source for a shot |
| `GENERATION_FAILED`, `PROVIDER_TIMEOUT`, `PROVIDER_QUOTA` | job error | Provider failures. `PROVIDER_TIMEOUT` from a billable provider has `provider_state: "UNCERTAIN"`: the request may have been processed and billed; it is not retried. |
| `JOB_INTERRUPTED`, `JOB_CANCELLED`, `OUTPUT_VERIFICATION_FAILED` | job error | Job lifecycle |
| `INTERNAL_ERROR` | 500 | Unexpected; see the server log by `request_id` |

## Statuses

- **Job**: `QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED`. `progress` is 0..1 and `stage` is free text. `result_id` is set only on SUCCEEDED: the `visual_plan_id` for `build_visual_plan`, the `asset_id` for `generate_image`.
- **Visual plan**: `BUILDING | BUILT | FAILED | CANCELLED`.
- **Shot**: `READY` (has an asset) or `GAP` (`asset_id: null` plus `gap_code`).
- **Generation record**: `PENDING | SUCCEEDED | FAILED`.

## Enumerations

- `asset_type`: IMAGE, VIDEO_CLIP, MAP, DIAGRAM, CHART, STAT_CARD, DOCUMENT, SATELLITE, SOURCE_PHOTO, AI_IMAGE, TITLE_CARD, TRANSITION, PLACEHOLDER_IMAGE (module-generated only; cannot be imported)
- `generation_kind`: AI_GENERATED (real model), PLACEHOLDER, TEST_FIXTURE, NOT_GENERATED
- `provenance_class` (derived): ORIGINAL_SOURCE, SYSTEM_GRAPHIC, AI_GENERATED, PLACEHOLDER, TEST_FIXTURE, UNKNOWN_ORIGIN
- `cost_status`: KNOWN (documented pricing × provider usage), UNKNOWN, NOT_APPLICABLE
- Provider `state` / `provider_state`: NOT_CONFIGURED, UNAVAILABLE, FAILED, SUCCEEDED, UNCERTAIN (added 2026-10-12: no result received but the request may have been processed/billed; the generation `status` is FAILED), plus AVAILABLE for configuration checks
- `timing_source`: ESTIMATED, SUPPLIED (was PROVIDED in v1.0; migrated)
- `source_type`: EXTERNAL_SOURCE, USER_PROVIDED, AI_GENERATED, SYSTEM_GENERATED, UNKNOWN
- `rights_status`: OPEN, LICENSED, PERMITTED, UNKNOWN, RESTRICTED
- `editorial_category` / `editorial_label`: FACTUAL_EVIDENCE, ILLUSTRATIVE_VISUAL, AI_GENERATED, PLACEHOLDER (generated placeholders only), UNKNOWN
- `intent`: GEOGRAPHIC_CONTEXT, ENTITY_INTRODUCTION, EVENT_EXPLANATION, TIMELINE, COMPARISON, STATISTIC, EVIDENCE, HARDWARE_DETAIL, MILITARY_CAPABILITY, LOCATION, ESCALATION, CONCLUSION
- `motion.type`: STATIC, SLOW_ZOOM_IN, SLOW_ZOOM_OUT, PAN_LEFT, PAN_RIGHT, PAN_UP, PAN_DOWN, CROP_FOCUS, PARALLAX, HIGHLIGHT_REGION
- `crop.composition`: wide, medium, close, subject, region
- Generation `quality`: draft, standard, high. `aspect_ratio`: 16:9, 9:16, 1:1, 4:3, 3:2, 21:9. `purpose`: HOOK, HERO, ILLUSTRATION, ATMOSPHERE, TRANSITION, CONCEPT, CONCLUSION

## Endpoints

### System
| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `{status, service, version, database}`. Always unauthenticated. |
| GET | `/diagnostics` | Versions, storage dirs, job counts by status, providers, limits, `api_key_required` (the key itself is never shown) |
| GET | `/diagnostics/logs?lines=200` | Tail of `storage/logs/video_module.log` (credential-redacted) |
| GET | `/providers` | `{default, allow_placeholder_fallback, adapter_contract_version, providers:[{name, model, is_mock, generation_kind, is_real_ai, requires_credentials, billable, state, aliases, label, description}]}` |
| GET | `/providers/{name}/status?probe=false` | Configuration check; **never generates**. With `probe=true` and a configured key, makes one non-billable `GET /v1/models/{model}`. Returns `{…describe, probe:{state, probed, detail}}`. |
| GET | `/capabilities` | `{version, previews:{video_frames, pdf_pages, basemap_tiles}, providers}`: what this install can actually do (no network) |
| GET | `/schemas/visual-timeline` | JSON Schema of the renderer contract |

### Projects
| Method | Path | Body / response |
|---|---|---|
| POST | `/projects` | `{name, description?, ai_image_budget_usd?}` → 201 `{project_id, name, description, ai_image_budget_usd, created_at}` |
| GET | `/projects` | List (newest first) |
| GET | `/projects/{project_id}` | Project plus `counts{assets, scenes, visual_plans, jobs}` |

### Assets
| Method | Path | Notes |
|---|---|---|
| POST | `/assets/import` | multipart: `project_id`, `asset_type`, `source_type`=UNKNOWN, `rights_status`=UNKNOWN, `title`, `description`, `creator`, `licence`, `attribution`, `source_url`, `duration` (VIDEO_CLIP: optional when the ffmpeg probe can read it, otherwise required), `editorial_category`, `extra_metadata` (JSON object), **v1.1:** `source_identity`, `licence_url` (http/https), `usage_rights`, `attribution_required` (bool; requires `attribution`), `source_retrieved_at` (ISO-8601, not in the future), `file`. Returns 201 for new and 200 for a dedup. |
| GET | `/assets` | Filters: `project_id, asset_type, source_type, rights_status, is_mock, generation_kind, limit, offset` |
| GET | `/assets/{asset_id}` | Asset object |
| GET | `/assets/{asset_id}/file` | Stored bytes (`nosniff`). 410 if the file is missing. |
| GET | `/assets/{asset_id}/preview?t=0&page=1&max_side=640` | **v1.1** real PNG preview: an image thumbnail, the video frame at `t` seconds (imageio-ffmpeg) or PDF page `page` (pypdfium2). Header `X-Preview-Kind`. `409 PREVIEW_UNAVAILABLE` with the reason otherwise. |
| GET | `/generations?project_id=` · `/generations/{id}` | **v1.1** generation records: `{generation_id, provider, model, generation_kind, is_real_ai, status, provider_state, attempts, error_code, error_message, request_id, provider_request_id, cost_status, estimated_cost, pre_call_estimate_usd, is_fallback, fallback_from, fallback_reason, provider_metadata, …}` |
| POST | `/assets/{asset_id}/variants` | `{crop:{x,y,w,h}, title?, note?}` → 201 new asset holding real cropped pixels. Inherits provenance and rights. `extra_metadata.variant_of`. Rejects crops that cut a protected region. |
| POST | `/assets/stats` | `{project_id, tags?, spec:{graphic_type, title, data:[{label, value, unit, source, as_of, confidence?, event?}]}}` → 201 STAT_CARD or CHART asset |
| POST | `/assets/maps` | `{project_id, tags?, spec:{title, data_source, markers?[{label,lat,lon}], route?[{lat,lon}], region?{label, polygon[{lat,lon}]}, labels?[{text,lat,lon}], movements?[{label, order, from{lat,lon}, to{lat,lon}}], bbox?, basemap?:bool}}` → 201 MAP asset. `basemap:true` uses the operator-configured, attributed tile source only if one is configured **and** network access is enabled. The outcome is recorded in `extra_metadata.basemap {used, requested, reason, attribution, zoom, projection}`; the schematic map is the fallback. |
| POST | `/assets/generate` | `{project_id, prompt, provider?, aspect_ratio?, resolution?, quality?, purpose?, scene_id?, reference_assets?, allow_placeholder_fallback?}` → 202 Job (`generate_image`). Returns `409 PROVIDER_NOT_CONFIGURED` immediately if a real AI provider has no credentials and fallback was not opted into. |
| POST | `/assets/generate/estimate` | Same body → `{provider, model, is_mock, generation_kind, is_real_ai, provider_state, billable, estimated_cost (null unless documented), estimate_status (ESTIMATED / UNKNOWN / NOT_APPLICABLE), estimate_basis, currency, budget_usd, spent_usd (known), committed_usd, unknown_cost_generations, within_budget (null if it cannot be checked)}` |

**Asset object** fields: `asset_id, project_id, asset_type, source_type, source_url, title, description, creator, licence, attribution, rights_status, rights_label, editorial_category, is_mock, provider, model, generation_cost` (KNOWN amount or null), `checksum_sha256, local_path` (relative to storage), `mime_type, file_size_bytes, width, height, duration, acquired_at, reuse_count, extra_metadata, file_url`, and `deduplicated` (import only).

**v1.1 additions:** `generation_kind, provenance_class, is_real_ai, is_placeholder, evidence_eligible, rights_usable, cost_status, is_fallback, fallback_from, fallback_reason, provider_request_id, source_identity, licence_url, usage_rights, attribution_required, source_retrieved_at, perceptual_hash` (64-bit dHash), `duplicate_of` (near-duplicate within the project).
- `evidence_eligible` is true only for ORIGINAL_SOURCE or SYSTEM_GRAPHIC assets categorised FACTUAL_EVIDENCE with OPEN/LICENSED/PERMITTED rights.
- It is never true for AI, placeholder, fixture or unknown-origin assets.

Recognised matching metadata (v1.1): `tags`, `keywords`, `aliases`, `subjects`, `location_names` (lists or strings) and `keywords_i18n` (`{"es": ["puerto"]}`). Tokens are Unicode-aware and accent-folded, and a synonym/variant table maps equivalent words (plural forms and some non-English terms) to shared concepts.

Recognised `extra_metadata` keys:
- `tags` (list or CSV string)
- `focus_point` `{x,y}`
- `regions` `[{x,y,w,h,label?,page?}]`: document or region highlights
- `protected_regions` `[{x,y,w,h,label?}]`: never cropped or moved out of frame
- `clip` `{in_point, out_point}`

All rectangles are normalised to 0..1.

### Scenes
| Method | Path | Notes |
|---|---|---|
| POST | `/scenes` | `{project_id, narration?, segments?[{text,start,end,segment_id?,scene?}], words_per_minute?, start_offset?, replace_existing?, transition?}`. Supply exactly one of `narration` or `segments`. Returns 201 `{project_id, scene_count, scenes:[Scene]}`. |
| GET | `/scenes?project_id=` | Ordered by time |
| GET | `/scenes/{scene_id}` | Scene |

**Scene** fields: `scene_id, project_id, order_index, title, start_time, end_time, duration, narration_text, narration_segments[{segment_id,text,start,end,timing_source,normalized?}], narration_segment_ids, timing_source (ESTIMATED|SUPPLIED), visual_intents[{intent, preferred_visual, fallback_visual, ai_allowed}], asset_ids, shot_ids, transition, importance, created_at`.

**Timing rules (v1.1).** Narration text gives `ESTIMATED` timing (word count at the configured WPM). Timed `segments` give `SUPPLIED` timing, used exactly as given.

Supplied segments are validated:
- A segment missing text, `start` or `end` returns `MISSING_NARRATION_ANCHOR`.
- These return `INVALID_TIMING`:
  - non-finite or negative values, or `end <= start`
  - duration under 0.05 s or over 600 s
  - more than 15 words per second (for 3 or more words)
  - a duplicate `segment_id`
  - out-of-order segments, or an overlap over 10 ms

Normalisation:
- An overlap of 10 ms or less is normalised by moving the later start to the previous end. The segment records this as `normalized`.
- Pauses between segments are allowed. The planner closes them by **holding the previous shot** (`narration_anchor.hold_extended_s`), so the visual timeline is always contiguous.

### Visual plans and shots
| Method | Path | Notes |
|---|---|---|
| POST | `/visual-plans/build` | `{project_id, scene_ids?, options?}` → 202 Job (`build_visual_plan`). Options: `generate_ai`=false, `ai_provider` (default from config), `max_ai_images`=4 (0..12), `ai_hook`=true, `ai_quality`, `ai_resolution`, `allow_unknown_rights`=false, `max_shot_seconds` (1..60), `min_shot_seconds` (0.5..30). **v1.1:** `allow_placeholder_fallback` (null = operator setting), `output_width` + `output_height` (given together), `frame_rate`. `ai_provider` defaults to `placeholder-procedural` (not AI). |
| GET | `/visual-plans?project_id=` | Plans without the timeline |
| GET | `/visual-plans/{id}?include_timeline=true` | `{visual_plan_id, project_id, job_id, scene_ids, status, options, stats, warnings, timeline_path, error_code, error_message, created_at, completed_at, timeline}` |
| GET | `/visual-plans/{id}/timeline` | Renderer contract (409 unless BUILT) |
| GET | `/visual-plans/{id}/contact-sheet` | PNG (first frame of up to 48 shots) |
| GET | `/shots/{visual_plan_id}` | `{visual_plan_id, shot_count, shots:[Shot]}` |
| GET | `/shots/{visual_plan_id}/{shot_id}/preview?t=0..1` | PNG still of the shot's crop and motion at time t |

`stats` includes `scene_count, beat_count, visual_change_count, shot_count, total_duration_s, avg_shot_duration_s, distinct_asset_count, source_asset_count, ai_asset_count, ai_images_generated_in_plan, mock_assets_used, reused_asset_count, max_shots_per_asset, fallback_duration_splits, gap_count, gaps_by_code, shots_by_visual_type, shots_by_source_type, visual_change_reasons`.

**v1.1 stats:**
- `real_ai_asset_count, placeholder_asset_count`
- `images_generated_in_plan, real_ai_images_generated_in_plan, placeholder_images_generated_in_plan, fallback_placeholders_in_plan, fixture_images_generated_in_plan`
- `timing_sources, timing_gaps_closed, timing_gap_seconds_closed`

Field meanings:
- `ai_asset_count` counts AI_GENERATED source types (real AI plus MOCK fixtures) and never placeholders.
- `ai_images_generated_in_plan` is a **deprecated name**. It equals `images_generated_in_plan` (images of any kind); use the kind-specific fields.
- `source_asset_count` excludes placeholders.

Selection rules (v1.1):
- Rights-cleared assets always rank above UNKNOWN-rights assets, however relevant the latter are. UNKNOWN rights are used only with `allow_unknown_rights` and when no cleared alternative exists, and are flagged.
- RESTRICTED assets are never selected.
- Placeholders are a last-resort tier for illustrative intents only. They are never used for factual intents (evidence, statistic, map/location, timeline, comparison).

### Renderer contract (timeline v1.1)

v1.1 adds the following to v1.0; every v1.0 field is unchanged:
- top level: `start`, `output{width, height, frame_rate, defined, note}`, `timing{sources, timing_source (ESTIMATED|SUPPLIED|MIXED), estimated, gaps_closed, gap_seconds_closed, units}`
- per shot: `frame_start` / `frame_end` (null unless `frame_rate` is defined), and `narration_anchor.timing_source` and `hold_extended_s`
- per asset: `generation_kind, provenance_class, evidence_eligible, is_fallback, fallback_from, provider, model, provider_request_id, creator, source_identity, licence_url, attribution_required, acquired_at`
- per attribution: `attribution_required, provenance_class`
- the `PLACEHOLDER` editorial label, and `PLACEHOLDER_IMAGE` as a visual type

```json
{
  "schema": "video-visual-timeline", "version": "1.1",
  "visual_plan_id": "…", "project_id": "…", "generated_at": "…Z", "start": 0.0, "duration": 621.5,
  "output": {"width": 1920, "height": 1080, "frame_rate": 30, "defined": true, "note": null},
  "timing": {"sources": ["ESTIMATED"], "timing_source": "ESTIMATED", "estimated": true, "gaps_closed": 0, "gap_seconds_closed": 0.0, "units": "seconds on the narration timeline"},
  "shots": [{
    "shot_id": "…", "scene_id": "…", "order": 0, "start": 0.0, "end": 5.5, "duration": 5.5,
    "asset_id": "…", "visual_type": "SOURCE_PHOTO", "source_type": "EXTERNAL_SOURCE",
    "editorial_label": "FACTUAL_EVIDENCE", "intent": "GEOGRAPHIC_CONTEXT", "status": "READY", "gap_code": null,
    "narration_anchor": {"segment_ids": ["seg-0001"], "text": "…", "start": 0.0, "end": 5.5, "timing_source": "ESTIMATED"},
    "change_reason": "opening hook / scene begins", "framing": "wide/SLOW_ZOOM_IN", "reuse_index": 0,
    "motion": {"type": "SLOW_ZOOM_IN", "from_scale": 1.0, "to_scale": 1.08, "anchor": {"x": 0.5, "y": 0.5}, "easing": "linear", "duration": 5.5},
    "crop": {"composition": "wide", "x": 0, "y": 0, "w": 1, "h": 1, "aspect_preserved": true, "protect_regions": ["markings"]},
    "transition": "FADE_IN", "overlays": [{"type": "source_caption", "text": "…"}],
    "attribution": "…", "editorial_reason": "Relevant legitimate source/evidence asset (priority 1)."
  }],
  "assets": {"<asset_id>": {"asset_id": "…", "asset_type": "…", "source_type": "…", "editorial_label": "…", "is_mock": false,
              "uri": "storage://assets/<project>/<file>.png", "url": "/assets/<id>/file", "mime_type": "image/png",
              "width": 1280, "height": 720, "duration": null, "title": "…", "attribution": "…", "licence": "…",
              "rights_status": "LICENSED", "rights_label": "…", "source_url": "…", "checksum_sha256": "…"}},
  "attributions": [{"asset_id": "…", "title": "…", "attribution": "…", "creator": "…", "licence": "…", "rights_label": "…", "source_url": "…", "editorial_label": "…"}],
  "summary": { "...": "plan stats" }
}
```

Motion parameters:
- `SLOW_ZOOM_IN` / `SLOW_ZOOM_OUT`: `from_scale`, `to_scale`, `anchor`
- `PAN_*`: `scale` (1.1), `from_offset`, `to_offset` (fractions of the crop size), `anchor`
- `CROP_FOCUS`: `focus_region`
- `HIGHLIGHT_REGION`: `region`, `style`, `label`
- `PARALLAX`: `layers`, `depth`, `direction`, `fallback` (a SLOW_ZOOM_IN for renderers without layer separation)
- `VIDEO_CLIP` shots: `clip_segment{in_point,out_point}`
- Downgraded motions: `downgraded_from` and `reason`

Crops are normalised to the source image with `w == h` (source aspect ratio preserved). Overlay types are `editorial_label` (required on AI shots and on PLACEHOLDER shots: "PLACEHOLDER IMAGE - NOT AI - NOT EVIDENCE"), `source_caption` and `rights_warning`. Renderers must treat `status: "GAP"` shots as missing material; filling them with AI imagery is not allowed.

`validate_timeline` (run before a plan job can succeed) rejects:
- missing fields
- non-positive or inconsistent durations
- overlaps, **gaps (> 10 ms)** and non-sequential `order`
- unknown labels or motions
- crops outside 0..1
- unresolved asset refs
- AI assets not labelled AI, or without their on-screen label
- placeholders not labelled PLACEHOLDER, or without their on-screen label
- AI, placeholder, fixture or unknown-origin assets labelled FACTUAL_EVIDENCE
- RESTRICTED assets

### Future renderer responsibilities (this module never renders, spec §0/§35)

The timeline is the complete hand-off. A renderer that consumes it must:
1. **Resolve assets** via `assets[asset_id].url` (`/assets/{id}/file`) or `uri` (`storage://…`, relative to the module storage root), and verify `checksum_sha256`.
2. **Lay out time.** Play shots in `order`, from `start` to `end` on the narration timeline (seconds; `timeline.start` may be > 0). Shots are contiguous; convert to frames at `output.frame_rate`, or use `frame_start` / `frame_end` when defined.
3. **Choose output size and frame rate** when `output.defined` is false. Crops are normalised to the source, so they apply at any output size. Letterbox rather than stretch.
4. **Realise motion and crop** declaratively (see "Motion parameters"). Ease linearly unless `easing` says otherwise. For `PARALLAX` without layer separation, use the provided `fallback`.
5. **Use VIDEO_CLIP `clip_segment` in/out points.** When the note says the clip is shorter than the shot, hold the last frame or loop.
6. **Apply `transition`** at each shot's start (CUT, DISSOLVE, FADE, FADE_IN, FADE_OUT, WIPE). Durations are a renderer default; the plan does not invent them.
7. **Burn in every overlay with `required: true`**, and show `source_caption` and `rights_warning` overlays.
8. **Never fill `GAP` shots** with AI or placeholder imagery. Surface them to an editor.
9. **Publish the `attributions` list.** Entries with `attribution_required: true` must be credited.
10. **Never present** `PLACEHOLDER`, `TEST_FIXTURE` or `AI_GENERATED` material as evidence. Do not publish placeholders (`provenance_class: PLACEHOLDER`) or mocks.

Validated end to end on the synthetic 10-minute project by `tests/test_renderer_contract.py`.

### Jobs
| Method | Path | Notes |
|---|---|---|
| GET | `/jobs?project_id&status&job_type&limit` | Newest first |
| GET | `/jobs/{job_id}` | `{job_id, job_type, project_id, status, progress, stage, created_at, started_at, completed_at, error_code, error_message, result_id, retry_of, cancel_requested, idempotency_key}` |
| POST | `/jobs/{job_id}/cancel` | QUEUED becomes CANCELLED. RUNNING sets `cancel_requested`. 409 if already terminal. |
| POST | `/jobs/{job_id}/retry` | 202 new job (FAILED or CANCELLED only, otherwise 409) |

### Costs
`GET /costs/{project_id}` (`cost_report_version: "2.0"`). Only **KNOWN** costs are summed: documented provider pricing × provider-returned usage. **UNKNOWN costs are counted and flagged, never summed as zero.** Placeholder and fixture generations are NOT_APPLICABLE and are never AI images.

- `currency, cost_basis, totals_complete, known_cost_count, unknown_cost_count, not_applicable_count`
- `ai_image_count` (**REAL AI only**), `ai_generation_cost` (sum of KNOWN), `ai_generation_cost_is_lower_bound`, `ai_generation_cost_known`
- `placeholder_image_count, fallback_placeholder_count, mock_generation_count, mock_ai_image_count`
- `failed_generation_count, failed_by_provider_state`
- `uncertain_outcome_count` (added 2026-10-12): failed generations with `provider_state: "UNCERTAIN"`. Each has an UNKNOWN-cost record with no asset, so it is included in `unknown_cost_count` and makes `totals_complete` false.
- `source_asset_count, total_visual_asset_count, assets_by_provenance_class`
- `cost_per_generated_image` (KNOWN-cost real AI images only; null when none), `cost_per_generated_image_basis`
- `finished_minutes, finished_minutes_basis, cost_per_finished_minute` (null when no real AI generation), `cost_per_finished_minute_is_lower_bound`
- `budget_usd, budget_remaining_usd, budget_remaining_is_upper_bound`
- `by_provider[{provider, count, known_cost, unknown_cost_count, not_applicable_count, is_mock, generation_kind, models}]`
- `records[{cost_record_id, provider, is_mock, model, generation_kind, is_real_ai, is_fallback, asset_id, quality, resolution, cost_status, estimated_cost, actual_cost, pre_call_estimate_usd, cost_basis, usage, currency, request_id, provider_request_id, created_at}]`. `estimated_cost` and `actual_cost` hold the KNOWN amount, or null.

## Compatibility notes (v1.0 → v1.1)

| Change | Old | New |
|---|---|---|
| Local generator | `local-procedural`, `source_type AI_GENERATED`, `asset_type AI_IMAGE`, cost 0.01/image | `placeholder-procedural` (old name still accepted), `PLACEHOLDER_IMAGE` / `SYSTEM_GENERATED` / editorial `PLACEHOLDER`, cost NOT_APPLICABLE (null). Migration 0002 reclassifies existing rows and stored timelines. |
| Cost amounts | always a float | float only when KNOWN, otherwise `null` |
| `ai_image_count`, `ai_generation_cost` | included local procedural images | real AI only |
| `timing_source` | `PROVIDED` | `SUPPLIED` (migrated) |
| Overlapping supplied segments | `MISSING_NARRATION_ANCHOR` | `INVALID_TIMING` |
| VIDEO_CLIP `duration` | always required | optional when the media probe can read it |
| Timeline `version` | `1.0` | `1.1` (additive; v1.0 fields unchanged) |
| Provider/generation error HTTP statuses | 400 default | 502/503/504/409 as listed above |

## Artifact locations (relative to `storage_root`)

| Artefact | Location |
|---|---|
| Imported files | `assets/`, `maps/`, `graphics/`, `documents/`, `clips/` |
| Generated images | `generated/<project>/` |
| Provenance sidecars | `source_metadata/<asset>.json` |
| Timeline | `plans/<plan>.json` |
| Attribution list | `attribution/<plan>.json` |
| Scene snapshots | `scenes/<scene>.json` |
| Job snapshots | `jobs/<job>.json` |
| Log | `logs/video_module.log` |

## Limitations of the contract

There is no pagination cursor (limit/offset only), no webhooks (poll `/jobs/{id}`), no delete endpoints, and no multi-user auth. See `KNOWN_LIMITATIONS.md`.
