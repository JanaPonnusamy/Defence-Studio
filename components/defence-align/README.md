# Defence Align — standalone word-level audio alignment engine

Defence Studio Phase 4, **Component D**. A self-contained service that maps a
real narration transcript to timing information derived from **actual audio**.

It is intentionally isolated: it imports nothing from Defence Studio Phase 1–3
and nothing from the TTS component. A future TTS module connects to it only
through the versioned JSON contracts described in
[`INTEGRATION_CONTRACT.md`](INTEGRATION_CONTRACT.md).

**It never** synthesizes speech, generates narration, alters scripts, renders
video, or publishes content. There is no UI, TTS provider, script generator,
scene planner, renderer, or publisher here.

---

## What it does

Given (1) an audio file, (2) a versioned JSON transcript, and (3) an explicit
alignment request, it produces a validated alignment JSON artifact with
per-token start/end timestamps, engine-provided confidence/uncertainty where
available, a machine-readable report of unalignable content, artifact hashes,
and engine/version metadata.

Core guarantees:

- **Real measured duration.** Duration comes from decoding the audio header
  (stdlib `wave`), never estimated. Word timestamps are **never** produced by
  evenly dividing duration across the transcript.
- **Original transcript preserved exactly.** Tamil Unicode is never silently
  transliterated; embedded English technical terms/acronyms/designations are
  never stripped. Any `normalized_text` is separate, optional metadata, and
  every token keeps its exact original substring + char offsets.
- **Honest capability reporting.** Declared language support (codes accepted)
  is kept distinct from tested capability (codes actually verified). An engine
  that only produces sentence-level timing is labelled `sentence`, never
  mislabelled as word-level.
- **No fake real alignment.** When a real forced-alignment model is not
  installed, the real adapter reports `available=false` and refuses to run
  rather than returning fabricated timings. The API still starts and serves.
- **Deterministic artifacts.** Identical inputs + identical engine output
  serialize to an identical `artifact_sha256` (creation timestamp excluded).

## Engines

| Engine key | Name | Purpose |
|---|---|---|
| `deterministic-test` | `deterministic-test-aligner` | **Synthetic, test-only.** Reproducible, length-weighted timings for offline testing. Every result is flagged `is_synthetic=true`; the service refuses it unless the request sets `config.allow_synthetic=true`. **Never** a measurement of real speech. |
| `real` | `torchaudio-forced-aligner` | Gated real CPU forced-alignment adapter (see below). The CTC alignment call is **implemented**; it reports `available=false` and refuses to run until torch/torchaudio **and** a local operator-provisioned model bundle are present. |

### Why torchaudio forced alignment for the real adapter

CTC forced alignment (torchaudio's `forced_align` + `merge_tokens` with a CPU
Wav2Vec2 / MMS-FA acoustic model) was selected because it is **CPU-compatible**
(no GPU), **offline** after a one-time operator-initiated model fetch (nothing
is downloaded at import/startup), and produces **genuine word/token-level**
spans (not sentence fragments). Each word's `[start, end]` comes from the CTC
frame alignment of its characters, converted to seconds against the *measured*
audio duration — never an even split of duration across the transcript. Broad
language coverage (English well supported; Tamil via the multilingual model +
romanization); Tamil is listed only under *declared* languages until actually
verified on a device — accepting a language code is not a tested capability.

> **License / provenance — operator must verify.** torchaudio's bundled MMS-FA
> model is derived from Meta's *Massively Multilingual Speech* (MMS) and is
> distributed under **CC-BY-NC 4.0 (non-commercial)**. This is **not** a
> permissive license and may be incompatible with commercial use of Defence
> Studio. The adapter loads whatever local model the operator provisions and
> asserts nothing about its license; selecting and licensing an acceptable
> model is the operator's responsibility. This is a **remaining blocker** for
> commercial deployment until a suitably-licensed model is chosen.

### Enabling real alignment

Real alignment is **off by default** and never auto-installed. The CTC
alignment call is already implemented in
`align_engine/engines/real_adapter.py`; enabling it is purely an operator
provisioning step — no code change:

```bash
pip install -e '.[real]'            # torch + torchaudio (CPU wheels are fine)

# One-time, explicit, operator-run model acquisition (the ONLY place a model is
# downloaded — never at import or server startup). Requires acknowledging the
# model license shown by the command:
python -m align_engine.tools.fetch_model \
    --out /abs/path/to/forced_align_model.pt --i-accept-model-license

# Point the adapter at the provisioned bundle:
export DEFENCE_ALIGN_FORCED_MODEL_PATH=/abs/path/to/forced_align_model.pt
```

`fetch_model` writes two files the adapter consumes: the model object
(`<out>`) and a metadata sidecar (`<out>.meta.json`) holding the tokenizer
`dictionary`, `sample_rate`, and CTC `blank_id`. To use a different
(e.g. commercially-licensed) model, save it with `torch.save` and write a
matching sidecar by hand. Until both the libraries and the model **and** its
sidecar are present, `/capabilities` and `/health` report real alignment as
unavailable and `align` raises `UnsupportedCapabilityError` rather than
returning fabricated timings.

Broader audio decoding (MP3/FLAC/OGG) beyond the stdlib WAV path:
`pip install -e '.[audio]'` (soundfile) and extend `align_engine/audio/inspect.py`.

## Installation

```bash
cd defence-align
python -m venv .venv && . .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -e '.[test]'
```

Core dependencies: `fastapi`, `uvicorn`, `pydantic`. Everything else used by
the core path is the Python standard library. **No GPU, no paid credentials,
no network at import time.**

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `DEFENCE_ALIGN_INPUT_ROOT` | `./data/input` | Registered root holding caller-provided audio. Request `audio_path` is resolved **inside** this root; traversal/absolute paths are rejected. |
| `DEFENCE_ALIGN_STORAGE_ROOT` | `./data/storage` | Where this component writes its own output artifacts. |
| `DEFENCE_ALIGN_DB_PATH` | `./data/align.sqlite3` | SQLite job store. |
| `DEFENCE_ALIGN_MAX_AUDIO_BYTES` | `536870912` (512 MiB) | Hard ceiling on audio file size. |
| `DEFENCE_ALIGN_FORCED_MODEL_PATH` | *(unset)* | Local real-alignment model file. Unset ⇒ real alignment unavailable. |

## Launch the API

```bash
# From the defence-align directory:
export DEFENCE_ALIGN_INPUT_ROOT="$PWD/data/input"
python -m uvicorn align_engine.api.app:app --host 127.0.0.1 --port 8030
```

The server starts even when the real model is absent.

### Endpoints

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness + whether real alignment is available (honest). |
| `GET /capabilities` | Per-engine capabilities (declared vs tested languages, formats, granularity, availability). |
| `POST /alignments/validate` | Validate a request + confine its audio path; no alignment run. |
| `POST /alignments` | Create (idempotent by `alignment_job_id`) and run alignment; returns the job. |
| `GET /alignments/{id}` | Job status/progress and error detail. |
| `POST /alignments/{id}/cancel` | Cancel a non-terminal job. |
| `POST /alignments/{id}/retry` | Retry a FAILED job, reusing verified inputs. |
| `GET /alignments/{id}/artifact` | The alignment JSON artifact (COMPLETED only). |
| `GET /alignments/{id}/export` | The artifact as a downloadable attachment. |

Errors render as `{"detail", "code"}` with a mapped status (`422` validation /
unsafe path / invalid output, `404` not found, `409` conflict / bad state).

## Try it offline (synthetic engine)

```bash
python samples/make_sample.py --out-dir ./_sample_run --language en
export DEFENCE_ALIGN_INPUT_ROOT="$PWD/_sample_run"
export DEFENCE_ALIGN_STORAGE_ROOT="$PWD/_sample_run/storage"
export DEFENCE_ALIGN_DB_PATH="$PWD/_sample_run/align.sqlite3"
python -m uvicorn align_engine.api.app:app --port 8030 &
curl -s localhost:8030/health
curl -s -X POST localhost:8030/alignments \
  -H 'content-type: application/json' --data @_sample_run/request_en.json
```

The sample uses the **synthetic** `deterministic-test` engine with
`allow_synthetic: true` — its timings are reproducible test data, not measured
alignment of real speech.

## Tests

Fully offline — no network, no model downloads, no paid credentials.

```bash
# Focused first:
python -m pytest tests/test_contracts.py tests/test_audio_and_paths.py \
                 tests/test_normalize.py tests/test_engines.py -q
# Then the whole suite:
python -m pytest -q
```

## Limitations

- **Real CTC alignment is implemented and exercised against the real
  `torchaudio.functional.forced_align` API** (with controlled emission tensors
  in the test suite), but a **full real-acoustic-model run has not been
  executed here** because no model bundle is provisioned (acquiring one is an
  explicit operator action). Real Tamil/English alignment *accuracy* on a
  downloaded acoustic model has therefore **not** been verified; the adapter's
  math, word grouping, frame→second conversion, ordering, and honest-failure
  paths have. Tamil remains *declared*, not *tested*.
- **Model licensing** (MMS-FA → CC-BY-NC 4.0) is a remaining blocker for
  commercial use; see "License / provenance" above.
- The dependency-free core decodes **WAV/PCM** only; other formats require the
  optional `soundfile` backend.
- Execution is synchronous (alignment runs inside the create request); the job
  store is a small persistence/recovery layer, deliberately **not** a general
  workflow engine.
- The deterministic engine is for tests only and must never be presented as
  measured alignment.

## Repository layout

```
align_engine/
  contracts/        versioned request + output JSON contracts
  audio/            path safety + stdlib WAV inspection (real duration)
  engines/          provider-neutral interface, deterministic + real adapters, registry
  tools/            operator utilities (fetch_model: explicit one-time model acquisition)
  normalize.py      Tamil/English tokenization, original<->normalized mapping
  service.py        orchestration + timestamp/structural validation
  store.py          immutable, path-safe output artifact store
  jobs.py           SQLite job store (states, idempotency, retry, recovery)
  api/app.py        standalone FastAPI app
tests/              offline test suite (incl. test_real_adapter.py: real forced_align integration)
samples/            synthetic fixtures + generator
INTEGRATION_CONTRACT.md
```
