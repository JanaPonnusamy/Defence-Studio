# Manual verification walkthrough (PowerShell)

Assumes the server is running at http://127.0.0.1:8000 and
`fixtures/test_fixture_speech_like.wav` / `test_fixture_transcript.txt`
exist (generate with `python fixtures/generate_fixture.py`).

```powershell
# 1. Health check
curl http://127.0.0.1:8000/health

# 2. Register + upload a reference with a transcript
curl -X POST http://127.0.0.1:8000/references `
  -F "title=Manual Test" -F "language=en" `
  -F 'rights_metadata={"authorized": true, "notes": "test fixture"}' `
  -F "transcript_format=plain" `
  -F "transcript_text=$(Get-Content fixtures\test_fixture_transcript.txt -Raw)" `
  -F "audio_file=@fixtures\test_fixture_speech_like.wav;type=audio/wav"
# -> note the returned reference_id

# 3. Kick off processing (normalize -> align -> analyze)
curl -X POST http://127.0.0.1:8000/references/<reference_id>/process
# -> note the returned job_id

# 4. Poll job status until SUCCEEDED
curl http://127.0.0.1:8000/jobs/<job_id>

# 5. Inspect results
curl http://127.0.0.1:8000/references/<reference_id>
curl http://127.0.0.1:8000/alignment/<alignment_id>
curl http://127.0.0.1:8000/analysis/<analysis_id>
curl http://127.0.0.1:8000/audio/<audio_id>
curl http://127.0.0.1:8000/audio/<audio_id>/waveform

# 6. Build a performance profile from the analysis
curl -X POST http://127.0.0.1:8000/profiles/build `
  -H "Content-Type: application/json" `
  -d '{"profile_name": "Test Profile", "analysis_ids": ["<analysis_id>"]}'
# -> poll /jobs/<job_id> for result_id = profile_id

# 7. Build a performance plan
curl -X POST http://127.0.0.1:8000/profiles/plan `
  -H "Content-Type: application/json" `
  -d '{"profile_id": "<profile_id>", "script": "This is a major escalation.", "language": "en"}'

# 8. Synthesize with the MOCK adapter (no credentials needed)
curl -X POST http://127.0.0.1:8000/tts/synthesize `
  -H "Content-Type: application/json" `
  -d '{"text": "This is a major escalation.", "provider": "mock-tts"}'
# -> poll /jobs/<job_id>, then GET /generated/<generated_id> and /generated/<generated_id>/file

# 9. Synthesize with the REAL local adapter (uses the OS speech engine)
curl -X POST http://127.0.0.1:8000/tts/synthesize `
  -H "Content-Type: application/json" `
  -d '{"text": "This is a major escalation.", "provider": "local-pyttsx3"}'
```
