# P1 English pipeline verification

- MP4: `E:\Jana Soft\DefenceStudio\AGENT_WORK\CODEX_EVIDENCE\master-readiness-20261015\real-module-subtitled.mp4`
- SHA256 match: True
- Decode exit: 0
- Git branch: main

See `report.json` for speech metrics, frame analysis, and TTS control measurements.

## 2026-10-15 continuation addendum

Execution recovered in PowerShell. `python --version` returned 3.12.10, FFmpeg/ffprobe report 8.1.1, and Git commands work. The verification runner was reviewed before use. It writes only under this P1 evidence directory and the retained MP4 is read-only. Earlier blocked and first live-run reports were copied to `report.blocked-20261015.*` and `report.first-live-run.*`; the intermediate report is retained as `report.second-live-run.*` and `report.pre-final-run.*`.

The runner's first live pass exposed an output-format mistake: FFmpeg inferred the `.pcm` file as an ALP container. A second pass exposed that the adapter correctly refuses to overwrite prior WAVs. `run_p1_verify.py` now specifies raw signed 16-bit PCM and creates timestamped frame/TTS directories; it exits nonzero when PCM extraction or TTS measurement fails. The final command `python AGENT_WORK/CODEX_EVIDENCE/p1-pipeline-20261015/run_p1_verify.py` exited 0; full output is retained in `run-p1-verify-final.log`, exit in `run-p1-verify-final.exit`, and machine-readable results in `report.json`.

The MP4 hash still matches `914f576bb18575e023fe23ea01debf9c187ce1f198abe8ade07af1f531fda265` (153,069 bytes). FFmpeg full decode exits 0. ffprobe reports H.264 1280×720 at 25 fps for 8.720 s and AAC mono 48 kHz for 8.716 s (4 ms difference). The retained output QC measurement remains −21.7 LUFS / −7.4 dB true peak. New 48 kHz PCM analysis measured −22.56 dBFS RMS and three contiguous framesets below −35 dB: 1.75–2.75 s, 5.25–6.25 s, and 7.75–8.7253 s. Each exceeds 0.25 s; these align with scene transitions and the clip tail, but remain a listening/QC observation, not a silence-free claim. Frame RMS coefficient of variation is 0.8263, indicating varying energy; it is not a speech classifier.

Frames at 1.0, 3.5, and 6.5 s were visually inspected. All show legible, high-contrast English captions placed near the bottom; the underlying picture is the fixture's explicit “VISUAL GAP / No approved visual material” card. No Tamil subtitle was rendered. There is no retained subtitle sidecar, so exact subtitle cue timing cannot be independently reconstructed. Prior integration evidence establishes cue count and total-duration assertions only.

Real local SAPI voice `TTS_MS_EN-GB_HAZEL_11.0` generated the retained WAV samples under `tts-20261015-233755/`, measured with the existing `app.pipeline.measure` analyzer. Requested rate 110 vs 230 produced 4.7943 vs 2.4973 s for the same seven-word fixture (87.60 vs 168.18 whitespace-derived WPM). Volume 0.25 vs 1.0 produced −37.08 vs −18.32 dBFS RMS. Pitch 0 vs +3 semitones produced pYIN median 181.89 vs 218.81 Hz; duration stayed 3.4586 s. SHA-256, voice ID, settings, confidence fields, and other metrics are in `report.json`. These pairs are mechanically distinguishable single samples, not statistical or human listening acceptance.

Code review confirmed the core Audio Module client omitted voice controls and AudioService always recorded `applied: false`. The new `voice-controls-before-fix.log` preserves the regression reproduction: the outgoing request had no controls (1 failed, exit 1). The client now passes supported non-neutral rate/pitch settings through the module's existing optional controls field. AudioService maps relative rate around the existing 170 WPM neutral profile and relative pitch to semitones, only for local-pyttsx3 and within the adapter's documented bounds. It records applied values only when the returned provider metadata matches; unsupported tone and unconfirmed/range-exceeding settings are listed explicitly. Emphasis remains unsupported. A synthetic render regression verifies 350 ms of exact inserted PCM silence and the next scene's measured start time.

Focused command from `backend/`: `& "$env:TEMP\defence-master-backend-20261015\Scripts\python.exe" -m pytest -q -ra -p no:cacheprovider tests/test_audio_rm4.py tests/test_render_rm5.py::test_concat_narration_inserts_requested_inter_scene_silence` exited 0: 10 passed, 1 skipped, 1 warning in 21.25 s. The skip is `test_real_audio_module_integration_english` because no Audio Module service was running; the standalone adapter itself was exercised directly with real OS speech. The complete backend suite was not rerun. `git diff --check` exited 0; Git printed only an LF-to-CRLF advisory for the evidence runner.

Tamil speech remains BLOCKED: this session made no provider call, model/font installation, or Tamil synthesis. The retained clip is an isolated synthetic English fixture, not an approved episode. No protected job/database/review history was accessed in this continuation; no credentials, commit, or push were changed by this work. Human speech quality and exact burned-in cue timing remain NOT VERIFIED.
