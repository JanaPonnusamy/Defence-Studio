# Voice Lab — programme status (Agent A backend, 2026-11-01)

Labels follow the project convention. IMPLEMENTED means code exists; TESTED means automated tests pass; LIVE-VERIFIED means
it was run with a real model or real hardware; BLOCKED names the missing prerequisite. Evidence:
`docs/voice_lab/evidence/benchmark_report_20261101.json`, `docs/voice_lab/evidence/acceptance_report_20261101.json`,
and `backend/tests/test_voice_lab.py` (21 tests).

## Phase status
| Phase | Status | Notes |
|---|---|---|
| VL-0 evaluation/contract | COMPLETED | `VL0_EVALUATION_AND_CONTRACT.md` |
| VL-1 model install + benchmark | **LIVE-VERIFIED on the ungated runtime-test model; Tamil model BLOCKED** | Parler-TTS runtime installed in the isolated `.venv-voicelab`. `parler-tts-mini-v1.1@fbb2dd28` (Apache-2.0, English, EVALUATION_ONLY) produced valid audio on CPU. **Indic Parler-TTS (`ai4bharat/indic-parler-tts@660bc53c`) is BLOCKED**: gated (HTTP 401), with `HF_TOKEN_MISSING`. No Tamil sample has been generated. |
| VL-2 multi-voice DB + profiles | IMPLEMENTED + TESTED | 23 `vl_` tables plus self-versioning. Identity key UNIQUE across narrators. Unlimited narrators. Rollback by activating an older version. No EXT-03 table altered. |
| VL-3 datasets | IMPLEMENTED + TESTED (synthetic signals) | Real male/female recordings and consent records: **BLOCKED (owner)**. Word alignment: NOT_ALIGNED (no licensed Tamil aligner). |
| VL-4 training | Orchestration IMPLEMENTED + TESTED with a test-only trainer. **Real training BLOCKED by hardware.** | This PC: blockers `NO_CUDA_GPU`, `INSUFFICIENT_VRAM`, `INSUFFICIENT_RAM`. Cloud is disabled by default. The Parler fine-tune adapter has never run on compatible hardware (UNVERIFIED). |
| VL-5 reference analysis | IMPLEMENTED + TESTED (known-truth signals) + LIVE-VERIFIED on model speech | Music/speech separation NOT_AVAILABLE (flagged, not removed). URL import needs yt-dlp (not installed). |
| VL-6 style matching | IMPLEMENTED + TESTED + LIVE-VERIFIED (English evaluation model) | See results below. |
| VL-7 UI | **Agent B (Cursor).** UI exists. It calls `/voice-lab/training`, `/voice-lab/script-generation` and `/voice-lab/hardware`. Its mixer is browser-local and **not yet wired** to the server projects, narrators, datasets, references or generations APIs. | Browser acceptance is B's. Not done by A. |
| VL-8 Script Studio | IMPLEMENTED + TESTED + LIVE-VERIFIED (paragraph reuse) | Script Studio itself does not call it yet (NOT INTEGRATED on the Script Studio side). |
| VL-9 storage/reliability | IMPLEMENTED + TESTED | Restart reconcile, retry, cancel, disk guard (507), missing file (410), concurrency limit, isolation across owners. The Storage Manager classifier hook was requested from the `lifecycle.py` owner (coordination §22). Until then Voice Lab files are UNCLASSIFIED, which means always preserved. |

## VL-1 benchmark (real inference; CPU; i7-9700, 15.7 GB RAM, no CUDA; torch 2.15.0+cpu)
Model `parler-tts-mini-v1.1@fbb2dd281092c5b414ef29cf9d8895f386f1feef` (3,763,406,228 B, download manifest with per-file SHA-256).
Text: 2 English sentences, 4.46 s of audio.

| Run | model load | generation | RTF | output |
|---|---|---|---|---|
| cold first run | 40.7 s | 494.3 s | 110.9 | valid, speech 3.9 s, peak -0.2 dBFS, no clipping |
| warm repeat (model cached) | 0 s | 55.6 s | **12.5** | identical bytes (same seed) |
| after runner restart | 23.3 s | 271.7 s | 60.9 | identical bytes |

During the acceptance run, warm generation of about 10–12 s of audio took 140–173 s (RTF ≈ 12–15). Runner RSS was about 4.0 GB, self-reported.
The OS CPU/RSS counters in the benchmark report describe the venv launcher process and are **not valid**. The script was fixed afterwards.
**Conclusion:** CPU inference works but runs at roughly 12× slower than real time once warm, and much slower when cold. A 10-minute narration would take
about 2 hours on this PC. DirectML (AMD) execution is being evaluated by another session (`execution.py`); all runs here used `provider: cpu`.

## Acceptance run (spec §14) — 26/26 checks passed, real model, real API, isolated DB
| Step | Result |
|---|---|
| Male reference (model-generated, speaker "Jon") | 12.2 s, f0 median 175.8 Hz (5th–95th percentile 117.6–275.9 Hz), 4 phrases, mean pause 913 ms. Note: this base speaker is high-pitched for a male voice. |
| Female narrator plain / styled | f0 median 205.1 / 216.2 Hz: the pitch centre moved +0.91 st and was **not** pulled to the reference |
| Delivery transfer | PHRASE_MAPPED; relative pitch correlation with the reference **0.982** (MAE 0.79 st); no pitch clamps |
| Timing | target 12,202 ms → output 12,324 ms (**+122 ms**). Phrase-start error mean 430 ms, max 1160 ms. 3 of 4 phrases hit the 0.85–1.15 stretch cap: the two voices split the sentences into different acoustic phrases (requested stretches 0.44 and 1.57 were refused rather than forced). |
| Mixer | 4 comparison tracks, range-request playback (HTTP 206), waveforms; cut/copy/paste/trim/split/join + undo/redo; project identical after application restart (revision 16) |
| Export | WAV stereo normalised to -16.7 LUFS (peak-limited at -1 dBFS) and MP3; decoded duration = expected (13,091 ms) |
| Script Studio | unchanged paragraph reused, with provenance and limitations returned |

**No listening evaluation was performed.** Nothing here claims naturalness, accent quality or similarity.

## Licence decisions (code / weights / data recorded separately: `backend/config/voice_lab_models.json`)
Indic Parler-TTS: PROVISIONAL (gate + data review pending). Parler mini: EVALUATION_ONLY (English, runtime test).
IndicF5 / Indic-TTS: EVALUATION_ONLY. Indic-Speak: LEGAL_REVIEW_REQUIRED. F5-TTS base, MMS-TTS Tamil: NOT_ELIGIBLE.
`production_allowed` is false for every model, and every generation result carries that limitation.

## Significant decisions
1. `vl_` tables, self-versioned (not `database.py`), to avoid colliding with other sessions' uncommitted v14/v15.
2. Jobs run on the existing workflow queue (own stages and in-process worker) instead of a second queue; `reconcile()` repairs rows after a restart.
3. Style transfer works at phrase level with formant-preserving rubberband: identity is preserved by construction. Frame-level contour reshaping
   (WORLD vocoder) and word-level alignment are the next accuracy steps. Both need components (licence check pending).
4. The default narrator pitch range is ±6 st around her measured median. Measuring the "range" of one flat generation proved too narrow (found by a test).
5. The segment QC noise floor comes from the whole recording, because a tightly cut segment has no silence (found by a test).

## Owner-controlled prerequisites (not fabricated, not bypassed)
1. Accept the `ai4bharat/indic-parler-tts` terms on Hugging Face; set `HF_TOKEN` in the environment. Then run
   `.venv-voicelab\Scripts\python.exe -I backend\scripts\voice_lab_download.py indic-parler-tts` and the benchmark with `--language ta`.
2. Authorised male (~10 h) and female narrator recordings, plus signed consent records.
3. Training hardware (NVIDIA GPU ≥ 24 GB VRAM, ≥ 32 GB RAM), or explicitly enable cloud training and configure a worker.
4. Legal review for IndicF5, Indic-TTS, Indic-Speak, and the Indic Parler-TTS training-data licences.
5. `E:\Jana Soft\DefenceStudio` does not exist on this machine. Sync happens through Git (both remotes).
