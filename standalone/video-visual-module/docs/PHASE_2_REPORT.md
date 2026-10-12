# Phase 2 Report: Real Provider Integration and Production Readiness

Date: 2026-10-12. Module version 1.0.0 → **1.1.0**. Timeline contract 1.0 → **1.1**. Image adapter contract **2.0**. Cost report **2.0**.

Everything stated here was run in this session; see `SELF_TEST_REPORT.md` for the raw numbers.

## Baseline vs final

| | Baseline (before changes) | Final |
|---|---|---|
| pytest | 113 passed, 16.04 s | **180 tests: 178 passed, 2 skipped (opt-in paid tests), 0 failed**, 26.04 s |
| PowerShell self-test | 31/31 (v1.0 report) | **33/33 PASS, exit 0** |
| UI browser check | headless Edge screenshots (v1.0) | **38/38** automated checks, 17 screenshots |
| Benchmark (shots / changes / distinct / gaps) | 139 / 89 / 54–58 / 0 | 139 / 89 / 54–58 / 0; plan build 0.21–0.23 s (no generation), 0.48–0.51 s (4 placeholders) |

**Five existing tests were changed** because they asserted behaviour that Phase 2 deliberately removed:
- `test_generation.py`: `test_local_procedural_is_real_and_labelled` became `test_placeholder_provider_is_labelled_placeholder_not_ai`. `test_budget_exceeded_blocks_generation` now uses the billable adapter with a fake key, and the request is never sent.
- `test_jobs.py`: `test_failed_job_carries_explicit_error_and_can_retry` triggers a budget failure in the same way.
- `test_planning.py`: `test_timed_segments_contract_and_validation` now expects `SUPPLIED` and `INVALID_TIMING`.
- `test_ten_minute_plan.py`: placeholders are no longer counted as AI images, and the per-minute cost is null.

## Per-phase changes

### A. Placeholder truthfulness

- **Renamed the provider.** `local-procedural` is now `placeholder-procedural` (the old name is an alias), in `app/generation/placeholder_procedural.py`, with a compatibility shim at `local_procedural.py`.
  - Output is `asset_type PLACEHOLDER_IMAGE`, `source_type SYSTEM_GENERATED`, editorial `PLACEHOLDER` and `generation_kind PLACEHOLDER`.
  - Each image carries the band "PLACEHOLDER - NOT AI - NOT EVIDENCE" and a required on-screen overlay.
- **New classification fields** on assets, generations, cost records, timeline assets and attributions:
  - `generation_kind` (AI_GENERATED | PLACEHOLDER | TEST_FIXTURE | NOT_GENERATED)
  - `provenance_class`, `is_real_ai`, `is_placeholder`, `evidence_eligible`
- **Removed the invented rate** of USD 0.01 per image.
  - Cost status is now `KNOWN | UNKNOWN | NOT_APPLICABLE`, and amounts are null unless KNOWN.
  - The cost report sums only KNOWN costs. It counts UNKNOWN costs and flags lower and upper bounds (`totals_complete`, `*_is_lower_bound`), with separate placeholder, fallback and fixture counts.
- **Alembic migration `0002_phase2`** adds the columns, makes the cost amounts nullable, reclassifies v1.0 rows (assets, generations, cost records, shots and stored timeline JSON) and renames timing `PROVIDED` to `SUPPLIED`. It is tested against a real 0001 database.
- **Guards.** `validate_timeline` rejects placeholders mislabelled as AI or evidence, or missing their overlay. Placeholders cannot be imported. The selector uses placeholders only as a last-resort illustrative tier and never for factual intents.

### B. Optional real AI provider

- **Audit.** OpenAI official docs were read on 2026-10-12. `gpt-image-1` is marked Deprecated, so the default is **`gpt-image-2`** (configurable). The justification is in `PROVIDER_SETUP.md`.
- **Adapter** (`app/generation/openai_images.py`). It uses httpx and the REST endpoint (no SDK).
  - The key comes from env or `.env` as a `SecretStr`, is redacted from errors and is never logged.
  - It uses preset sizes and the quality mapping.
  - It records provider, model, module and provider request IDs, created timestamp, dimensions, revised prompt, usage and C2PA-chunk presence.
- **States:** `NOT_CONFIGURED`, `UNAVAILABLE`, `FAILED` and `SUCCEEDED`, plus `AVAILABLE` for configuration checks. They are stored per generation as `provider_state`.
  - New codes: `PROVIDER_NOT_CONFIGURED`, `_UNAVAILABLE`, `_AUTH_FAILED`, `_RATE_LIMITED`, `_RESPONSE_INVALID`, `_CONTENT_REJECTED`.
  - Transient failures are retried; quota and auth failures are not.
- **Cost** (`app/generation/openai_pricing.py`). Documented token rates × response `usage` gives KNOWN cost; otherwise UNKNOWN. The documented per-image table is used only for the pre-call budget estimate. Unknown-cost generations count at their estimate in the budget guard.
- **Fallback is explicit only:** the request sets `allow_placeholder_fallback`, or the operator sets `VIDEO_MODULE_ALLOW_PLACEHOLDER_FALLBACK`. Fallback output is labelled FALLBACK. Otherwise the request fails with **409 `PROVIDER_NOT_CONFIGURED`** at submit time (assets/generate and visual-plans/build).
- **New endpoints:** `GET /providers/{name}/status[?probe=true]` (never generates; the probe is `GET /v1/models/{model}` and runs only with a key), and `GET /generations`.
- **Tests:** 21 mocked-HTTP tests, plus the opt-in live test file. A conftest network guard blocks real requests.

### C. Provenance and rights

- **New fields:** `source_identity`, `licence_url` (validated), `usage_rights`, `attribution_required` (requires attribution text), `source_retrieved_at` (ISO, not in the future), `perceptual_hash` (dHash) and `duplicate_of` (near-duplicate). These sit alongside the existing sha256 dedup and `acquired_at`.
- The fields are carried into sidecars, timeline assets and attributions.
- The selector ranks rights-cleared assets above UNKNOWN-rights assets regardless of relevance. Unknown-rights selections are flagged "NOT evidence-eligible". RESTRICTED assets are never selected, and the validator also rejects them.

### D. Previews, base maps, matching

- **Tools found on this machine:** system ffmpeg/ffprobe (`D:\ffmpeg\bin`), Node, Edge 155. PyMuPDF, pypdfium2, imageio-ffmpeg and Playwright were not installed.
- **Installed into the module venv:**
  - `pypdfium2` 5.14.0 (Apache-2.0/BSD-3) and `imageio-ffmpeg` 0.6.0 (BSD-2; its bundled ffmpeg binary is LGPL/GPL), added to `requirements.txt`
  - `playwright` 1.63.0 (Apache-2.0), added to `requirements-dev.txt`
  - AGPL PyMuPDF was avoided.
- **Previews** (`app/pipeline/media_probe.py`):
  - real video frames at `t` and a container probe (width, height, fps, duration), so VIDEO_CLIP `duration` is optional when probe-able
  - real PDF page rendering and an accurate page count
  - `GET /assets/{id}/preview`, and shot previews and contact sheets now decode video and PDF
  - Anything undecodable returns `409 PREVIEW_UNAVAILABLE` with the reason. Nothing is faked.
- **Capabilities:** `GET /capabilities`, also included in `/diagnostics`.
- **Base map** (`app/basemap.py`). It needs an operator-configured XYZ tile URL, attribution and `BASEMAP_NETWORK_ENABLED=true`, and the map spec must set `basemap:true`.
  - It renders in Web Mercator and caches tiles.
  - Attribution is drawn on the map and stored on the asset.
  - It falls back to the schematic map with the reason recorded.
  - Network is off by default. It has been tested only against a mocked tile server.
- **Matching:**
  - Unicode tokens (any script) with accent folding.
  - A synonym/variant concept table, covering plurals and es/de/fr/ru terms.
  - Normalised metadata keys: `keywords`, `aliases`, `subjects`, `location_names`, `keywords_i18n`.
  - Plain-ASCII English tokens are unchanged. Benchmark shots, changes, distinct assets and gaps are unchanged; reuse went from 46 to 44–45.

### E. Timed narration

- Labels are `ESTIMATED | SUPPLIED`, shown per scene, per segment, per shot anchor and in timeline `timing`. Plans with estimated timing carry a warning.
- **Validation (`INVALID_TIMING`):**
  - non-finite or negative values, or `end <= start`
  - duration under 50 ms or over 600 s
  - more than 15 words/s
  - duplicate IDs, out-of-order segments, or overlap over 10 ms
- Missing timestamps return `MISSING_NARRATION_ANCHOR`.
- **Normalisation:** an overlap of 10 ms or less is clamped (recorded in `normalized`). Pauses are closed by holding the previous shot (`hold_extended_s`), so the timeline is always contiguous.
- **Performance:** unchanged (see the benchmark). Long narration of 360 sentences (over 30 min) is planned in under 30 s in tests.

### F. Rendering boundary

- Timeline v1.1 adds:
  - `output{width, height, frame_rate, defined}` from optional plan options, explicitly null when undefined
  - per-shot `frame_start` and `frame_end`
  - `timing`, `start`, and per-asset provenance
- The validator now also checks contiguity (no gaps over 10 ms), sequential order and the required labels.
- The future renderer's responsibilities are documented in `API_CONTRACTS.md` ("Future renderer responsibilities").
- Contract test on the 10-minute project: `tests/test_renderer_contract.py`. No rendering engine was added.

### G. UI

- The UI was updated for all of the above:
  - classification badges, a classification filter and column, provenance import fields
  - video and PDF thumbnails and detail previews
  - basemap option, fallback options, output option
  - provider state and check button, cost-status-aware estimate
  - cost report with known, unknown and not-applicable columns
  - loading and empty states
- Fixed a real console error (favicon 404).
- Automated headless Edge run: 38/38 checks. See `docs/ui_screenshots/UI_VISUAL_TEST_REPORT.md`.

### H. Final verification

- pytest 178/180 pass, with 2 opt-in skips.
- Self-test 33/33.
- The browser check passes 38/38.
- The benchmark was run 3 times plus 1.
- The isolation grep found 0 hits.
- Parent repository `git status` is unchanged from the session start.
- No git state was changed.

## Real-provider status

**Implemented but untested live.** The OpenAI Images adapter has been exercised only against mocked HTTP. No API key exists on this machine and paid calls were not authorised, so **zero real or paid provider requests were made**. Without a key, the configuration check reports `NOT_CONFIGURED` and no request is attempted.

## Cost handling (summary)

| Case | What is recorded |
|---|---|
| Real AI + usage | `KNOWN`: documented token rates × usage, with the source and retrieval date in `cost_basis` |
| Real AI, no usage / unknown model | `UNKNOWN`: amount null; counted, never summed as zero; totals flagged as bounds |
| Placeholder / mock | `NOT_APPLICABLE`: amount null; never counted as AI |

The pre-call budget estimate uses the documented per-image price only.

## Changed files

All of these are backed up in `backups/phase2-20261012_021702/`, which mirrors the relative paths and is git-ignored. pytest only collects `tests/`, and the isolation scan covers named directories only.

- **Modified:** `.gitignore`, `.env.example`, `README.md`, `requirements.txt`
  - `app/`: `config.py`, `errors.py`, `api/{assets,costs,scenes,serializers,system,visual_plans}.py`, `db/models.py`, `generation/{base,local_procedural,mock_local,registry,service}.py`, `jobs/handlers.py`, `pipeline/{graphics,import_assets,intent,preview,scene_parser,selector,shot_planner,timeline}.py`
  - `docs/`: `API_CONTRACTS.md`, `KNOWN_LIMITATIONS.md`, `SELF_TEST_COMMANDS.ps1`, `SELF_TEST_REPORT.md`
  - other: `fixtures/synthetic.py`, `scripts/benchmark_10min.py`, `tests/{conftest,test_generation,test_jobs,test_planning,test_ten_minute_plan}.py`, `ui/{app.js,index.html,styles.css}`
- **New:**
  - `alembic/versions/0002_phase2_truthfulness_provenance.py`
  - `app/basemap.py`, `app/generation/{openai_images,openai_pricing,placeholder_procedural}.py`, `app/pipeline/media_probe.py`
  - `requirements-dev.txt`, `scripts/ui_browser_check.py`
  - `tests/{test_phase2_truthfulness,test_openai_provider,test_provenance_rights,test_previews_matching,test_timing,test_renderer_contract,test_provider_live_optin}.py`
  - `docs/{PROVIDER_SETUP,PHASE_2_REPORT}.md`, `docs/ui_screenshots/*` (17 PNG, `results.json`, `UI_VISUAL_TEST_REPORT.md`)

## Reproduce

```powershell
cd "D:\Jana Soft\defenceStudio\standalone\video-visual-module"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt        # includes requirements.txt
.\.venv\Scripts\python.exe -m pytest                                      # 178 passed, 2 skipped
powershell -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1      # PASS=33, exit 0
.\.venv\Scripts\python.exe scripts\benchmark_10min.py                     # 139 shots, 0 gaps
.\.venv\Scripts\python.exe scripts\ui_browser_check.py                    # 38/38, screenshots in docs\ui_screenshots
.\.venv\Scripts\python.exe scripts\serve.py                               # http://127.0.0.1:8765/ui/
# Optional, paid, NOT run here: see docs\PROVIDER_SETUP.md (VVM_ENABLE_PAID_PROVIDER_TESTS=1 + OPENAI_API_KEY [+ VVM_LIVE_GENERATE=1])
```

## Remaining limitations

See `KNOWN_LIMITATIONS.md`. The main ones:
- The live OpenAI path is unverified.
- Prices are a dated snapshot.
- Intent classification is still English-keyword based.
- The basemap is tested only with a mocked tile server.
- Stored plan JSON files and sidecars on disk are not rewritten by the migration.
- The bundled ffmpeg binary's LGPL/GPL terms need review before redistribution.
- UI verification is automated only.
