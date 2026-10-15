# Voice Engine (Voice Analyzer + Voice Generation)

Two independent, reusable components. Neither imports Defence Studio application code.

| Package | Role | Own schema |
|---|---|---|
| `voice_analyzer/` | Authorized audio/video -> measured, versioned **Voice Style Profile** (delivery characteristics, not identity) | `voice_sources`, `voice_profiles`, `voice_profile_versions`, `voice_style_segments` |
| `voice_generation/` | Script + **Voice Direction** -> narration WAV + alignment, behind a `VoiceModel` interface | `voice_licenses`, `voice_models`, `voice_model_versions`, `voice_generations`, `voice_generation_segments`, `voice_alignments` |

Profile versions and segments are immutable (SQLite triggers abort UPDATE/DELETE). Corrections are new versions.

## Contracts

* **Profile**: `voice_style_id`, `version`, `analysis_version` (1.0), `analysis_hash`, `pitch`, `speaking_rate`, `tempo`, `pause_profile`, `energy_profile`, `intonation_profile`, `rhythm_profile`, `emphasis_profile`, `emotion_profile`, `confidence`, `source_segments`. Estimates are labelled (`syllables_kind: ESTIMATED`, `emotion kind: INFERENCE`); unmeasurable values are `null`, never invented.
* **Voice Direction**: `voice_id`, `language`, `style_profile_id`, `emotion{label,intensity 0-1}`, `tempo 0.5-2.0`, `pitch_variation 0-1`, `energy 0-1`, `emphasis[{text,strength 0-1}]`, `pauses[{position,duration_ms 0-5000}]`; positions: `before_speech|after_sentence|before_emphasis|after_emphasis`.
* **Generation result**: `generation_id`, `voice_id`, `style_profile_id`, `language`, `audio_format`, `sample_rate` (48000), `duration_ms` (measured), `audio_reference`, `generation_version`, `generation_hash`, `status`.
* **Alignment**: every item carries `accuracy` = `MEASURED_RENDER_BOUNDARY` | `MODEL_DERIVED` | `ESTIMATED_PROPORTIONAL`. Currently sentences are measured from the render and words are proportional estimates; `aligner.DefenceAlignAdapter` is an availability-only slot.

## Adapters

| Adapter | Reality |
|---|---|
| `SapiModel` | Windows OS voices (verified: Zira en-US, Hazel en-GB). Applies tempo + energy only. **No Tamil.** Commercial/YouTube terms of OS voices not verified. |
| `SyntheticModel` | Deterministic tones for pipeline tests. **Not speech.** Hidden in the application unless `DEFENCE_STUDIO_VOICE_TEST_TONE=1`. |

`DEFENCE_ERA_FEMALE_001` (owned/licensed narrator) is **not provisioned**.

## Tests

```
cd components/voice-engine
python -m pytest -q -p no:cacheprovider        # 59 tests (needs ffmpeg; SAPI test skips off Windows)
```

## Integration boundary

Defence Studio reaches the engines only through `backend/defence_studio/services/voice_engine.py` (thin adapter),
which also builds the honest voice catalog. See `docs/voice_engine/INTEGRATION.md` in the repo root docs.

## Known limitations

Tamil speech not generated/verified; word timings are estimates; emotion is heuristic; syllable rate is
envelope-peak based; pitch uses single-pass autocorrelation (octave errors possible on noisy input); URL
acquisition is a separate, unimplemented layer.
