# Voice Lab — VL-0: hardware, model/licence evaluation, shared contract

Owner: Agent A (Claude Code, backend/AI). Date: 2026-11-01. Status: **VL-0 COMPLETED (evaluation + contract only; no code, no downloads, no installs)**.
Source spec: "Defence Era Voice Lab — Final Master Implementation Specification" (owner prompt 2026-11-01).
Builds on EXT-03 `components/voice-engine/` (analysis, style profiles, `VoiceModel` adapter interface, licence tables). Voice Lab extends it; nothing is re-implemented.

## 1. Hardware (measured 2026-11-01 on this PC)

| Item | Measured |
|---|---|
| CPU | Intel Core i7-9700 @ 3.00 GHz, 8 cores / 8 threads |
| RAM | 15.7 GB |
| GPU | AMD Radeon RX 560X (4 GB) + Intel UHD 630. **No NVIDIA GPU; `nvidia-smi` absent; no CUDA.** |
| Disk | C: 417 GB free · D: 1,772 GB free |
| Python / torch | 3.13.15 / torch **not installed** |
| ffmpeg | 9.0.2 (gyan.dev full build) |

**Consequence:** local fine-tuning of a modern neural TTS model (spec §4, §14 step 8) is **NOT feasible on this machine**. There is no CUDA, and 4 GB of AMD VRAM is not a supported training target for these stacks.
CPU **inference** is feasible in principle; its speed is **not yet measured**. Cloud GPU training stays **disabled** unless the owner explicitly enables it (spec §11).
Training requirements are reported separately. Inference will never be labelled as training.

## 2. Repository / checkpoint evaluation

Code licence and weight licence were checked **separately** (GitHub license API; Hugging Face model and dataset metadata). Commits were pinned with `git ls-remote … HEAD`.

| Candidate | Code (pinned) | Code licence | Weights (HF rev) | Weight licence | Training data licence | Commercial eligibility |
|---|---|---|---|---|---|---|
| **AI4Bharat Indic Parler-TTS** | github.com/huggingface/parler-tts @ `d108732cd57788ec86bc857d99a6cabd66663d68` | Apache-2.0 | `ai4bharat/indic-parler-tts` @ `660bc53c64ae83150613264111708a0465211237` (gated, auto) | Apache-2.0 (HF tag) | HF tag lists `ai4b-hf/GLOBE-annotated` only. The Indic speech sources behind it are **not yet verified** | **PROVISIONALLY ELIGIBLE**: recommended first install, pending (a) gate terms read and accepted by the owner, (b) the full model card and dataset licences reviewed |
| AI4Bharat IndicF5 | github.com/AI4Bharat/IndicF5 @ `13f7c4d627cc10111aea8fe9c0039462cacacdc7` | **None detected** (no LICENSE file; GitHub API 404) | `ai4bharat/IndicF5` @ `fb652a96e508091045890dae5bb99fe511facbcf` (gated, auto) | MIT (HF tag) | IndicVoices-R CC-BY-4.0, Rasa CC-BY-4.0 (attribution required) | **EVALUATION ONLY** until the code licence is confirmed and it is verified that weights are not initialised from F5-TTS base (CC-BY-NC). It is a reference-conditioned cloner: it may be conditioned **only** on the narrator's own authorised audio, never on a third-party reference (spec §6) |
| AI4Bharat Indic-TTS (FastPitch + HiFi-GAN) | github.com/AI4Bharat/Indic-TTS @ `ad2461c22c373f89a140c5d9fb617b101219bdad` | MIT | GitHub release `v1-checkpoints-release`, `ta.zip` 1,520,799,925 B. No separate weight licence stated | Unstated | IIT Madras IndicTTS database. Terms **UNVERIFIED** (iitm.ac.in unreachable during the check) | **EVALUATION ONLY** (MIT code does not license the weights or the data) |
| Bodhan AI Indic-Speak | No public GitHub repo found | n/a | `bodhan-ai/indic-speak-preview-v2`. **Revision not pinned** (HF API did not respond) | Indic Open Model License v1.0 + Llama 3.2 Community License (per model page) | Not verified | **LEGAL REVIEW REQUIRED**: hosting as a service needs written approval; share-alike derivatives. Not for production by default |
| SWivid F5-TTS (base) | github.com/SWivid/F5-TTS @ `45868abaabd74aabf03cd673d6dd00cc9d9c01ae` | MIT | `SWivid/F5-TTS` @ `84e5a410…` | **CC-BY-NC-4.0** | Emilia | **NOT ELIGIBLE** (non-commercial weights) |
| Meta MMS-TTS Tamil | (transformers) | Apache-2.0 | `facebook/mms-tts-tam` @ `e9cf59da…` | **CC-BY-NC-4.0** | n/a | **NOT ELIGIBLE** |
| Coqui TTS | github.com/coqui-ai/TTS @ `dbf1a08a…` | MPL-2.0 | per-model (XTTS = CPML, non-commercial) | varies | varies | Code usable; **no eligible Tamil weights identified** |

Not yet downloaded. The managed locations will be `third_party/voice_lab/<name>@<commit>/`, with LICENSE files preserved, and `models/voice_lab/<name>@<rev>/`. Both stay git-ignored except a manifest. Transitive dependency licences are checked at install time (VL-1), not here.

## 3. Style-transfer approach (proposal; spec §6–7)

Identity comes **only** from the female narrator's own model and voice. The male reference contributes measured **relative** features only:
1. Analyse the reference with the existing `voice_analyzer`: F0 contour, pauses, energy, rate, and phrase boundaries with timestamps.
2. Generate with the female narrator. Map global controls (rate, pitch variation, expressivity) through model controls; Parler-TTS takes these as a text description.
3. Post-process with DSP on the female output: per-phrase time alignment to the reference pause grid, with a hard stretch-factor cap (default 0.85–1.15) and the deviation reported. Then F0 reshaping in **semitones relative to each speaker's median**, `f0_out = median_female · 2^(scale·(st_ref − median_ref_st)/12)`, clamped to the narrator's measured range. Absolute male pitch is never copied. Candidate toolkit: pyworld (MIT wrapper, WORLD modified-BSD), licence to be confirmed at VL-1.
4. No speaker embedding from the reference is ever computed or passed to the generator.

## 4. Schema (claim + naming decision)

- Spec table names `voice_profiles` and `voice_model_versions` **already exist** in EXT-03 with different meanings (the existing `voice_profiles` are style profiles). To avoid a collision, all new Voice Lab tables use the prefix **`vl_`**. Existing engine tables are reused where they match: `voice_licenses` → licence records, `voice_profiles`/`voice_profile_versions` → style profiles.
- Until v14 (uncommitted, another session) and A's v15 settle, Voice Lab tables are created by `voice_lab/schema.py` with `CREATE TABLE IF NOT EXISTS` (the same pattern as Image Research). They move into `database.py` under a claimed version later. **Tentative claim: v16.**
- Large audio is never stored in SQLite; rows hold path + SHA-256 + bytes + duration.

## 5. API contract for Cursor (proposal; namespace `/api/voice-lab/*`, owner-auth, `Idempotency-Key` on POST that creates jobs)

| Route | Returns |
|---|---|
| `GET /system` | measured hardware `{cpu, ram_bytes, gpus:[{name, vendor, vram_bytes, cuda}], disks:[…], training_supported, training_blockers:[…], measured_at}` |
| `GET /models` · `GET /models/{id}` | installed and evaluated models with `{code_repo, commit, weights_rev, code_licence, weight_licence, data_licence, commercial: ELIGIBLE\|PROVISIONAL\|EVALUATION_ONLY\|NOT_ELIGIBLE, local_path, installed}` |
| `GET/POST /narrators` | narrator identities `{narrator_id, gender_label, consent_record_id, model_version_id}`; male and female are always separate |
| `POST /datasets` · `POST /datasets/{id}/files` (multipart WAV) · `GET /datasets/{id}` · `GET /datasets/{id}/segments` · `PATCH /segments/{id}` (transcript) · `POST /datasets/{id}/approve` | dataset + per-segment QC `{duration_ms, clipping, snr_db_est, silence_ratio, duplicate_of, transcript, alignment_status}` and quality report |
| `POST /training-jobs` · `GET /training-jobs/{id}` | returns HTTP 409 `TRAINING_UNSUPPORTED_ON_HARDWARE` here, with requirements; never simulated |
| `POST /references` (URL or upload) · `POST /references/{id}/analyse` · `GET /references/{id}/prosody` | `{segments:[{t0_ms,t1_ms,f0_hz:[…],f0_step_ms,energy_db:[…],pause_after_ms,emphasis,emotion:{label,confidence,kind:"INFERENCE"}}], summary:{f0_min,f0_max,f0_median,rate_syl_s,…}, quality}` |
| `POST /style-profiles` · `POST /style-profiles/{id}/approve` · `PATCH …/labels` (human review) | style profile bound to analysis hash |
| `POST /generations` `{text, narrator_id, style_profile_id?, target_duration_ms?, chapter_id?, paragraph_id?, config}` · `GET /generations/{id}` · `GET /generations/{id}/audio` | `{artifact_id, sha256, duration_ms, sample_rate, timing:{reference_ms, generated_ms, diff_ms, stretch_factor, pause_alignment:[…]}, provenance}` |
| `GET/POST /projects` · `GET/PUT /projects/{id}` (with `revision` for optimistic locking) · `POST /projects/{id}/ops` · `POST /projects/{id}/undo` · `/redo` · `POST /projects/{id}/export` `{format: wav\|flac\|mp3, sample_rate, normalize_lufs?}` | non-destructive timeline (below) |
| `GET /artifacts/{id}/audio` · `GET /artifacts/{id}/waveform?px_per_s=` · `GET /artifacts/{id}/pitch` | streamed audio, peak data, F0 curve for the comparison views |
| `POST /reviews` | listening notes + 1–5 ratings per artifact |

**Timeline JSON (shared by UI and backend):**
```json
{"project_id":"…","revision":7,"sample_rate":48000,
 "tracks":[{"track_id":"…","name":"Female · ref style","role":"female_styled","gain_db":0,"mute":false,"solo":false,"locked":false,
   "clips":[{"clip_id":"…","artifact_id":"…","start_ms":0,"in_ms":120,"out_ms":6370,"gain_db":0,"fade_in_ms":10,"fade_out_ms":10,"crossfade_next_ms":0}]}]}
```
Edits never modify source artifacts. Cut, split, trim, move, duplicate, silence and gain are clip-list operations logged in `vl_audio_edit_operations`, and undo/redo replays that log. Rendering happens only on export.
UI-only state (zoom, playhead, selection) stays client-side.

## 6. Proposed phases (each needs owner authorisation; one per prompt)

VL-1 install Indic Parler-TTS (after gate acceptance) + environment check script + baseline Tamil sample + CPU speed measurement ·
VL-2 `vl_` schema + system/models/narrators APIs · VL-3 dataset ingest/QC/segmentation · VL-4 reference prosody API (wrap `voice_analyzer`) + style profiles ·
VL-5 generation + style transfer + timing report · VL-6 project/timeline/edit-ops/export backend · VL-7 Script Studio integration · VL-8 acceptance (spec §14) with Cursor's UI.

## 7. Blockers / owner decisions

1. Accept the Hugging Face gate terms for `ai4bharat/indic-parler-tts` (and provide an HF token locally) before VL-1. Agent A will not accept licence terms on the owner's behalf.
2. Authorised male (~10 h) and female narrator recordings, plus consent records: **not present**.
3. Training: needs an NVIDIA GPU (or an explicitly enabled cloud GPU). Unavailable locally.
4. Confirm the `vl_` table prefix (vs the spec's bare names, which collide with EXT-03).
5. `E:\Jana Soft\DefenceStudio` still does not exist on this machine (see coordination §2).
