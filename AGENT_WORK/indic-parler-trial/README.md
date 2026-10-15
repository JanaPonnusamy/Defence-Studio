# Indic Parler-TTS isolated trial

Disposable venv generated a real Tamil WAV with `ai4bharat/indic-parler-tts` on CPU using the official two-tokenizer Parler API (description tokenizer from `google/flan-t5-large`, prompt tokenizer from the model).

## Result

- Output: `output/tamil_female_sample.wav` (mono, 44.1 kHz, 10.217 s, 901164 bytes)
- Peak amplitude 0.242, RMS 0.024 — not silent, not clipped
- Load ~190 s, generate ~427 s, peak RSS ~925 MB
- Speaker description: recommended Tamil speaker **Jaya**, female newsreader, moderate pace, very clear audio

The Hugging Face token was set only for that generation process. It is not stored in this README, `trial_report.json`, git, or logs.

**Tamil narration stays BLOCKED** until a Tamil speaker listens to the sample. Do not register `DEFENCE_ERA_FEMALE_001`. Production voice-engine files were not modified.
