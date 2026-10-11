# M2.1 — Asset Service (reference-only registration, rights evaluation, scene assignment, human review) — Implementation Plan

**Status:** PLAN ONLY. No code written, no migration run, no download/fetch/paid call, no commit.
**Date:** 2026-10-11 (Asia/Calcutta)
**Authorization scope of this task:** plan M2.1; do **not** implement it. M2.0 asset contracts (`contracts/assets.py`, BE-42) are accepted (242 passed / 2 skipped).
**Inspected this session (narrow):** `CLAUDE.md` §1A, `project_status.md`, Phase 4 production-foundation plan, `backend/CONTRACTS.md` (Phase 3/4 M1 + Phase 5 M2.0 sections), `component_registry/REGISTRY.md`, `contracts/assets.py`, and the existing service/persistence/API patterns in `services/review.py`, `services/script.py`, `services/scene.py`, `services/evidence.py`, `database.py` (`_upgrade_v5_to_v6`), `storage/artifacts.py`.

---

## 0. BLOCKING DECISION — M2.1 requires a schema change (needs explicit authorization)

Reference-only asset registration + an auditable rights-approval history **cannot be built within M2.1's quality bar without new persistence**, and the cleanest fit with existing conventions is a **schema v6→v7 additive migration**. This task forbids running a migration *now* and says to flag any schema change as a separate decision — so this is the one hard gate:

- **Why not pure content-addressed artifacts (no migration)?** Discovery/F4/scene outputs are content-addressed artifacts through the F1 artifact-reference mechanism, which needs *no* migration. Asset *descriptors* and *assignments* could ride that same mechanism. **But** the human **rights-approval decision history** is mutable-state-with-audit (a current decision per asset + an append-only trail), which every comparable phase (F5 `claim_reviews`/`source_reviews`, Phase 2 `evidence_packages` ledger, Phase 3 `script_review_history`, M1 `scene_plan_review_history`) implements as **SQLite tables**, not artifacts — because approval supersession, idempotent replay, and stale/conflict 409s need row-level transactional upserts that content-addressed JSON blobs can't give. Forcing approvals into artifacts would be a novel, weaker pattern.
- **Recommendation:** one additive `_upgrade_v6_to_v7` (the same shape as `_upgrade_v5_to_v6`: `BEGIN EXCLUSIVE`, `CREATE TABLE IF NOT EXISTS`, bump `schema_info` to 7, accept prior versions `{1..7}`), adding the tables in §5. **This is the separate decision to authorize before M2.1 coding starts.**
- **Fallback if a migration is refused:** a strictly reduced M2.1a — register/evaluate/assign assets as content-addressed artifacts and expose reads, but **defer human approval + history** (the ledger) to a later migration-authorized step. This still delivers registration, deterministic rights evaluation, and assignment binding; it does *not* deliver auditable approval. Call this out explicitly rather than shoehorning approvals into artifacts.

No other blocker found. The M2.0 contract is sufficient and has **no defect** that blocks M2.1 (one small ergonomic note in §8).

---

## 1. Architecture — reuse, not a new stack

`AssetService` **subclasses `SceneService`** (→ `ScriptService` → `EvidenceService` → `ReviewService`), inheriting, unchanged:
- `_check_ownership` / `_check_ownership_locked` (job/tenant isolation, 404-equivalent for wrong owner),
- `ArtifactStore` (`self.store`) + `_read_json_artifact` (content-addressed JSON read with integrity),
- `_validate_note` / `_validate_idempotency_key`,
- the approved-scene-plan accessor path (`SceneService.get_plan` / the `scene_plans WHERE status='APPROVED'` row), so M2.1 reads the exact approved plan, its `plan_sha256`, `version`, and scene IDs **without re-implementing or modifying M1**.

No second execution architecture; human-paced synchronous HTTP under `/api/jobs/{job_id}/assets/*` and `/scenes/{scene_id}/assets/*`, exactly like F5/Phase 2/3/M1. No worker stage in M2.1 (automated discovery is a later, separately-authorized milestone).

Error family: define `AssetError`/`AssetNotFound`/`AssetValidationError`/`AssetStaleRevision`/`AssetRevisionConflict` deriving from the `ReviewError` family (as `Scene*`/`Script*` do), so the **existing single `ReviewError` handler in `api/app.py` maps 404/422/409 with no new handler**.

---

## 2. Service operations (file: `services/assets.py`)

**Registration & retrieval (reference-only — no bytes fetched):**
- `register_asset(job_id, owner_id, descriptor_input, idempotency_key)` — validate against `AssetDescriptorV1`; compute `asset_id = asset_digest(descriptor_without_id)`; dedup idempotently (byte-identical descriptor + same key → replay existing row, no second row); persist `review_status=DRAFT`; store the canonical descriptor bytes via `store.write_bytes` under `jobs/<job_id>/assets/<asset_id>.json` and a `assets` row. Content stays `REFERENCED_ONLY` (M2.0 rule; no download).
- `set_rights(job_id, owner_id, asset_id, rights_input, idempotency_key)` — validate `RightsRecordV1`; compute `rights_digest`; attach to the asset as a new immutable rights revision bound to the asset; recompute and store the asset's `effective_rights` via `evaluate_effective_rights(...)`. Setting rights never approves.
- `get_asset` / `list_assets(job_id, owner_id, *, review_status?, effective_rights?, kind?)` — owner-scoped reads with filters; returns descriptor + current rights + current `effective_rights` + review state.

**Rights evaluation (deterministic, pure — reuse the M2.0 predicate):**
- All verdicts come from `contracts/assets.evaluate_effective_rights(rights, provenance, now_iso=_now(), for_commercial=<job/config>, target_use=<assignment use>)`. The service **re-derives** the verdict at register, set-rights, assign, approve, and read time — it never trusts a stored/claimed verdict (the "never trust, re-verify" rule). Expiry uses the service clock injected as `now_iso` so the pure predicate stays deterministic in tests.
- Explicit handling: `UNVERIFIED`/`EXPIRED`/`BLOCKED` assets **cannot be approved or assigned to an approved plan for a commercial render**; `RESTRICTED` requires a recorded reviewer override note to assign for the restricted use. Each refusal is a specific, human-readable reason (mirroring evidence readiness blockers).

**Scene assignment (bound to the exact approved plan):**
- `assign_asset(job_id, owner_id, language, scene_id, asset_id, target_use?, idempotency_key)` — inside `BEGIN IMMEDIATE`: read the **current APPROVED** scene plan for `(job_id, language)` via the inherited M1 path; refuse if none (`AssetStaleRevision`/409). Validate `scene_id` **exists in that approved plan** (else `AssetValidationError`/422). Build an `AssetAssignmentV1` pinned to that plan's exact `scene_plan_sha256` + `scene_plan_version` + `scene_id`, the `asset_id`, and the **re-derived** `effective_rights`. Refuse assignment when the re-derived verdict is non-`CLEAR` (except `RESTRICTED` with override). Content-address + persist as an `asset_assignments` row + artifact. Idempotent by `(job_id, language, scene_id, asset_id, idempotency_key)`.
- `remove_assignment(...)` / `list_assignments(job_id, owner_id, language)` — replace assets without rewriting the plan or script (core-design "replace assets without rewriting scripts"); removing/replacing never re-approves rights.
- **Stale-plan protection:** every assignment stores `scene_plan_sha256`+`version`; a read/export re-checks it against the *current* approved plan and flags `STALE_PLAN` if the plan was re-approved since (same stale-409 discipline as Phase 2/3/M1). Assignment does not auto-migrate to a new plan version.

**Human review & auditable history (needs the ledger — see §0/§5):**
- `review_asset(job_id, owner_id, asset_id, decision, reviewed_asset_sha256, reviewed_rights_sha256, expected_revision, note, idempotency_key)` — inside `BEGIN IMMEDIATE`, mirroring `ReviewService`: verify ownership; **pin the decision to the exact `asset_digest` and `rights_digest` the reviewer saw** (mismatch → `AssetStaleRevision`/409 — can't approve rights that changed under them); `expected_revision` optimistic-concurrency (mismatch → `AssetRevisionConflict`/409); duplicate `(asset, reviewer, idempotency_key)` → replay existing decision; write current-decision upsert **and** append-only history row **in the same transaction**. Decisions: `APPROVED`/`REJECTED`/`RESTRICTED`/`CHANGES_REQUESTED`. Approval is refused when the re-derived `effective_rights` is not `CLEAR`/`RESTRICTED` (an `UNVERIFIED`/`EXPIRED`/`BLOCKED` asset is never approvable). Ingestion is never approval (already enforced by the M2.0 contract: `APPROVED` descriptor requires a rights record).
- `asset_review_history(job_id, owner_id, asset_id)` — the append-only trail incl. reviewer, timestamp, decision, note, and the exact asset+rights digests reviewed.

**Export-readiness (read-only gate, no new approval):**
- `assignment_readiness(job_id, owner_id, language)` — per approved-plan scene, list blockers: unassigned material scene, assigned asset not `APPROVED`, re-derived `effective_rights` not `CLEAR` (or `RESTRICTED` without override), `STALE_PLAN`, missing attribution text where `attribution_required`. A specific reason per blocker; this is the structural precursor to the Phase 8 QC rights check, surfaced now so a future export can gate on it.

---

## 3. API (file: edits to `api/app.py` — routes only, no new handler)

All require a verified bearer token, owner-scoped, registered **before** the `StaticFiles` mount (the invariant every prior phase keeps):
- `POST /api/jobs/{job_id}/assets` (Idempotency-Key; body = descriptor input) → 201
- `GET /api/jobs/{job_id}/assets[?review_status=&effective_rights=&kind=]`
- `GET /api/jobs/{job_id}/assets/{asset_id}`
- `POST /api/jobs/{job_id}/assets/{asset_id}/rights` (Idempotency-Key) → 201
- `POST /api/jobs/{job_id}/assets/{asset_id}/review` (Idempotency-Key; body = decision+digests+expected_revision+note)
- `GET /api/jobs/{job_id}/assets/{asset_id}/history`
- `POST /api/jobs/{job_id}/scenes/{scene_id}/assets` (Idempotency-Key; body = {language, asset_id, target_use?}) → 201 (assign)
- `DELETE|POST .../scenes/{scene_id}/assets/{asset_id}/remove` (Idempotency-Key)
- `GET /api/jobs/{job_id}/assignments?language=` and `GET /api/jobs/{job_id}/assignments/readiness?language=`

Status codes via the inherited exception family: 401 unauth, 404 cross-owner/unknown, 422 validation, 409 stale/conflict.

---

## 4. Persistence — content-addressed artifacts vs. ledger (explicit separation per requirement 5)

| Data | Mechanism | Migration? |
|---|---|---|
| Asset descriptor (immutable, content-addressed) | artifact bytes under `jobs/<job_id>/assets/<asset_id>.json` **+** an `assets` index row | row needs a table (v7) |
| Rights record (immutable revisions per asset) | artifact bytes + `asset_rights` rows | v7 |
| Scene assignment (immutable, content-addressed, versioned) | artifact bytes + `asset_assignments` rows | v7 |
| **Rights-approval decisions (current + append-only audit)** | `asset_reviews` (current) + `asset_review_history` (append-only) **tables** | **v7 — the ledger** |

The immutable, content-addressed items (descriptor/rights/assignment JSON) are clearly separated from the **append-only approval ledger** (reviews/history), exactly as Phase 2 separates the evidence package artifacts from the `evidence_packages` approval ledger.

---

## 5. Proposed schema v6→v7 (additive; SEPARATE AUTHORIZATION REQUIRED — not run in this task)

`_upgrade_v6_to_v7` (same safety shape as `_upgrade_v5_to_v6`): `BEGIN EXCLUSIVE`; `CREATE TABLE IF NOT EXISTS`; bump to 7; `initialize_database` version guard widened to `{1,2,3,4,5,6,7}`; no F1–M1 table touched; DDL + version bump commit/rollback together.

- `assets(job_id, asset_id, kind, uri, sha256, review_status, effective_rights, created_by, created_at, idempotency_key, PRIMARY KEY(job_id, asset_id), UNIQUE(job_id, idempotency_key))`
- `asset_rights(job_id, asset_id, rights_sha256, uri, sha256, created_at, PRIMARY KEY(job_id, asset_id, rights_sha256))` — immutable rights revisions
- `asset_assignments(job_id, language, scene_id, asset_id, version, assignment_sha256, uri, scene_plan_sha256, scene_plan_version, effective_rights, target_use, status, created_by, created_at, idempotency_key, PRIMARY KEY(job_id, language, scene_id, asset_id, version), UNIQUE(job_id, language, idempotency_key))`
- `asset_reviews(job_id, asset_id, decision, reviewed_asset_sha256, reviewed_rights_sha256, reviewer_id, note, revision, updated_at, PRIMARY KEY(job_id, asset_id))` — current decision
- `asset_review_history(history_id PK, job_id, asset_id, decision, reviewed_asset_sha256, reviewed_rights_sha256, reviewer_id, note, occurred_at, idempotency_key, UNIQUE(job_id, asset_id, reviewer_id, idempotency_key))` — append-only

All `*_sha256` 64-hex; FKs to `workflow_jobs(job_id) ON DELETE CASCADE`.

---

## 6. Cross-cutting behavior (requirement 6)

- **AuthZ / tenant isolation:** every method calls `_check_ownership`; a wrong-owner/unknown job is `AssetNotFound`/404 (no existence leak), identical to F5/M1.
- **Validation:** all inputs parsed through the M2.0 pydantic models (`extra=forbid`, strict); a contract failure surfaces as `AssetValidationError`/422 (the `_as_validation_error` pattern M1 uses).
- **Idempotency:** `(job_id, idempotency_key)` for register/rights/assign; `(asset, reviewer, idempotency_key)` for review — replay returns the existing row, never a duplicate (the F5/M1 rule).
- **Concurrency:** all writes in `BEGIN IMMEDIATE`; approval/assignment take the ownership re-check and the stale/conflict checks **inside** the transaction; `expected_revision` on review gives optimistic concurrency.
- **Recovery:** no worker in M2.1, so no lease/fencing needed; all state is a single transactional SQLite write + an atomic artifact write (BE-09 temp-file+rename), so a crash mid-op leaves either a complete row+artifact or neither — a retry with the same key replays cleanly.
- **Safety:** reference-only; **no fetch, no byte ingestion, no SSRF surface** in M2.1 (the guarded-fetch path is a later milestone). The service writes only JSON descriptors it was handed, through BE-09's path containment.

---

## 7. Test plan (requirement 7)

**Focused (`tests/test_asset_service_m2_1.py`):**
- Register: valid descriptor persists DRAFT + referenced-only; idempotent replay (no second row); cross-owner 404; dedup of byte-identical descriptor.
- Rights: set-rights recomputes `effective_rights`; unknown basis/commercial-use/missing-provenance → `UNVERIFIED`; `cc_by_nc`/prohibited → `BLOCKED`; past expiry → `EXPIRED`; target-use outside permitted → `RESTRICTED`; fully-specified → `CLEAR` (deterministic via injected `now_iso`).
- Assignment: binds to the exact current approved `scene_plan_sha256`+version+scene_id; unknown scene_id → 422; no approved plan → 409; assigning a non-`CLEAR` asset refused (with the `RESTRICTED`-override path tested); idempotent replay; `STALE_PLAN` flagged after the plan is re-approved; replacing an asset does not touch the plan/script.
- Review: approval pinned to exact asset+rights digests (stale digest → 409); `expected_revision` conflict → 409; idempotent duplicate replay; approve refused for `UNVERIFIED`/`EXPIRED`/`BLOCKED`; ingestion-not-approval; append-only history records reviewer/time/decision/note/digests.
- Readiness: a deliberately unassigned/unapproved/blocked scene yields the correct specific blocker; a fully-approved-plan-with-CLEAR-assets passes.
- API via `TestClient`: 401/404/422/409 on the routes; static mount not shadowed.

**Regression:** full backend suite once at the gate; must stay **≥ 242 passed / 2 skipped** with no prior test modified or weakened. M0/M1/M2.0 suites unchanged.

**Measurable acceptance criteria:**
1. A registered asset is `DRAFT`, `REFERENCED_ONLY`, never auto-approved.
2. `effective_rights` is always the re-derived verdict; a stored/claimed verdict is never trusted.
3. An assignment carries the exact current approved plan hash/version/scene_id; a non-existent scene or non-`CLEAR` asset is refused with a specific reason.
4. Approval binds to the exact asset+rights digests and 409s on any later change; every decision has an append-only audit row.
5. `UNVERIFIED`/`EXPIRED`/`BLOCKED` assets can never appear approved or in a passing readiness check.
6. No M0/M1/M2.0 contract or test changed; full suite green.

---

## 8. Prerequisites / contract observations

- **Prerequisite (hard):** §0/§5 schema v7 authorization. Without it, only the reduced M2.1a (no approval ledger) is buildable.
- **Prerequisite (soft):** a job/config signal for `for_commercial` (the rights gate's commercial flag). Recommend a per-job boolean read from existing job config, defaulting to `True` (commercial) so the gate fails *safe*. No contract change needed.
- **M2.0 contract observation (NOT a blocker, no change proposed now):** `AssetDescriptorV1.asset_id` is a required 64-hex field, but its intended value is `asset_digest` of the descriptor *without* the id — a chicken-and-egg the **service** resolves (compute digest over the id-less body, then set `asset_id`). This is a service responsibility, not a contract defect; flagging so the implementer computes the id correctly rather than hashing a body that already contains it. If a future cleanup wants the contract to model "id-less input vs. id-bearing stored" as two types (like scene's request/plan split), that is a separate, optional M2.0 revision — **not** required for M2.1.

---

## 9. File-level implementation scope (when authorized)

1. `database.py` — add `_upgrade_v6_to_v7` + widen the version guard (the schema decision).
2. `backend/defence_studio/services/assets.py` — **new** `AssetService` (subclass `SceneService`) + the `Asset*` exception family.
3. `backend/defence_studio/api/app.py` — the routes in §3 (no new exception handler).
4. `backend/tests/test_asset_service_m2_1.py` — **new** focused suite (§7).
5. Docs: `CONTRACTS.md` Phase 5 M2.1 section; `REGISTRY.md` BE-43 (service) + schema-v7 note; `project_status.md` M2.1 status + next gate.

**First coding task (once v7 is authorized):** `database.py::_upgrade_v6_to_v7` (additive migration + version-guard widening) **with its migration test** (populated v6 DB upgrades to v7, data preserved, unsupported version fails closed) — the smallest unit everything else depends on, and the one requiring the explicit schema decision. Everything in steps 2–5 builds on it.

**Explicitly NOT in M2.1:** automated asset discovery/worker stage, any byte download/fetch/SSRF path, `scene-plan.v2`, TTS/alignment/render/QC/publish, M0/M1 contract edits. Those remain unauthorized.

---

## Stop

This is a plan. No code, migration, fetch, or commit was performed. **The schema v6→v7 decision (§0/§5) is the gate to authorize before M2.1 coding begins.** Awaiting approval.
