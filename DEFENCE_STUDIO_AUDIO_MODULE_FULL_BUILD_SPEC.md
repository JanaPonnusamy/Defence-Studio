# Standalone Audio Module — Full Build Specification

## 0. Document Purpose

This document is the **complete build specification for the standalone Audio Module**.

A developer/agent receiving only this document must be able to create the component as an independent software project.

This document intentionally does **not** explain or depend on the parent Defence Studio application.

The finished component must be reusable by any application through its public API/contracts.

---

# 1. Component Identity

**Component name:** Voice Audio Lab / Audio Module

**Primary responsibility:**

- reference audio/video ingestion
- audio extraction
- audio normalization
- transcript/SRT ingestion
- forced alignment
- acoustic analysis
- speech-performance analysis
- reusable performance profile creation
- narration performance planning
- provider-neutral TTS synthesis
- generated-audio validation
- waveform/timeline data
- job processing and status

**Not responsible for:**

- news research
- article writing
- video editing
- final video rendering
- YouTube publishing
- application-wide authentication
- application-wide database ownership

---

# 2. Standalone Requirement

The component must run independently.

It must have:

- its own source tree
- its own configuration
- its own storage
- its own database/schema if persistence is required
- its own migrations
- its own tests
- its own API
- its own job system
- its own documentation
- its own logging
- its own error model

No dependency on a parent application's internal files is permitted.

---

# 3. Architecture

```text
                INPUTS
                  |
       +----------+----------+
       |                     |
   Audio File            Video/URL
       |                     |
       +----------+----------+
                  |
                  v
        +-------------------+
        | Ingestion         |
        +-------------------+
                  |
                  v
        +-------------------+
        | Normalize/Validate|
        +-------------------+
                  |
                  v
        +-------------------+
        | Transcript/SRT    |
        | Acquisition       |
        +-------------------+
                  |
                  v
        +-------------------+
        | Forced Alignment  |
        +-------------------+
                  |
                  v
        +-------------------+
        | Audio Analyzer    |
        +-------------------+
                  |
                  v
        +-------------------+
        | Performance       |
        | Profile Builder   |
        +-------------------+
                  |
                  v
        +-------------------+
        | Performance       |
        | Planner            |
        +-------------------+
                  |
                  v
        +-------------------+
        | TTS Adapter       |
        +-------------------+
                  |
                  v
        +-------------------+
        | Audio Validator   |
        +-------------------+
                  |
                  v
             OUTPUT AUDIO
```

---

# 4. Input Types

## 4.1 Direct audio

Supported conceptually:

- WAV
- PCM
- MP3
- M4A
- FLAC
- other formats through a safe decoder

Preferred analysis format:

```text
PCM WAV
mono/stereo as required
known sample rate
normalized loudness
```

## 4.2 Reference video

The module may accept an authorized video source and extract audio.

The module must not assume that every URL can legally or technically be downloaded.

Acquisition must respect:

- source permissions
- platform restrictions
- licensing
- applicable terms

## 4.3 Transcript

Supported:

- SRT
- VTT
- plain transcript
- provider transcript output
- manually supplied transcript

---

# 5. Reference Processing

Each reference should have:

```text
reference_id
source_type
source_location
title
duration
language
audio_id
transcript_id
alignment_id
analysis_id
created_at
status
rights_metadata
```

Processing pipeline:

```text
REGISTER
  ↓
ACQUIRE
  ↓
EXTRACT AUDIO
  ↓
NORMALIZE
  ↓
LOAD TRANSCRIPT
  ↓
ALIGN
  ↓
ANALYZE
  ↓
STORE RESULTS
```

---

# 6. Audio Normalization

Normalization should produce deterministic analysis input.

Store:

- sample rate
- channels
- bit depth
- duration
- loudness measurements
- peak measurements
- format
- checksum

Never overwrite the original reference unless explicitly requested.

---

# 7. Forced Alignment

Purpose:

Map transcript text to actual spoken audio.

Minimum conceptual output:

```json
{
  "segments": [
    {
      "text": "example sentence",
      "start": 12.42,
      "end": 15.83,
      "words": [
        {
          "text": "example",
          "start": 12.42,
          "end": 13.10,
          "confidence": 0.91
        }
      ]
    }
  ]
}
```

The implementation must support word/phrase timing where the selected aligner supports it.

Alignment quality must be reported.

---

# 8. Acoustic Analyzer

Measure where technically possible:

### Timing

- total speech duration
- silence duration
- speech ratio
- speaking rate
- articulation rate
- pause frequency
- pause duration
- micro-pause duration

### Pitch

- F0
- pitch range
- pitch movement
- rising contour
- falling contour
- flat contour

### Energy

- RMS/energy
- relative loudness
- energy variation
- emphasis candidates

### Prosody

- stress/emphasis candidates
- phrase-level modulation
- sentence-level modulation
- transition behaviour

### Pronunciation/delivery

Where supported:

- articulation characteristics
- pronunciation confidence
- repeated delivery patterns

---

# 9. Repeated Word Rule

The system must NOT store:

```text
"military" = pitch X, tempo Y
```

as one permanent value.

Instead:

```text
Occurrence 1:
word = military
context = introduction
tempo = ...
pitch = ...
energy = ...

Occurrence 2:
word = military
context = warning
tempo = ...
pitch = ...
energy = ...
```

The profile builder may derive patterns from many observations.

---

# 10. Performance Profile

A profile describes **how narration should be performed**, not whose voice it is.

Example conceptual profile:

```json
{
  "profile_name": "Authoritative Defence Storytelling",
  "tempo": "moderate",
  "pause_style": "medium",
  "energy": "controlled-high",
  "emphasis": "selective",
  "pitch_style": "moderately dynamic",
  "transition_pause": "medium"
}
```

Actual schema may be richer.

---

# 11. Performance Planner

Input:

```text
script
performance_profile
language
provider_capabilities
```

Output:

```text
performance instructions
```

Example:

```text
Sentence 14
tone: authoritative
pace: normal
emphasis: "major escalation"
pause_after: medium
energy: high
```

The planner must remain semantic/provider-neutral.

---

# 12. Target Voice vs Reference Performance

Two independent concepts:

```text
VOICE IDENTITY
= target/licensed voice model

PERFORMANCE
= profile learned from authorized references
```

Reference analysis must not automatically become voice cloning.

Voice-model creation is outside the performance-analysis responsibility unless explicitly implemented as a separately licensed subsystem.

---

# 13. TTS Adapter Architecture

```text
             TTS Request
                  |
                  v
        Provider-Neutral Interface
          /        |         \
         /         |          \
   Local TTS   Cloud A     Cloud B
```

Core code must not contain provider-specific assumptions.

Each adapter reports:

- provider
- model
- voice
- language
- capabilities
- output format
- estimated cost where applicable
- request/result identifier

---

# 14. TTS Request

Conceptual:

```json
{
  "text": "...",
  "voice_id": "...",
  "language": "en",
  "performance_plan_id": "...",
  "output_format": "wav"
}
```

Provider adapters translate this into provider-specific settings.

---

# 15. Generated Audio Validation

Validate:

- file exists
- duration
- sample rate
- channels
- silence
- clipping
- corrupted output
- expected language/output
- provider metadata

Return explicit validation status.

---

# 16. Job System

Every expensive/long operation uses a job.

```text
job_id
job_type
status
progress
stage
created_at
started_at
completed_at
error_code
error_message
result_id
```

Statuses:

```text
QUEUED
RUNNING
SUCCEEDED
FAILED
CANCELLED
```

---

# 17. Storage

Module-owned:

```text
audio_module/
  references/
  audio/
  transcripts/
  alignment/
  analysis/
  profiles/
  generated/
  waveforms/
  jobs/
  logs/
```

Use stable IDs rather than user-facing filenames as primary identity.

---

# 18. UI — Voice Audio Lab

The component may provide a standalone professional UI.

Main areas:

```text
Reference Library
Processing Queue
Waveform / Timeline
Transcript
Audio Playback
Analysis Overlay
Performance Profile
Audio Creator
Job Status
```

Reference screen should allow:

- add reference
- add authorized URL
- upload audio
- attach transcript/SRT
- process
- inspect status
- inspect analysis
- remove reference

Audio Creator should allow:

- enter text
- choose target voice
- choose performance profile
- select TTS provider
- generate
- preview
- inspect duration
- export

---

# 19. Public API

Minimum API:

```text
POST /references
GET  /references
GET  /references/{id}
DELETE /references/{id}

POST /references/{id}/process

GET /jobs/{id}

GET /audio/{id}
GET /transcripts/{id}
GET /alignment/{id}
GET /analysis/{id}

POST /profiles/build
GET /profiles
GET /profiles/{id}

POST /tts/synthesize
GET /generated/{id}
```

The API must return machine-readable errors.

---

# 20. Configuration

Configuration must support:

```text
storage root
database
job worker
alignment provider
TTS providers
model configuration
limits
logging
feature flags
```

Secrets must come from secure environment/configuration mechanisms.

Never expose secrets in the UI or logs.

---

# 21. Testing

Required test categories:

- unit tests
- API tests
- schema/contract tests
- alignment tests
- analyzer tests
- profile tests
- planner tests
- TTS adapter tests
- job lifecycle tests
- storage tests
- failure/retry tests
- isolation tests

Mock TTS must be clearly marked as mock.

Real inference must be separately tested.

---

# 22. Licensing / Rights

Reference audio/video must be authorized for the intended analysis/use.

The module must store rights metadata where available.

A technically accessible URL does not automatically mean reusable content.

Any model used for commercial voice generation must have compatible licensing.

---

# 23. Integration Contract

External applications consume:

```text
Generated Audio
Audio Metadata
Duration
Word/Sentence Timing
Performance Metadata
Job Status
```

External applications must NOT access:

```text
internal analyzer classes
internal database tables
internal storage paths
provider implementation
private module functions
```

---

# 24. Definition of Done

The module is complete only when:

- standalone project runs independently
- ingestion works
- normalization works
- transcript handling works
- alignment works
- analysis produces structured results
- repeated occurrences remain separate
- profiles can be built
- performance plans can be generated
- TTS adapters work
- generated audio is validated
- jobs are observable
- UI can operate the module independently
- tests pass
- contracts are documented
- licensing is explicit
- no parent application dependency exists
