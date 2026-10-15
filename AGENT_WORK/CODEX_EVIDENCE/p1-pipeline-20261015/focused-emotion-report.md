# Focused emotion handoff — 2026-10-15

## Test invocation

Previous failure: pytest logs were resolved relative to the component directory, so the evidence file was never written and `$LASTEXITCODE` could be overwritten. This run used `run_focused_emotion_tests.py` with absolute paths under `AGENT_WORK/CODEX_EVIDENCE/p1-pipeline-20261015/`, created the directory first, and wrote each pytest exit code immediately after that process returned.

- Log: `focused-emotion-tests.log`
- Exit: `focused-emotion-tests.exit` = 0
- `git diff --check`: `diff-check-emotion.exit` = 0 (LF/CRLF advisory only)

## Results

1. cwd=`components/voice-engine`  
   `pytest -q -ra -p no:cacheprovider tests/test_generation.py`  
   **34 passed, 0 skipped, 0 warnings, exit 0** (0.98s)

2. cwd=`backend`  
   two `test_voice_profile_application` contract tests  
   **2 passed, 0 skipped, exit 0** (5.54s)

Tests started (pytest collected and printed progress). Combined exit 0.

## Emotion honesty

- Forwarded only if `capabilities()["controls"]` includes `emotion`.
- Default `VoiceModel.applied_controls` never implies emotion.
- `control_application.applied` includes emotion only when the adapter confirms it.
- Advertising but ignoring emotion: forwarded, listed in `unsupported`, not applied.
- SAPI/synthetic: emotion unsupported; tempo/energy/emphasis/pauses unchanged.
- Profile application still does not auto-apply heuristic emotion labels.

Tamil TTS remains BLOCKED. No models downloaded, no paid APIs, protected job/DB untouched.
