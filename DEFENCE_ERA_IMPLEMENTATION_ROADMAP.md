# Defence Era — Implementation Roadmap (audit 2026-10-14)

Order follows the locked pipeline in core design §3. One phase at a time; each ends with an acceptance report before the next begins (Rule 7). Phase labels below are **roadmap steps (RM)**; they map to the official phase numbers and do not replace them.

## Dependency order

| Step | Name | Official phase | Pending items closed | Prereqs |
|---|---|---|---|---|
| **RM-0** | Housekeeping gate | P0 follow-up | A1, A2, A3 | User authorization for any commit |
| **RM-1** | **Content Search & Discovery** | Phase 1A/1B | B1, B2, B3, part of D5 | RM-0; search provider/credential decision |
| RM-2 | Research-to-script live gate | Phase 1D, 2, 3 | D1, D2, D5, C5 | OpenAI credential and budget |
| RM-3 | Scene ↔ visual planning integration | Phase 4/5 | C1, B12 (scene, asset views) | RM-2 |
| RM-4 | Voice integration | Phase 6 | B4, C2, D6 | RM-3 |
| RM-5 | Render integration and subtitles | Phase 7 | B5, B6, C3 | RM-4 |
| RM-6 | QC and approval | Phase 8 | B7 | RM-5 |
| RM-7 | Branding and publish package | Phase 9a | B8 | RM-6 |
| RM-8 | YouTube publishing | Phase 9b | B9 | RM-7, Google credentials, YT-01 plan approval |
| RM-9 | Cross-cutting | §7, §10 | B10, B11, A4, E1–E6 | Any time after RM-1, in own phases |
| RM-10 | Additional formats | Phase 10 | — | RM-8 |

## Boundaries of each step

**RM-0 Housekeeping gate (documentation and repo hygiene only).** Rewrite the `project_status.md` tracker from `DEFENCE_ERA_PHASE_STATUS.md`. Record the single-writer rule. If the user authorizes, un-ignore `standalone/audio-module` and `standalone/video-renderer` (source only). No product code changes. Exit: A1–A3 closed or explicitly deferred.

**RM-1 Content Search & Discovery.** Defined in `DEFENCE_ERA_PHASE1_CONTENT_SEARCH_SPEC.md`. Excludes source collection, extraction, scripts, TTS, rendering. Exit: spec §13 acceptance.

**RM-2 Research-to-script live gate.** Run the real pipeline once end to end in a browser for one topic; first live script generation with usage records. No scene/voice/render work. Exit: D1, D2 recorded.

**RM-3 Scene ↔ visual planning.** Decide whether the Visual Module is the planner behind the core asset/scene contracts; map scene plan to its input and its timeline v1.1 back to an artifact. No voice or render changes.

**RM-4 Voice.** HTTP adapter from approved script/scene to the Audio Module; store audio with SHA-256; word alignment artifact referenced by scene plan `alignment_ref`. Tamil voice outcome documented honestly; no emotional-voice claims without evidence.

**RM-5 Render.** Package builder from scene plan + audio + assets to `video-render-package` v1; stage handler calling the renderer; Tamil text rendering decision and subtitle burn-in. The renderer is not re-architected.

**RM-6 QC.** Extend renderer `qc_report.json` into the design's Phase 8 checks, a review screen, and selective re-run.

**RM-7/8** per the design and `DEFENCE_STUDIO_YOUTUBE_INTEGRATION_SPECIFICATION.md`; YT-01 is inspect-and-plan with no code until approved.

## Gate template (every step)

Scope and exclusions · prerequisites · plan · source changes · automated tests · regression (backend 287, frontend 7, and affected standalone suites at the baselines in the audit) · build/runtime check · list of unverified items · updated docs and phase status · acceptance report.

## Baselines to hold (from this audit)

Backend 287 passed / 2 skipped · frontend 7 passed · Audio 31 · Visual Module 206 passed / 2 skipped · Renderer 117 passed / 1 skipped. A step may add tests; it may not reduce these.
