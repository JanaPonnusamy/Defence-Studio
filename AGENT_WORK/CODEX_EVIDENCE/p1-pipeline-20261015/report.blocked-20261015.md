# P1 English media pipeline verification (2026-10-15)

## Summary

This session **could not execute shell commands** (integrated terminal spawn failure: Windows `workspace_readwrite` sandbox backend unavailable). No ffmpeg, git, or Python subprocess checks were run here.

The retained MP4 at `AGENT_WORK/CODEX_EVIDENCE/master-readiness-20261015/real-module-subtitled.mp4` **is present** (binary read attempted). **SHA256 was not recomputed** in this session; retained evidence expects `914f576bb18575e023fe23ea01debf9c187ce1f198abe8ade07af1f531fda265` (153069 bytes).

## Retained evidence (not re-run)

| Check | Value |
|--------|--------|
| Full decode exit | 0 (`media-validation.json`) |
| Video / audio duration | 8.720s / 8.716s (Δ 4 ms) |
| QC LUFS / peak | -21.7 LUFS, -7.4 dBFS true peak |
| Integration tests | 3 passed (`real-module-tests.log`) |

## Subtitles / timings

No SRT/VTT sidecar sits next to the retained MP4. Subtitles are **burned in** only. Prior integration test asserted `cue_count >= scene count` and `|video duration - audio duration| < 0.25s`.

English narration scenes: opening hook, one section, closing (see `test_audio_rm4.py::_english_plan`).

## Voice controls (code review)

- **Core pipeline** `AudioModuleClient.synthesize()` POSTs only `text`, `provider`, `language`, `voice_id` — no TTS controls.
- **AudioService** records `voice_direction.applied: false`.
- **PROFILE** (`controls.py`): pauses and emphasis unsupported; energy maps to volume in performance plans only.
- **Renderer** `pause_after_ms` inserts PCM silence in `_concat_narration` (mix-time, not TTS).
- **Local pyttsx3** supports `rate_wpm`, `volume`, and optional `pitch_semitones` via librosa post-process — **not exercised** this session.

## Local completion

Run:

```powershell
& "E:\Jana Soft\DefenceStudio\AGENT_WORK\CURSOR_EVIDENCE\modules-e2e\venv-tts\Scripts\python.exe" `
  "E:\Jana Soft\DefenceStudio\AGENT_WORK\CODEX_EVIDENCE\p1-pipeline-20261015\run_p1_verify.py"
```

This overwrites `report.json` with live hash, decode, speech, frames, and TTS measurements.

## Gaps

Tamil TTS, human episode sign-off, and all runtime p1 checks listed in `report.json`.
