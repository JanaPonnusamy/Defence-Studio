# T1 — Owner voice identity: proof-of-concept design

Status: **DESIGN ONLY (2026-11-01).** No model is downloaded, installed or run. No owner recording is used.
Every install, download, gated-model access, recording and listening session below needs separate,
explicit owner approval (§8).

Goal: owner's own voice + reference narrator's delivery + new Tamil script → Tamil narration in the
owner's voice, with identity and delivery kept separate and measured.

Evidence labels used in this document:
- **VERIFIED**: read from repository source, or from licence and metadata APIs (GitHub, Hugging Face) on 2026-11-01.
- **CLAIM**: stated by the model authors (README, paper). Not checked here.
- **ESTIMATE**: derived by arithmetic from verified numbers. Measured values replace it.
- **HYPOTHESIS**: to be tested by the POC.

---

## 1. Existing architecture this builds on (VERIFIED from source)

| Stage | Module | What it does today | T1 relevance |
|---|---|---|---|
| Tamil text prep | `services/tts_text.prepare` via `voice_lab/generation.prepare_text` | lexicon + designation substitution; approved text kept for subtitles; provenance `text_preparation` | unchanged; T1 sentences go through it |
| Base synthesis | `voice_lab/generation.Generation.run` → backend (Parler runner) | chunks → `GENERATED_RAW` artifact → `style.apply_style` → `GENERATED` artifact | conversion slots between RAW and style (T0 plan stage 3) |
| Production Tamil voice | RM-4 `services/audio.py` → audio module `edge-neural` (ta-IN Valluvar/Pallavi) | online synthesis; fails closed on undeclared language; mock refused | POC base-speech source (text only leaves the PC) |
| Reference delivery | `voice_lab/prosody.py` (16 kHz, 10 ms F0/energy, phrases, pauses, emphasis, syllable-rate ESTIMATE) → `style.profile_features` | identity-free features; absolute reference Hz kept only as provenance | reused unchanged (§4) |
| Delivery DSP | `voice_lab/style.apply_style` | per phrase: duration stretch (cap 0.85–1.15), pause = reference pause, relative median pitch (±4 st, clamped to narrator range), energy offset (±6 dB); rubberband `formant=preserved` | reused; gaps in §4 |
| Identity conversion | `voice_lab/conversion.py` (T0-C) | `VoiceConverter` protocol + `run_conversion` guards; only `UnavailableConverter` | T1 adds one real adapter (after approval) |
| Speaker verification | `voice_lab/verification.py` (T0-D) | `SpeakerVerifier` protocol; `judge` never PASS without validated `Calibration` for the same model | T1 adds one real adapter + calibration evidence |
| Datasets / consent | `voice_lab/datasets.py`, `narrators.py` | WAV upload, QC (clipping, SNR estimate, silence, duplicates), consent scopes | owner enrolment recordings enter here |
| RM-5 render | `services/render.py` | refuses mock audio; concat + pauses → MP4 + subtitles | **not touched in T1** |

Voice Lab output does not reach RM-4/RM-5 today, and T1 does not change that. Integration is step P7 of
`OWNER_VOICE_INTEGRATION_PLAN.md`, after POC acceptance.

---

## 2. T1-A — Owner voice identity: candidates

### 2.1 Licence and capability table

Licence values are **VERIFIED** from the GitHub licence API or Hugging Face model-card metadata on
2026-11-01, unless marked otherwise. Code, weights and training data are listed separately, as VL-0 requires.

#### Voice conversion (training-free), the first experiment

| Candidate | Code licence | Weights licence / gate | Training data | Tamil | Owner audio needed | Weights size (VERIFIED) | Eligibility (proposed) |
|---|---|---|---|---|---|---|---|
| **kNN-VC** (`bshall/knn-vc`) | MIT (LICENSE file; API shows NOASSERTION) | WavLM-Large re-hosted from `microsoft/unilm` (repo MIT; HF card states no licence); prematched HiFi-GAN in repo releases (licence of release binaries not separately stated) | vocoder: LibriSpeech (CC-BY-4.0, VERIFIED); WavLM pretraining corpora per WavLM paper (**terms not reviewed**) | not evaluated by authors. Language-agnostic by design (CLAIM); Tamil is a HYPOTHESIS | CLAIM (paper): quality rises with more target audio, minutes rather than seconds | WavLM-Large 1,262 MB + prematched vocoder 66 MB (the 981 MB `do_*` files are training discriminators, not needed) | **Recommended first experiment.** EVALUATION_ONLY until weight and data terms are reviewed |
| **OpenVoice V2** tone-colour converter (`myshell-ai/OpenVoice`) | MIT | MIT (`myshell-ai/OpenVoiceV2`, ungated); README: "Free Commercial Use" | converter training data not published (**unknown**) | README: input speech "can be in any language" (CLAIM); native list excludes Tamil | short reference clip (CLAIM) | converter 131 MB | **Second arm.** EVALUATION_ONLY (data unknown) |
| Seed-VC (`Plachtaa/seed-vc`) | **GPL-3.0**; repo **archived** | HF metadata not retrievable anonymously | not reviewed | unknown | short clip (CLAIM) | not measured | Not for T1. GPL copyleft on integration and archived upstream; legal review needed |
| FreeVC (`OlaWod/FreeVC`) | MIT | not reviewed | not reviewed | unknown | needs speaker encoder | not measured | Backup only |
| Chatterbox VC mode (`resemble-ai/chatterbox`) | MIT | MIT (`ResembleAI/chatterbox`) | not reviewed | **not in its 23-language list** (VERIFIED); VC on Tamil unknown | short clip (CLAIM) | not measured | Backup only |
| RVC | MIT | per-voice model **must be trained** | — | — | 10+ min + training | — | **Out of scope**: not training-free; local training not feasible (§6) |
| Amphion Vevo | MIT | **CC-BY-NC-4.0** (VERIFIED) | — | — | — | — | **NOT_ELIGIBLE** (non-commercial) |

#### Reference-conditioned cloning (alternative)

| Candidate | Code | Weights / gate | Training data | Tamil | Owner audio needed | Size | Eligibility (proposed) |
|---|---|---|---|---|---|---|---|
| **IndicF5** (`AI4Bharat/IndicF5`) | **no licence file** (GitHub API: none) | MIT; **gated (`auto`)**: requires accepting terms on HF | Rasa (CC-BY-4.0, gated), IndicVoices-R (CC-BY-4.0, gated), IndicTTS (IITM terms **not reviewed**), LIMMITS (challenge terms **not reviewed**) | **Yes**, `ta` in card languages (VERIFIED) | reference clip **plus its exact transcript** (README) | model 1,403 MB (+ vocoder, not measured) | Alternative arm. Already EVALUATION_ONLY in `voice_lab_models.json`; gate needs approval |
| XTTS-v2 (`coqui/XTTS-v2`) | MPL-2.0 | `coqui-public-model-license` (non-commercial) | — | not in its documented list (not re-verified) | — | — | **NOT_ELIGIBLE** |
| Chatterbox multilingual | MIT | MIT | — | **no Tamil** (VERIFIED) | — | — | Not suitable |
| CosyVoice (now `QwenAudio/CosyVoice`) | Apache-2.0 | not reviewed | — | Tamil not documented | — | — | Not suitable without evidence |
| F5-TTS base / MMS-TTS-tam | — | CC-BY-NC-4.0 (registry) | — | — | — | — | NOT_ELIGIBLE (existing registry) |

### 2.2 Why voice conversion goes first

1. Separation of concerns. VC changes only timbre and keeps the source's words and timing. Tamil
   pronunciation therefore comes from a base voice that is already known to be intelligible. Edge
   Valluvar was used in the Su-57 sample, but its intelligibility is still NOT_VERIFIED.
2. CPU feasibility. kNN-VC has no autoregressive decoder: one WavLM encoder pass, a nearest-neighbour
   lookup and a HiFi-GAN vocoder. RTF is unmeasured (HYPOTHESIS: much cheaper than Parler-class TTS,
   which measured warm RTF 12.5 on this PC).
3. Licensing. MIT code, and no gate.
4. The cloning arm (IndicF5) is gated and lacks a code licence file, so it needs more owner decisions.

### 2.3 Known risks (HYPOTHESES the POC tests)

- kNN-VC output is 16 kHz (WavLM frame rate). Voice Lab generation outputs 48 kHz (`OUT_SR`), so the converted
  audio will be band-limited after resampling. Record this as a limitation; do not hide it.
- Cross-gender or large pitch-range mismatch between the base voice and the owner degrades kNN-VC.
  Mitigation: pick the Edge Tamil voice (Valluvar or Pallavi) closest to the owner's range, chosen from
  the measured owner F0 median, not from assumptions.
- Tamil retroflex and geminate consonants may blur when WavLM features from little owner audio are
  matched. The POC measures this through listener transcription (§5.4).
- Converting synthetic speech (Edge) is a different domain from converting natural speech.

---

## 3. T1-B — Speaker verification

### 3.1 Candidate verifiers

| Candidate | Licence (VERIFIED) | Gate | Size | Runtime | Training data | Use |
|---|---|---|---|---|---|---|
| **SpeechBrain ECAPA-TDNN** `speechbrain/spkrec-ecapa-voxceleb` | code Apache-2.0; card Apache-2.0 | no | 83 MB embedding model | PyTorch CPU | VoxCeleb (YouTube-derived; **terms not reviewed**) | **Primary** |
| **WeSpeaker ResNet34-LM** `Wespeaker/wespeaker-voxceleb-resnet34-LM` | code Apache-2.0; card CC-BY-4.0 (attribution) | no | 26.5 MB ONNX | onnxruntime CPU (DirectML EP: HYPOTHESIS) | VoxCeleb | **Second opinion** (independent architecture) |
| WavLM-Base-Plus-SV `microsoft/wavlm-base-plus-sv` | card states **no licence** | no | 405 MB | transformers | — | Excluded until licence resolved |
| NVIDIA TitaNet-L | CC-BY-4.0 | no | not measured | needs NeMo (large dependency) | — | Backup |
| pyannote/embedding | MIT | **gated** | — | — | — | Excluded (gate) |
| Resemblyzer | Apache-2.0; last push 2023 | no | bundled | CPU | — | Sanity check only |

None of these documents Tamil-specific evaluation. Performance on Tamil speech, and on converted or
synthetic speech, is a HYPOTHESIS. That is why calibration (§3.4) is mandatory.

### 3.2 Owner enrolment recording requirements

- Owner only, consented (Voice Lab consent record with scope **GENERATION**; add **TRAINING** only if
  fine-tuning is ever approved). Uploaded through the existing dataset API, so the existing QC applies.
- 3–5 min of clean read Tamil speech, plus ~1 min of the owner's natural narration style.
- Mono, ≥16 kHz (prefer 44.1/48 kHz WAV), no music, no other speakers, no heavy processing (no
  denoiser or compressor). Room noise as low as possible. Existing QC thresholds (clipping, SNR
  estimate, silence) must pass.
- Same microphone and room as the intended production recordings. Channel mismatch inflates false rejection.
- Split before any use, by recording session where possible:
  - **Matching set** (VC target / cloning prompt): ~60 %.
  - **Enrolment set** (verifier owner template): ~20 %.
  - **Held-out genuine set** (calibration trials, never used for conversion or enrolment): ~20 %, cut into 3–5 s segments.
  A segment must never appear in two sets. Record the split in the evidence file.

### 3.3 Embedding extraction and similarity

- Resample to the verifier's rate (16 kHz for both candidates). Trim silence with the existing energy VAD.
- Segment into 3–5 s windows and compute one embedding per window.
- Owner template = mean of the L2-normalised enrolment embeddings (re-normalised).
- Score = cosine similarity between the candidate embedding (mean over its windows) and the template.
  Also report the per-window spread, because a single mean can hide unstable windows.
- Report three similarities per candidate. The T0-D contract has the first two; the third is a
  **proposed contract extension** to make after approval, and is not implemented:
  - `owner_similarity`
  - `reference_similarity` (leakage from the delivery narrator)
  - `source_similarity` (similarity to the base TTS voice, which detects "conversion did nothing")

### 3.4 Calibration (required before any PASS)

The `Calibration` object in `verification.py` stays `validated=False` until all of the following exist
for the exact model revision:

| Trial set | Pairs | Purpose |
|---|---|---|
| Genuine: held-out owner segments vs owner template | ≥ 30 segments | false-rejection rate |
| Impostor A: base TTS voices (Valluvar, Pallavi) vs owner template | ≥ 30 segments | "conversion did nothing" boundary |
| Impostor B: reference narrator clip segments vs owner template | all available (≥ 5) | leakage boundary |
| Impostor C: other consented / licensed Tamil speakers vs owner template | ≥ 100 segments, ≥ 10 speakers | general false acceptance |
| Domain check: converted outputs vs owner template | all POC outputs | shows where synthetic speech lands relative to genuine speech; **not** used to set thresholds |

- Thresholds come from the genuine and impostor score distributions, at an operating point the owner
  chooses: FAR target first, then the FRR that results is reported. Report EER and the distributions
  (histograms), not only a single number.
- **Statistical floor:** with zero false accepts in *M* impostor trials, the 95 % upper bound on FAR is
  ≈ 3/*M*. 100 trials therefore support "FAR ≤ 3 %" at best, and FAR ≤ 1 % needs ≥ 300 independent
  impostor trials. Do not claim a FAR below what the trial count supports.
- Impostor C requires a source of other Tamil speakers that is licensed for this use. That is an owner
  decision (download + licence), see §8. Without it, only Impostors A and B exist, and the calibration
  must be labelled **PARTIAL**: it may support FAIL verdicts, but not PASS.
- Store the calibration as an evidence file (model id + revision, trial lists, scores, chosen operating
  point, date) and set `Calibration.evidence_ref` to it.

### 3.5 Minimum evidence before identity can pass

All of the following:
1. A verifier score `SCORED` with finite owner, reference and source similarities.
2. A validated (not PARTIAL) calibration for the same model revision.
3. owner ≥ calibrated threshold; owner − reference ≥ calibrated margin; owner − source ≥ calibrated margin.
4. Second-verifier agreement (WeSpeaker) on the same verdict, or a documented disagreement → FAIL.
5. Human listening (§5.4): the owner and ≥ 1 other listener who knows the owner's voice identify it
   blind, above chance.

An automatic score alone is never reported as identity PASS.

---

## 4. T1-C — Reference delivery (reuse, identity-free)

Reuse `prosody.analyse_signal` → `style.profile_features` → `style.apply_style` unchanged. The reference
narrator contributes only relative numbers, never audio frames or embeddings, to synthesis or conversion.

| Delivery dimension | Extracted today (VERIFIED) | Applied today (VERIFIED) | T1 POC treatment |
|---|---|---|---|
| Pitch contour | 10 ms F0; per phrase `rel_median_st`, `range_st`, `slope_st`, `intonation` | **only** the phrase median offset (±4 st, narrator-range clamp) | measure; contour-shape transfer (range/slope) is a *candidate* extension, not in T1 |
| Speaking rate | `rate_syl_per_s_estimate` (energy-peak ESTIMATE) | indirectly, via phrase-duration stretch (cap 0.85–1.15) | measure cap hits; widening caps is a test variable |
| Pauses | `pause_after_ms`, leading/trailing silence | yes, set to the reference (max 3 s) | as is |
| Energy | `energy_rel_db` per phrase | yes, ±6 dB | as is |
| Emphasis | `emphasis_rel_ms` per phrase | **not applied** | report as a gap; no T1 work |
| Rhythm | phrase durations + pauses | phrase level only; no word/syllable alignment | report as a gap |

Order. The primary arm is **base → VC → delivery DSP**, because formant-preserving DSP keeps the
converted timbre. The optional arm is **base → delivery DSP → VC**: frame-wise VC keeps timing exactly,
but may partly re-map pitch. Verifier scores and listener ratings decide between them; this is a
HYPOTHESIS.

The distinction holds throughout. Identity is checked against the **owner** template. Delivery is
checked by `style.compare` (relative-pitch correlation, timing error) against the **reference profile**.
The reference similarity score exists only to detect leakage.

---

## 5. T1-D — POC experiment

### 5.1 Inputs (each requires approval, §8)

| Input | Spec |
|---|---|
| Owner speech | 3–5 min clean + ~1 min natural narration, consented, split as in §3.2 |
| Reference narrator | one authorised 20–30 s clip, uploaded (never downloaded from YouTube); rights record kept |
| Script | 2–3 short Tamil sentences (≈ 8–15 words each) following `docs/TAMIL_SCRIPT_RULES.md`, including ≥ 1 English military designation (exercises `tts_designations.json`) and ≥ 1 retroflex/geminate-heavy word |
| Impostor speech | per §3.4 (optional; without it calibration is PARTIAL) |

### 5.2 Steps

1. `prepare_text` on each sentence; record the spoken form + sha.
2. Base Tamil speech: Edge ta-IN voice closest to the owner's measured F0 median, through the audio module
   (online; text only leaves the PC). Optional: Indic Parler, if its gate is approved.
3. Reference analysis: `/api/voice-lab/references` on the reference clip → style profile.
4. Identity: kNN-VC (arm 1) and OpenVoice V2 (arm 2) on CPU, matching set = owner matching split.
   Results go through `run_conversion`, so consent and model identity are enforced.
5. Delivery: `apply_style` with the step-3 profile (primary order), plus the optional reversed order for kNN-VC.
6. Verification: ECAPA + WeSpeaker on base, converted and styled outputs, plus genuine held-out and
   reference segments → `judge` with the stored calibration.
7. Blind listening test (§5.4).
8. Evidence report: all scores, timings, the listener sheet, and the limitations. Unmeasured items stay NOT_MEASURED.

### 5.3 Measurements

| Metric | How | Pass criterion (proposed; owner to confirm) |
|---|---|---|
| Processing time, RTF | wall-clock per stage, cold (first load) and warm (median of 3); `torch.get_num_threads`; audio seconds | reported only; no target is set before the first measurement |
| Peak memory | process peak RSS (as `parler_runner` already records) | < 6 GB per stage, so render and OS headroom remain |
| Tamil intelligibility | each listener transcribes every clip blind → character error rate against the approved text | converted CER ≤ base CER + small margin (owner sets it) |
| Owner identity | verifier verdicts (§3.5) + blind ABX: genuine held-out owner clip vs converted | §3.5 items 1–5 |
| Reference delivery similarity | `style.compare` relative-pitch correlation + timing error; listener 1–5 "delivery like the reference" | compare against the unstyled converted clip |
| Leakage | owner − reference margin; listener "sounds like reference narrator?" | no leakage per calibration; listeners do not identify the reference |
| Artifacts / pronunciation | listener notes per clip (metallic, buzzing, slurred, wrong word), with timestamps | logged; any wrong-word error is a defect |

### 5.4 Native Tamil listening test

- ≥ 2 native Tamil listeners (3 preferred). At least 1 knows the owner's voice well, plus the owner.
- Blind, randomised order. Clips: base, converted (each arm), converted + styled, genuine owner
  held-out, reference.
- Per clip: transcription; naturalness 1–5; "whose voice?" (owner / reference / base / unsure);
  delivery similarity 1–5; artifact notes.
- With this few listeners the results are **qualitative evidence**, not statistics. Report raw sheets, not only means.

---

## 6. T1-E — Hardware feasibility (home development PC)

### 6.1 Measured facts (VERIFIED, home audit 2026-11-01)

- CPU Intel Core i7-9700, 8 cores / 8 threads; RAM 15.7 GiB usable (6.4 GiB free at audit time with other work running).
- AMD Radeon RX 560X, 4 GiB VRAM. **No CUDA** (no NVIDIA GPU).
- DirectML: torch-directml in an isolated venv passed a tensor probe (matmul, relu, softmax, conv2d) on
  the RX 560X. **No speech model has ever run on the GPU.**
- Measured CPU speech inference here: Parler-TTS Mini only (warm RTF 12.5, cold 61–111, runner RSS ≈ 4 GB).

### 6.2 Per-workload assessment

| Workload | Weights (VERIFIED file size) | RAM (ESTIMATE: weights × 1.5–2.5 for runtime) | CPU | DirectML |
|---|---|---|---|---|
| kNN-VC inference | 1,328 MB | ≈ 2–3.5 GB; owner matching set adds ≈ 60 MB (300 s × 50 frames/s × 1024 × 4 B) | feasible by memory; **RTF unmeasured** | HYPOTHESIS. WavLM uses standard transformer/conv ops, untested |
| OpenVoice V2 converter | 131 MB (+ its speaker-embedding extraction) | < 1.5 GB | feasible by memory; RTF unmeasured | untested |
| IndicF5 cloning | 1,403 MB + vocoder | ≈ 3–4 GB | feasible by memory; iterative flow-matching decoding, expected slow (HYPOTHESIS) | untested; fp32 + activations near the 4 GiB limit |
| ECAPA / WeSpeaker scoring | 83 MB / 27 MB | < 1 GB | feasible (HYPOTHESIS: well under real time) | not needed |
| Fine-tuning any TTS/VC on owner data | — | — | not feasible (days–weeks; 16 GB RAM) | **not feasible**: no CUDA, 4 GB VRAM. Cloud GPU only, with approval |

### 6.3 Rules for the POC run

- CPU first. Run DirectML only as a separate measured arm after the CPU baseline exists, and report it
  as LIVE-VERIFIED only if outputs match CPU outputs and the device report confirms GPU execution.
- Run one model at a time. Do not run concurrently with the Parler runner (≈ 4 GB) or video render. Use
  the system-resources scheduler when it is available.
- Isolated venv for VC and verifier packages, kept out of the backend test venv and the existing Parler venvs.

---

## 7. Implementation sketch for T1 execution (after approval; not started)

1. Install chosen packages + pinned weights into an isolated venv; write the SHA-256 manifest (as for Parler).
2. Add `KnnVcConverter` (implements `VoiceConverter`) as an out-of-process runner, like `parler_runner`.
   It returns the model identity `{model_id, weights_revision, code_commit, licence}` that `run_conversion` requires.
3. Add `EcapaVerifier` / `WeSpeakerVerifier` (implement `SpeakerVerifier`).
4. A calibration script writes the evidence file → `Calibration(validated=...)`.
5. POC runner script (evaluation only, not wired into `Generation.run` or RM-4).

---

## 8. Owner decisions required before T1 execution

| # | Decision | Default if not decided |
|---|---|---|
| 1 | Approve **kNN-VC** (WavLM-Large + prematched HiFi-GAN) download and install in an isolated venv, after reviewing the WavLM weight and pretraining-data terms | nothing installed |
| 2 | Approve **OpenVoice V2** converter as the second VC arm (training data undisclosed) | single-arm POC |
| 3 | Approve **SpeechBrain ECAPA** (primary) and **WeSpeaker ResNet34-LM ONNX** (second opinion); accept that VoxCeleb data terms are unreviewed | no verification → identity stays NOT_EVALUATED |
| 4 | Whether to accept the **IndicF5** HF gate (no code licence file; IITM/LIMMITS data terms unreviewed) for the cloning arm | cloning arm skipped |
| 5 | Provide owner recordings (§3.2) and a consent record (scope GENERATION) | POC cannot start |
| 6 | Provide and authorise one 20–30 s reference narrator clip, with a rights record | delivery arm skipped |
| 7 | Approve a licensed source of other Tamil speakers for impostor calibration | calibration PARTIAL → no identity PASS possible |
| 8 | Name ≥ 2 native Tamil listeners (≥ 1 who knows the owner's voice) | no listening evidence → nothing ACCEPTED |
| 9 | Approve sending POC script text (not audio) to the online Edge service for base speech; or approve the Indic Parler gate for local base speech | Edge (text only), which is the existing production path |
| 10 | Confirm the proposed pass criteria in §5.3 (CER margin, memory ceiling, FAR operating point) | criteria remain proposals |
