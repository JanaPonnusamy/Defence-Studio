# Standalone Video Visual / Scene Composition Module — Full Build Specification

## 0. Document Purpose

This document is the **complete build specification for the standalone Video Visual / Scene Composition Module**.

A developer/agent receiving only this document must be able to build the component independently.

This document intentionally does **not** describe the parent application and must not require knowledge of any parent application's source code.

---

# 1. Component Identity

**Component name:** Visual Composition / Scene Visual Engine

**Primary responsibility:**

Convert narration/script meaning plus available visual assets into a timed, reusable visual shot plan.

It must determine:

- which visual to show
- when to show it
- how long to show it
- whether it is source material or AI-generated
- how it should move/crop
- what attribution is required
- what the renderer should receive

It does NOT render the final video.

---

# 2. Standalone Requirement

The module must have:

- independent source tree
- independent configuration
- module-owned storage
- module-owned processing jobs
- independent tests
- independent API
- independent asset metadata
- independent logs
- stable public contracts

No parent-application internal import is allowed.

---

# 3. Core Architecture

```text
                 SCRIPT / NARRATION
                         |
                         v
                +----------------+
                | Scene Parser   |
                +----------------+
                         |
                         v
                +----------------+
                | Meaning /      |
                | Visual Intent  |
                +----------------+
                         |
             +-----------+-----------+
             |                       |
             v                       v
     Source Asset Library       AI Generation
             |                       |
             +-----------+-----------+
                         |
                         v
                +----------------+
                | Asset Selector |
                +----------------+
                         |
                         v
                +----------------+
                | Shot Planner   |
                +----------------+
                         |
                         v
                +----------------+
                | Motion Planner |
                +----------------+
                         |
                         v
                +----------------+
                | Visual Timeline|
                +----------------+
                         |
                         v
                  VIDEO RENDERER
```

---

# 4. Content-Collection Relationship

When research/content acquisition discovers a news item, it should preserve related visual opportunities alongside the textual evidence.

Conceptual package:

```text
CONTENT ITEM
  |
  +-- sources
  +-- evidence
  +-- images
  +-- video clips
  +-- maps
  +-- documents
  +-- statistics
  +-- attribution metadata
  +-- rights/licence metadata
```

The Visual Module receives these assets through an import contract.

It must not assume an asset is reusable merely because it was discovered online.

---

# 5. Supported Asset Types

```text
IMAGE
VIDEO_CLIP
MAP
DIAGRAM
CHART
STAT_CARD
DOCUMENT
SATELLITE
SOURCE_PHOTO
AI_IMAGE
TITLE_CARD
TRANSITION
```

Each asset must have stable identity and provenance.

---

# 6. Asset Metadata

Minimum conceptual model:

```text
asset_id
asset_type
source_type
source_url
local_path/reference
title
description
creator
licence
attribution
rights_status
acquired_at
checksum
width
height
duration
provider
model
generation_cost
```

Source type:

```text
EXTERNAL_SOURCE
USER_PROVIDED
AI_GENERATED
SYSTEM_GENERATED
UNKNOWN
```

Unknown rights status must never be labelled "open source".

---

# 7. Visual Selection Priority

Default priority:

### Priority 1 — Relevant legitimate source/evidence

Use where permitted:

- official images
- licensed images
- public-domain images
- permitted source media
- permitted satellite imagery
- official documents
- maps
- verified statistics
- evidence screenshots where appropriate

### Priority 2 — AI-generated explanatory visuals

Use for:

- hook
- cinematic opening
- conceptual explanation
- atmosphere
- transitions
- non-evidentiary illustration

### Priority 3 — Local motion

Use motion to increase visual variety without generating another image.

---

# 8. AI Is Not Evidence

An AI-generated fighter jet, missile, ship, battlefield, politician, map, satellite image, or document must not be presented as an actual factual photograph/evidence item.

AI visual metadata must remain explicit:

```text
source_type = AI_GENERATED
```

The UI/renderer can optionally display an editorial label where required.

---

# 9. 10-Minute Video Asset Strategy

Initial planning target:

```text
10-minute finished video
        |
        +-- approximately 35–60 distinct visual assets
        |
        +-- approximately 100+ rendered shots possible
```

This is a planning range, not a hard maximum.

The exact number depends on narration density and story complexity.

---

# 10. AI Image Budget

For a normal 10-minute news episode:

```text
Primary AI hook/hero images:
3–4 recommended

Additional AI images:
only when editorially useful
```

The architecture must NOT require 40–60 AI generations.

Most visuals should come from:

- collected source material
- maps
- statistics
- documents
- charts
- video clips
- reused assets with new framing

This is both cheaper and more factually grounded.

---

# 11. Generate Once, Reuse Intelligently

One asset may create multiple shots:

```text
ASSET-001
   |
   +-- Shot 001: wide
   +-- Shot 002: slow zoom
   +-- Shot 003: close crop
   +-- Shot 004: pan
   +-- Shot 005: highlighted region
```

Reuse must include meaningful reframing.

Do not show the exact same unchanged frame repeatedly.

---

# 12. Visual Change Logic

The engine must be narration-driven.

Visual changes should happen when:

- subject changes
- location changes
- a new entity appears
- a new claim is introduced
- evidence is discussed
- a statistic appears
- a map is needed
- a document is referenced
- emotional/story emphasis changes
- a major sentence/idea begins

A fixed "change every N seconds" rule may be a fallback, not the primary editorial logic.

---

# 13. Scene Model

Each scene contains:

```text
scene_id
title
start_time
end_time
narration_segment_ids
visual_intents
asset_ids
shot_ids
transition
importance
```

---

# 14. Visual Intent

Visual intent converts narration meaning into a visual requirement.

Examples:

```text
GEOGRAPHIC_CONTEXT
ENTITY_INTRODUCTION
EVENT_EXPLANATION
TIMELINE
COMPARISON
STATISTIC
EVIDENCE
HARDWARE_DETAIL
MILITARY_CAPABILITY
LOCATION
ESCALATION
CONCLUSION
```

Example:

```text
Narration:
"The fleet is concentrated near the Black Sea."

Intent:
GEOGRAPHIC_CONTEXT

Preferred visual:
MAP

Fallback:
SOURCE_IMAGE + MAP OVERLAY
```

---

# 15. Shot Model

Minimum:

```text
shot_id
scene_id
start_time
end_time
duration
asset_id
visual_type
source_type
narration_anchor
motion
crop
transition
attribution
editorial_reason
```

---

# 16. Motion System

Supported base motion:

```text
STATIC
SLOW_ZOOM_IN
SLOW_ZOOM_OUT
PAN_LEFT
PAN_RIGHT
PAN_UP
PAN_DOWN
CROP_FOCUS
PARALLAX
HIGHLIGHT_REGION
```

Motion must be deterministic and renderer-friendly.

Motion must not distort factual content.

---

# 17. Image Cropping

The planner may create:

```text
wide composition
medium composition
close composition
subject-focused crop
region-focused crop
```

Important text, logos, faces, military markings, and evidence details must not be accidentally cropped where they matter.

---

# 18. Maps

Maps are first-class assets.

Map system should support:

- location markers
- route
- region highlight
- zoom
- labels
- timeline movement
- source attribution

Maps must use appropriate geographic data/licensing.

---

# 19. Statistics / Data Graphics

First-class graphic types:

```text
STAT_CARD
BAR_CHART
LINE_CHART
COMPARISON
TIMELINE
NUMBER_CALLOUT
```

Graphics must be generated from structured data, not hallucinated text.

Each statistic should preserve:

```text
value
unit
source
date/as-of
confidence/status
```

---

# 20. Documents / Evidence

Documents can include:

- official releases
- reports
- permitted screenshots
- maps
- charts
- satellite/public evidence

The visual plan should specify the relevant region/page/area when possible.

---

# 21. Video Clips

Source video clips may be used when rights permit.

Clip metadata:

```text
clip_id
source
in_point
out_point
duration
rights
attribution
```

The module may recommend a segment; final compositing belongs to the renderer.

---

# 22. AI Image Generation Adapter

Core module uses a provider-neutral contract:

```text
ImageGenerationRequest
        |
        v
AIImageProvider
        |
        +-- OpenAI
        +-- Local Model
        +-- Other Provider
```

Core logic must not contain provider-specific API calls.

---

# 23. Image Generation Request

Conceptual:

```json
{
  "prompt": "...",
  "aspect_ratio": "16:9",
  "resolution": "...",
  "quality": "...",
  "purpose": "HOOK",
  "scene_id": "...",
  "reference_assets": []
}
```

Result:

```text
asset_id
provider
model
request_id
generation_time
estimated_cost
metadata
```

---

# 24. Cost Accounting

Every paid image generation must record:

```text
provider
model
asset_id
quality
resolution
estimated_cost
currency
request_id
created_at
```

Episode-level reporting:

```text
AI image count
AI generation cost
source asset count
total visual asset count
cost per generated image
cost per finished minute
```

---

# 25. Processing Jobs

Long-running operations use:

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

# 26. Storage

Module-owned:

```text
visual_module/
  assets/
  source_metadata/
  generated/
  maps/
  graphics/
  documents/
  clips/
  scenes/
  plans/
  jobs/
  attribution/
  logs/
```

Do not depend on a parent application's asset folder.

---

# 27. Standalone UI

If a UI is implemented, it should provide:

## Asset Library

- imported assets
- generated assets
- source type
- licence status
- attribution
- preview

## Scene Planner

- scene list
- narration anchors
- visual intents
- shot timeline

## Visual Preview

- asset preview
- crop
- motion
- duration
- transition

## Generation Panel

- provider
- model
- prompt
- quality
- resolution
- estimated cost
- generation status

## Cost Panel

- episode cost
- provider cost
- generated asset count
- cost/minute

---

# 28. Public API

Minimum conceptual API:

```text
POST /projects
GET  /projects/{id}

POST /assets/import
GET  /assets
GET  /assets/{id}

POST /assets/generate
POST /assets/{id}/variants

POST /scenes
POST /visual-plans/build
GET  /visual-plans/{id}

GET  /shots/{visual_plan_id}

GET  /jobs/{id}

GET  /costs/{project_id}
```

---

# 29. Renderer Contract

The Visual Module outputs a renderer-neutral visual timeline.

Example conceptual:

```json
{
  "shot_id": "SHOT-001",
  "start": 0.0,
  "end": 5.5,
  "asset_id": "ASSET-001",
  "motion": {
    "type": "SLOW_ZOOM_IN",
    "from_scale": 1.0,
    "to_scale": 1.08
  }
}
```

The renderer converts this into actual video frames.

The Visual Module must not depend on FFmpeg internals or a particular rendering engine.

---

# 30. Audio Synchronization

Audio timing is an input boundary.

The visual planner may use:

```text
sentence start
sentence end
word/phrase anchor
scene duration
```

The Audio Module remains independent.

The Visual Module must not access Audio Module internals.

Integration happens through timing contracts.

---

# 31. Failure Handling

Examples:

```text
SOURCE_UNAVAILABLE
RIGHTS_UNKNOWN
ASSET_INVALID
GENERATION_FAILED
PROVIDER_TIMEOUT
PROVIDER_QUOTA
MAP_RENDER_FAILED
INVALID_STAT_DATA
MISSING_NARRATION_ANCHOR
```

Failures must be explicit.

Do not silently replace factual source material with fabricated AI imagery.

---

# 32. Security

- provider API keys backend-side only
- no credentials in prompts/logs
- sanitize imported metadata
- validate URLs
- validate downloaded files
- restrict filesystem access
- protect job endpoints
- avoid arbitrary command execution from asset metadata

---

# 33. Testing

Required:

### Asset tests

- import
- validation
- metadata
- checksum
- rights state

### Planning tests

- scene parsing
- visual intent
- asset selection
- shot timing
- reuse
- motion

### Generation tests

- provider adapter
- cost recording
- failure handling
- retry behavior

### Graphics tests

- statistics
- maps
- charts
- documents

### Contract tests

- public API
- renderer output
- schema compatibility

### Isolation tests

Prove that the module can run without the parent application.

---

# 34. Editorial Safety

The system must distinguish:

```text
FACTUAL EVIDENCE
ILLUSTRATIVE VISUAL
AI GENERATED
UNKNOWN
```

Never use an AI-generated image to imply that it is an actual photograph of an event.

Never invent statistics.

Never invent map boundaries or military locations.

When source material is unavailable, the system may use an explicitly illustrative AI visual instead of pretending it is evidence.

---

# 35. Non-Goals

This module does NOT:

- research news
- decide whether a news claim is true
- write the final narration
- generate TTS
- render final video
- upload to YouTube
- schedule publication
- own the application-wide user system
- guarantee rights for third-party content

---

# 36. Definition of Done

The standalone module is complete when:

- it runs independently
- source assets can be imported
- rights metadata is preserved
- AI assets can be generated through adapters
- 3–4 primary AI hook/hero images can be used as an episode strategy
- source/evidence visuals can be reused
- maps/data/documents are first-class
- one asset can produce multiple meaningful shots
- narration anchors drive visual timing
- motion instructions are renderer-neutral
- cost is tracked
- jobs are observable
- public contracts are documented
- tests pass
- no parent application source dependency exists

---

# 37. Final Locked Rule

**Do not build this component inside the main application.**

Build it as an independent reusable product.

After independent verification:

```text
Standalone Audio Module
          +
Standalone Visual Composition Module
          |
          v
     Public Contracts
          |
          v
   Any compatible application
```

The parent application is only one possible consumer.
