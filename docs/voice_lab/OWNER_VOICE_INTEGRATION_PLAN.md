# Owner-voice narration: integration plan (T0-E)

Status: **PLAN ONLY.** Nothing here is connected to the production video pipeline. Basis: `DEFENCE_ERA_HOME_TTS_AUDIT.md` (2026-11-01).
Goal: owner's own voice + reference narrator's delivery + new Tamil script → Tamil narration in the owner's voice.

## 1. Pipeline (order is fixed: identity first, then delivery)

```
Script Studio paragraph (approved text; subtitles use this text unchanged)
 1. Tamil text preparation   services/tts_text.prepare           [T0-B DONE: Voice Lab generation uses it]
 2. Tamil synthesis          Voice Lab backend (Parler runner)  → GENERATED_RAW artifact
                             or the Edge audio module (online, EVALUATION_ONLY terms) via an adapter (step P2)
 3. Owner voice conversion   voice_lab/conversion.run_conversion [T0-C interface; provider = UNAVAILABLE]
                             → OWNER_CONVERTED artifact
 4. Reference delivery DSP   style.apply_style (phrase-level, formant-preserving, relative pitch)
                             → GENERATED artifact
 5. Quality validation       A.quality / A.loudness (exists) + verification.judge (owner vs reference similarity)
                             [T0-D interface; provider = UNAVAILABLE] + Tamil intelligibility + listener review
 6. RM-4 audio_set           per-scene audio + measured durations → defence-studio.audio-set.v1
 7. RM-5 render              unchanged (concat + pauses → MP4 + subtitles)
```

Why this order: formant-preserving DSP (step 4) keeps the converted timbre. Conversion applied after styling could flatten or re-time the borrowed prosody. Verification (step 5) runs on the final audio, and also on the step-3 output as the baseline, to detect a drop in owner similarity caused by DSP.

## 2. Contracts in place

| Stage | Module | Input | Output | Today |
|---|---|---|---|---|
| 1 | `services/tts_text.py` via `voice_lab/generation.prepare_text` | approved text, language | spoken text + replacements + warnings + lexicon hash (provenance `text_preparation`) | IMPLEMENTED + TESTED |
| 3 | `voice_lab/conversion.py` (`defence-studio.voice-lab.conversion.v1`) | source WAV, `OwnerVoice` (narrator, consent, dataset version or owner recordings), config | status, output WAV, model identity, processing time, quality, errors, limitations | interface + guards TESTED; **no model** |
| 4 | `voice_lab/style.py` | audio + identity-free style features | styled audio + timing report | IMPLEMENTED + TESTED (English model-to-model live) |
| 5 | `voice_lab/verification.py` (`defence-studio.voice-lab.speaker-verification.v1`) | candidate WAV, owner enrolment, reference WAV | owner/reference similarity, margin, identity and leakage verdicts | interface + verdict rules TESTED; **no model; PASS impossible until calibrated** |

## 3. Planned steps (each needs owner authorisation; one per prompt)

| Step | Work | Approval needed |
|---|---|---|
| P1 | Choose and licence-review one training-free VC model and one speaker-verification model (code, weights and data separately, as in VL-0) | yes: licence decision |
| P2 | Install both in an isolated venv (`.venv-voicelab-vc`); real `VoiceConverter` and `SpeakerVerifier` adapters; CPU RTF benchmark on this PC | yes: install/download |
| P3 | Owner dataset: consent record (scopes GENERATION + TRAINING if ever needed) + 3–5 min clean recordings via the existing dataset API | owner recordings + consent |
| P4 | Calibration: enrolment/held-out split of owner audio + other-speaker audio → `Calibration` thresholds with evidence file | none beyond P2/P3 |
| P5 | Wire stage 3 into `Generation.run` (optional `owner_conversion` config; raw → converted → styled artifacts, each with provenance); verification report attached to the generation | review gate |
| P6 | Proof of concept per audit §10: reference analysis → Edge/Parler Tamil → VC → delivery → similarity + blind A/B + native Tamil listener | listener + owner acceptance |
| P7 | Only after P6 is accepted: RM-4 adapter that takes Voice Lab generations as scene audio (`audio_set` provider `voice-lab`, `is_mock=false`, provenance + limitations carried); RM-5 unchanged | explicit go-ahead |

## 4. Invariants (carried forward)

- The approved script and subtitles are never altered; only the spoken form sent to the model changes.
- The reference narrator contributes only relative delivery numbers. No reference speaker embedding is ever passed to synthesis or conversion. The verifier computes reference similarity **only to detect leakage**.
- Owner audio is used only with a consent record covering the use. It is never uploaded to external services without approval.
- No fallback voice and no placeholder conversion. `UNAVAILABLE` is reported as such.
- Existing English and Tamil production paths (RM-4 via the audio module) stay unchanged until P7.
- No local fine-tuning on the RX 560X. Training, if ever needed, uses an explicitly approved cloud GPU.

## 5. Open risks

- CPU speed of VC models on the i7-9700 is unmeasured (P2 measures it).
- Training-free VC quality on Tamil with only minutes of owner audio is unknown.
- Phrase-level delivery transfer degrades when the two voices segment phrases differently (3/4 phrases capped in VL acceptance).
- Edge Tamil voices are EVALUATION_ONLY (terms unresolved), and Indic Parler is gated (HF token + terms).
