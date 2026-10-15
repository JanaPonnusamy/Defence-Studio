# Voice intelligence acceptance — 2026-10-15

**VERIFIED WITH LIMITATIONS:** real English speech, original delivery controls, measured audio and standalone preview. Tamil speech, reference analysis, human listening acceptance and interrupted standalone-job recovery remain blocked/unverified. This is not production-pipeline acceptance.

## Fixes and evidence

- `defect-before.log` reproduced three unsafe requests: blank text, unavailable Tamil, and a missing explicit voice all reached synthesis (3 failed assertions, exit 1). `app/tts/local_pyttsx3.py` now fails closed, verifies installed voice/language through `pyttsx3_worker.py`, normalizes Unicode NFC, isolates OS synthesis in a child with a 60-second timeout, validates output/metadata, and exclusively publishes a complete file using a same-volume hard link. Existing outputs cannot be overwritten. Hard-link support is required and worked on this host.
- `duplicate-before.log` reproduced repeated execution of one persisted job (1 failure, exit 1). `app/jobs/queue.py` now atomically claims QUEUED rows and fences late updates against RUNNING. Distinct HTTP requests still create distinct jobs; this is not request-level idempotency.
- `app/jobs/handlers.py` refuses FAILED audio validation before creating a successful generated record. Nonfinite samples fail validation. Missing voice metadata cannot publish output. Regression tests cover timeout/missing-provider/corrupt-output/metadata cleanup and terminal-state fencing.
- `app/pipeline/analyze.py` previously divided speaking and articulation rate by the same active duration. Speaking WPM now includes pauses; articulation WPS excludes them. A two-word/two-second regression verifies 60 WPM versus 2 articulation WPS.
- `librosa` was already declared in requirements.txt. A new TEMP venv installed the complete declared dependency file; pip check passed. No production/shared interpreter was modified. The TEMP environments may be removed externally; `dependencies.txt` preserves the tested versions.

## Reference provenance

Requested https://www.youtube.com/watch?v=kwG9uCDeSk4&t=167s (ID kwG9uCDeSk4, start 167 seconds), then https://www.youtube.com/@toshitalks. Both public page fetches were throttled. No audio was acquired/downloaded and no restrictions bypassed. Actual segment/language/activity/duration/pitch/pace/pauses/energy/emotion/noise measurements are unavailable. See `reference-provenance.json`. An authorized local audio segment with provenance is required to continue. The profile contains original editorial targets, not invented reference measurements or an imitation.

## Original profile and measured controls

`original-profile.json` is versioned and hashed; GET `/tts/delivery-profile` exposes it. Presets: neutral 170/.85, serious 150/.85, urgent 195/.95, calm 140/.75, documentary 160/.95 (requested WPM / volume). Controls: native requested rate 80–260 WPM, native volume 0–1, post-processed pitch -3 to +3 semitones using librosa. Pitch output is attenuated if needed to avoid new clipping.

Delivery labels approximate rate/volume only. Punctuation is delegated to the provider; exact pause durations, native emotion and guaranteed word emphasis are unsupported. Operators can spell acronyms as intended. Pitch uses pYIN at 16 kHz, 60–500 Hz, probability >= .5 plus an energy mask. Low-confidence pitch becomes null. A 200 Hz calibration signal and silence test passed; those test tones are not speech evidence. Energy activity is not a speech/music classifier. RMS is dBFS, not LUFS; dynamic range is frame-RMS p95 minus p10. WPM uses supplied whitespace-delimited words/full duration, not recognized-word correctness. Word alignment remains proportional/approximate. Human pronunciation, intelligibility, naturalness and emotion review remain pending.

| Real sample | Duration | Median F0 | Result |
|---|---:|---:|---|
| neutral.wav | 13.921905 s | 187.2 Hz | Baseline, requested 170 WPM |
| fast.wav | 10.862268 s | 187.2 Hz | Requested 210 WPM; duration ratio .78023 |
| pitch-plus2.wav | 13.921905 s | 211.4 Hz | Measured pitch ratio 1.12896; requested 1.12246 |

All three came from actual Zira SAPI speech via the HTTP service. Two additional browser previews are real OS speech. All five files passed independent FFprobe and complete FFmpeg decoding (PCM s16le, mono, 22050 Hz). `artifact-manifest.json` records exact paths, sizes, durations and SHA-256 for every retained WAV. `*-analysis.json` and `preview-*.json` contain configurations, detailed energy/pause/confidence metrics and hashes. No human listening acceptance is claimed.

## Provider states and Tamil setup

| Provider | Adapter | Installed/configured | Real speech | Independently validated |
|---|---|---|---|---|
| local-pyttsx3/SAPI English | Repaired | Tested TEMP venv; Hazel en-GB, Zira en-US | Yes | Yes: decode and metrics; listening pending |
| windows-sapi component | Existing, unchanged | OS voices present | Component real test ran | 59 component tests passed |
| mock-tts | Existing | Available | No, deliberate tone | Unit tests only |
| local Tamil | Language guard implemented | No matching voice | BLOCKED | Real request failed closed: tamil-result.json |
| eSpeak NG | No dedicated adapter added | Executable absent | NOT RUN | NOT VERIFIED |
| Azure Tamil | Not provisioned | No configured adapter/credential | NOT RUN | NOT VERIFIED |

eSpeak NG documents Tamil and GPL licensing: https://github.com/espeak-ng/espeak-ng/blob/master/docs/languages.md and https://github.com/espeak-ng/espeak-ng/blob/master/COPYING . Local use has no metered API invoice; executable/data/voice installation, integration and licensing review are required. Verify `--voices=ta` and actual output before acceptance. No installation was performed.

Azure documents ta-IN-PallaviNeural and ta-IN-ValluvarNeural: https://learn.microsoft.com/en-us/azure/cognitive-services/speech-service/language-support?tabs=tts. . Requires a resource, region, credentials, authorized spend, bounded adapter and applicable terms review. Billing depends on the selected plan: https://azure.microsoft.com/en-us/pricing/details/speech/ . No price/invoice or operational claim is made. No paid call occurred. OS-voice commercial/publication rights remain unverified.

## Exact validation

Standalone/component interpreter: `%TEMP%/defence-voice-intelligence-20261015/Scripts/python.exe`; backend: `%TEMP%/defence-studio-p0-recheck-20261015/Scripts/python.exe`. These were disposable environments, not deployment prerequisites to reuse verbatim.

| Working directory / command | Result | Log |
|---|---|---|
| root: python -m venv TEMP/defence-voice-intelligence-20261015; venv python -m pip install -r standalone/audio-module/requirements.txt | exit 0 | install.log |
| root: venv python -m pip check | exit 0 | pip-check.log |
| standalone/audio-module: python -m pytest -q -p no:cacheprovider --ignore=tests/test_tts_safety.py | 31 passed, 7 warnings | baseline-tests.log |
| standalone/audio-module: python -m pytest -q -ra -p no:cacheprovider | 61 passed, 0 skipped, 7 warnings, exit 0 | release-audio-tests.log |
| components/voice-engine: python -m pytest -q -ra -p no:cacheprovider | 59 passed, 0 skipped, 1 warning, exit 0 | component-tests.log |
| backend: python -m pytest -q -ra -p no:cacheprovider tests/test_voice_integration.py tests/test_voice_profile_application.py | 28 passed, 0 skipped, 1 warning, exit 0 | backend-voice-tests.log |
| frontend: npm test | 65 passed, 0 skipped, exit 0 | frontend-tests.log |
| root: venv python AGENT_WORK/CODEX_EVIDENCE/voice-intelligence-e2e-20261015/acceptance.py --serve | real HTTP synthesis/decoding/restart assertions, exit 0 | acceptance-final.log |
| root: node AGENT_WORK/CODEX_EVIDENCE/voice-intelligence-e2e-20261015/browser.mjs | 12 checks, exit 0 | browser-results.json, browser-acceptance.log |
| root: git diff --check | exit 0 | diff-check.log |

Logs preserve full warnings (existing framework/config deprecations and component warnings). No final test skipped. Incremental totals 48/59 are superseded by 61. The first reproduction attempt began before pip completed (pytest absent); the separate prior-env `defect-before.log` establishes the actual defect. Browser harness retries failed due Windows URL path decoding and the wrong helper name `fill`; corrected to fileURLToPath/setValue. Failures are retained in browser.log/browser-retry.log, not counted as passing.

## UI, persistence, ownership and deployment

Audio Lab supports preset selection, provider inventory, supported controls, genuine previews, two-sample comparison, playback and metadata download. Unsupported mock controls and unavailable Tamil generation are disabled; server enforcement is separate. Desktop/mobile screenshots were inspected; 390px has no overflow. Main frontend files were untouched.

acceptance.py uses new TEMP DB/storage, compares persisted jobs and artifact metadata across separate server processes, and never accesses the protected episode. `restart.json` proves completed-state persistence. Interrupted standalone RUNNING jobs still have no leases/recovery policy; no unsafe startup sweep was added. Standalone cancellation and HTTP idempotency-key endpoints do not exist. Main backend lifecycle remains separate; its focused tests passed. Optional rendering was not run in this voice task.

Changes are confined to standalone/audio-module TTS, schemas/API, handlers/queue, measurement/validation, tests, UI and README; own log/evidence. See final-status.txt and final-tracked.diff. New files are controls.py, pyttsx3_worker.py, measure.py and two focused test files. Claude's concurrent log append is not our work. Initial HEAD 7d05ae0ba50cbcd5a824b7e9e7ef0deae80f78a7, main. No staging/commit/push. New evidence contains no DB, venv or credentials. Prior committed environments were left untouched.

preservation.json verifies unchanged production and protected episode DB byte hashes. Original review history was not queried. No approval, credentials, system configuration or shared coordinator change. Acceptance servers stopped; TEMP storage recorded in runtime.json was retained for inspection.

Use a dedicated environment with full requirements.txt, FFmpeg/ffprobe, and explicit AUDIO_MODULE_STORAGE_ROOT/AUDIO_MODULE_DATABASE_URL before app import. Bind uvicorn app.main:app to loopback: this standalone development service is unauthenticated. Production exposure, interrupted-job recovery, human listening, Tamil provisioning, real forced alignment and provider rights require separate closure.

Coordinator proposal only: voice path VERIFIED WITH LIMITATIONS; 61 standalone + 59 component + 28 backend voice + 65 frontend tests and 12 browser checks. Real English decode/measurement/restart passed. Tamil/reference/human listening/interrupted recovery remain open. No production acceptance or episode approval.
