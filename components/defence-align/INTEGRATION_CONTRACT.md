# Integration contract — TTS → Alignment

This document defines the **only** supported way a future Text-to-Speech (TTS)
module connects to the alignment component. The TTS module and the alignment
component share no code and no private models; they couple solely through the
versioned JSON contracts below. This integration is **described here, not
implemented** — the alignment component does not import, call, or depend on any
TTS module.

Versions are explicit so either side can evolve independently:

- Request: `defence-align.request.v1`
- Output: `defence-align.output.v1`

---

## 1. What the TTS module provides

The TTS module is the *producer* of two artifacts the alignment component
*consumes*:

1. **An audio artifact** — the synthesized narration as an audio file, placed
   under the alignment component's configured **input root**
   (`DEFENCE_ALIGN_INPUT_ROOT`). The dependency-free core accepts PCM **WAV**;
   other formats require the optional `soundfile` backend.
2. **A transcript** — the exact narration text, as ordered sections/segments,
   embedded directly in the alignment request.

The TTS module never calls alignment internals; it builds a request document
and submits it to `POST /alignments` (or hands the document to an orchestrator
that does).

### Handoff by reference, confined to a root

The TTS module writes audio to a path **inside** the configured input root and
passes a **relative** `audio_path`. The alignment component resolves that path
against the input root and rejects absolute paths, backslashes, and `..`
traversal (HTTP `422`, code `unsafe_path`). The TTS module must not pass
absolute paths or paths outside the root.

### Integrity by hash

The TTS module must supply:

- `audio_sha256` — SHA-256 of the exact audio bytes it wrote.
- `transcript_sha256` — the transcript content digest (see §3).

The alignment component recomputes both and rejects mismatches (`422`,
`hash_mismatch`). This makes the handoff verifiable and tamper-evident: the
alignment component aligns exactly the audio and transcript the TTS module
committed to, or it refuses.

---

## 2. Request contract (`defence-align.request.v1`)

```jsonc
{
  "schema_version": "defence-align.request.v1",
  "alignment_job_id": "string (stable; idempotency key)",
  "project_id": "string",
  "audio_artifact_id": "string (TTS-side artifact id, carried through)",
  "audio_path": "relative/path/under/input_root.wav",
  "audio_sha256": "<64 hex>",
  "transcript_id": "string (TTS-side transcript id)",
  "transcript_sha256": "<64 hex>",
  "language": "ta | en",
  "sections": [
    {
      "section_id": "string (unique)",
      "segments": [
        {
          "segment_id": "string (unique across the transcript)",
          "original_text": "exact narration text (preserved verbatim)",
          "normalized_text": "optional, separate; never replaces original_text",
          "pronunciation": { "optional": "metadata object or null" }
        }
      ]
    }
  ],
  "config": {
    "engine": "deterministic-test | real",
    "granularity": "word | sentence",
    "allow_synthetic": false
  }
}
```

Rules the TTS module must honor:

- **Preserve the narration exactly** in `original_text`. Do not transliterate
  Tamil, drop English technical terms/acronyms/model designations, or fold
  punctuation into words. If the TTS module has a normalized form, send it in
  `normalized_text` (separate, optional) — it never overrides the original.
- **Stable, unique IDs.** `alignment_job_id` is the idempotency key: resubmitting
  the same id with the same payload replays the existing job; the same id with a
  different payload is a conflict (`409`). `section_id` and `segment_id` must be
  unique; duplicates are rejected (`422`).
- **Engine selection.** For production, set `engine: "real"`. The
  `deterministic-test` engine is synthetic and requires `allow_synthetic: true`;
  its output is test data, never a measurement — a TTS/production caller must
  not treat it as real alignment.
- **Granularity is a request.** The output always reports the granularity the
  engine actually produced (`actual_granularity`); if only sentence-level timing
  was possible it is labelled `sentence`, not word.

## 3. Transcript digest (`transcript_sha256`)

Computed over transcript content only (independent of job/project identity), so
the same transcript always yields the same digest:

```
canonical = compact, sorted-key, UTF-8 JSON (NaN/Infinity rejected) of:
{
  "schema_version": "defence-align.request.v1",
  "language": <language>,
  "sections": [
    { "section_id": ...,
      "segments": [ { "segment_id", "original_text", "normalized_text", "pronunciation" } ] }
  ]
}
transcript_sha256 = sha256(canonical)
```

Reference implementation: `align_engine.contracts.transcript_digest`. The TTS
side may reimplement it or vendor that one function; either way the digests must
agree.

## 4. Output contract (`defence-align.output.v1`)

The alignment component returns (and stores) an immutable artifact:

```jsonc
{
  "schema_version": "defence-align.output.v1",
  "alignment_job_id": "...", "project_id": "...",
  "audio_artifact_id": "...", "audio_sha256": "<64 hex>",
  "transcript_id": "...", "transcript_sha256": "<64 hex>",
  "language": "ta | en",
  "audio_duration_seconds": 12.34,          // measured from the audio
  "engine_name": "...", "engine_version": "...",
  "is_synthetic": false,                     // true ⇒ test data, not a measurement
  "requested_granularity": "word",
  "actual_granularity": "word | sentence",   // what was truly produced
  "sections": [
    { "section_id": "...",
      "segments": [
        { "segment_id": "...", "original_text": "...",
          "tokens": [
            { "token_id": "...", "kind": "word | punctuation | segment",
              "original_text": "...", "normalized_text": "...",
              "char_start": 0, "char_end": 3,
              "start": 0.0, "end": 0.42,      // null for non-spoken / unaligned
              "confidence": null,              // engine-provided only; never invented
              "is_uncertain": false, "aligned": true }
          ] }
      ] }
  ],
  "unaligned_tokens": [
    { "token_id": "...", "segment_id": "...", "original_text": "...", "reason": "..." }
  ],
  "validation": { "passed": true, "validator_version": "...", "issues": [ ... ] },
  "created_at": "ISO-8601 UTC",
  "artifact_sha256": "<64 hex>"              // digest of the body excluding created_at + this field
}
```

Consumer guarantees:

- Only `kind: "word"` (or `"segment"` for sentence-level) tokens carry timing;
  `punctuation` tokens are preserved for reconstruction but are never spoken
  words with their own timing.
- Every timestamp is finite, non-negative, ordered, and within
  `audio_duration_seconds` — structurally invalid engine timing is rejected
  (`422`, `alignment_output_invalid`) rather than emitted as a trusted artifact.
- `char_start`/`char_end` index the segment's `original_text`, so the original
  is always reconstructable and normalization is fully traceable.
- `artifact_sha256` is deterministic for identical inputs + identical engine
  output; a downstream module can content-address the alignment.

## 5. Lifecycle a downstream module can rely on

- Idempotent create by `alignment_job_id`; completed artifacts are **immutable**
  (a later retry/failure never overwrites a completed artifact).
- `FAILED` jobs are retryable and reuse the verified inputs; a job interrupted
  by a crash is recovered to `FAILED` (retryable) on restart.
- A failed or cancelled job never yields a fabricated "successful" alignment.

## 6. Explicit non-responsibilities

The alignment component does not synthesize speech, choose voices, generate or
edit narration, plan scenes, render video, or publish. The TTS module owns audio
synthesis and the transcript; the alignment component owns only the mapping from
that transcript to timing in that audio, exposed through the contracts above.
