# M2 — Asset Management & Rights Provenance — Implementation Plan

**Status:** PLAN ONLY. No code written, no asset downloaded, no paid service called, no schema changed, no contract modified, no commit made.
**Date:** 2026-10-11 (Asia/Calcutta)
**Scope read this session:** `CLAUDE.md` §1A, `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` §§8 (Phase 4/5) + §3 locked order, `project_status.md`, `component_registry/REGISTRY.md`, `backend/defence_studio/contracts/scene.py` (M0), `contracts/jobs.py` (`JOB_STAGES`), `storage/artifacts.py` (BE-09), `backend/CONTRACTS.md` Phase 3/4 sections, the Phase 4 production-foundation plan.

---

## 0. DEPENDENCY GATE — READ FIRST (blocker found)

The task says: *"Assume M1 is complete only if the actual project status and implementation confirm it. If M1 is incomplete, stop and report the dependency."*

**M1 is NOT complete. This is a hard dependency blocker.** Verified this session:

| M-level (Phase 4 production plan numbering) | What it is | Real state |
|---|---|---|
| **M0** — scene-plan *contract* | `backend/defence_studio/contracts/scene.py` (BE-40) | **DONE.** Contract + 15 unit tests. No service, no route, no DB, no worker stage. |
| **M1** — scene *service* + worker stage | `services/scene.py`: generate/edit/reorder/version/approve scene plans | **NOT STARTED.** No `services/scene.py` exists (`Glob services/scene*.py` → none). No `scene_creation_and_voice_direction` stage handler is registered. No scene-plan persistence or routes. No frontend scene view. |
| **M2 / this task** — asset management & rights provenance | the stage `asset_rights_review_and_assignment` (core design Phase 5) | Blocked on M1. |

**Why M1 blocks M2 (not a formality):**

1. **Nothing to attach assets to.** M2 assigns assets *per scene* (core design §8 Phase 5: "Find assets relevant to each topic/scene"). The scene plan is the parent object an asset binds to. M0 defines the scene-plan *shape* but no service persists, versions, or approves a scene plan. Until a real `scene_plan` artifact with real `scene_id`s exists for a job, an asset assignment has no stable, approved anchor — it would bind to IDs that no persisted/approved object yet guarantees.
2. **The hash-binding pattern M2 must reuse is defined by M1.** Every prior phase (F5/Phase 2/Phase 3) binds approval to the exact upstream revision hash with a stale-revision 409. M2's asset assignments must bind to the exact approved *scene-plan* hash the same way. That binding target (the versioned, approvable scene plan + its `plan_digest`) is produced by M1, not M0.
3. **The core design locks the order** (§3; `JOB_STAGES` index 6 `scene_creation_and_voice_direction` precedes index 7 `asset_rights_review_and_assignment`). Building M2 before M1 inverts the locked pipeline.

**Additionally, Phase 4 execution is NOT authorized.** `project_status.md` "Next task" states plainly: *"Phase 4 (voice/scene/render/publish) is NOT authorized — do not begin TTS, voice generation, scene creation, rendering, or publishing."* M1 (scene creation) is itself inside that non-authorized band, and M2 is one step further.

**Required action before any M2 coding:**
- (a) User authorizes Phase 4, **and**
- (b) M1 (scene service) is implemented and accepted, **then**
- (c) this M2 plan's first coding task becomes eligible.

The remainder of this document is the implementation-ready M2 plan so it is on the shelf the moment (a)+(b) land. **It does not authorize starting now.**

---

## 1. What already exists and will be reused (not re-derived)

| Piece | Location | Reuse in M2 |
|---|---|---|
| Path-safe per-job byte store (atomic write, SHA-256, containment) | `storage/artifacts.py` (BE-09) | Store any locally-held asset bytes under `jobs/<job_id>/assets/` with the same containment + atomic-write + content-hash guarantees. No new storage mechanism. |
| SSRF URL guard + guarded fetcher | `acquisition/url_guard.py` (BE-10), `http_client.py` (BE-11) | **Any** fetch of an asset or its metadata goes through `GuardedFetcher`/`UrlGuard` (loopback/private/link-local/redirect re-validation). No new network surface. Same pattern F3/discovery already use. |
| Declared-vs-unavailable metadata status | `contracts/acquisition.py` `MetadataStatus` (BE-12) | Reuse the `COLLECTED`/`UNAVAILABLE`/`NOT_COLLECTED` vocabulary for asset metadata fields (creator, publisher, licence) — nothing fabricated. |
| Strict model + canonical digest | `contracts/evidence.py` `StrictModel`, `digest()` | The M2 asset contracts subclass `StrictModel` and bind approval to `digest()` — identical to scene/script/evidence contracts. |
| Durable F2 worker (claims/leases/retry/recovery) | `services/worker.py` (BE-07) | If M2 does any automated asset *discovery*, it runs as the retry-safe handler for the already-locked `asset_rights_review_and_assignment` stage — no second execution architecture. Human rights-review stays synchronous (like F5/Phase 2/3). |
| Human-review-with-hash-binding pattern | `services/review.py`, `services/evidence.py`, `services/script.py` | M2's rights review reuses: pin decision to exact revision hash, `StaleRevision` 409, `expected_revision` optimistic-concurrency 409, idempotent `(subject, reviewer, Idempotency-Key)` replay, non-blank note required. |
| Scene-plan contract (M0) | `contracts/scene.py` (BE-40) | M2 binds asset assignments to a `scene_id` within an **approved** scene plan and to that plan's `script_revision_sha256`/`package_sha256` upstream identity. M0 already reserves `VisualSuggestionV1` (explicitly "Not an asset assignment"), so M2 is its intended successor. |

**Conclusion:** M2 adds asset contracts + an asset/rights service + (optionally) one worker handler + routes + a frontend view. It introduces **no** new storage, SSRF, worker, or approval mechanism — all are reused.

---

## 2. The nine required design elements

### 2.1 Asset identity, metadata, content-hash integrity
- **Identity:** each asset gets a stable `asset_id` = content hash of its canonical descriptor (same content-addressed pattern as evidence/script/scene artifacts), so a byte-identical asset re-registered is idempotent (reuses the row), never duplicated.
- **Metadata** (all declared-vs-unavailable, never fabricated — reuse `MetadataStatus`): `title`, `creator`/`publisher`, `media_kind` (`image`/`footage`/`audio`/`graphic`/`music`), `dimensions`/`duration` where applicable, `retrieved_at` (UTC), `mime_type`.
- **Content-hash integrity:** if bytes are held locally, `content_sha256` is computed over exactly what landed on disk via BE-09 and re-verified on every read (the F4 "never trust, re-verify" rule). If the asset is referenced-only (not downloaded — the M2 default, since the task forbids downloads), the descriptor records `content_status: NOT_COLLECTED` with the source URL and declared hash (if the provider supplied one), never a fabricated hash.

### 2.2 Source URL & provenance tracking
- `source_url` (the canonical page/API the asset came from, normalized via F3's `normalize_url`), `source_provider` (how it was found/obtained), `retrieved_at`, and a provenance chain: who selected it, when, against which scene/plan revision. All SSRF-guarded on any fetch.
- No URL is ever invented. A missing source URL is an explicit `UNAVAILABLE`, which forces a rights state of `UNVERIFIED` (see 2.6).

### 2.3 Licence, attribution, permitted-use & commercial-use restrictions
Per core design §8 Phase 5 + §Rights. A dedicated `RightsRecordV1`:
- `rights_basis`: enumerated — `creator_owned` / `licensed` / `public_domain` / `cc_by` / `cc_by_sa` / `cc_by_nc` / `official_release_permitted` / `fair_use_claimed` / `unknown`.
- `licence_id`/`licence_url`, `attribution_required` (bool) + `attribution_text` (verbatim, stored for render/export use), `permitted_uses` (set: `broadcast`/`web`/`derivative`/`thumbnail`), `commercial_use`: `allowed`/`prohibited`/`unknown`.
- **Hard rule:** `commercial_use: prohibited` or `unknown` → the asset **cannot** be assigned to a scene in an approved plan destined for a commercial render; it is flagged/blocked per the configured policy (2.6). (Directly mirrors the CC-BY-NC alignment-model blocker already documented in the Phase 4 readiness note — the project already treats NC as production-blocking.)
- Nothing here is fabricated: an unknown licence is recorded as `unknown`, never guessed.

### 2.4 Human review & approval requirements
- Asset rights review is **human-paced and synchronous** (like F5/Phase 2/3), not a worker step. A reviewer sets an explicit `rights_review_status`: `PENDING` → (`APPROVED` | `REJECTED` | `CHANGES_REQUESTED` | `BLOCKED_RIGHTS`).
- Approval of an asset-to-scene assignment binds to **(scene-plan hash, asset descriptor hash, rights-record hash)** and requires a non-blank note. Same `StaleRevision`/`RevisionConflict` 409s and idempotent replay as prior phases.
- **No implicit approval:** generation/discovery/assignment never approves rights. Export is blocked unless every assigned asset's rights are `APPROVED` (2.9 acceptance).

### 2.5 Linking scene plans ↔ assigned assets **without modifying the M0 contract unnecessarily**
- **Preferred (no M0 change):** store assignments in a **separate** `asset_assignment` artifact/ledger keyed by `(job_id, language, scene_plan_sha256, scene_id, asset_id)`. M0 already reserves per-scene downstream *reference slots* (`audio_ref`/`alignment_ref`/`render_ref` as `ArtifactRefV1`) but deliberately has **no asset slot** and states `VisualSuggestionV1` is "Not an asset assignment." Keeping assignments external preserves M0 byte-for-byte and keeps scene-plan versioning independent of asset churn (core design: "Allow replacing assets without rewriting scripts").
- **If a later decision wants an in-plan slot:** that is a *separate, additive* `scene-plan.v2` migration (new `asset_refs: list[ArtifactRefV1]` slot), explicitly out of M2 scope and requiring its own approval. M2 does not touch `scene.py`.
- The assignment artifact re-verifies the referenced scene plan's hash and that `scene_id` exists in it before accepting — an assignment can never point at a scene the approved plan does not contain (same containment rule M0 enforces for claim IDs).

### 2.6 Missing / expired / restricted / unverified rights handling
A deterministic, configurable **rights gate** (policy via env/config, like the discovery feed config):
- `UNVERIFIED` (no rights record, or `rights_basis: unknown`, or missing source URL) → **warn + block from approved export** by default.
- `RESTRICTED` (`commercial_use: prohibited`, or `permitted_uses` missing the target use) → **block** from a commercial render; may be allowed only if policy explicitly permits and a reviewer overrides with a recorded justification note.
- `EXPIRED` (`licence_expires_at` in the past vs. a trusted clock) → treated as `UNVERIFIED`/blocked; expiry is re-evaluated at assignment time and at export time (never cached as "once valid, always valid").
- `MISSING` (asset referenced but no descriptor/bytes resolvable) → blocks the scene from completing; surfaced as a specific blocker, not a silent skip.
- Every block carries a specific, human-readable reason (same pattern as the evidence readiness blockers).

### 2.7 Secure asset storage & safe file handling
- All locally-held bytes go through **BE-09** under `jobs/<job_id>/assets/` — path containment, atomic temp-file + `os.replace`, length-capped filenames, content-addressed uniqueness. No path is derived unchecked from an external filename/URL.
- **MIME/type safety:** validate declared `mime_type` against an allow-list per `media_kind`; never execute, never trust an extension lifted from a URL. Cap file size (reuse BE-11's size/time caps for any fetch).
- **SSRF:** every metadata/asset fetch via BE-10/BE-11. No raw `httpx`.
- Since the task forbids downloads, M2's first increment is **reference + rights metadata only**; the byte-handling path above is specified now but exercised only once downloads are separately authorized (tracked as an explicit sub-blocker, not silently assumed — mirrors how the TTS/eSpeak-NG real-synthesis gap is tracked).

### 2.8 API, persistence & worker integration points (consistent with existing architecture)
- **Persistence:** prefer the **content-addressed artifact** mechanism (F1 artifact references + BE-09) for asset descriptors and assignments — the same approach discovery/F4/scene use, requiring **no schema change** (the task forbids schema changes). A durable append-only approval ledger, *if* needed for rights approvals, would mirror the Phase 2 `evidence_packages` ledger — but that is a **schema change and is therefore out of M2's first increment**; the first increment records approvals as versioned artifacts, exactly as Phase 1A selection does. Flag the ledger-vs-artifact decision as an open design choice for the user.
- **API (new synchronous routes under the existing `/api/jobs/{job_id}/...` tree):** `POST .../assets` (register descriptor), `GET .../assets` (list/filter by rights status), `POST .../assets/{asset_id}/rights` (submit rights record), `POST .../scenes/{scene_id}/assets/{asset_id}` (assign, hash-bound), `POST .../assets/{asset_id}/review` (approve/reject/request-changes, note-required), `GET .../assets/export-readiness` (blockers). Registered **before** the `StaticFiles` mount, like every prior route.
- **Worker:** automated *discovery* of candidate assets (if built) is the retry-safe handler for the locked `asset_rights_review_and_assignment` stage, reusing BE-07 + the `retry_safe_stages` exact-match opt-in. Discovery never sets rights to verified/approved — only a human reviewer does.
- **Service:** `services/assets.py` subclasses/reuses the existing review-service machinery (ownership checks, `ArtifactStore`, hash re-verification) the same way `ScriptService` subclasses `EvidenceService`.

### 2.9 Focused tests, regression tests & explicit acceptance criteria
**Focused (new) tests — `tests/test_assets_m2.py`:**
- Asset descriptor validation: rejects fabricated/guessed licence; `unknown` rights basis forces `UNVERIFIED`; missing source URL is `UNAVAILABLE` not blank.
- Content integrity: a mismatched/corrupted asset hash is rejected, not trusted (F4 pattern); referenced-only asset records `NOT_COLLECTED`, never a fake hash.
- Rights gate determinism: `commercial_use: prohibited` and `unknown` both block a commercial-destined assigned asset; expired licence re-evaluated at export time; each block has a specific reason.
- Assignment containment: assigning to a `scene_id` not in the approved plan is rejected; assignment binds to the exact scene-plan hash and 409s on a stale plan.
- Approval: no implicit approval (assign/discover never approves); blank note rejected; idempotent `(asset, reviewer, Idempotency-Key)` replay; cross-owner `AssetNotFound` (404).
- SSRF reuse: an asset/metadata URL resolving to a blocked address is rejected (no new SSRF surface).
- Worker (if discovery built): missing/corrupt request fails closed; simulated lease-expiry retry reuses stored descriptors without re-fetching.
- **M0 untouched:** a test asserting `contracts/scene.py` SHA-256 is unchanged by M2.

**Regression:** full backend suite run once at the review gate; must remain **≥ current 193 passed / 2 skipped** with no prior test modified or weakened.

**Explicit acceptance criteria** (from core design §8 Phase 5 "Acceptance: every assigned asset has traceable provenance and visible rights/review status"):
1. Every assigned asset has a resolvable `source_url` (or an explicit `UNAVAILABLE`), a `rights_basis`, and an explicit `rights_review_status` — none fabricated.
2. An asset with `commercial_use ∈ {prohibited, unknown}` or `UNVERIFIED`/`EXPIRED` rights cannot appear in an approved export; export-readiness lists it as a specific blocker.
3. Replacing an asset on a scene does **not** rewrite the script or the scene narration (core design "replace assets without rewriting scripts"), and does not re-approve rights.
4. Approval binds to the exact (scene-plan, asset, rights-record) hashes; a later change supersedes the approval (stale 409).
5. No M0 contract change; `contracts/scene.py` hash unchanged.
6. No schema change, no download, no paid-service call in the test suite.

---

## 3. Milestone breakdown (ordered, with prerequisites)

Prerequisite for **all** of M2: Phase 4 authorized **and** M1 (scene service) accepted. Then:

- **M2.0 — Asset + rights contracts only** (no service/route/DB). `contracts/assets.py`: `AssetDescriptorV1`, `RightsRecordV1`, `AssetAssignmentV1`, status vocabularies; reuse `StrictModel`/`digest`/`MetadataStatus`. *Acceptance:* validates a hand-built descriptor/assignment derived from a real approved scene plan; rejects fabricated licence, out-of-plan `scene_id`, duplicate assignment. No network/DB. **← first coding task.**
- **M2.1 — Asset/rights service + review (synchronous, artifact-persisted).** `services/assets.py`: register descriptor, submit rights record, assign to scene (hash-bound), human review/approval, export-readiness gate. Reuses review-service machinery; no schema change. *Acceptance:* the 2.9 focused suite (minus worker/download items) passes.
- **M2.2 — API routes + frontend asset/rights view.** New `/api/jobs/{job_id}/assets/*` + `/scenes/{id}/assets/*` routes; a frontend "Assets" tab reusing existing UI primitives (UI-01/03/04/05/06), registered in `REGISTRY.md` as business-specific. *Acceptance:* live HTTP smoke (auth, empty, assign, block-on-unverified, cross-owner 404); `node --check`/frontend tests pass.
- **M2.3 (separately authorized) — automated asset *discovery* worker + byte download.** Only if the user authorizes downloads/providers. Retry-safe handler for `asset_rights_review_and_assignment`; SSRF-guarded fetch; BE-09 byte storage. *Acceptance:* round trip with a configured provider; corrupted hash rejected; discovery never sets rights verified.

Open decisions for the user (none resolved here): (i) artifact-persisted approvals vs. a durable `asset_rights` ledger (the latter = a schema change, deferred); (ii) whether to ever add an in-plan `asset_refs` slot (`scene-plan.v2`, deferred); (iii) when to authorize actual asset downloads/providers (M2.3).

---

## 4. First authorized coding task (once the dependency gate clears)

**M2.0 — `backend/defence_studio/contracts/assets.py` + `CONTRACTS.md`/`REGISTRY.md` Phase 5 asset section.**
The smallest fully self-contained unit: asset descriptor / rights record / assignment contracts with their status vocabularies and the deterministic rights-gate predicate, reusing `StrictModel`/`digest`/`MetadataStatus`. No service, route, DB, worker, network, download, or paid call. It fixes the schema M2.1+ build against and touches nothing in M0.

**Dependencies:** Phase 4 authorization + accepted M1 scene service (both currently absent). Until both land, **do not start** — this plan is shelved, not active.

**Explicitly NOT started by this plan:** no asset/rights code, no contract change, no schema change, no download, no provider/paid-service call, no M0 edit, no commit. Phases 0–3 behavior and the standalone TTS/alignment components are untouched; QVault and Whisper Studio are not referenced.
