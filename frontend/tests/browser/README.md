# Browser acceptance scripts (Fact Check F6, Voice F11)

Real headless Chrome driven over CDP (no extra dependencies; Node 24). Not part of `npm test`.
Start the app against a DISPOSABLE db/storage with dev auth and the in-app voice worker:

    DEFENCE_STUDIO_AUTH_SECRET=<32+ bytes> DEFENCE_STUDIO_DB_PATH=<tmp>/ds.sqlite3 DEFENCE_STUDIO_STORAGE_PATH=<tmp>/storage
    DEFENCE_STUDIO_DEV_MODE=1 DEFENCE_STUDIO_DEV_AUTH_BYPASS=1 DEFENCE_STUDIO_VOICE_WORKER=1
    python -m uvicorn defence_studio.api.app:app --app-dir backend --port 8030

Then `node tests/browser/fc.mjs` and `node tests/browser/voice.mjs` (BASE env overrides the URL).
They use synthetic fixtures and a locally synthesized OS-voice WAV; no external requests are made. Output in `tests/browser/out/`.

`inspector.mjs` (Inspector tab): seed a disposable db with mocked-provider attempts, serve it with
`DEFENCE_STUDIO_DEV_AUTH_OWNER` set to the seed owner, then `JOB=<job id> node tests/browser/inspector.mjs`.
