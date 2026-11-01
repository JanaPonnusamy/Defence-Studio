# Voice Lab API (backend contract, Agent A, 2026-11-01)

Base: `/api/voice-lab`. Every route uses the existing owner auth. Errors are `{"detail": ...}`, where `detail` is a string or `{code, ...}`. The codes:

- 404 not found or another owner's resource
- 409 blocked state (`TRAINING_BLOCKED`, `MODEL_NOT_INSTALLED`, `IDENTITY_*`, `CONSENT_*`, `STALE_REVISION`, `TRACK_LOCKED`, …)
- 410 `ARTIFACT_FILE_MISSING`
- 413 too large
- 422 validation
- 504 runner timeout
- 507 `INSUFFICIENT_DISK`

Jobs (dataset segmentation, reference analysis, generation, training) run asynchronously on the shared workflow queue. Poll the resource's `status`.

## System, models, storage
| Route | Notes |
|---|---|
| `GET /system?refresh=` | measured CPU/RAM/GPUs/torch/CUDA/disks, `training_supported`, `training_blockers`, per-model capability |
| `GET /hardware` | (DirectML session) execution-provider report |
| `GET /models`, `GET /models/{id}` | code/weights/data licence kept separate, `commercial`, `production_allowed`, `install` |
| `GET /licences` | stored licence register (`vl_model_licences`) |
| `GET /storage` | bytes by kind (all PRESERVE), temp leftovers, missing files, model store size |

## Identities
`POST /consents` `{subject_name, subject_role: NARRATOR|REFERENCE_SPEAKER, scope:[TRAINING|GENERATION|COMMERCIAL|ANALYSIS], document_ref}` · `GET /consents` · `POST /consents/{id}/revoke`

`GET/POST /narrators` `{display_name, gender_label?, languages:["ta"], description?, consent_id?}` (no limit on count) · `GET/PATCH /narrators/{id}` (GET includes versions, datasets, training jobs, recent generations) · `POST /narrators/{id}/archive|restore`

`GET /narrators/{id}/versions` · `POST /narrators/{id}/versions` `{model_id, speaker, description, f0_median_hz?, f0_min_hz?, f0_max_hz?, activate=true}` adds a BASE_SPEAKER version (untrained). `POST /narrators/{id}/versions/{mvid}/activate` is also the rollback path. A speaker or checkpoint identity can belong to one narrator only (`IDENTITY_IN_USE`).

## Datasets (one narrator each; NARRATOR consent with TRAINING scope)
`POST /datasets` `{narrator_id, name, language, consent_id}` · `GET /datasets?narrator_id=` · `GET /datasets/{id}` (files, versions, readiness) · `GET /datasets/{id}/readiness`

`POST /datasets/{id}/files` multipart `file` + `source_note` → 202 `{file_id, qc, status: QUEUED}`, then the file is segmented asynchronously. A file already in another narrator's dataset is rejected (`IDENTITY_MIXING`).

`GET /datasets/{id}/segments?limit&offset` → `{total, items:[{segment_id, artifact_id, start_ms, end_ms, duration_ms, transcript, transcript_status, qc:{…, issues}, qc_status: PASS|WARN|FAIL, duplicate_of, excluded, split, alignment_status: "NOT_ALIGNED"}]}`

`PATCH /segments/{id}` `{transcript?, transcript_status?: NONE|DRAFT|REVIEWED, excluded?}` → `{warnings}`. A non-Tamil-script transcript warns.

`POST /datasets/{id}/versions` creates an immutable manifest from eligible segments (reviewed transcript, not FAIL, not duplicate, not excluded), with a deterministic train/validation/test split. `POST /dataset-versions/{id}/approve` requires readiness `READY` and an intact manifest.

## Training
`POST /training-jobs/preflight` `{narrator_id, dataset_version_id, model_id, target: local|cloud}` → `{ready, blockers:[{code, detail}], hardware, licence, adapter}`

`POST /training-jobs` (+`Idempotency-Key`) returns **409 `TRAINING_BLOCKED` with the blockers and creates nothing** when blocked. On this PC the blockers are `NO_CUDA_GPU`, `INSUFFICIENT_VRAM` and `INSUFFICIENT_RAM`. Cloud needs `DEFENCE_STUDIO_VOICE_LAB_CLOUD_TRAINING=1` plus a configured worker URL.

`GET /training-jobs?narrator_id=` · `GET /training-jobs/{id}` (status QUEUED|RUNNING|PAUSE_REQUESTED|PAUSED|CANCEL_REQUESTED|CANCELLED|FAILED|COMPLETED, `metrics.checkpoints`, `metrics.history`) · `GET /training-jobs/{id}/log` · `POST …/cancel` · `POST …/pause` (only when the trainer supports it). Resume is `POST /training-jobs` with `resume_from_job`.

`POST /training` keeps Agent B's shape (`{identity, consentRef, datasetVersionId?}`) and maps onto the same gate.

## Reference delivery + style profiles
`POST /references` multipart `file`, `name`, `authorization_ref` → 202 (analysis queued) · `POST /references/url` `{name, source_url, authorization_ref}` (409 `URL_IMPORT_UNAVAILABLE` without yt-dlp) · `GET /references` · `GET /references/{id}` (`separation.status` is `NOT_AVAILABLE`) · `POST /references/{id}/retry`

`GET /references/{id}/prosody?frames=true` → `summary` (f0 median/min/max Hz, variation and range in semitones, rate estimate, pauses, energy range, emotion as an INFERENCE), `phrases[]` (t0/t1, pauses, `rel_median_st`, `range_st`, `slope_st`, intonation, energy, emphasis), `regions[]` (SPEECH/SILENCE/NON_SPEECH_SOUND), `frames` (10 ms f0/energy).

`POST /style-profiles` `{reference_id, name}` · `GET /style-profiles[/{id}]` · `PATCH /style-profiles/{id}/labels` `{feature_key: emotion|delivery_label, value}` (human review) · `POST /style-profiles/{id}/approve` (immutable afterwards).

## Generation + Script Studio
`POST /generations` `{narrator_id, text, language, model_version_id?, style_profile_id?, target_duration_ms?, config{seed, style_options}, force}` → 202. An identical request returns `reused: true`. Without an installed runtime it returns 409 `MODEL_NOT_INSTALLED`, with no fallback voice. `GET /generations[/{id}]` returns `audio{artifact_id, url, sha256, duration_ms, sample_rate}`, `timing{reference/target/raw/generated duration, duration_diff_ms, style_report{mode, phrases[stretch_requested/applied/capped, pitch_shift_st, pitch_clamps, gain_db]}, comparison{phrase_start_error_ms, relative_pitch_correlation, output_f0_median_hz}}` and `provenance`. `POST /generations/{id}/cancel|retry`.

`POST /script-generation` `{text|scriptText, narrator_id|voiceId, style_profile_id|styleProfileId, model_version_id, script_id, chapter_id|chapterId, paragraph_id, target_duration_ms|timingTargetSec, config, force}` → `{generation_id, status, reused, audio, duration_ms, format, narrator{narrator_id, model_version_id, model_version}, style_profile_id, config, timing, provenance, error, limitations[]}`.

`POST /script-generation/batch` `{script_id, chapter_id, narrator_id, style_profile_id?, paragraphs:[{paragraph_id, text, target_duration_ms?, force?}]}` regenerates only paragraphs whose content or settings changed. `GET /script-generation/{generation_id}`.

## Audio artifacts, comparison, reviews
`POST /artifacts` multipart (import into a project, stored as 48 kHz WAV) · `GET /artifacts/{id}` · `GET /artifacts/{id}/audio` (range requests supported) · `GET /artifacts/{id}/waveform?px_per_s=` → `{min[], max[]}` · `GET /artifacts/{id}/pitch?step_ms=` → `{f0_hz[], energy_db[]}` · `GET /artifacts/{id}/analysis` (QC, EBU R128 loudness, phrases) · `POST /artifacts/{id}/approve`

`GET /compare?artifact_ids=a,b,c` → per item: duration, phrase starts, pauses, relative pitch, and `vs_first` differences.

`POST /reviews` `{artifact_id, generation_id?, ratings{name: 1..5}, notes}` (bound to the model version) · `GET /reviews?artifact_id=&model_version_id=`

## Mixer projects (non-destructive)
`POST /projects` `{name, sample_rate}` · `GET /projects` · `GET /projects/{id}` → doc `defence-studio.voice-lab-project.v1` `{revision, sample_rate, duration_ms, can_undo, can_redo, tracks:[{track_id, name, role, gain_db, mute, solo, locked, effects[], clips:[{clip_id, artifact_id, start_ms, in_ms, out_ms, gain_db, fade_in_ms, fade_out_ms, crossfade_next_ms}]}]}`

`POST /projects/{id}/ops` `{op, expected_revision}` → `{doc, result}`. Op types:

- `set_project` {name?, sample_rate?}
- `add_track` {role, name?, gain_db?}. Roles: male_reference, male_narrator, female_narrator, female_styled, alternative, music, other
- `update_track` {track_id, name?, role?, gain_db?, mute?, solo?, locked?, effects?}. Effects: eq{freq_hz, gain_db, q}, highpass/lowpass{freq_hz}, denoise{reduction_db}
- `remove_track`, `move_track` {index}
- `add_clip` {track_id, artifact_id, start_ms, in_ms?, out_ms?, gain_db?, fades?}
- `update_clip` {clip_id, start_ms?/in_ms?/out_ms?/gain_db?/fade_*?/crossfade_next_ms?/track_id?}. This one call covers move, trim, gain and fades
- `split_clip` {clip_id, at_ms}
- `delete_clip`, `duplicate_clip` {clip_id, start_ms?, track_id?}
- `cut_range` {track_id, t0_ms, t1_ms, ripple} → `result.clipboard`
- `copy_range` (read-only, not logged)
- `paste` {track_id, at_ms, clipboard, ripple?}
- `silence_range` {track_id, t0_ms, t1_ms}
- `insert_silence` {track_id, at_ms, duration_ms}
- `join_clips` {clip_ids}. Adjacent windows merge. Different sources render into a new artifact; the originals stay untouched

Locked tracks reject edits (`TRACK_LOCKED`), except mute, solo and unlock.

`POST /projects/{id}/undo|redo` · `GET /projects/{id}/history` · `POST /projects/{id}/export` `{format: wav|flac|mp3|ogg, sample_rate?, channels 1|2, normalize_lufs?, t0_ms?, t1_ms?, expected_revision?}` → `{artifact, url, integrity{decoded vs expected duration, sha256}, loudness_before/after, normalization_gain_db, peak_limited}`

`POST /projects/{id}/comparison` `{tracks:[{role, name?, artifact_id|generation_id|reference_id, start_ms?}]}` adds one track per item. Use it to build the side-by-side view (male reference, male narrator, female narrator, female styled, alternatives).

Playback, zoom, seek, loop, speed and A/B are client-side. The server supplies audio, waveform peaks and pitch curves.
