# Component Registry — Index

**Updated:** 2026-10-10 · See [`README.md`](README.md) for the "one copy + README" rule and portability classes.

Status legend: **VERIFIED** source inspected & tested · **PARTIAL** built, limited tests · **PLANNED** not built yet.
Portability: **STANDALONE** (lift single file) · **BUNDLE** (lift listed files) · **COUPLED** (port behind an interface).

---

## Backend — core workflow (F1/F2)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-01 | Bearer-token auth dependency | `backend/defence_studio/auth.py` | BUNDLE (needs FastAPI) | fastapi | `CONTRACTS.md` → HTTP endpoints | VERIFIED |
| BE-02 | Config / path resolution | `backend/defence_studio/config.py` | COUPLED (project layout) | stdlib | module docstring | VERIFIED |
| BE-03 | SQLite bootstrap + additive migration | `backend/defence_studio/database.py` | COUPLED | stdlib sqlite3 | `CONTRACTS.md` → persistence; `WORKER_CONTRACT.md` | VERIFIED |
| BE-04 | Job/stage/artifact contracts | `backend/defence_studio/contracts/jobs.py` | STANDALONE (typed shapes) | stdlib | `CONTRACTS.md` → JobCreateV1 / Stage transition / ArtifactReferenceV1 | VERIFIED |
| BE-05 | Job repository (persistence) | `backend/defence_studio/repositories/jobs.py` | COUPLED (BE-03) | stdlib | `CONTRACTS.md` | VERIFIED |
| BE-06 | Job service (create/transition/queue) | `backend/defence_studio/services/jobs.py` | COUPLED (BE-04, BE-05) | stdlib | `CONTRACTS.md` | VERIFIED |
| BE-07 | Durable worker engine (claims/leases/retry/recovery) | `backend/defence_studio/services/worker.py` | BUNDLE (BE-03, BE-05) | stdlib | `WORKER_CONTRACT.md` | VERIFIED |
| BE-08 | FastAPI app wiring | `backend/defence_studio/api/app.py` | COUPLED (all above) | fastapi, uvicorn | `backend/README.md` | VERIFIED |

## Backend — reusable utilities (prime cross-project candidates)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-09 | Path-safe per-job artifact byte store (atomic write + SHA-256, containment) | `backend/defence_studio/storage/artifacts.py` | **STANDALONE** | stdlib only | module docstring | VERIFIED |
| BE-10 | SSRF URL guard (scheme/credential/private-IP/redirect re-validation) | `backend/defence_studio/acquisition/url_guard.py` | **STANDALONE** | stdlib only | module docstring | VERIFIED |
| BE-11 | Guarded HTTP fetcher (per-hop re-validation, size/time caps, header allow-list) | `backend/defence_studio/acquisition/http_client.py` | BUNDLE (BE-10) | httpx | module docstring | VERIFIED |

## Backend — content acquisition (F3)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-12 | Acquisition contracts (declared-vs-unavailable metadata) | `backend/defence_studio/contracts/acquisition.py` | STANDALONE | stdlib | module docstring | VERIFIED |
| BE-13 | Acquisition provider interface + registry | `backend/defence_studio/acquisition/provider.py` | BUNDLE (BE-10, BE-11, BE-12) | stdlib | module docstring | VERIFIED |
| BE-14 | YouTube subtitle/caption provider (subtitles only, host-restricted) | `backend/defence_studio/acquisition/providers/youtube.py` | BUNDLE (BE-13) | yt-dlp (injectable) | module docstring | VERIFIED |
| BE-15 | HTTP page/document provider (declared metadata only) | `backend/defence_studio/acquisition/providers/http_page.py` | BUNDLE (BE-13) | stdlib html.parser | module docstring | VERIFIED |

## Backend — source analysis (F4)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-16 | Analysis request/result/claim/evidence contracts | `backend/defence_studio/contracts/analysis.py` | STANDALONE | stdlib | `CONTRACTS.md` → F4 analysis contract | VERIFIED |
| BE-17 | HTML/subtitle/plain-text normalization + script-based language guess | `backend/defence_studio/analysis/normalize.py` | STANDALONE | stdlib html.parser | module docstring | VERIFIED |
| BE-18 | Exact / identical-content / near-duplicate source grouping | `backend/defence_studio/analysis/dedup.py` | STANDALONE | stdlib | module docstring | VERIFIED |
| BE-19 | Deterministic verbatim claim/passage extraction | `backend/defence_studio/analysis/claims.py` | STANDALONE | stdlib | module docstring | VERIFIED |
| BE-20 | Claim grouping + cross-source conflict flagging | `backend/defence_studio/analysis/relationships.py` | STANDALONE | stdlib | module docstring | VERIFIED |
| BE-21 | Strict AI-output validation seam (no provider wired) | `backend/defence_studio/analysis/ai_schema.py` | STANDALONE | stdlib | module docstring | VERIFIED |
| BE-22 | Analysis orchestration service + retry-safe F2 worker handler | `backend/defence_studio/services/analysis.py` | BUNDLE (BE-16..21) | stdlib | `CONTRACTS.md` → F4 analysis contract | VERIFIED |

## Backend — feed discovery (Phase 1A)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-26 | Discovery request/result/candidate/ranking contracts | `backend/defence_studio/contracts/discovery.py` | STANDALONE (reuses BE-12's `MetadataStatus`) | stdlib | `CONTRACTS.md` → Phase 1A feed discovery contract | VERIFIED |
| BE-27 | Discovery providers (`rss_feed`, `configured_search_api`) + registry | `backend/defence_studio/discovery/providers.py` | BUNDLE (BE-10, BE-11) | stdlib xml.etree | module docstring | VERIFIED |
| BE-28 | Deterministic, explainable candidate ranking | `backend/defence_studio/discovery/ranking.py` | STANDALONE | stdlib | module docstring | VERIFIED |
| BE-29 | Discovery orchestration (per-provider isolation, canonical dedup) | `backend/defence_studio/discovery/service.py` | BUNDLE (BE-26..28, BE-10) | stdlib | module docstring | VERIFIED |
| BE-30 | Retry-safe F2 worker handler for the discovery stage | `backend/defence_studio/discovery/handler.py` | BUNDLE (BE-26, BE-29, BE-09) | stdlib | `CONTRACTS.md` → Phase 1A feed discovery contract | VERIFIED |
| BE-31 | Discovery API service (request/browse/select, F3 handoff) | `backend/defence_studio/services/discovery.py` | COUPLED (BE-06, BE-26, BE-09) | stdlib | `CONTRACTS.md` → Phase 1A feed discovery contract | VERIFIED |

## Backend — evidence review & approval (F5)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-23 | Review decision/subject/history contracts | `backend/defence_studio/contracts/review.py` | STANDALONE | stdlib | `CONTRACTS.md` → F5 evidence review contract | VERIFIED |
| BE-24 | Review/history SQL repository | `backend/defence_studio/repositories/review.py` | COUPLED (schema v3) | stdlib sqlite3 | `CONTRACTS.md` → F5 evidence review contract | VERIFIED |
| BE-25 | Review decision service (stale/concurrency/idempotency, read-models) | `backend/defence_studio/services/review.py` | COUPLED (BE-23, BE-24, F3/F4 artifacts) | stdlib | `CONTRACTS.md` → F5 evidence review contract | VERIFIED |

## Backend — evidence package, timeline, approval & export (Phase 2)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-32 | Evidence-package + timestamp-rule + strict schema contracts | `backend/defence_studio/contracts/evidence.py` | STANDALONE | pydantic | `CONTRACTS.md` → Phase 2 evidence-package contract | VERIFIED |
| BE-33 | Evidence package/timeline/approval/export service (readiness gate, revision-bound approval, deterministic export) | `backend/defence_studio/services/evidence.py` | COUPLED (BE-25, BE-16, BE-26, schema v4) | stdlib | `CONTRACTS.md` → Phase 2 evidence-package contract | VERIFIED |
| BE-34 | Research worker entry point (reuses F2 Worker; stops at human-review boundary) | `backend/defence_studio/research_worker.py` | BUNDLE (BE-30, F3/F4 handlers, F2 worker) | stdlib | module docstring | VERIFIED |
| BE-35 | Script request/response + revision/validation/approval contracts + offline request helper (extended into the full Phase 3 boundary below) | `backend/defence_studio/contracts/script.py`, `backend/defence_studio/services/script_contract.py` | COUPLED (BE-32, BE-33) | pydantic | `CONTRACTS.md` → Phase 3 script contract | VERIFIED |

## Backend — source-grounded script generation, review & approval (Phase 3)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-36 | OpenAI Chat-Completions provider behind a testable `client_factory` seam; env key; bounded retries; INR cost table | `backend/defence_studio/providers/openai_text.py` | STANDALONE (reuses F3's client-factory pattern) | httpx | `CONTRACTS.md` → Phase 3 script contract | VERIFIED |
| BE-37 | Deterministic, source-grounded prompt construction (approved-claims-only, terminology preservation) | `backend/defence_studio/services/script_prompt.py` | STANDALONE | stdlib | module docstring | VERIFIED |
| BE-38 | Deterministic evidence-grounded structural validation (citation-leak, unapproved/unknown claims, required sections) | `backend/defence_studio/services/script_validation.py` | STANDALONE | pydantic | `CONTRACTS.md` → Phase 3 script contract | VERIFIED |
| BE-39 | Script generation/revision/review/approval/export service | `backend/defence_studio/services/script.py` | COUPLED (BE-33, BE-36..38, schema v5) | httpx | `CONTRACTS.md` → Phase 3 script contract | VERIFIED |

## Backend — scene-plan contract (Phase 4, M0 — contract foundation only)

> M0 is a contract only: no service, worker stage, route, DB, or TTS/alignment/render
> integration. It fixes the versioned scene-plan schema that later Phase 4–7 milestones
> (M1+) implement independently. See `CONTRACTS.md` → Phase 4 scene-plan contract.

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-40 | Scene-plan request/output contract (ordered scenes, referenced-not-rewritten narration, approved-claim binding, planned-vs-measured timing, downstream asset/TTS/alignment/render reference slots, schema versioning) | `backend/defence_studio/contracts/scene.py` | STANDALONE (reuses BE-32's `StrictModel`/`digest`) | pydantic | `CONTRACTS.md` → Phase 4 scene-plan contract | VERIFIED (contract + 15 unit tests; no execution) |

## Backend — scene planning, persistence, editing & approval (Phase 4, M1)

> M1 generates/persists/edits/approves scene plans deterministically from an approved
> Phase 3 script export. No TTS, alignment, render, asset, or rights integration —
> those are later milestones (M2+). See `CONTRACTS.md` → Phase 4 M1 section.

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-41 | Scene planning/persistence/edit/approval service (deterministic generation from the approved script export, per-scene isolated edits, plan-hash-bound approval, script-change re-sync gate, schema v6) | `backend/defence_studio/services/scene.py` | COUPLED (BE-39, BE-40, schema v6) | pydantic | `CONTRACTS.md` → Phase 4 M1 section | VERIFIED |

## Backend — asset-management & rights-provenance contract (Phase 5, M2.0 — contract foundation only)

> M2.0 is a contract only: no service, worker stage, route, DB, byte storage, fetch,
> or asset/rights integration. It fixes the versioned asset-descriptor / rights-record /
> scene-assignment schema that later Phase 5 milestones (M2.1+) implement independently,
> and does not modify the M0 scene contract. See `CONTRACTS.md` → Phase 5 asset contract.

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-42 | Asset descriptor / rights record / scene-assignment contract + deterministic fail-closed rights-gate predicate (content-hash integrity with no fabricated digest, declared-vs-fabricated provenance, operator-asserted rights, ingestion≠approval, assignment bound to the exact approved scene-plan hash/version/scene_id, canonical digests) | `backend/defence_studio/contracts/assets.py` | STANDALONE (reuses BE-32's `StrictModel`/`digest`) | pydantic | `CONTRACTS.md` → Phase 5 asset contract | VERIFIED (contract + 20 unit tests; no execution) |

## Backend — asset-management & rights-provenance service (Phase 5, M2.1 — reference-only)

> M2.1 registers reference-only assets, re-derives rights deterministically, assigns to an
> approved scene plan (hash/version/scene_id-pinned), and records an auditable human rights
> decision. No byte download/fetch, no SSRF surface, no frontend. Schema v7 (additive).
> See `CONTRACTS.md` → Phase 5 asset service (M2.1).

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-43 | Asset service: reference-only registration/retrieval, immutable rights revisions + deterministic re-evaluation, approved-plan-bound scene assignment, digest-pinned human review + append-only audit, readiness blockers, schema v7 | `backend/defence_studio/services/assets.py` | COUPLED (BE-41, BE-42, schema v7) | pydantic | `CONTRACTS.md` → Phase 5 asset service (M2.1) | VERIFIED |

## Frontend — UI primitives (built once, reused everywhere)

> Built for the first time in F5, which is also Defence Studio's first UI.
> Framework-free (plain HTML/CSS/ES modules, no build step) so any future page
> imports these modules directly rather than re-implementing them.

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| UI-01 | Text field (single-line input / textarea, labelled, length-limited) | `frontend/js/components/text-field.js` | STANDALONE | none (vanilla JS) | module docstring | VERIFIED |
| UI-02 | Date / time picker (timezone-aware, future-date validation) | _tbd_ | STANDALONE (target) | none (vanilla JS) | folder `README.md` (on build) | PLANNED (not needed by F5; no date-entry UI in the review flow) |
| UI-03 | Status badge (decision/acquisition/normalization status, color-coded, no fake %) | `frontend/js/components/status-badge.js` | STANDALONE | none (vanilla JS) | module docstring | VERIFIED |
| UI-04 | Modal / confirm-dialog shell (keyboard + touch, Escape/backdrop cancel, focus on primary action) | `frontend/js/components/confirm-dialog.js` | STANDALONE | none (vanilla JS) | module docstring | VERIFIED |
| UI-05 | Toast / transient status message | `frontend/js/components/toast.js` | STANDALONE | none (vanilla JS) | module docstring | VERIFIED |
| UI-06 | Inline-SVG icon set (line icons, `currentColor`, no emoji) | `frontend/js/components/icons.js` | STANDALONE | none (vanilla JS) | module docstring | VERIFIED |

<!-- Add new UI primitives here (select, date picker, tag input, file drop, table, etc.) as single canonical copies. -->

## Frontend — F5 evidence review views (business-specific, not reusable primitives)

> Per the standing rule to keep business logic out of reusable primitives,
> these are registered for traceability but are NOT portable UI components —
> they encode F5's specific review semantics and data shapes.

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| F5-V1 | API client (fetch wrapper, bearer auth, Idempotency-Key) | `frontend/js/api.js` | COUPLED (Defence Studio API) | none (vanilla JS) | module docstring | VERIFIED |
| F5-V2 | Review decision panel + history (shared by claim/source detail) | `frontend/js/views/decision-panel.js` | COUPLED (F5 API shape) | UI-01, UI-04, UI-05 | module docstring | VERIFIED |
| F5-V3 | Sources / claims list + detail views and app shell/router | `frontend/js/views/sources-view.js`, `frontend/js/views/claims-view.js`, `frontend/js/app.js` | COUPLED (F5 API shape) | UI-03, F5-V1, F5-V2 | module docstrings | VERIFIED |

## Frontend — Phase 1A discovery view (business-specific, not a reusable primitive)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| F1A-V1 | Discovery request form, candidate list/ranking display, selection confirmation | `frontend/js/views/discovery-view.js` | COUPLED (Phase 1A API shape) | UI-01 | module docstring | VERIFIED |

## Frontend — Phase 2 evidence package view (business-specific, not a reusable primitive)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| P2-V1 | Evidence package view: readiness/blockers, timeline, approval + export (calls `/package/*` + `/timeline`) | `frontend/js/views/evidence-view.js` | COUPLED (Phase 2 API shape) | UI-04, F5-V1 | module docstring | VERIFIED (code; browser pending) |

## Frontend — Phase 3 script view (business-specific, not a reusable primitive)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| P3-V1 | Script view: generation controls, narration/evidence/validation viewer, revision history, review feedback, approval, export (calls `/script/*`) | `frontend/js/views/script-view.js` | COUPLED (Phase 3 API shape) | UI-01, UI-04, F5-V1 | module docstring | VERIFIED (code; browser pending) |

## Frontend — Phase 4 M1 scene view (business-specific, not a reusable primitive)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| P4-V1 | Scene plan view: generation controls (script-readiness gated), scene list/detail with draft/awaiting-review/changes-requested/approved/superseded state display, review feedback, approval (calls `/scene/*`) | `frontend/js/views/scene-view.js` | COUPLED (Phase 4 M1 API shape) | UI-01, UI-04, P3-V1 | module docstring | VERIFIED (code; browser pending) |

## Standalone external components (consolidated under the project root, not yet integrated)

> Each of these is an independently-versioned standalone project (own `pyproject.toml`,
> own test suite, own internal module layout) rather than a single reusable file/bundle —
> so this section registers it at the folder level and points to its own README/contracts
> rather than enumerating internal files. Neither is wired into core Defence Studio yet;
> see `project_status.md` for integration status and known gaps.

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| EXT-01 | Asset & Rights Engine (ARE) — asset registration, rights revisions, usage evaluation | `components/asset-rights-engine/` | COUPLED (own SQLite, needs a host adapter to wire into Defence Studio's asset model) | stdlib only (sqlite3) | `components/asset-rights-engine/README.md`, `CONTRACTS.md`, `docs/HOST_ADAPTER.md` | PARTIAL — standalone VERIFIED (140 passed, 116 subtests passed), not drop-in compatible with Defence Studio's own Phase 5 asset model (`docs/asset_rights_integration/COMPATIBILITY.md`), not integrated |
| EXT-02 | Alignment engine (align_engine) — forced-alignment of spoken text to audio (CTC/torchaudio) | `components/defence-align/` | COUPLED (FastAPI service, needs torch/torchaudio; MMS-FA model is CC-BY-NC 4.0, non-commercial) | fastapi, torch, torchaudio | `components/defence-align/README.md`, `INTEGRATION_CONTRACT.md` | PARTIAL — standalone VERIFIED (76 passed), real CTC adapter implemented but not run against a real downloaded model, Tamil/English accuracy unverified, not integrated with TTS or core Defence Studio |
| EXT-03 | Voice Analyzer + Voice Generation engines (delivery-style profiles; TTS adapter interface) | `components/voice-engine/` | BUNDLE (SQLite, ffmpeg; engines import no app code) | numpy, ffmpeg, Windows SAPI (optional) | `components/voice-engine/README.md`, `docs/voice_engine/INTEGRATION.md` | PARTIAL — standalone + integrated into core (schema v12, workflow_jobs, `/api/voice/*`, Voice tab) and TESTED; Tamil speech NOT verified (no Tamil provider), word alignment estimated, reference YouTube source not analyzed |
| FC-01 | Claim fact-check and media-context engines | `backend/defence_studio/verification/` | BUNDLE (service + schema v13) | stdlib; optional tesseract/ffmpeg | `backend/defence_studio/verification/README.md` | TESTED offline (11 engine/API tests). Reverse image search, OCR, and object detection are not live. |

## YouTube publishing (Phase 9 / YT spec) — not built yet

> Components specified in `DEFENCE_STUDIO_YOUTUBE_INTEGRATION_SPECIFICATION.md`
> (YT-01..YT-08). Register each here as it is built (OAuth service, publishing
> worker, caption/thumbnail uploaders, publishing settings UI, etc.).

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| YT-P1 | YouTube OAuth / channel-connection service | _tbd (YT-02)_ | COUPLED (target) | google-auth libs | YT spec §5–6 | PLANNED |
| YT-P2 | Publishing job state machine + worker | _tbd (YT-03)_ | BUNDLE (reuse BE-07) | — | YT spec §8 | PLANNED |
| YT-P3 | Resumable video uploader | _tbd (YT-05)_ | COUPLED (target) | YouTube Data API v3 | YT spec §7 | PLANNED |
| YT-P4 | Thumbnail + caption uploaders (independently retryable) | _tbd (YT-06)_ | COUPLED (target) | YouTube Data API v3 | YT spec §7 | PLANNED |
| SE-1 | Research monitor + evidence explorer (backend `services/research_monitor.py`, `api/monitor_routes.py`; UI `frontend/js/views/research-view.js`) | read-only over durable job state + policy run | BUNDLE | evidence_policy | this task | TESTED (backend 12, frontend 7) |
| SE-2 | Pipeline trace + OpenAI request/response inspector (backend `services/pipeline_trace.py`, `services/script_inspection.py`, `providers/openai_text.py` capture; UI `frontend/js/views/inspector-view.js`) | read-only diagnostics over stored artifacts; sanitized per-attempt records under `jobs/{id}/scripts/{lang}/inspection/` | BUNDLE | evidence_policy, script service | this task | TESTED (backend 10 new, FE 3 new, browser 25/25; mocked provider only, no live call) |
