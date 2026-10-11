# Phase 4 Production Foundation Plan — Scene Planning → Export

**Status:** PLAN ONLY. No code written, no model downloaded, no API called, no config changed, no commit made.
**Scope authorized to read:** `project_status.md`, `CLAUDE.md` §1A, `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` §§4/8 (Phase 4–7), `component_registry/REGISTRY.md`, the two standalone components' contracts, and the prior Codex audits. No re-audit performed where one already exists.

## 1. What already exists (verified this session, not re-derived)

| Piece | Location | Real state |
|---|---|---|
| Approved-script export (Phase 3→4 boundary) | `backend/CONTRACTS.md` §Phase 3, `GET /api/jobs/{job_id}/script/export/{language}` | VERIFIED. Returns the one APPROVED revision per language, 409 if none/stale. This is the only legitimate Phase 4 input. |
| TTS generation component | `C:\Users\Pharma\.codex\artifacts\defence-studio-tts-20261011\` | VERIFIED standalone, 69 tests pass, CHECKSUMS.json 27/27 match. Contracts `defence-studio.tts-input/-job/-manifest.v1`. `EspeakNGProvider` real but **eSpeak NG not installed on this machine** — real synthesis unverified end-to-end. `OfflineToneProvider` is explicitly `is_mock:true`. |
| Alignment component | `E:\Jana Soft\defence-align\` | VERIFIED standalone, 76 tests pass. Real CTC forced-alignment adapter exists (`engines/real_adapter.py`) but has **not been run against a real downloaded model** (tests use controlled/mocked emissions). Tamil/English accuracy unverified (`tested_languages=()`). |
| Cross-component contract | `INTEGRATION_CONTRACT.md` (defence-align) + prior audit `tts-alignment-contract-audit-20261011-172048` | Request/output JSON contracts (`defence-align.request/output.v1`) are well-specified: hash-verified handoff by reference under a confined input root, idempotent by `alignment_job_id`, immutable completed artifacts, fail-closed on bad timing. **Two known defects re-confirmed live**: (C19) alignment API has no auth/owner scoping; (C17) `jobs.py::_complete` has no status guard → cancel→complete race; (C18) crash recovery is unfenced + execution is synchronous. |
| Model licensing | `phase4-readiness-20261011/PHASE4_READINESS.md` | MMS-FA (torchaudio's only ready-to-use Tamil-capable forced-aligner) is **CC-BY-NC 4.0 — non-commercial, blocks production use**. No commercially-licensed Tamil-capable alternative identified yet; MIT wav2vec2 alternatives are English-only. |
| Renderer | QVault `backend/app/services/video_render_service.py` + `integrations/ffmpeg.py` (per `REPOSITORY_AUDIT.md` lines 38/53/55/80) | Real, working FFmpeg H.264/AAC pipeline, but **quiz-shaped**: reloads a source JSON, builds WAV mixdown from quiz TTS clips, draws Pillow frames for quiz segments, no scene-plan input, no narrative asset/caption/music-cue model, Tamil glyph shaping unverified (Poppins font only), overwrite-mode output (no version-safe retry). Must be adapted, not replaced (core design §7 instruction). |
| Scene/voice/asset contracts | none | Not started. No `contracts/scene.py`, no scene-plan schema, nothing in `component_registry/REGISTRY.md` beyond BE-39 (script). |

**Conclusion:** Phases 4–7 are genuinely at zero. The two standalone components (TTS, alignment) are real, tested, and *not wired into DefenceStudio or into each other*. The renderer is real but coupled to QVault's quiz domain. Nothing here can be "wired up" cheaply — an orchestration layer, three new contracts, and one license decision are all prerequisite.

## 2. Minimum architecture (components to add, each respecting existing boundaries)

Preserve component independence per instruction 4: DefenceStudio orchestrates via HTTP/contract calls to the TTS and alignment components as external services (same pattern F3 already uses for SSRF-guarded external fetch, and Phase 3 already uses for the OpenAI provider behind a `client_factory` seam). **No internal module of either standalone component is imported.**

```
Approved script (Phase 3 export)
        │
        ▼
 ┌─────────────────┐   new: contracts/scene.py + services/scene.py
 │ Scene Plan       │   DefenceStudio-owned; versioned, editable, re-orderable
 │ (Phase 4)        │   scene_id, narration slice, purpose, linked claim IDs,
 └────────┬─────────┘   visual suggestions, voice direction (tone/rate/pitch/
          │             pauses — metadata only), caption/transition/crop/
          │             graphic/music-cue instructions, review status
          ▼
 ┌─────────────────┐   new: contracts/assets.py + services/assets.py (Phase 5)
 │ Asset assignment │   per scene: url, creator/publisher, rights basis,
 │ + rights review  │   attribution requirement, retrieval time, review status
 └────────┬─────────┘   Never fabricated; unknown rights flagged/blocked.
          │
          ▼
 ┌─────────────────┐   new: providers/tts_adapter.py (HTTP client to the
 │ Voice/TTS        │   standalone TTS component's /v1/* API — never imports
 │ orchestration    │   tts_generation internals). Maps scene narration →
 └────────┬─────────┘   TTSInputV1 sections, drives job→generate→manifest,
          │             stores manifest + audio artifact references.
          ▼
 ┌─────────────────┐   new: providers/align_adapter.py (HTTP client to
 │ Word alignment   │   defence-align's /alignments — never imports
 └────────┬─────────┘   align_engine internals). Writes TTS audio under
          │             the alignment input root, submits hash-verified
          │             request built from the *same* spoken-text chunks
          │             TTS reports, consumes defence-align.output.v1.
          ▼
 ┌─────────────────┐   new: services/render_adapter.py — a *stable adapter*
 │ Render           │   over the existing QVault FFmpeg builder (core design
 │ (Phase 7)        │   §7 explicit instruction: adapt, do not replace).
 └────────┬─────────┘   Scene plan + asset assignments + measured audio +
          │             alignment timing → renderer input shape; versioned
          │             output path (no silent overwrite); progress/log
          │             surface; cancel/retry.
          ▼
 ┌─────────────────┐   new: services/qc.py (Phase 8)
 │ QC gate          │   file validity, duration match, missing asset, black-
 └────────┬─────────┘   gap, clipping, subtitle timing/overflow, rights/
          │             attribution presence, source register presence.
          ▼
 ┌─────────────────┐   Phase 9 — already speced separately (YT-01..YT-08);
 │ Branding/export  │   out of this plan's scope, unchanged.
 └─────────────────┘
```

All five new DefenceStudio-owned pieces (scene, assets, tts_adapter, align_adapter, render_adapter) are locked worker stages reusing the existing F2 durable `Worker` (BE-07) — the same pattern F3/F4/discovery already use — not a second execution architecture.

## 3. Cross-cutting requirements (instruction 5)

- **Authentication**: DefenceStudio→TTS and DefenceStudio→alignment calls need owner-scoped bearer credentials. The alignment component currently has **no auth/owner scoping at all** (C19) — this is a hard blocker for calling it from a multi-tenant orchestrator; it must be fixed in the standalone alignment component (owned by that component's repo, not DefenceStudio) before DefenceStudio can safely issue requests on behalf of different owners.
- **Cancellation**: DefenceStudio stage handlers must be cancel-safe mid-TTS and mid-alignment (long-running external calls). The alignment component's cancel→complete race (C17, no status guard in `_complete`) must be fixed upstream first, or DefenceStudio's adapter must defensively re-check job state after every poll before trusting a "completed" response.
- **Retry**: must reuse F2's existing retry-safe-stage opt-in (`retry_safe_stages` exact-match registration, already enforced fail-closed). TTS/alignment jobs are idempotent by `Idempotency-Key`/`alignment_job_id` respectively, so retries can safely replay rather than duplicate spend.
- **Restart recovery**: the alignment component's crash recovery is currently unfenced and execution synchronous (C18) — a crash mid-align is not provably safe yet. DefenceStudio's adapter must not assume a restarted alignment job resumes cleanly until that is fixed or the adapter adds its own fencing (e.g., treat any non-terminal alignment job older than a lease timeout as failed-retryable, mirroring F2's own lease-expiry pattern).
- **Artifact integrity**: both external contracts already hash-verify (TTS `sha256` per chunk/manifest; alignment `audio_sha256`/`transcript_sha256`/`artifact_sha256`). DefenceStudio's adapters must re-verify these hashes on receipt (same "never trust, re-verify" pattern F4 already applies to F3's artifacts) before writing a DefenceStudio-side artifact reference.
- **Licensing/provenance**: every TTS artifact already carries `is_mock`/provider/cost-basis; every alignment artifact carries `is_synthetic`/`engine_name`/`engine_version`. DefenceStudio must propagate these flags into its own scene/render artifacts unmodified — a rendered video must never claim real synthesis/alignment if either upstream flag says otherwise. This is a hard acceptance gate, not a UI nicety.

## 4. Alignment-model licensing path (instruction 6 — decision needed, not made here)

Current blocker, confirmed from `PHASE4_READINESS.md`: MMS-FA (the only ready, Tamil-capable forced-aligner in torchaudio) is CC-BY-NC 4.0 — incompatible with commercial use. Three paths exist; **none should be executed without separate approval**:

1. **Commercial license negotiation** with Meta/fairseq MMS licensors for CC-BY-NC override — unknown cost/availability, needs a business decision.
2. **Alternative commercially-licensed multilingual aligner** (e.g., a Whisper-based forced-alignment approach using OpenAI's or a self-hosted Whisper model under its actual license terms, not torchaudio's MMS pipeline) — needs a fresh licensing check per candidate model; wav2vec2-MIT alternatives found so far are English-only and don't cover Tamil.
3. **Sentence-level fallback without word-level MMS**: ship Phase 6 initially at `actual_granularity: "sentence"` (already a first-class, honestly-labelled output the alignment contract supports) using a commercially-clear sentence aligner, deferring word-level Tamil alignment to a later model decision. This unblocks Phase 4–7 without resolving the license question immediately, at the cost of coarser caption/scene timing.

This plan does not pick one — it is the first open decision the user must make before Milestone 3 below can start for real audio. Milestones 1–2 do not depend on the outcome.

## 5. Milestones, ordered, with prerequisites and acceptance tests

**M0 — Contracts only (no execution).** Prerequisite: none (Phase 3 already provides the input).
Add `backend/defence_studio/contracts/scene.py` (scene-plan schema: ordered scenes, `scene_id`, narration slice referencing the approved script's claim IDs, visual suggestions, voice-direction metadata kept separate from spoken text, caption/transition/crop/music-cue fields, review status) and extend `component_registry/REGISTRY.md` + `CONTRACTS.md` with a Phase 4 section.
*Acceptance test*: schema validates a hand-built scene plan derived from a real approved Phase 3 export; rejects an unknown field, a scene referencing a claim ID not present in the approved package, and an out-of-order/duplicate `scene_id`. No network, no DB write beyond the contract's own unit tests.

**M1 — Scene service + worker stage (editable, versioned, no render).** Prerequisite: M0.
`services/scene.py`: generate an initial scene plan deterministically from the approved script's sections (1 scene per section as a starting default), support add/remove/reorder/edit of individual scenes without rebuilding untouched approved scenes, versioned like `script_revisions`, explicit review/approval state.
*Acceptance test*: editing one scene does not change the content hash of any other scene's stored version; approving a scene plan binds to the exact approved-script revision hash it was built from (same stale-revision 409 pattern as Phase 2/3); re-approving after the underlying script changes is rejected until re-synced.

**M2 — TTS adapter (HTTP, hash-verified, no model changes).** Prerequisite: M1; TTS component already real and tested — no change needed there.
`providers/tts_adapter.py` + a `voice_tts_orchestration` worker stage: maps each scene's narration into `TTSInputV1` sections, submits via the TTS component's real `/v1/jobs` API with an owner-scoped credential (per §7 of core design — user's own API credentials, not shared), polls to completion, re-verifies every returned `sha256` before storing a DefenceStudio artifact reference to the manifest.
*Acceptance test*: a real round trip against the standalone TTS component (using `OfflineToneProvider`, explicitly `is_mock:true` — **not** claimed as real narration) produces a stored, hash-verified manifest artifact; a corrupted/mismatched hash from the TTS side is rejected, not silently trusted; cancelling mid-job leaves no orphaned "completed" artifact. eSpeak NG real-voice synthesis is **out of scope for this milestone's acceptance** until eSpeak NG is actually installed and verified — tracked as a separate, explicit blocker, not silently assumed.

**M3 — Alignment adapter.** Prerequisite: M2 AND one of: (a) alignment component's C19/C17/C18 defects fixed upstream, or (b) DefenceStudio's adapter adds its own compensating owner-scoping/fencing as described in §3. **Also prerequisite: the licensing decision in §4 is made** (at minimum, a decision to proceed at `sentence` granularity if word-level MMS licensing is unresolved).
`providers/align_adapter.py`: writes the TTS-produced audio under the alignment component's configured input root, builds a `defence-align.request.v1` from the *exact* spoken-text chunks TTS reported (not a re-derivation), submits, polls, re-verifies `artifact_sha256`/timing invariants on receipt.
*Acceptance test*: a real round trip against the standalone alignment component with a real TTS-produced WAV produces a stored, hash-verified alignment output; `is_synthetic` is propagated faithfully (true if the alignment engine used was `deterministic-test`, which must never be used in a production path); a structurally invalid timing response from alignment is rejected end-to-end, not forwarded.

**M4 — Render adapter (reuse the QVault FFmpeg builder, narrative-shaped).** Prerequisite: M1 (scene plan) + M3 (measured audio/timing) are both available for the same job; renderer itself is not touched beyond a new adapter layer.
`services/render_adapter.py`: maps {scene plan, asset assignments [stub-only if Phase 5 not yet built — see note], measured audio, alignment timing} into the existing FFmpeg builder's input shape; versioned output path (never overwrites a prior approved render); real progress/log surface; cancel/retry.
*Acceptance test*: renders one real short narrative video end to end from a real approved scene plan + real TTS/alignment output (sentence- or word-level, per §4's decision) with no fabricated timing; output duration matches measured audio duration within the renderer's own frame-rate tolerance; retrying a cancelled render does not corrupt or silently overwrite a prior completed one.

**Note on Phase 5 (assets):** the core design locks Phase 4 before Phase 5, but a render can proceed with placeholder/no visual assets (narration + captions only) for M4's acceptance test; full asset collection/rights-review is a separate milestone (M4a, same ordering position as core design §8 Phase 5) and is a prerequisite only for a *publishable* (not merely renderable) video.

**M5 — QC gate (Phase 8).** Prerequisite: M4.
Deterministic checks per core design §8 (duration match, missing asset, black gaps, clipping, subtitle timing/overflow, rights/attribution presence, source register). Failing QC blocks job completion, not just a warning.
*Acceptance test*: a deliberately broken render (e.g., truncated audio, missing asset reference) fails the correct QC check with a specific reason; a clean render passes all checks.

## 6. First authorized coding task

**M0 — `backend/defence_studio/contracts/scene.py` + `CONTRACTS.md`/`REGISTRY.md` Phase 4 section.**
Dependencies: none beyond what already exists (Phase 3's approved-script export). No external call, no model, no renderer change, no standalone-component change. This is the smallest, fully self-contained unit that unblocks M1, and is the correct next step per the locked phase order (core design §3) and the explicit non-authorization of Phase 4 execution in `project_status.md`'s "Next task" line.

**Explicitly not started by this plan**: no scene/asset/TTS/alignment/render code, no eSpeak NG install, no model download, no licensing commitment, no commit. The alignment-licensing decision (§4) and the eSpeak-NG-install decision are both open items for the user, independent of M0.
