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

## Backend — evidence review & approval (F5)

| ID | Component | Canonical path | Portability | External deps | README / contract | Status |
|---|---|---|---|---|---|---|
| BE-23 | Review decision/subject/history contracts | `backend/defence_studio/contracts/review.py` | STANDALONE | stdlib | `CONTRACTS.md` → F5 evidence review contract | VERIFIED |
| BE-24 | Review/history SQL repository | `backend/defence_studio/repositories/review.py` | COUPLED (schema v3) | stdlib sqlite3 | `CONTRACTS.md` → F5 evidence review contract | VERIFIED |
| BE-25 | Review decision service (stale/concurrency/idempotency, read-models) | `backend/defence_studio/services/review.py` | COUPLED (BE-23, BE-24, F3/F4 artifacts) | stdlib | `CONTRACTS.md` → F5 evidence review contract | VERIFIED |

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
