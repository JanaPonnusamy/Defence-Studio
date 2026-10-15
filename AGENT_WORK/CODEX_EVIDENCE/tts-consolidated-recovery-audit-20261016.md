# Defence Studio — TTS Consolidated Recovery Audit

Date: 2026-10-16 (Asia/Calcutta)

This is a read-only reconciliation of retained TTS reports, source contracts,
test logs, cached-model metadata, and existing WAV outputs. No model download,
package installation, paid API call, audio generation, production-data change,
voice registration, or credential disclosure occurred during this audit.

## Component status matrix

| Component | Status | Evidence | Limitation |
|---|---|---|---|
| Voice Analyzer (`components/voice-engine/voice_analyzer`) | IMPLEMENTED / TESTED / REAL-AUDIO-VERIFIED (mechanical) / ACCEPTED: NO / Tamil: BLOCKED | Analyzer/service/store code; component suite 59 passed; `voice-intelligence-e2e-20261015/REPORT.md` | Measures delivery style, not speaker identity. Emotion and syllables are labelled inference/estimated. No authorized Tamil reference segment was acquired or listened to. |
| Voice Direction and profile application | IMPLEMENTED / TESTED / REAL-AUDIO-VERIFIED for English control handoff / ACCEPTED: NO | `voice_generation/direction.py`, `direction_from_profile.py`, `profile_application.py`; backend voice tests 28 passed; P1 English control report | Rate/volume/pitch mappings are measurable in the English path. Exact emotion, pauses, and word emphasis are unsupported or approximated. No Tamil model consumes this contract. |
| Reusable Voice Generation Engine | IMPLEMENTED / TESTED / REAL-AUDIO-VERIFIED with SAPI English / ACCEPTED: NO for Tamil | `voice_generation/engine.py`, `models.py`; component suite 59 passed; `voice-engine-tamil-path-20261015/focused-tests-final.log` 35 passed | Registered adapters are SAPI and synthetic test tone. No production Tamil adapter or model is registered. |
| Standalone `local-pyttsx3` adapter | IMPLEMENTED / TESTED / REAL-AUDIO-VERIFIED for English / ACCEPTED: NO for Tamil | `standalone/audio-module/app/tts/local_pyttsx3.py`; standalone suite 61 passed; real Hazel/Zira outputs in `voice-intelligence-e2e-20261015/REPORT.md` | Host inventory contains only Hazel en-GB and Zira en-US. Tamil requests fail closed with empty audio; no Tamil voice is installed. |
| Synthetic/mock adapters | IMPLEMENTED / TESTED / REAL-AUDIO-VERIFIED: NO / ACCEPTED: NO | `SyntheticModel`, `mock_adapter.py`; test suites | Deliberate tones/placeholders, not speech and never evidence of Tamil quality. |
| Indic Parler-TTS trial | IMPLEMENTED / TESTED / REAL-AUDIO-VERIFIED mechanically / ACCEPTED: NO / Tamil: BLOCKED | `indic-parler-trial/kavitha_trial_report.json`, `trial_report.json`; three retained WAVs; independent wave/soundfile checks | Real inference works from cache, but there is no Defence Studio provider adapter, no native pronunciation check, no human listening, and no proof the preset is youthful or intelligible. |
| Audio validation/QC | IMPLEMENTED / TESTED / REAL-AUDIO-VERIFIED mechanically / ACCEPTED: NO | `app/pipeline/validate.py`, QC tests, P1 report; FFprobe/decode and non-silence checks passed for retained English/Parler WAVs | Metadata, RMS, clipping, and VAD do not establish pronunciation, naturalness, or news suitability. |
| Alignment/timing | IMPLEMENTED / TESTED / REAL-AUDIO-VERIFIED as approximate/measured boundaries / ACCEPTED: NO | `standalone/audio-module/app/pipeline/align.py`; alignment tests; component engine records measured sentence boundaries | No neural forced aligner/model bundle is verified. Word timings are proportional estimates; Parler trial WAVs have no verified text alignment. |

## Provider and model facts

| Provider/model | Installed or cached | Tested | Usable conclusion |
|---|---|---|---|
| Windows SAPI / `local-pyttsx3` | Installed; Hazel en-GB and Zira en-US | Real English synthesis and validation passed | Usable for English only. Tamil is blocked by the actual voice inventory. |
| `ai4bharat/indic-parler-tts` | Cached under `E:\Jana Soft\hf-cache\indic-parler-trial`; 17 files, about 3.77 GB; trial venv has `parler_tts 0.2.2`, CPU Torch 2.14.1, Transformers 4.46.1 | Real CPU inference produced three valid Tamil-caption WAVs | Accessible and mechanically usable as an isolated trial. Not integrated, human-reviewed, or accepted. |
| Indic-Speak | Proposed only; not cached or tested in this audit | None | Candidate from prior model review. Custom Indic Open Model License and roughly 3B/BF16 CPU/RAM risk require separate authorization and review. |
| IndicF5 | Proposed only; not installed/cached/tested | None | Candidate only; Tamil capability does not establish Tamil-English mixed-text behavior or delivery acceptance. |
| eSpeak NG/Piper | eSpeak executable absent on audited host; no verified adapter/model | None | Not a current provider. Installation and voice/license review would be required. |
| Azure/Google/other paid services | Not provisioned; no calls made | None | Not available evidence and outside this audit. |

## Existing sample facts

No listening judgment is made.

- Jaya: `AGENT_WORK/indic-parler-trial/output/tamil_female_sample.wav`; 901,164 bytes; mono 16-bit 44.1 kHz; 10.217 s; model load 189.52 s; generation 427.29 s; total 617.02 s.
- Jaya news-description run: `.../tamil_female_sample_news_desc.wav`; 935,980 bytes; mono 16-bit 44.1 kHz; 10.612 s; model load 18.34 s; generation 770.32 s; total 788.8 s.
- Kavitha candidate: `.../tamil_female_youth_trial.wav`; 861,228 bytes; mono 16-bit 44.1 kHz; 9.764 s; SHA-256 `c3c355f37583efc427a4497046464e5476453420379a3cb0e25eeeca1b41746d`; model load 16.97 s; generation 618.65 s; total 635.70 s; peak RSS 922.6 MB.

All three are non-silent and unclipped by retained metadata checks. Those checks do not prove youthfulness, Tamil intelligibility, pronunciation, or broadcast suitability.

## Bottleneck and gaps

The measured performance bottleneck is CPU inference, not model loading: the
Kavitha candidate took about 619 seconds to generate 9.8 seconds of audio and
used about 923 MB peak RSS. The prior Jaya runs took 427 and 770 seconds of
inference. The voice-character and Tamil-pronunciation bottlenecks are unknown
because no native listener review exists. Unsupported controls are a separate
integration gap: Parler delivery is requested through the description caption,
while the reusable engine's production adapters only expose SAPI rate/volume
and limited post-process pitch. Alignment is approximate at word level.

The cached Parler generation configuration records `max_length: 2610` and the
trial makes one `model.generate` call after a single model load. The timings do
not show repeated initialization as the main cause: the Kavitha load was 16.97 s
versus 618.65 s of inference. The trial is CPU-only (`torch 2.14.1+cpu`, no
CUDA), and no persistent Parler worker/model cache exists in the Defence Studio
provider architecture. Reducing generation length, changing sampling, or
adding a persistent worker could change audio duration or job semantics and was
not justified without a new authorized audio verification. This makes the
current Parler path operationally unsuitable as a default CPU provider until a
separate benchmark/integration decision is approved.

## Completed work that must not be repeated

The following evidence is already retained: standalone audio E2E (61 tests),
voice-engine component tests (59 tests), backend voice tests (28 tests), real
English SAPI synthesis/decode/restart checks, the 35-test focused voice-engine
regression, the Jaya Parler run, the Jaya news-description run, and the single
Kavitha candidate run. Tamil font resolution and backend P0 work were separately
verified. No new full-suite run or generation is justified by this audit.

## Single recommended next action

Run one blind listening and pronunciation review by a native Tamil speaker on
the three existing Parler WAVs, using the same Tamil transcript and a rubric for
intelligibility, phoneme accuracy, age impression, pace, emphasis, and news
naturalness. This is better than another blind generation because the current
evidence already proves model access and artifact integrity; only listening can
decide whether Jaya or Kavitha is acceptable or whether a different provider is
needed. Keep `DEFENCE_ERA_FEMALE_001` unprovisioned until that review and a
separate licensing/integration decision are complete.

## Audit changes and safety

This audit added this report and an append-only entry to `AGENT_WORK/CODEX.md`.
No application source, database, review history, job state, voice registration,
existing WAV, model cache, or credential was changed. `HF_TOKEN` was not read
into output and is absent from this report.
