# Phase 2 — Independent Audit

- **Date:** 2026-10-12
- **Module:** `standalone\video-visual-module` v1.1.0
- **Approach:** every claim in `PHASE_2_REPORT.md` was treated as unverified. Nothing here was taken from that report; all of it was re-run or re-read in this session.
- **What was not done:** no paid API call was made, no OpenAI key exists on this machine, and `VVM_LIVE_GENERATE` / `VVM_ENABLE_PAID_PROVIDER_TESTS` were never set. No git command that changes state was run.
- **Backups:** `backups\audit-20261012\`. It mirrors the relative paths of every pre-existing file modified, plus `docs\ui_screenshots\` (see §8).

## 1. Test results (actual)

| Suite | Before audit fixes | After audit fixes |
|---|---|---|
| `pytest` | **178 passed, 2 skipped** (26.9 s) | **190 passed, 2 skipped** (36.9 s; +12 audit regression tests) |
| `powershell -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1` | **PASS=33 FAIL=0, exit 0** | **PASS=33 FAIL=0, exit 0** |
| `.\.venv\Scripts\python.exe scripts\ui_browser_check.py` (headless Edge) | **38 passed, 0 failed**, 17 screenshots | **38 passed, 0 failed**, 17 screenshots |

Both skips are in `tests\test_provider_live_optin.py`. Neither ran.

**`test_live_connectivity`**
- **Skipped unless** `VVM_ENABLE_PAID_PROVIDER_TESTS=1` **and** a key is in the process environment (`OPENAI_API_KEY` / `VIDEO_MODULE_OPENAI_API_KEY`). A key that exists only in `.env` does not enable it.
- **What it does:** removes the network guard for this module and calls `GET /providers/openai-images/status?probe=true`. That triggers one real `GET https://api.openai.com/v1/models/gpt-image-2`.
- **Billing:** **not billable**.
- **Asserts:** state `AVAILABLE`.

**`test_live_generation`**
- **Skipped unless**, in addition to the above, `VVM_LIVE_GENERATE=1` is set.
- **What it does:** submits one `POST /assets/generate` with provider `openai-images`, quality `draft` (sent as `low`), resolution `1024x1024` and `allow_placeholder_fallback: false`. That becomes **one real `POST /v1/images/generations` — BILLABLE**, about USD 0.006 at the documented gpt-image-2 low 1024² price.
- **Asserts:** `SUCCEEDED`, `generation_kind AI_GENERATED`, `is_real_ai`, a `provider_request_id`, and `cost_status` KNOWN or UNKNOWN.
- **Caveats:**
  - The normal retry policy still applies (up to 3 POSTs on timeout).
  - It writes into the pytest temp storage, which is deleted at session end, so the image is not kept.
  - The new runbook script is the safer path (§5).

## 2. Claim-by-claim verification

Each claim was checked by reading the code and tests, and by live HTTP probes against a server started on a free port with isolated temp storage and empty key variables (the same method as the self-test). That live probe made 21 checks: 20 passed, and the 1 miss was a wrong expectation in my probe, not a defect. A second live probe ran against a real v1.0 (0001) database upgraded to head. All processes I started were stopped, and all temp storage was removed.

| Claim | Result | Evidence |
|---|---|---|
| 178 passed / 2 skipped | **Holds** | Re-run (§1) |
| Self-test 33/33 | **Holds** | Re-run (§1) |
| Browser 38/38 | **Holds** | Re-run (§1) |
| Placeholder renamed `placeholder-procedural`, labelled, no cost | **Holds** | Live: default provider asset is `[PLACEHOLDER]…`, `PLACEHOLDER_IMAGE`, `generation_kind`/`provenance_class` `PLACEHOLDER`, `cost_status NOT_APPLICABLE`, cost `null`, not evidence. The legacy alias `local-procedural` gives the same result. |
| No key gives `409 PROVIDER_NOT_CONFIGURED`; no request made | **Holds** | Live: 409 on `/assets/generate`; `status?probe=true` gives `NOT_CONFIGURED`, `probed:false`. An explicit `allow_placeholder_fallback:false` also gives 409. |
| Failure is never silently a placeholder; opt-in fallback is labelled FALLBACK | **Holds**, with a **coverage gap now closed** | Live: fallback=true gives `[FALLBACK PLACEHOLDER]`, `is_fallback`, `fallback_from: openai-images`. The existing tests covered fallback only for NOT_CONFIGURED. New tests cover a **configured** key with a mocked 401 or 503 and fallback=true: the asset is labelled, the failed real-AI record is kept as `FAILED`/`UNAVAILABLE` with `asset_id` null, and the cost is NOT_APPLICABLE. With fallback=false, the error is raised and 0 assets are created. |
| RESTRICTED never selected | **Holds** | Live: the most relevant asset is RESTRICTED and is never used, even with `allow_unknown_rights`. Existing tests agree. |
| UNKNOWN rights rank below cleared | **Holds** | Live: a LICENSED one-keyword asset is chosen over an UNKNOWN all-keyword asset. UNKNOWN is used only when allowed, and is then flagged "NOT evidence-eligible" with a `rights_warning`. |
| UNKNOWN cost is null and flagged, never summed as 0 | **Holds** | New API-level test: a mocked success without `usage` gives `UNKNOWN`/null, `unknown_cost_count 1`, `totals_complete false`, `ai_generation_cost_is_lower_bound true`, and all record amounts null. Placeholder cost records are NOT_APPLICABLE/null. |
| Timing ESTIMATED/SUPPLIED; `INVALID_TIMING` | **Holds for new data** | Live: segments are `SUPPLIED` on the scene, the segments, the shot anchors and the timeline. `end<=start` and overlap give 422 `INVALID_TIMING`. Narration gives `ESTIMATED` with a plan warning. **Two defects were found next to this** (D1, D3). |
| Timeline v1.1 renderer handoff | **Holds** | Live: `version 1.1`; `output{1920,1080,25,defined:true}`; integer contiguous `frame_start`/`frame_end`; per-asset `generation_kind`, `provenance_class`, `evidence_eligible`, `is_fallback`, `provider`, `checksum_sha256`, `uri`, `url`. With no output given, `defined:false` and the values are null. `/schemas/visual-timeline` is served. |
| Migration 0002 relabels v1.0 records | **Partly holds** — defect D3 | Live, on a real 0001 DB upgraded to head and served over the API: the local-procedural asset, its generation and its cost record become PLACEHOLDER with cost null. The mock becomes TEST_FIXTURE. An imported photo stays NOT_GENERATED and evidence-eligible. The cost report shows 0 AI images and the old 0.01 is not summed. Scene `PROVIDED` becomes `SUPPLIED`. **But** shot `narration_anchor.timing_source` and the stored timeline JSON kept `PROVIDED`, even though 0002's own docstring says "scene/shot". |
| Key read from env or `.env` | **Holds**, but caused defect D2 | The `.env` support meant the test harnesses could pick up a real key. |
| Previews use imageio-ffmpeg / pypdfium2; licence "flagged as unreviewed" | **Holds**, but the licence wording was inaccurate | The binary is **GPLv3**, not "LGPL/GPL" (§4). |
| Backups in `backups\phase2-20261012_021702\` | **Holds** | The folder exists. |
| Credentials never stored or logged | **Holds** | A sweep of 266 files (source, docs, backups, `storage\`, UI artefacts, `.pyc`), including binary files, found only obviously fake test keys (`sk-test-FAKE…`, `sk-FAKE-…`) in four test files. There is no `.env`, no Bearer token and no stored key. The real `storage\` has no DB or logs. The live invalid-key run's logs, DB and manifest contained 0 occurrences of the key used. |

## 3. Defects found and fixed

Each defect was demonstrated before it was fixed, and each has regression tests.

| # | Defect (demonstrated) | Fix | Regression |
|---|---|---|---|
| **D1** | **Data loss.** `POST /scenes` with `replace_existing:true` deleted and **committed** the project's scenes *before* validating the new segments. A request rejected with `INVALID_TIMING` or `MISSING_NARRATION_ANCHOR` therefore wiped every existing scene. Shown live: after a 422 the scene list was empty, and the next plan build returned 422 with no scenes. | `app/api/scenes.py`: parse and validate first, then delete and insert in one commit. Behaviour for valid requests is unchanged. | `test_rejected_replace_existing_keeps_existing_scenes` (3 cases; **all 3 fail on the original code** and pass now), `test_valid_replace_existing_still_replaces` |
| **D2** | **Paid-call safety.** `tests/conftest.py`, `scripts/ui_browser_check.py` and the self-test's `Start-Backend` *removed* the key variables, but pydantic-settings then falls back to the module `.env`, which `PROVIDER_SETUP.md` documents as a key location. With a key in `.env`, the self-test and browser check would have sent **real billable** `openai-images` generations: self-test line 469; the browser check's generate and plan-build steps, up to 4 images. Pytest was saved only by its network guard. Demonstrated with a fake `.env`: removed variables meant the key loaded (`True`); empty variables meant no key (`False`). | Set both key variables to **empty** (Python) or a single **space** (PS 5.1 cannot hold an empty variable; the adapter strips whitespace). conftest also forces the cached setting to `None`. | `test_empty_env_vars_override_dotenv_key`, `test_normal_suite_holds_no_provider_key`. End to end: with a **fake** key temporarily in `.env`, the 54 provider-related tests passed and the fake `.env` was removed. |
| **D3** | **Migration incomplete.** v1.0 plans built from timed segments still served `narration_anchor.timing_source: "PROVIDED"` on `GET /shots/{plan}` and `GET /visual-plans/{plan}/timeline` after 0002. That value is outside the v1.1 contract. | New data-only, idempotent migration `alembic/versions/0003_audit_shot_timing_label.py` (`0003_audit_timing`, revises `0002_phase2`). It rewrites the shot anchors and stored timelines, and marks a changed timeline `timing_relabelled_by_migration`. No schema change; downgrade is a no-op. An edit to 0002 would not reach databases already at 0002. | `test_migration_relabels_v1_shot_anchor_and_timeline_timing` (real 0001 DB, upgrade run twice). Live re-probe: both endpoints now return `SUPPLIED`. The existing 0002 test still passes. |

Other new tests, added to close coverage gaps rather than to fix defects:
- the configured-provider failure tests (with and without fallback)
- the unknown-cost cost-report test (API level)
- the timeline frames and supplied-timing handoff test

All are in `tests/test_audit_regressions.py`.

**v1.1.0 compatibility.**
- The API shapes are unchanged.
- D1 only stops a destructive side effect.
- D3 adds an Alembic head. `init_db` upgrades automatically at startup, so existing databases, including the real `storage\` database once one exists, will move to `0003_audit_timing` the next time they start. The real `storage\` currently has no database.

## 4. OpenAI provider audit

Official docs were reachable. All sources were retrieved 2026-10-12:
- <https://developers.openai.com/api/docs/models/gpt-image-2>
- <https://developers.openai.com/api/docs/models/gpt-image-1>
- <https://developers.openai.com/api/docs/deprecations>
- <https://developers.openai.com/api/docs/pricing>
- <https://developers.openai.com/api/docs/guides/image-generation>
- <https://developers.openai.com/api/reference/cli/resources/images/index.md>
- <https://ffmpeg.org/legal.html>

The page `…/api/reference/resources/images/methods/generate` returned 404; the CLI reference markdown above was used instead.

### Endpoint and request — conformant

| Area | Code | Official docs | Verdict |
|---|---|---|---|
| Endpoint and auth | `POST {base}/images/generations`, base `https://api.openai.com/v1`; `Authorization: Bearer <key>`; `follow_redirects=False` | `post /images/generations` | Matches |
| Body | `{model, prompt, n:1, size, quality, output_format:"png"}` | — | Valid for GPT image models |
| `n` | 1 | 1–10 | OK |
| `size` | presets 1024x1024 / 1536x1024 / 1024x1536 | Documented presets; gpt-image-2 also accepts custom WxH | OK; 16:9 becomes 3:2 (already documented) |
| `quality` | low / medium / high | Valid for gpt-image-2 (`xhigh`/`max` are for 2.5 only) | OK |
| `output_format` | png | png / jpeg / webp | OK |
| `response_format` | not sent | Unsupported for GPT image models | Correctly omitted |
| `input_fidelity`, `background` | not sent | gpt-image-2 must omit `input_fidelity`; transparent background is preview-only | Correctly omitted |
| Prompt length | module limit 2000 | API limit 32000 | Within the limit |

### Model

- **`gpt-image-2` exists and is current.** Its default snapshot is `gpt-image-2-2026-04-21`. It supports `v1/images/generations`, `v1/images/edits` and `v1/batch`, but not `v1/responses`. It is listed in the API's model enum.
- **"`gpt-image-1` is deprecated" holds.** The deprecations page (2026-04-22 entry) gives a **shutdown date of 2026-10-23**. The model page itself only says "previous image generation model", so the report's "marked Deprecated on its model page" is loosely worded, but the conclusion is correct.
- **`gpt-image-1.5`, `gpt-image-1-mini` and `chatgpt-image-latest` shut down on 2026-12-01.** All four deprecated models point to `gpt-image-2.5-sunburst` / `-flare` as replacements, and those are not in the module's tables. If selected, they would be refused by the budget guard ("no documented price") rather than mis-billed.
- **Default rate limits for gpt-image-2:** Build tier 250k TPM / 20 IPM, Launch 3M / 150, Grow 8M / 250. The module does no client-side rate limiting; one image per job is well within the limits.

### Auth and error mapping — conformant, with one cost-safety risk

- **Status code mapping:**
  - 401/403 → `PROVIDER_AUTH_FAILED` (FAILED, not retried)
  - 429 with `insufficient_quota` → `PROVIDER_QUOTA` (FAILED, not retried); other 429s → `PROVIDER_RATE_LIMITED`
  - 5xx → `PROVIDER_UNAVAILABLE`
  - moderation (`moderation_blocked`, `image_generation_user_error`) → `PROVIDER_CONTENT_REJECTED`
- **Network failures:** timeouts → `PROVIDER_TIMEOUT`; connection errors → `PROVIDER_UNAVAILABLE`.
- **Verified live (non-billable):**
  - `scripts\live_provider_check.py invalid-key --models-only` with a deliberately invalid key (not a real one) made one `GET /v1/models/gpt-image-2`. It returned **HTTP 401 with an `x-request-id`**, mapped to `FAILED`, and sent 0 generation POSTs. That shows the live auth path, the header name and connectivity from this machine.
  - The billable generation endpoint was **not** contacted.
- **Retry and backoff (risk, documented, not changed):**
  - Transient codes are retried up to 2 times with a linear 0.2 s × attempt delay. That is not exponential and does not honour `Retry-After`.
  - Retrying a **timed-out POST** can bill more than once, and those attempts are not recorded.
  - This is a design choice rather than a demonstrated defect, so it was documented in `KNOWN_LIMITATIONS.md` and neutralised for the live test (`MAX_RETRIES=0` plus a one-POST guard).
  - Recommendation: do not retry `PROVIDER_TIMEOUT` for billable providers, and honour `Retry-After` on 429.

### Response handling — conformant

- **Image data:**
  - Requires `data[0].b64_json` (GPT image models always return base64) and never fetches URLs.
  - Applies a size cap, then strict base64 decoding.
  - Decodes the image with PIL and accepts only PNG, JPEG or WEBP.
  - Writes to `generated/<project>/`.
- **Service-side checks:**
  - the path stays inside `generated/`
  - the file is non-empty
  - it re-decodes successfully
  - the dimensions match the provider's claim
- **Recorded fields:**
  - provider, model, the module `request_id` and `x-request-id`
  - `created` (as ISO), the response size, quality and format
  - `usage` and C2PA chunk presence (detection only)
  - `revised_prompt` if present; the reference says GPT image models do not return it, so it will usually be absent
- **`usage` caveat:** the reference's generate section describes `usage` as "gpt-image-1 only", while the streaming schemas apply it to GPT image models generally. If gpt-image-2 omits `usage`, the cost is correctly recorded as **UNKNOWN**, never guessed. Only the live test can settle this.

### Pricing — VERIFIED (2026-10-12)

- **Token rates.** `openai_pricing.py` matches the official pricing page exactly for every model (Standard tier, USD per 1M tokens):

  | Model | Text input | Image input | Image output | Text output |
  |---|---|---|---|---|
  | gpt-image-2 | 5.00 | 8.00 | 30.00 | — |
  | gpt-image-1.5 | 5.00 | 8.00 | 32.00 | 10.00 |
  | gpt-image-1 | 5.00 | 10.00 | 40.00 | — |
  | gpt-image-1-mini | 2.00 | 2.50 | 8.00 | — |

- **Per-image table.** It matches the guide's cost table **exactly** for all 4 models × 3 qualities × 3 sizes (for example, gpt-image-2 low $0.006 / $0.005 / $0.005).
- **Excluded tiers.**
  - Batch (half price) and cached-input rates are not modelled. That is acceptable: the module uses the synchronous API, and cached rates for GPT Image 2 apply only to the Responses API.
- **Because the numbers were verified, no code change was needed.** Cost stays UNKNOWN whenever `usage` is missing.
- **Sources:**
  - <https://developers.openai.com/api/docs/pricing>
  - <https://developers.openai.com/api/docs/guides/image-generation>

## 5. Paid-call safeguards (confirmed) and the live-test tooling

These safeguards were already in place:
- No key means 409 at submit and no request.
- `config_state()` makes no network call.
- The probe is `GET /models` only.
- The billable POST is gated by the budget guard, and a model without a documented price is refused.
- pytest strips the key and installs a network-refusing `MockTransport`.
- The live tests need two or three explicit environment flags.

Added by this audit:
- the D2 fix (`.env` keys can no longer reach the test harnesses)
- `scripts/live_provider_check.py`
- `docs/LIVE_PROVIDER_TEST_RUNBOOK.md`

The script's behaviour:
- It is not collected by pytest and defaults to a dry run.
- It runs against throwaway storage in `%TEMP%\vvm_live_check\<run-id>`.
- It never shows the key; it shows a boolean and the length only.
- `generate` requires `VVM_ENABLE_PAID_PROVIDER_TESTS=1`, `--confirm-paid` **and** an estimate no greater than `--max-usd` (default 0.01).
- It forces 0 retries and allows **exactly one** generation POST through a guard transport.
- It validates the format, dimensions, metadata, cost status, file, checksum and `generation_kind`.
- `cleanup --run-id` deletes only that run's directory.

Exercised during the audit, without any billing:
- dry-run
- status
- the `generate` refusal (no key)
- the `probe` refusal (no key)
- `invalid-key --models-only` (live 401, as above)
- cleanup

The cheapest test configuration is gpt-image-2, low, **1536×1024 = USD 0.005**, plus about USD 0.0001 of prompt tokens.

## 6. FFmpeg and licensing (detail in `LICENSING_AUDIT.md`)

- **The binary:** `imageio-ffmpeg` 0.6.0 bundles `ffmpeg-win-x86_64-v7.1.exe` (sha256 `2CE797A0…71A3`). Its version string is "7.1-essentials_build-www.gyan.dev", and it is static.
- **Its licence:**
  - It is built with `--enable-gpl --enable-version3`, without `--enable-nonfree`.
  - It includes **libx264, libx265, libxvid, libvidstab and librubberband**.
  - `-L` declares **GPL v3 or later**. **It is GPL-enabled, not LGPL-only.**
- **Missing notices:** the wheel ships no GPL text and no source offer.
- **How the module uses it:** only through a subprocess, which is aggregate use. The module's own code is therefore not GPL-bound, and LGPL relinking does not apply.
- **What redistributing it would require:**
  - full GPLv3 compliance: licence text, notices, and **complete corresponding source for FFmpeg 7.1 and every statically linked library**
  - separate consideration of H.264/HEVC/AAC patent-pool exposure
- **pypdfium2/PDFium:** Apache-2.0 / BSD-3, with full bundled dependency licences. Permissive.
- **Playwright:** Apache-2.0 and dev-only.
- **Recommendation:**
  - Do **not** redistribute the bundled binary.
  - Ship without it (`pip install --no-binary imageio-ffmpeg imageio-ffmpeg`, or exclude `imageio_ffmpeg/binaries/`), and have users supply ffmpeg (an LGPL build is preferred) via `IMAGEIO_FFMPEG_EXE`.
  - The fallback is to bundle an LGPL-only shared build and meet the LGPL obligations.
- **Internal use needs no action.**
- This is technical analysis, not legal advice.

## 7. Remaining limitations

- **The real OpenAI generation path has NOT been verified.** No successful real generation has been run. The following are all untested against the live API: response fields, whether `usage` is present for gpt-image-2, latency and moderation behaviour.
- ~~The retry-on-timeout double-billing risk remains for normal module use.~~ Fixed 2026-10-12 (see §10).
- `GET /capabilities` still reports `"ffmpeg binary LGPL/GPL"`. It was left unchanged because the binary is swappable; the docs are corrected.
- Plan JSON files and sidecars already on disk are not rewritten by 0002 or 0003. The API serves the database copy.
- The original Phase 2 screenshots in `docs\ui_screenshots\` were regenerated by the audit's browser-check runs before they could be backed up. Only `results.json`, `UI_VISUAL_TEST_REPORT.md` and two PNGs from the in-progress run were saved. The module is not tracked by git, so the originals are not recoverable. They are reproducible artefacts of the same script, and the current set is from a 38/38 run.
- Everything else in `KNOWN_LIMITATIONS.md` still applies.

## 8. Changed files (all pre-existing files were backed up to `backups\audit-20261012\` first)

- **Modified:**
  - `app/api/scenes.py` (D1)
  - `tests/conftest.py`, `scripts/ui_browser_check.py`, `docs/SELF_TEST_COMMANDS.ps1` (D2)
  - `docs/PROVIDER_SETUP.md`, `docs/KNOWN_LIMITATIONS.md`
  - `docs/ui_screenshots/*` (regenerated by the browser check)
- **New:**
  - `alembic/versions/0003_audit_shot_timing_label.py` (D3)
  - `tests/test_audit_regressions.py` (12 tests)
  - `scripts/live_provider_check.py`
  - `docs/LIVE_PROVIDER_TEST_RUNBOOK.md`, `docs/LICENSING_AUDIT.md`, this file
- `docs/SELF_TEST_REPORT.md` was backed up but not modified.

## 9. Exact next steps

1. **Authorise the single paid test (about USD 0.005, cap USD 0.01)** and run `docs\LIVE_PROVIDER_TEST_RUNBOOK.md` Steps 1 to 9:
   - set the key with hidden input
   - set `$env:VVM_ENABLE_PAID_PROVIDER_TESTS="1"`
   - run `probe` (free)
   - run `generate --confirm-paid --max-usd 0.01` (the only billable step)
   - run `invalid-key` (free)
   - run `cleanup`
   - unset the variables
2. Record the outcome in §7 of this file: `provider_request_id`, size and format, `cost_status`/amount against the OpenAI dashboard, and whether `usage` was present.
3. Decide FFmpeg distribution before any packaging (recommendation B).
4. ~~Consider changing the retry policy for billable POSTs.~~ Done 2026-10-12 (§10). Honouring `Retry-After` remains open; it only matters if billable retries are explicitly enabled.
5. Before 2026-12-01, decide whether to add priced support for `gpt-image-2.5-*`. `gpt-image-2` itself is not on a published shutdown list as of 2026-10-12.

## 10. Post-audit change: billable retry safety (2026-10-12, authorized)

The user authorized removing automatic retries after OpenAI timeouts. Full detail in `CHANGELOG.md` and `docs/PROVIDER_SETUP.md` ("Retry policy").

- **Policy:** billable generation requests are sent at most once by default (`billable_generation_max_retries=0`). If retries are explicitly enabled, only "connection never established" and explicit 429 rate limits are resent. Timeouts, connections dropped after sending, and 5xx are never resent.
- **Uncertain outcomes:** recorded as generation `status=FAILED`, `provider_state=UNCERTAIN`, with an UNKNOWN-cost record (no asset) that the cost report counts (`uncertain_outcome_count`, `totals_complete=false`) and the budget guard counts at its pre-call estimate. Never fallen back to a placeholder, even with fallback opted in.
- **Files:** `app/generation/base.py`, `app/generation/openai_images.py`, `app/generation/service.py`, `app/config.py`, `app/api/costs.py`, `scripts/live_provider_check.py`, `tests/test_openai_provider.py` (expected call counts 3 -> 1), new `tests/test_timeout_no_retry.py`, docs. Backups: `backups\retry-fix-20261012_141618\`.
- **Verification (all mocked HTTP; no paid request was made, no key present):**
  - Before: 190 passed, 2 skipped.
  - After: 206 passed, 2 skipped (16 new tests). Every ambiguous failure type (ReadTimeout, WriteTimeout, ReadError, RemoteProtocolError) sends exactly one generation POST, even with `billable_generation_max_retries=3` and a successful response queued behind it.
  - PowerShell self-test: 33/33 PASS, exit 0 (API workflow, health, docs and UI load).
