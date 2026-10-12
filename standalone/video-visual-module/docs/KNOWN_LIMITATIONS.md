# Known Limitations (v1.1.0, after Phase 2)

## Scope (by design, spec §0/§35)

- **No video rendering or compositing.** The output is the renderer-neutral timeline JSON (v1.1). The renderer's responsibilities are listed in `API_CONTRACTS.md`. PNG stills and the contact sheet are previews of crop and motion only.
- The module does not research news, verify claims, write narration, generate TTS, upload or schedule, or guarantee third-party rights.

## Image generation

- **The bundled local generator is a PLACEHOLDER, not AI.** `placeholder-procedural` (formerly `local-procedural`) draws abstract procedural art with a permanent "PLACEHOLDER - NOT AI - NOT EVIDENCE" band.
  - It is classified `PLACEHOLDER` everywhere and never counted as an AI image.
  - It has no cost (`NOT_APPLICABLE`).
- **The real AI provider (`openai-images`) is implemented but untested live.**
  - It is unit-tested against mocked HTTP only.
  - No API key was available and paid calls were not authorised, so it has never made a real request.
  - Real-world behaviour (exact response fields, `usage` presence for `gpt-image-2`, latency, moderation) is therefore unverified.
  - If `usage` is absent, the cost is recorded as `UNKNOWN`.
- **Prices are a snapshot** retrieved 2026-10-12 (`app/generation/openai_pricing.py`). The module does not fetch prices, so they must be re-verified manually.
- **Pre-call estimates** cover image output only (documented per-image table) and exclude the small prompt text-input cost. Recorded costs come only from usage × rates.
- **Request handling:**
  - `openai-images` always requests a preset size (1024², 1536×1024 or 1024×1536). A 16:9 request becomes 3:2, so the renderer crops or letterboxes.
  - `reference_assets` and `aspect_ratio` are recorded but not sent to any provider (no image-edit flow).
- **C2PA:** the adapter only detects whether a PNG carries a C2PA chunk. It does not validate manifests.
- **Retries of billable requests (fixed 2026-10-12, see `CHANGELOG.md`):**
  - Billable generation requests are no longer retried automatically (`billable_generation_max_retries` = 0).
  - A timeout or a connection drop after sending is recorded as outcome `UNCERTAIN`, never resent and never covered by placeholder fallback. Its cost is recorded as `UNKNOWN` and counted by the budget guard.
  - Remaining limitation: an UNCERTAIN request may in fact have been billed; the module cannot tell. Check the OpenAI usage dashboard (the request ID is usually unavailable because no response arrived).
  - `Retry-After` headers are still not honoured (only relevant if billable retries are explicitly enabled, and then only for 429 / connection-never-established).
- **Budget guard:**
  - It is a soft per-project check before each generation. Two concurrent generations can both pass it.
  - UNKNOWN-cost generations count at their pre-call estimate.

## Provenance and rights

- Rights are as declared by the importer. The module enforces consistency rules (UNKNOWN never open, RESTRICTED never selected, unknown never outranks cleared, attribution required ⇒ attribution present) but cannot verify a licence.
- Near-duplicate detection uses a 64-bit dHash (Hamming distance 4 or less) within one project. It flags duplicates (`duplicate_of`) and does not block them. Cropped, mirrored or heavily edited copies are not detected.
- The migration reclassified placeholder rows in the **database** (assets, generations, costs, shots and stored timeline JSON). Plan JSON files already written to `storage/plans/` and old `source_metadata/` sidecars are **not** rewritten. The API serves the corrected DB copy.

## Planning heuristics

- Intent classification is a deterministic English keyword heuristic, not an LLM. Unusual phrasing can be misclassified. Errors appear as an explicit GAP or "fallback" reason; AI is never substituted.
- Matching (v1.1) is lexical plus a fixed synonym/variant table:
  - It uses Unicode tokens and accent folding.
  - It has a small multilingual table (es/de/fr/ru terms for common defence concepts).
  - There is no stemming beyond the table, no translation, and no language detection.
  - The stop-word list and intent keywords are English-only, so non-English narration matches assets but intents mostly default to EVENT_EXPLANATION.
- The synonym change shifted `reused_asset_count` in the 10-minute benchmark from 46 to 44–45, and the value now varies slightly between runs (UUID tie-breaks). Shots (139), changes (89), distinct assets (54/58) and gaps (0) are unchanged.
- Entity extraction relies on capitalisation. Numbers written as words are not statistics.
- Estimated timing uses word count (150 WPM + 0.3 s/sentence) and is labelled `ESTIMATED` in scenes, shots, timeline and plan warnings. Use supplied segments (`SUPPLIED`) for accurate sync.
- There is no face, text or logo detection. Protected regions must be supplied.

## Media handling

- **Video:** frames and probe data use `imageio-ffmpeg`.
  - It is a BSD-2 wrapper. The bundled ffmpeg binary (gyan.dev 7.1 essentials, static) is **GPL-3.0-or-later**: `--enable-gpl --enable-version3`, libx264/libx265 included, not nonfree. The wheel ships no GPL text or source offer. Do not redistribute it without meeting GPLv3; see `LICENSING_AUDIT.md` (the recommendation is that users supply ffmpeg themselves).
  - If it is missing, or a container cannot be decoded, previews return `409 PREVIEW_UNAVAILABLE` and imports need an explicit `duration`.
  - Frame seeks are by time (fast input seek), so they are not frame-exact.
- **PDF:** pages are rendered with `pypdfium2` (Apache-2.0/BSD-3). AGPL PyMuPDF is deliberately not used.
  - Encrypted or broken PDFs return `PREVIEW_UNAVAILABLE`.
  - The planner does not crop inside PDFs; regions are passed to the renderer.
- **Images:** PNG, JPEG, WEBP and GIF only.
- **Maps:**
  - Schematic by default.
  - The optional raster basemap is off by default and needs an operator-supplied tile URL, attribution and an explicit network opt-in. **No tile provider is bundled or defaulted.**
  - The basemap has only been tested against a mocked tile server.
  - With a basemap the projection is Web Mercator, stretched to the map frame.
  - Tiles are cached in `storage/maps/tiles/` with no expiry.

## Platform / operations

- **In-process job queue** (thread pool): one process per storage root. Jobs RUNNING at a crash become FAILED/`JOB_INTERRUPTED` on restart and must be retried.
- Cancellation is cooperative.
- SQLite is suited to single-user or local use.
- **Auth** is a single optional shared API key; the default bind is 127.0.0.1.
- There are no delete/update endpoints, pagination cursors or webhooks.
- Previews accumulate in `storage/plans/previews/` (asset previews are served from memory).

## Verification gaps

- The UI was verified by **automated** headless Microsoft Edge (Playwright, 38 checks, `docs/ui_screenshots/`). It was not used by a human.
- The test suite and self-test were run on Windows 10 with Python 3.13.5 only.
- The PowerShell self-test ran from an elevated (Administrator) shell in this session. No step requires elevation.
