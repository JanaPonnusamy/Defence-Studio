# Self-Test Report (v1.1.0, Phase 2)

Every number below comes from real runs on 2026-10-12 and was copied from the command output. Nothing is estimated or invented. The previous v1.0 report is preserved at `backups/phase2-20261012_021702/docs/SELF_TEST_REPORT.md`.

## Environment

| Item | Value |
|---|---|
| OS | Microsoft Windows 10 Pro 10.0.19045 |
| PowerShell | 5.1.19041.6456 (Desktop) |
| Python | 3.13.5 (module `.venv`) |
| Key packages | fastapi 0.115.6, SQLAlchemy 2.0.36, Pillow 11.0.0, alembic 1.14.0, httpx 0.28.1. **New (Phase 2):** pypdfium2 5.14.0, imageio-ffmpeg 0.6.0 (bundled ffmpeg 7.1); playwright 1.63.0 (dev only) |
| Browser (UI check) | installed Microsoft Edge 155.0.4283.45 |
| Elevation | In this session the self-test reported `Running as Administrator: True` (informational). **No step requires elevation.** |
| Provider credentials | none (`OPENAI_API_KEY` unset in process, user and machine scope; no `.env`) |

## 1. pytest suite

Command: `.\.venv\Scripts\python.exe -m pytest`

| | Tests | Result | Duration |
|---|---|---|---|
| Baseline (before any Phase 2 change) | 113 | 113 passed | 16.04 s |
| Final | 180 | **178 passed, 0 failed, 2 skipped** | 26.04 s |

The 2 skips are the **opt-in paid-provider tests** (`tests/test_provider_live_optin.py`). They are skipped unless `VVM_ENABLE_PAID_PROVIDER_TESTS=1` and a key are set, and the billable test additionally needs `VVM_LIVE_GENERATE=1`. They were never enabled.

| File | Tests | Phase 2 coverage |
|---|---|---|
| `test_phase2_truthfulness.py` (new) | 7 | Provider classification; placeholders cannot be imported or relabelled; placeholder and fixture never AI or evidence (assets, generations, costs, filters); never selected for factual intents; placeholder shot labels plus validator tamper checks; UNKNOWN cost reported, not summed as zero; **real 0001→0002 data migration** of v1.0 rows |
| `test_openai_provider.py` (new) | 21 | Mocked HTTP only. Success with KNOWN cost from usage; no usage → UNKNOWN; auth / quota / moderation (not retried); rate limit / 5xx / timeout / connection (retried, UNAVAILABLE); 5 malformed responses; not configured (no request, 409); explicit and configured fallback; probe; key redaction in errors and logs; pricing never guesses |
| `test_provenance_rights.py` (new) | 6 | RESTRICTED never selected (even when most relevant or unknown allowed); UNKNOWN never outranks PERMITTED; UNKNOWN only when allowed and flagged; full provenance in asset, sidecar, timeline and attributions; validation rules; exact and near-duplicate detection |
| `test_previews_matching.py` (new) | 9 | Capabilities; real MP4 probe and time-dependent frames; undecodable video → `PREVIEW_UNAVAILABLE` and no invented duration; real PDF pages; shot preview from a PDF page; basemap off by default, used with mocked attributed tiles, tile failure → schematic fallback; synonyms, accents, Cyrillic, non-English tags |
| `test_timing.py` (new) | 16 | Supplied timings used exactly (SUPPLIED) with a pause closed by hold; ESTIMATED labelled; very short narration; long narration (360 sentences, over 30 min) planned under 30 s; 10 malformed/overlapping/missing cases; non-finite; tiny-overlap normalisation |
| `test_renderer_contract.py` (new) | 6 | 10-minute project: top level and output, ordered contiguous shots with frames, asset refs with provenance, on-screen labels, explicit "output not defined", validator catches gaps and disorder |
| `test_provider_live_optin.py` (new) | 2 | opt-in only, **skipped** |
| Existing files | 113 | Unchanged, except 5 tests updated because they asserted the old untruthful behaviour (see `PHASE_2_REPORT.md`) |

## 2. `docs\SELF_TEST_COMMANDS.ps1`

Command: `powershell -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1`

Final run: **exit code 0, RESULT: PASS. PASS=33 FAIL=0 SKIP=0 BLOCKED=0.** The live backend used isolated temp storage (`%TEMP%\vvm_selftest_20261012_105928\storage`) and was started with provider keys stripped from its environment.

New or changed checks in Phase 2 (results from the final run):

| Check | Result |
|---|---|
| Configuration valid | providers `mock-local` TEST_FIXTURE/AVAILABLE, `openai-images` AI_GENERATED/**NOT_CONFIGURED**, `placeholder-procedural` PLACEHOLDER/AVAILABLE |
| pytest suite | 178 passed, 2 skipped in 24.67 s |
| GET /health · /openapi.json | v1.1.0 · 35 paths |
| Fetch plan, timeline + shots | 5 shots (1 PLACEHOLDER-labelled, 0 AI, 2 explicit GAPs), timeline v1.1, timing ESTIMATED |
| Placeholder generation (via legacy alias `local-procedural`) | kind PLACEHOLDER, `PLACEHOLDER_IMAGE`, not real AI, not evidence-eligible, cost NOT_APPLICABLE (null), 13,677 B PNG |
| **Provider states without credentials** | status probe → NOT_CONFIGURED (not probed: no key); plain request → **409 PROVIDER_NOT_CONFIGURED**; explicit fallback → labelled `[FALLBACK PLACEHOLDER]` asset; NOT_CONFIGURED generation recorded |
| Preview capabilities | video_frames True (imageio-ffmpeg 0.6.0), pdf_pages True (pypdfium2 5.14.0), basemap off ("no tile source configured") |
| **Cost report (no invented costs)** | real AI images 0, known cost $0, placeholders 3 (1 fallback), unknown-cost 0, totals_complete True, per-minute null, no record carries an amount |
| Restart recovery, dedup, rejects, occupied port, own-process cleanup | PASS (unchanged from v1.0) |

The first final-stage run was PASS=32 FAIL=1. That was a script defect, not a module defect: in PowerShell, `-like '[FALLBACK PLACEHOLDER]*'` treats the brackets as a wildcard set. It was fixed with `.StartsWith()` and the rerun passed 33/33.

## 3. 10-minute planning benchmark

Command: `.\.venv\Scripts\python.exe scripts\benchmark_10min.py`, run 3 times on the final code plus 1 run after relabelling. The run is in-process with isolated temp storage, using the synthetic episode: 1,463 words, 24 scenes, 121 segments, 621.5 s ESTIMATED, 54 assets.

| Measurement | Baseline (v1.0, 3 runs) | Final (3 runs) |
|---|---|---|
| Import 54 assets | 3.12–3.19 s | 3.37–3.50 s |
| Scene parsing | 0.072–0.077 s | 0.072–0.079 s |
| Plan build, no generation | 0.223–0.224 s | 0.205–0.226 s |
| Plan build, 4 generated hook images | 0.466–0.493 s | 0.476–0.513 s |
| Shots / narration changes / gaps | 139 / 89 / 0 | 139 / 89 / 0 |
| Distinct assets (no gen / with gen) | 54 / 58 | 54 / 58 |
| Reused assets | 46 | 44–45 (synonym matching; tie-breaks now vary slightly between runs) |
| Generated images | 4 "AI" (actually procedural) | 4 **PLACEHOLDER**, 0 real AI |
| Cost report | 4 AI images, $0.04, $0.003862/min (invented rate) | ai_image_count 0, $0, per-image null, per-minute null, placeholder_image_count 4, totals_complete true |

Planning performance is unchanged within run-to-run noise.

## 4. UI verification (automated)

`scripts\ui_browser_check.py` drove headless Edge 155 via Playwright: **38/38 checks passed**, 17 screenshots. See `docs/ui_screenshots/UI_VISUAL_TEST_REPORT.md`. This is automated browser verification, not human interaction.

## 5. Isolation and safety

- No imports from the parent app or audio-module: the AST test, plus a grep of every module `.py/.js/.html/.ps1` source file (excluding `.venv`, `backups`, `storage`) for forbidden imports, `audio-module` and `../../` paths, gave 0 hits outside the test files, which contain deliberate attack strings.
- Parent repository `git status`: identical to the session start.
- **Zero real or paid provider requests.**
  - No key exists on the machine.
  - The pytest network guard fails any adapter request that isn't mocked.
  - Live tests were skipped.
  - The self-test and UI-check backends were started with keys stripped.
