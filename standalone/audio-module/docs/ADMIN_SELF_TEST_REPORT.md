# Admin / Self-Test Report — Voice Audio Lab / Audio Module

Date: 2026-10-12
Scope: everything inside `D:\Jana Soft\defenceStudio\standalone\audio-module` only. No other part of
the Defence Studio repository was read or modified. No git commands were run. `.bak` copies were made
of every file edited during this pass (`app/pipeline/align.py.bak`, `app/jobs/queue.py.bak`).

Status key used throughout: **VERIFIED** (ran it, saw the output myself), **PLAUSIBLE/UNVERIFIED**
(reasoned from objective data but not independently confirmed), **BLOCKED** (could not be done with
the tools available, stated plainly).

---

## 1. Administrator privilege check — VERIFIED

```powershell
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
# -> False
whoami /groups | Select-String "Administrators|Mandatory Level"
# -> BUILTIN\Administrators ... Group used for deny only
# -> Mandatory Label\Medium Mandatory Level
```

**Result: the current shell is NOT elevated.** The user account (`desktop-53u6m3s\gigabyte`) is a
member of the local `Administrators` group, but UAC's split-token model means the group membership is
present only as "deny only" on the current (medium integrity) token — i.e. a normal, non-elevated
session. I did **not** attempt to self-elevate, bypass UAC, or work around this in any way.

To open an elevated PowerShell, the user would run, from an already-open non-elevated PowerShell or
Start menu:

```powershell
Start-Process powershell -Verb RunAs
```

**Practical impact: none observed.** Every check and fix in this report — venv creation, pip installs
(user-writable project directory), SQLite file DB, uvicorn binding to a free high port, registry
*reads* for SAPI5 voice metadata, `Get-PnpDevice`/`Get-NetTCPConnection` queries — worked fully without
elevation. Nothing in this module requires administrator rights on Windows.

---

## 2. Environment / dependency check — VERIFIED

| Item | Result |
|---|---|
| Python | 3.13.5 (`.venv` created fresh under the project folder) |
| pip | 26.2.1 |
| ffmpeg | Found at `D:\ffmpeg\bin\ffmpeg.exe` (not a hard dependency of this module — librosa/soundfile decode WAV natively — but present and on PATH) |
| Audio output device | `Get-PnpDevice -Class AudioEndpoint -Status OK` → one device: "Speakers (High Definition Audio Device)" |
| Installed packages (`pip freeze`, key ones) | fastapi 0.143.0, uvicorn 0.54.0, sqlalchemy 2.1.4, pydantic 2.14.0, numpy 2.5.4, scipy 1.18.1, librosa 1.0.0, soundfile 0.14.0, pyttsx3 2.99, pytest 9.1.1, numba 0.68.0, alembic 1.20.0 |

**Note on `requirements.txt`:** the exact pins from the original build (`scipy==1.13.1`) failed to
install on this machine because scipy 1.13.1 has no prebuilt wheel for Python 3.13 on Windows and its
source build requires a Fortran compiler (`gfortran`/`ifort`/etc.) that is not installed. This was
already fixed in the prior build pass by loosening pins to `>=` minimums; this self-test re-confirmed
the loosened `requirements.txt` installs cleanly end-to-end in a brand-new venv.

---

## 3. Fixture / server / UI smoke checks — VERIFIED

```
python fixtures/generate_fixture.py
  -> wrote test_fixture_speech_like.wav (13.47s) and test_fixture_transcript.txt

python -m pytest -v
  -> 31 passed, 0 failed, 0 skipped, 7 warnings (all deprecation notices, not failures)

uvicorn app.main:app --port 8020
  GET /health  -> 200 {"status":"ok","service":"audio-module"}
  GET /docs    -> 200
  GET /ui/     -> 200
```

`/health` already existed from the build pass (`app/main.py`); no addition was needed.

Port check before starting: `Get-NetTCPConnection -LocalPort 8000` showed port 8000 was actually free,
but per instructions I used port **8020** anyway to avoid any collision with a server the user might
start independently. No unrelated python process was touched; every `Stop-Process` below was scoped by
`CommandLine like '%uvicorn%8020%'` so only this module's own test server was ever killed.

---

## 4. Full pipeline — live API calls — VERIFIED

Ran the entire pipeline against the real running server with real curl requests (not just pytest):

| Step | Result |
|---|---|
| `POST /references` (upload + plain transcript) | 200, `status: INGESTED`, `audio_id`/`transcript_id` set |
| `POST /references/{id}/process` → poll `/jobs/{id}` | `SUCCEEDED`, `status: ANALYZED`, **both** `alignment_id` and `analysis_id` populated (see defect #1 below — this was broken until fixed in the build pass, re-confirmed fixed here) |
| `GET /alignment/{id}` | real segments/words with honest `quality_label`/`confidence` |
| `GET /analysis/{id}` | real measured stats (33 word occurrences, f0_mean≈158Hz, speech_ratio≈0.71 on the synthetic fixture) |
| `GET /audio/{id}` + `/waveform` + `/file` | 200, real waveform peaks, real WAV bytes |
| `POST /profiles/build` → poll | `SUCCEEDED`, profile bucketed from real metrics (tempo=fast, energy=high for this fast synthetic fixture) |
| `POST /profiles/plan` | 2 sentence-level, provider-neutral instructions |
| `POST /tts/synthesize` (`mock-tts`) → poll → `GET /generated/{id}` | `SUCCEEDED`, `is_mock: true` everywhere, `validation_status: PASSED` |
| `POST /tts/synthesize` (`local-pyttsx3`) → poll → `GET /generated/{id}` | `SUCCEEDED`, `is_mock: false`, real 4.59s speech audio, `validation_status: PASSED` |

## 5. Edge-case / malformed-input testing — VERIFIED

| Test | Result |
|---|---|
| Unsupported extension (`.exe` disguised as audio) | `POST /references` → 400-class, `{"error":{"code":"unsupported_media", ...}}`. Correctly rejected **before** any job was created. |
| Corrupt bytes with a `.wav` extension (ingestion doesn't sniff content, only extension — by design) | Upload succeeds (`status: INGESTED`); `POST .../process` → job `FAILED` with `error_code: LibsndfileError`, clear `error_message` ("Format not recognised"), traceback logged. Reference status correctly stayed at `INGESTED` (did **not** falsely advance to `NORMALIZED`/`ANALYZED`). |
| Empty (0-byte) `.wav` file | Same as above: clean `FAILED` job, no crash, no silent corruption. |
| Invalid SRT timestamp (single reversed `end < start` cue) | **Found a real defect — see below.** Fixed and re-verified. |
| Forced missing/invalid TTS provider (`"provider": "nonexistent-provider"`) | Job submission returns 200 (queued), then the job itself reports `FAILED` with `error_message` naming the bad provider — exactly the intended async-failure contract (already covered by `tests/test_errors_and_isolation.py::test_tts_unknown_provider_rejected`, re-run and still passing). |
| Forced synthesis failure path (Tamil text through an English-only SAPI5 voice) | **Found an interesting, correctly-handled edge case — see Female Voice Evaluation doc.** Synthesis produces 0-length audio (engine silently can't speak the script); the module's existing `validate_generated_audio` correctly marks this `FAILED` while the *job* itself still reports `SUCCEEDED` (mechanically, synthesis didn't raise). This is working as intended — validation, not job status, is the correct signal for output quality — but it is a subtle distinction worth knowing about when building any UI/alerting on top of this API: **check `validation_status`, not just job `status`, before trusting generated audio.** No code change was needed for this one; documented instead, since it is correct existing behavior, not a bug. |

Log review (`storage/logs/audio_module.log`) during all of the above: the only `Traceback` entries are
the two deliberate corrupt/empty-WAV failures above, each with a clear, specific, non-generic error
(`LibsndfileError: ... Format not recognised`) — no silent swallowing, no unrelated stack traces.

Temp-file leak check: `$env:TEMP` was inspected for leftover `tmp*.wav`/`tmp*.exe` files from the
upload code path (`tempfile.NamedTemporaryFile` in `app/api/references.py`) after all of the above
uploads — **0 found**, confirming the `finally: tmp_path.unlink(missing_ok=True)` cleanup works.

---

## 6. Defects found and fixed

### Defect 1 — alignment_id / analysis_id never populated on the Reference row (found during the *build* task's own verification, re-confirmed still fixed here)
- **Symptom:** `POST /references/{id}/process` job reported `SUCCEEDED` and `status: ANALYZED`, but
  `GET /references/{id}` showed `alignment_id: null` and `analysis_id: null`.
- **Root cause:** `app/jobs/handlers.py` read `alignment.alignment_id` / `analysis.analysis_id`
  (SQLAlchemy Python-side `default=_uuid` callables) immediately after `db.add(...)`, before any flush
  — so the attribute was still `None` at read time (the ORM only computes/attaches the default value at
  flush/INSERT time).
- **Fix:** added `db.flush()` right after each `db.add(...)` and before reading the generated id.
- **Verification:** `tests/test_api_end_to_end.py::test_full_pipeline_end_to_end` passes; re-confirmed
  live against the running server in this self-test (`alignment_id`/`analysis_id` both populated).

### Defect 2 — a single malformed SRT cue silently discarded valid timing for every other segment (found THIS pass)
- **Symptom:** a 3-cue SRT transcript with one reversed-timestamp cue (`end < start`) in the middle
  produced an alignment result where **all three** segments — including the two with perfectly valid
  real timestamps — were replaced with VAD-derived approximate timing (`quality_label:
  approximate_proportional_vad`), throwing away good data because of one bad cue.
- **Root cause:** `app/pipeline/align.py`'s `align_vad_proportional` used an all-or-nothing check
  (`has_real_segment_timing = all(s.end > s.start for s in segments)`) instead of validating each
  segment independently.
- **Fix:** rewrote the function to validate **per segment**: valid cues keep their real timing
  (confidence 0.95) and only the genuinely invalid cue(s) fall back to a VAD-derived region (confidence
  0.4). Added a new, honestly-named quality label, `mixed_segment_timed_and_vad_fallback`, for this
  partial-fallback case, distinct from the existing `segment_timed_word_proportional` (all valid) and
  `approximate_proportional_vad` (none valid) labels.
- **Verification:**
  - New regression test `tests/test_align_and_analyze.py::test_align_one_bad_cue_does_not_poison_other_valid_segments` — PASSED.
  - Full suite re-run after the fix: **31 passed, 0 failed** (no regression in the two pre-existing
    alignment tests that assert the all-valid and all-invalid cases).
  - Re-verified live: restarted the server, POSTed the same 3-cue mixed transcript, and confirmed via
    `GET /alignment/{id}` that segments 1 and 3 kept `start=0.00/end=2.00` and `start=6.00/end=9.00`
    respectively at confidence 0.95, while only segment 2 fell back to VAD timing at confidence 0.4,
    with `quality_label: mixed_segment_timed_and_vad_fallback`.

### Defect 3 — job success/failure log lines always printed `(None)` instead of the real job type
- **Symptom:** `storage/logs/audio_module.log` contained lines like
  `Job <id> (None) succeeded` for every job, regardless of its actual type (`process_reference`,
  `tts_synthesize`, etc.) — a leaked-`None` logging defect that would make the log file nearly useless
  for debugging which kind of job succeeded/failed.
- **Root cause:** `app/jobs/queue.py`'s `_run` method logged `payload.get("job_type")`, but `payload`
  is the job's business-logic payload dict (e.g. `{"reference_id": ...}`), which never contains a
  `job_type` key — that field lives on the `Job` ORM row itself.
- **Fix:** captured `job_type = job.job_type` from the DB row while the first session was still open
  (before it closes), and used that local variable in both the success and (newly also) the failure log
  lines.
- **Verification:** restarted the server, submitted a `mock-tts` synthesis job, and confirmed the log
  now reads `Job <id> (tts_synthesize) succeeded`. Full suite re-run: **31 passed, 0 failed.**

### Non-defect investigated and ruled out — apparent Tamil-text mojibake
While testing Tamil-script synthesis, the JSON response for `generated.text` displayed as visibly
garbled characters (`\u00e0\u00ae\u00b5...`) in my terminal capture. I did **not** assume this was a
real bug — I verified it directly by reading the stored value back from the SQLite database in Python
and printing its actual Unicode codepoints. The stored text was **exactly** the Tamil codepoints that
were submitted (`U+0BB5, U+0EB3, U+0BB3, U+0BAE, U+0BCD, ...`). The garbled appearance was purely a
Windows console/terminal-capture codepage artifact in my own tooling, not a defect in the application's
storage, encoding, or API. No fix was needed; documented here only so the "garbled Tamil" observation
isn't mistaken for an unaddressed bug.

---

## 7. Mock-TTS labeling audit — VERIFIED, no leaks found

Checked every response field across all code paths exercised in this session:

| Location | Mock run | Real run |
|---|---|---|
| `TTSAdapter.describe()["provider"]` | `"mock-tts"` | `"local-pyttsx3"` |
| `TTSAdapter.describe()["is_mock"]` | `true` | `false` |
| `GeneratedAudio.provider` (DB + API) | `"mock-tts"` | `"local-pyttsx3"` |
| `GeneratedAudio.provider_metadata.is_mock` | `true` | `false` |
| `GeneratedAudio.validation_details.provider_metadata.is_mock` | `true` | `false` |

`mock-tts` was never observed reporting itself as real, and `local-pyttsx3` was never observed
reporting `is_mock: true`, across the happy path, the plan-driven synthesis, and the forced-failure
(Tamil) path.

---

## 8. Female-voice evaluation — summary (full detail in `FEMALE_VOICE_EVALUATION.md`)

- Two SAPI5 voices are installed on this machine, confirmed **explicitly female** by real engine
  metadata (both `pyttsx3.getProperty('voices')[i].gender` and the raw
  `HKLM:\SOFTWARE\Microsoft\Speech\Voices\Tokens\*\Attributes` registry values report `Gender: Female`
  — not inferred from the voice name): Microsoft Zira Desktop (en-US) and Microsoft Hazel Desktop
  (en-GB). No Tamil-capable voice exists on this machine (confirmed by attempting Tamil synthesis with
  both — it produced 0-length audio, not fake/garbled output).
- All four reference YouTube URLs: titles/channels were fetched successfully via a text-only page fetch
  (no audio was downloaded, extracted, or listened to — no such capability exists in this environment).
  Voice/performance quality for all four is reported as **BLOCKED — not evaluable with available
  tools**, exactly as instructed. No voice-quality claims were fabricated.

---

## 8a. Defects found in the self-test script itself (`docs/SELF_TEST_COMMANDS.ps1`), while building it

Writing a genuinely repeatable PowerShell script surfaced three real PowerShell-5.1-specific bugs of
its own, each found by actually running the script and reading the failure, not assumed:

1. **Em-dash (`—`, U+2014) in a BOM-less UTF-8 `.ps1` file broke the parser.** Windows PowerShell 5.1
   reads `.ps1` files using the system codepage unless a UTF-8 BOM is present; without one, the 3-byte
   UTF-8 em-dash sequence was misread and corrupted string-literal boundaries several lines later,
   producing confusing "missing terminator"/"missing closing brace" errors far from the real cause.
   Found by bisecting the file with `[System.Management.Automation.Language.Parser]::ParseInput` at
   successive top-level block boundaries until the exact failing line was isolated. **Fixed** by
   replacing the em-dash with plain ASCII `--` and re-saving the file with an explicit UTF-8 BOM.
2. **`Invoke-RestMethod -Form` does not exist in Windows PowerShell 5.1** (it is a PowerShell 6+/7+
   cmdlet feature) — the multipart file upload step failed immediately with "A parameter cannot be
   found that matches parameter name 'Form'". **Fixed** by shelling out to the in-box
   `C:\Windows\System32\curl.exe` (confirmed present, version 8.13.0, no third-party tool needed).
3. **Inline JSON passed as a `-F` argument to curl got mangled by PowerShell's native-argument
   marshalling** (embedded `{`/`"` characters in a string literal passed to a native executable can be
   altered in transit), causing the server to correctly reject it as invalid JSON
   (`rights_metadata must be valid JSON`) — the server-side validation worked exactly as intended here;
   the bug was purely in how the self-test script constructed the curl call. **Fixed** by writing the
   JSON to a temporary file and using curl's `-F "field=<file"` syntax instead of an inline string, the
   same technique already used for the transcript text field.

All three were found by actually executing the script and reading the real error, then re-running it
to confirm the fix, exactly like every other defect in this report — not assumed or guessed. The script
was then run three more times to confirm repeatability (`PASSED` twice on free ports, and a third run
against a deliberately pre-occupied port to confirm it correctly detects the conflict, names the owning
PID, and exits nonzero rather than silently failing or picking a port on its own).

---

## 9. Remaining blockers / next steps

1. **Forced alignment remains an honest approximation** (VAD + proportional timing), not a true
   phoneme-level forced aligner — this is a pre-existing, documented limitation from the build task,
   unchanged by this self-test pass (no neural aligner was installed; same practicality reasoning as
   before).
2. **No female-voice reference audio could be evaluated** — this environment has no YouTube
   audio-extraction/listening capability, and the task explicitly forbade attempting one without
   authorization.
3. **Only 2 installed TTS voices total, both English, both female.** If a male or non-English (e.g.
   Tamil) voice is required, it is not available via `pyttsx3`/SAPI5 on this machine; see
   `FEMALE_VOICE_EVALUATION.md` for realistic alternatives (named, not installed).
4. **Deprecation warnings** (`pydantic` class-based `Config`, FastAPI `on_event`) are cosmetic and
   unchanged from the build pass — noted again here for completeness, not treated as defects, not
   fixed (out of scope for this self-test; fixing them is a trivial follow-up if desired).
5. Graceful (non-forced) shutdown was tested via `Stop-Process` without `-Force`; the process always
   terminated and freed its port, but Windows does not deliver a true POSIX `SIGINT` through
   `Stop-Process`, so uvicorn's own "Shutting down"/"Finished server process" log lines were not
   observed — only confirmed via process-exit and the freed port. A fully graceful SIGINT-handled
   shutdown would need to be tested interactively (Ctrl+C in a foreground terminal) rather than via a
   backgrounded PowerShell-launched process; this is a testing-method limitation, not a demonstrated
   module defect.

---

## 10. Exact repeat commands

```powershell
cd "D:\Jana Soft\defenceStudio\standalone\audio-module"

# admin check
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)

# env + deps
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python fixtures\generate_fixture.py

# tests
.venv\Scripts\python -m pytest -v

# server
.venv\Scripts\python -m uvicorn app.main:app --port 8020
# then: http://127.0.0.1:8020/health , /docs , /ui/

# voice enumeration
.venv\Scripts\python -c "import pyttsx3; [print(v.id, v.name, v.gender, v.languages) for v in pyttsx3.init().getProperty('voices')]"

# female-voice audit script (writes WAVs to fixtures/_female_voice_audit_out/)
.venv\Scripts\python fixtures\_female_voice_audit.py

# repeatable automated self-test (see docs/SELF_TEST_COMMANDS.ps1)
powershell -File docs\SELF_TEST_COMMANDS.ps1
```
