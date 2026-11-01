# Defence Era — Home PC TTS Audit (read-only)

Date: 2026-11-01 · Auditor: Claude Code (Opus 5.5) · Mode: STRICT READ-ONLY. This report is the only file created.
No pull/fetch/checkout/reset/stash/clean/commit/push, no installs, no downloads, no model runs, no source edits.

Goal under audit: **owner voice identity + reference narrator's delivery + new Tamil script → natural Tamil narration in the owner's voice.**

---

## 1. Session and computer identity — VERIFIED (home Lenovo)

| Item | Measured |
|---|---|
| Hostname | `DESKTOP-TPJPR62` |
| Make/model | LENOVO `F0E4000YIN` |
| OS | Windows 11 Home Single Language 10.0.26200 |
| User | `Jana Local Account` |
| Repo path | `D:\jana soft\DefenceEra` — exists |
| Git root | `D:/jana soft/DefenceEra` |
| Branch / HEAD | `develop` @ `c7f8d50` |
| Remotes | `origin` = github.com/JanaPonnusamy/Defence-Studio · `axythic` = github.com/axythic-dev/Defence-Studio |
| Worktrees | main tree + `D:/rsw1` (detached @ `c7f8d50`) |
| Office paths | `E:\Jana Soft\DefenceStudio*` not accessed (VOICE_LAB_STATUS.md also records that path does not exist on this machine) |

**Important finding:** "Defence Era" on the home PC is **the same Git project as the office DefenceStudio** (same `Defence-Studio` remotes). It is a separate clone, not a separate codebase. The Voice Lab backend (`70b7bb3`, 2026-11-01) was written **on this PC**. Most "office audit" findings therefore describe the same code, possibly at an older commit.

## 2. Git safety record

- HEAD `c7f8d50` docs: record acceptance sprint. Relevant history: `70b7bb3` Voice Lab backend (narrators, datasets, training gate, delivery matching, mixer) · `d03c7c7` VL-0 hardware/licence/contract · `f78c23f` English military designations in Tamil TTS · `5567f07` Tamil TTS normalisation + Edge Neural adapter + lexicon · `2c774e8` speech reference API · `2c22b2e` isolated Indic Parler trial.
- Working tree: **65 entries** (29 staged, 15 untracked, rest unstaged), all pre-existing from other sessions (system_resources module, speech-reference routes/tests, voice-lab frontend, acceptance-sprint analysis, etc.). **Nothing was touched.**
- This audit does not commit, per the task's explicit prohibition (that overrides the standing commit rule in CLAUDE.md for this task).

## 3. Hardware — measured now

| Item | Measured | Matches earlier description? |
|---|---|---|
| CPU | Intel Core i7-9700 @ 3.00 GHz, **8 cores / 8 threads** (no Hyper-Threading) | Yes |
| RAM | 15.7 GiB installed; **6.4 GiB free** at audit time | — |
| dGPU | AMD **Radeon RX 560X**, dedicated VRAM **4 GiB** (registry `qwMemorySize` = 4,294,967,296), driver 31.0.12019.9007 (2022-08-19) | Yes |
| iGPU | Intel UHD 630, driver 27.20.100.9316; WMI AdapterRAM 1 GiB (shared memory; Windows typically allows up to half of RAM as shared) | — |
| CUDA | **None.** No NVIDIA GPU, `nvidia-smi` absent; `torch.cuda.is_available()` = False in both venvs | — |
| DirectML | **Tensor-level only.** `.venv-voicelab-dml` (Py 3.12.10, torch 2.4.1+cpu, torch-directml 0.2.5.dev240914) sees 2 devices; cached probe (`models/voice_lab/directml_probe.json`): 1024² matmul, relu, softmax, conv2d OK on RX 560X | — |
| Python | 3.13 (user), 3.12 (`C:\dstmp\python312`), `.venv-voicelab` (3.13.15, torch 2.15.0+cpu, parler_tts 0.2.2, transformers 4.46.1, torchaudio, librosa, soundfile), `.venv-voicelab-dml` | — |
| ffmpeg | 9.0.2 full build, has `rubberband` and `loudnorm` filters | — |
| Disk | C: 411 GB free · D: 1,764 GB free | — |

Not installed in any venv: pyworld, parselmouth, speechbrain, resemblyzer, Coqui TTS, f5_tts, whisper/faster-whisper, onnxruntime(-directml), yt-dlp, pyannote, RVC, crepe/torchcrepe.
**`parler_tts` is NOT installed in the DirectML venv**, so no model has ever generated speech on the GPU.

## 4. TTS implementation map (from source)

### 4.1 Production narration path (Tamil videos today)
```
Script Studio → approved scene plan
  → AudioService RM-4 (backend/defence_studio/services/audio.py)
      tts_text.py: deterministic Tamil prep (tamil_tts_lexicon.json, tts_designations.json "Su-57"→English);
                   approved text kept unchanged for subtitles
  → AudioModuleClient (integrations/audio_module.py, HTTP)
  → standalone/audio-module  registry: edge-neural (DEFAULT for ta, ta-IN-ValluvarNeural) |
                                        local-pyttsx3 (OneCore SAPI fallback, "robotic ~55 wpm") | mock-tts (gated)
      native rate / volume / approximate pitch only
  → bytes SHA-256 + measured duration; alignment = proportional estimate unless an aligner ran
  → defence-studio.audio-set.v1 artifact
  → RenderService RM-5 (services/render.py): concat scene audio + requested pauses → ffmpeg MP4 + subtitles
     (mock audio refused)
```
Edge Neural is an **online Microsoft service**, not local inference. It has no voice cloning and no speaker conditioning.

### 4.2 Voice Lab path (separate, NOT integrated into RM-4)
`backend/defence_studio/voice_lab/` (≈3,400 lines) + `api/voice_lab_routes.py` + `api/voice_lab_gpu_routes.py`:
```
/api/voice-lab/references → prosody.py (10 ms F0 + energy via EXT-03 voice_analyzer, phrases, pauses,
                            emphasis, syllable-rate ESTIMATE, emotion INFERENCE) → style profile (identity-free features)
/api/voice-lab/generations → inference.py → runtime/parler_runner.py (warm runner in .venv-voicelab,
                            text + text "description"; DirectML only if a matmul succeeds, else CPU)
                          → style.py apply_style: per-phrase rubberband tempo/pitch, formant=preserved,
                            stretch cap 0.85–1.15, pitch relative to each speaker's median, clamped to narrator range,
                            ±6 dB energy → timing report
/api/voice-lab/projects   → non-destructive mixer, undo/redo, export WAV/FLAC/MP3 with loudnorm
narrators.py / datasets.py / training.py → consent, per-narrator model versions, dataset QC, training preflight
```
`grep voice_lab` over `services/`, `integrations/` and `standalone/audio-module` returns **no match**. Voice Lab output never reaches video rendering.

### 4.3 Other voice components
- `components/voice-engine/` (EXT-03): `voice_analyzer` (analysis), `voice_generation` (`VoiceModel` ABC with only `SyntheticModel` and `SapiModel`; `profile_application.py` applies numeric style profiles, "no speaker identity, embedding or audio"), licence tables.
- `services/voice.py`: style/analysis jobs; its own providers endpoint states *"Tamil speech synthesis is not verified for any listed voice"*.
- `services/speech_reference*.py`: Tamil YouTube reference library (metadata + captions, text-based rate). **YouTube audio is never downloaded**; acoustic analysis only from uploaded recordings.

## 5. Voice identity vs reference delivery

### A. Owner voice identity
| Question | Answer | Evidence |
|---|---|---|
| Accept own recording? | **Yes (ingest only)** | `datasets.py` multipart WAV upload, QC (clipping, SNR estimate, silence, duplicates), segmentation; needs a NARRATOR consent with TRAINING scope |
| Create reusable voice identity? | **Record only, no usable model** | `narrators.py` stores identity + model versions, but a model version comes only from training, which is blocked (`NO_CUDA_GPU`, `INSUFFICIENT_VRAM`, `INSUFFICIENT_RAM`). Real trainer never ran (UNVERIFIED) |
| Generate new speech in my voice? | **No** | Only base Parler speakers, Edge voices, SAPI. No cloner, no voice conversion, no speaker-embedding code |
| Preserve vocal characteristics across long narration? | **No** (nothing to preserve) | — |
| Multiple identities? | **Schema yes** | unlimited narrators, `identity_key` UNIQUE |

### B. Reference delivery
| Capability | Status | Notes |
|---|---|---|
| Analyse reference audio | IMPLEMENTED + TESTED + LIVE-VERIFIED on model speech | upload only; music separation NOT_AVAILABLE (flagged) |
| Pitch contour | Yes, 10 ms F0 | EXT-03 tracker; no pyworld/CREPE |
| Pause detection | Yes (≥250 ms default) | energy-based |
| Speaking speed | **Estimate** | energy-peak syllable count, labelled ESTIMATE; caption-based rate in speech_reference |
| Emphasis / energy | Yes, heuristic | per-phrase emphasis list, energy dB |
| Emotion | **Heuristic INFERENCE** | weighted pitch variance/range/energy/rate; "review required" |
| Apply to another narrator | IMPLEMENTED + TESTED, live on English model→model | acceptance: relative pitch correlation 0.982 (MAE 0.79 st), duration +122 ms, 3/4 phrases hit the stretch cap. **No listening evaluation** |

**Independence:** identity and delivery are independent **by construction**. The reference contributes only relative numbers, no speaker embedding is computed, and formant-preserving DSP keeps the narrator's timbre. **This is not perceptually verified**, and no speaker-similarity metric exists in the repo to test leakage.

Limits of the delivery transfer: it is phrase-level, not frame-level (no WORLD contour reshaping, no word alignment). When the two voices segment phrases differently, transfer degrades and stretches are refused.

## 6. Model inventory (this PC)

| Model | Path / size | Languages / Tamil | Speaker conditioning | Cloning | Prosody controls | Licence | Ran here? |
|---|---|---|---|---|---|---|---|
| Parler-TTS Mini v1.1 @ `fbb2dd28` | `models/voice_lab/parler-tts-mini-v1.1@fbb2dd281092/` · 3.76 GB (fp32 safetensors 3.75 GB), SHA-256 manifest | English only / **no Tamil** | text description + named base speakers | No | via description (pace, pitch, expressivity) | Apache-2.0; data CC-BY-4.0; EVALUATION_ONLY | **Yes, CPU only.** Warm RTF 12.5, cold RTF 61–111, runner RSS ≈ 4 GB |
| Edge Neural ta-IN Valluvar / Pallavi (+ ta-LK/SG/MY) | online service (edge-tts 7.3.1 in audio-module) | Tamil yes | fixed voice IDs | No | rate, volume, approx. pitch | Microsoft service terms (not reviewed here) | Yes (Su-57 sample, 12/12 syntheses, ~83 wpm). Intelligibility NOT_VERIFIED |
| Windows OneCore/SAPI (Valluvar legacy) | OS | Tamil yes | fixed | No | rate | OS | Yes, rejected as robotic |
| Indic Parler-TTS @ `660bc53c` | **not on this PC** (HF hub cache empty; gated, `HF_TOKEN_MISSING`) | Tamil + 11 | description + named speakers (Jaya, Kavitha…) | No | description | Apache-2.0 weights, PROVISIONAL | **No.** The 3 trial WAVs in `AGENT_WORK/indic-parler-trial/output/` were generated on the **office PC** (`E:\…` paths in trial_report.json): 427 s and 619 s CPU generation for ~10 s audio |
| IndicF5, Indic-TTS, Indic-Speak, F5 base, MMS-TTS-tam | not downloaded | Tamil | IndicF5 = reference-audio cloner | IndicF5 only | — | EVALUATION_ONLY / LEGAL_REVIEW / NOT_ELIGIBLE (`backend/config/voice_lab_models.json`) | No |

No voice-conversion, speaker-verification, ASR or vocoder-only models are present.

## 7. Hardware feasibility

| Workload | CPU inference | GPU (DirectML, RX 560X 4 GB) | Verdict |
|---|---|---|---|
| 1. Local Tamil inference (Indic Parler) | Works in principle. **Not measured here.** Office CPU: RTF ≈ 42–63. Same-family Parler Mini here: RTF 12.5 warm, so roughly **~2 h per 10 min** (documented estimate) | Unproven: parler_tts is not installed in the DML venv. fp32 weights (~3.5 GiB) alone nearly fill 4 GiB. fp16 might fit, but DirectML fp16 behaviour for this model is unknown | Usable for **short test clips only**; not practical for regular long-form |
| 2. Reference-conditioned cloning (IndicF5-class, owner's own audio as reference) | Plausible (smaller flow-matching model), **unmeasured**; expect many× slower than real time | Unknown; DirectML op coverage for F5/Vocos untested | Evaluation experiment only; licence EVALUATION_ONLY |
| 3. Fine-tuning on owner recordings | Not feasible (16 GB RAM; days–weeks) | **Not feasible**: no CUDA, 4 GB VRAM, mainstream TTS trainers assume CUDA. Voice Lab requires ≥24 GB VRAM / ≥32 GB RAM | **Cloud GPU or new hardware only** |
| 4. Voice conversion (training-free, e.g. kNN-VC/WavLM; or trained RVC-style) | Training-free VC inference on CPU is plausible, **unmeasured**; nothing installed | RVC-style training on 4 GB AMD: not feasible/unsupported | Training-free VC is the **most realistic local identity path** |
| 5. Long-form narration | Edge: yes (online, fast). Local Parler: hours per 10 min | — | Long-form today = Edge only |
| 6. Simultaneous video render + TTS | 8 threads, 6.4 GB free RAM now; Parler runner ≈ 4 GB RSS and saturates CPU; ffmpeg render competes | — | **Serialise** (staged `system_resources` scheduler exists in the working tree, uncommitted). Do not run both concurrently |

Inference with an already-trained owner model (e.g. a cloud-trained fine-tune) is a separate case: same costs as row 1 (CPU) unless a DirectML path is proven.

## 8. Comparison with the office findings

| Office finding | Home evidence |
|---|---|
| Voice Lab has reference prosody analysis + phrase-level DSP styling | **Same code, confirmed** (prosody.py, style.py); live-verified English model-to-model only |
| No owner-voice cloning or speaker embeddings | **Confirmed here too.** Invariant in README: "no speaker embedding is ever computed" |
| Indic Parler Tamil generated offline but very slow on office CPU | Confirmed from the office trial records stored in Git. **Never run on this PC**; model absent here |
| IndicF5 evaluation candidate only | Same registry entry (EVALUATION_ONLY, code licence missing) |
| No native-listener acceptance for Tamil | Confirmed: all Tamil outputs NOT_VERIFIED |
| Tamil TTS folder is a linked worktree | Analogous here: `D:/rsw1` is a linked worktree of the same repo |

What the home side adds: a measured DirectML tensor path on the RX 560X (model-level unproven), a provider-selection layer (`execution.py`), narrator/consent/dataset/training-gate schema, mixer/export, and the Edge Tamil production path with Tamil text normalisation.

## 9. Recommendation — **HYBRID ARCHITECTURE**

There is no separate "Defence Era engine" to choose against the "DefenceStudio Voice Lab": they are the same repository. The right decision is about layers:

1. **Tamil content layer: reuse.** `tts_text.py` normalisation + Edge Valluvar (online) for base Tamil speech now; Indic Parler as the local candidate once the gate is accepted.
2. **Delivery layer: reuse Voice Lab.** `prosody.py` + `style.py` (relative pitch, pauses, energy, phrase timing). Its no-embedding design is exactly what prevents reference identity leakage.
3. **Identity layer: new component required.** Nothing in the repo can produce the owner's voice. The smallest addition that fits this hardware is a **training-free voice-conversion stage** (owner's own consented recordings as the matching set) inserted after base TTS and before style DSP. A reference-conditioned cloner (IndicF5, conditioned only on the owner's audio) is the alternative to test. Fine-tuning requires cloud GPU.
4. **Integration: new work.** Wire the Voice Lab output into RM-4/RM-5 (currently zero coupling).
5. **Verification layer: new.** A speaker-verification metric (owner vs reference similarity) and native-listener review; neither exists.

Licences for any new VC/speaker-verification component must be checked (code, weights and data separately) before install, per the VL-0 policy.

## 10. Smallest practical experiment (owner authorisation needed for each install/download)

Inputs: ~3–5 min of the owner's clean, consented speech; one 20–30 s authorised reference narrator clip (uploaded, not downloaded from YouTube); one 2–3 sentence new Tamil script.

1. **Delivery extraction (no install).** Run Voice Lab `/references` analyse on the reference clip. Pass if F0 contour, pauses, phrase timing and rate estimate are produced and look plausible on a plot.
2. **Tamil synthesis (no install).** Generate the script with Edge Valluvar through the audio module (`tts_text` applied). Pass if a Tamil listener confirms intelligibility.
3. **Owner identity (one install).** Convert step-2 audio with a training-free VC model on CPU, using the owner's recordings as the target set. Record RTF. Pass if the owner and one other listener judge it "recognisably the owner".
4. **Delivery transfer without leakage.** Apply `style.apply_style` with the step-1 profile to the step-3 audio. Measure relative-pitch correlation and timing error (existing report). Add speaker-similarity scoring: pass if **sim(output, owner) > sim(output, reference)** by a clear margin and is not lower than sim(step-3, owner). Then a blind A/B listening check.

Order matters: identity first (VC), then delivery DSP. Formant-preserving DSP keeps the converted timbre, while VC applied after styling could flatten the transferred prosody.

## 11. Blockers / owner decisions

1. Approve installing one VC model + one speaker-verification model (after licence review) in an isolated venv.
2. Provide owner recordings + consent record (Voice Lab datasets require consent with TRAINING/usage scope).
3. Optional: accept the Indic Parler HF gate and set `HF_TOKEN` locally to test local Tamil (CPU, slow).
4. Training: cloud GPU must be explicitly enabled; local training is not feasible.
5. Native Tamil listener for acceptance.

## 12. Evidence used
`docs/voice_lab/VOICE_LAB_STATUS.md`, `docs/voice_lab/VL0_EVALUATION_AND_CONTRACT.md`, `backend/config/voice_lab_models.json`,
`models/voice_lab/directml_probe.json`, `models/voice_lab/parler-tts-mini-v1.1@fbb2dd281092/VOICE_LAB_DOWNLOAD.json`,
`backend/defence_studio/voice_lab/{prosody,style,execution,training}.py`, `voice_lab/runtime/parler_runner.py`,
`backend/defence_studio/services/{audio,voice,voice_engine,speech_reference,tts_text,render}.py`,
`standalone/audio-module/app/tts/{registry,edge_neural}.py`, `components/voice-engine/voice_generation/models.py`,
`AGENT_WORK/indic-parler-trial/*.json`, `analysis/su57_tamil_sample_20261029/FINAL_REPORT.md`; live WMI/registry/pip/torch probes run during this audit.
