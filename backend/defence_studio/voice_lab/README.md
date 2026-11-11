# Voice Lab backend (`defence_studio.voice_lab`)

Multi-narrator Tamil voice platform: narrator identities, datasets, training orchestration, reference
delivery analysis, cross-voice style matching, generation, a non-destructive mixer, and Script Studio integration.
API: `docs/voice_lab/VOICE_LAB_API.md`. Evaluation and licences: `docs/voice_lab/VL0_EVALUATION_AND_CONTRACT.md`,
`backend/config/voice_lab_models.json`. Status: `docs/voice_lab/VOICE_LAB_STATUS.md`.

| Module | Role |
|---|---|
| `schema.py` | `vl_*` tables, versioned in `vl_schema_meta` (independent of `database.py` SCHEMA_VERSION) |
| `context.py` | DB access, content-addressed artifact store (`<storage>/voice_lab/artifacts/<sha[:2]>/<sha>.<ext>`), disk guard, licence seeding |
| `narrators.py` | consents, narrators, per-narrator model versions; `identity_key` UNIQUE across narrators |
| `datasets.py` | ingest, QC, segmentation, duplicates, transcripts, readiness, immutable versions, approval |
| `training.py` | hardware/licence/dataset preflight; trainer adapters; checkpoints and model versions |
| `hardware.py` | measured CPU/RAM/GPU/VRAM/torch/CUDA/ROCm/disk; per-model training capability |
| `prosody.py` | 10 ms pitch/energy (EXT-03 tracker), phrases, pauses, emphasis, intonation, emotion INFERENCE |
| `references.py` | reference media, analyses, style profiles (identity-free features, human labels, approval) |
| `style.py` | phrase-level delivery transfer (formant-preserving rubberband, capped, relative pitch only) |
| `inference.py` + `runtime/parler_runner.py` | warm local runner in the isolated `.venv-voicelab`; no fallback voice |
| `execution.py` | (DirectML session) execution-provider selection |
| `generation.py` | content-keyed generation, provenance, Script Studio result shape; Tamil text via `services/tts_text.py` (approved text kept) |
| `conversion.py` | owner-identity voice-conversion interface + guards; only provider today is `UnavailableConverter` (no model approved) |
| `verification.py` | speaker-verification interface: owner/reference similarity, leakage + identity verdicts; never PASS without validated calibration |
| `projects.py` | timeline ops, undo/redo, persistence, mixing, effects, export with integrity check |
| `service.py` | facade, workflow-queue stages, in-process worker, restart reconcile, storage report |

## Invariants
* A narrator's speech always comes from that narrator's own model version. A reference supplies only relative
  delivery features, and no speaker embedding is ever computed.
* A recording or identity can belong to one narrator only. Datasets need a NARRATOR consent with TRAINING scope.
* Training jobs exist only after every preflight check passes. On unsupported hardware the API returns 409 with blockers.
* Source artifacts are immutable; edits are clip windows plus an operation log.
* Voice Lab never deletes registered files. It removes only its own per-job scratch files in `voice_lab/tmp/`.

## Configuration (environment)
`DEFENCE_STUDIO_VOICE_LAB_VENV`, `_MODELS`, `_MODELS_CONFIG`, `_WORKER` (default 1), `_RESERVE_BYTES` (2 GiB),
`_MAX_UPLOAD_BYTES`, `_MIN_DATASET_MINUTES` (60), `_MAX_FILE_MINUTES` (120), `_MAX_TRAINING_JOBS` (1),
`_CLOUD_TRAINING` (off), `_CLOUD_WORKER_URL`; `HF_TOKEN` (owner-provided, for gated models only).

## Scripts
`scripts/voice_lab_install.ps1`, `scripts/voice_lab_env_check.ps1`, `backend/scripts/voice_lab_download.py`,
`backend/scripts/voice_lab_benchmark.py`, `backend/scripts/voice_lab_acceptance.py`.

## Tests
`cd backend && python -m pytest -q tests/test_voice_lab.py`
