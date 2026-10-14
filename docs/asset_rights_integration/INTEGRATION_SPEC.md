# Phase 5C Workstream D — ARE 1.1.0 integration architecture proposal

This is a specification, not an implementation. No adapter code, route, or dependency
was added by this phase.

## Starting constraints (from Workstream A/C)

- ARE 1.1.0's core `Engine` has **no tenant isolation**; its own documentation requires
  "dedicated engines/storage per scope" for production use.
- Defence Studio's existing isolation is row-level in one shared SQLite file, enforced by
  `_check_ownership`/`_check_ownership_locked` on every call.
- ARE's host-adapter layer (`adapter_contracts.py`/`host_adapter.py`) is explicitly a
  "dependency-injected trusted execution boundary, not a security sandbox" — it validates
  shape and consistency, but a malicious or buggy host can still lie to it, and "direct
  Engine calls remain available for backward compatibility."
- Defence Studio already owns real authentication (`auth.py`) and already enforces
  ownership before any asset-service call. ARE provides neither and does not claim to.
- No DB migration is authorized this phase; the two systems' SQLite files remain separate
  artifacts regardless of which option is chosen.

## Option A — Integrate the ARE library into the trusted Defence Studio backend

The authenticated FastAPI backend imports `asset_rights_engine` as a Python dependency and
calls `Engine`/`AdapterGateway` methods directly inside `AssetService` (or a new service
that wraps it), in-process, no network hop.

## Option B — Run ARE as a separately isolated local service

ARE runs as its own local process (e.g. a small FastAPI/gRPC wrapper around `Engine`) with
its own SQLite file(s); Defence Studio's backend talks to it over a narrowly scoped,
authenticated local interface (e.g. loopback-only HTTP with a service-to-service token).

## Comparison

| Dimension | Option A (in-process library) | Option B (isolated local service) |
|---|---|---|
| **Authentication/authorization** | Reuses Defence Studio's existing `authenticated_user`/`_check_ownership` path unchanged — the caller is already authenticated and owner-checked before any `Engine` call is made. No new auth surface. | Requires a **second** authentication boundary between the DS backend and the ARE service, even though both run on the same trusted host — ARE-A2's own docs ("Do not expose the raw Engine... a production host must enforce access control independently") apply to *this* boundary specifically. Adds a service-to-service credential to provision, rotate, and audit for no corresponding gain, since both sides are already trusted. |
| **Preventing direct untrusted engine access** | `Engine` is never imported outside `backend/defence_studio/services/`; the only way to reach it is through the already-ownership-checked `AssetService` call path (see `SECURITY_REVIEW.md` §8's conclusion). Enforceable by code review / import-linting (`AssetService`-equivalent is the sole importer). | Requires the ARE service's own listener to be loopback-only and to reject any request lacking the service-to-service credential — a second place this guarantee must be independently verified, with its own failure mode if misconfigured (e.g. bound to `0.0.0.0` instead of `127.0.0.1`). |
| **SQLite storage ownership** | ARE's SQLite file(s) can live under the Defence Studio project root (e.g. `data/are/<job_id>/asset_rights.sqlite3`), owned and backed up alongside `data/defence_studio.sqlite3`, using one `Engine(data_directory=...)` instance per job (matching ARE's "dedicated engine per scope" requirement) via a lazily-created, cached `Engine` per `job_id`. | ARE's service process owns its own storage path independently; Defence Studio never touches those files directly, which is a cleaner boundary but means restore/backup must coordinate two independently-owned storage trees instead of one. |
| **Tenant isolation** | Satisfied by instantiating one `Engine` per Defence Studio `job_id` (not shared across jobs), directly addressing ARE's documented "dedicated engines/storage per scope" prerequisite, with the instantiation itself gated behind the same `_check_ownership` call already made for every other asset-service method. | Same per-job-`Engine` requirement still applies *inside* the ARE service process — Option B does not remove this requirement, it only relocates where the per-scope `Engine` instances live. Additionally, the service must independently re-derive "which job is this request for" from whatever the DS backend tells it, which is one more place the tenant key could be spoofed or dropped if the service-to-service protocol is designed loosely. |
| **Transaction boundaries** | A single Python call stack: DS's `BEGIN IMMEDIATE` (for DS-owned tables) and ARE's own internal transaction (for ARE-owned tables) are **still two separate SQLite connections/files** — there is no distributed transaction either way, so Option A does not actually get "one atomic commit" for free. A write split across DS tables and ARE tables can still partially fail (DS commits, ARE's call then throws) and vice versa. This must be handled by compensating logic or by deciding one side is authoritative and the other is best-effort/derived, not by assuming in-process calls are transactional together. | Identical problem, with an added network hop that can fail independently of either database (timeout, process not running), which is a third failure mode beyond "DS DB fails" / "ARE DB fails" that Option A does not have. |
| **Concurrency and idempotency** | DS's existing idempotency-key/optimistic-concurrency pattern (`expected_revision`, `BEGIN IMMEDIATE`) and ARE's own (`expected_revision`, internal locking) both apply, but independently — a request retried at the DS layer could succeed at DS but fail (or double-apply) at ARE if the retry isn't itself idempotent all the way through. This requires an explicit end-to-end idempotency design regardless of option, not something either side provides for free across the boundary. | Same requirement, plus retry/timeout semantics over the local network call must be designed (what does the DS backend do if the ARE service call times out after it may have already committed?). |
| **Host/component failure recovery** | If the DS process crashes mid-operation, both DB writes are simply incomplete (no partial side effects persist beyond what SQLite itself guarantees per-file) — recovery is "restart the process, retry the idempotent call," same as every other DS service today. | An additional failure mode exists: the ARE service process itself can crash or hang independently of the DS backend process, requiring its own supervision/restart policy, health check, and a defined behavior for "ARE service unreachable" (fail closed vs. degrade) that Option A does not need. |
| **Packaging and upgrades** | ARE ships as a wheel; upgrading means bumping a pinned dependency version in Defence Studio's own environment and running its test suite — the same mechanism already used for every other third-party dependency. Version skew between "what DS expects" and "what's installed" is caught at import/test time, in-process. | Requires deploying and versioning a second running process, with its own release/rollback procedure, and a defined compatibility contract between "DS backend version N" and "ARE service version M" that doesn't exist today — meaningfully more operational surface for a single-host, single-operator deployment. |
| **Audit provenance** | Both DS's existing append-only history tables and (if adopted) ARE's `get_review_history()`/`ReviewEvent` log live under one process's identity; correlating "who did what, when" across both is a matter of joining two SQLite files on disk, no network log correlation needed. | Audit trail is split across two processes; correlating DS's HTTP access log with ARE's service-call log (to answer "which authenticated DS request caused this ARE-side history entry") requires a shared request ID threaded through the service boundary — an explicit design item, not automatic. |
| **Backup and restore** | One additional SQLite file (or one per job) sits alongside `data/defence_studio.sqlite3` under the same project root; existing backup tooling that already covers `data/` is extended, not duplicated. | A second process's storage location must be independently included in backup scope — easy to simply forget, since it is not under the path anyone is already backing up by habit. |
| **Operational complexity** | Lower: one process, one deployment unit, one Python environment, consistent with every other Defence Studio service (F1-M2.1 are all in-process Python modules inside the same FastAPI app). | Higher: a second process to start, monitor, and keep in sync with the main backend's lifecycle (dev loopback bypass conventions, `DEFENCE_STUDIO_AUTH_SECRET` rotation, etc. would all need an ARE-service-side equivalent). |
| **Compatibility with current FastAPI/SQLite architecture** | Directly consistent — every other phase (F1-M2.1) is an in-process Python service module calling SQLite directly; ARE as a library fits the exact same pattern used by `JobService`, `ReviewService`, `EvidenceService`, `ScriptService`, `SceneService`. | A genuine architectural departure — this would be the first networked inter-process component in Defence Studio's backend, introducing a class of failure (service unavailable) and a class of security surface (the inter-service channel) that nothing else in the codebase currently has to handle. |

## Recommendation

**Option A (in-process library integration), with ARE's `Engine` instantiated once per
Defence Studio `job_id` and reached only through a new `AssetRightsAdapter` module that
sits where `AssetService` is today** — not by replacing `AssetService` wholesale, and not
by letting any route or other module import `asset_rights_engine` directly.

Rationale: Defence Studio's entire backend to date is a set of in-process Python service
modules sharing one FastAPI app and one trust boundary (`require_owner` at the route
layer, `_check_ownership`/`_check_ownership_locked` inside every service method). Option B
would introduce the first networked component and the first second-process trust boundary
in the system for a benefit (process isolation from a one-host, single-operator backend)
that does not currently correspond to any stated requirement. ARE's own documentation
does not require network isolation — it requires authentication, authorization, tenant
mapping, and stable transactional state to be supplied by *a* host, and says nothing about
that host needing to be a separate process. Option A can satisfy every one of ARE's
documented production prerequisites using machinery Defence Studio already has (real
HS256 auth, per-request ownership checks, `BEGIN IMMEDIATE` transactions) without adding a
new failure domain.

### Explicit assumptions

1. Defence Studio remains single-host, single-operator for the foreseeable deployment
   horizon (no requirement surfaced for horizontally scaling the asset/rights component
   independently of the rest of the backend).
2. One ARE `Engine` instance (and therefore one ARE SQLite file) per Defence Studio
   `job_id` is an acceptable storage-count increase; the file is small (rights/assignment
   metadata only, no media bytes) and the existing project already creates per-job
   artifact directories under `jobs/<job_id>/`.
3. ARE's wheel (1.1.0, hash `b626d9eed519b038f0fdd9e97d9fcb62c5b1128a2f2b4c93de4ea4b987dfa4ca`)
   is added as a pinned dependency in the backend's own environment/`pyproject.toml`
   (not yet done — this phase makes no dependency change), and its own test suite
   (94 passed per `A2_ACCEPTANCE_REPORT.md`) is treated as a vendor acceptance gate, not
   re-run by Defence Studio's own CI as a substitute for Defence Studio's own tests.
4. DS's own `AssetService`/`contracts/assets.py` (M2.0/M2.1) either (a) becomes a
   thin translation layer in front of the per-job `Engine`, reusing DS's ownership
   checks but delegating rights-state storage and effective-rights evaluation to ARE, or
   (b) remains authoritative and ARE is adopted only for net-new capability DS doesn't
   have (e.g. the structured `Evaluation.blockers`/`warnings` list) — **which of these two
   is intended is itself an unresolved question**, not something this phase decided (see
   `COMPATIBILITY.md` item 3 and 5).

### Unresolved questions (must be answered before any adapter is implemented)

1. Does the real adapter replace `AssetService`'s rights/assignment storage with ARE's, or
   layer ARE alongside DS's existing tables as a second source of truth for a subset of
   fields? (`COMPATIBILITY.md` items 1, 3, 7.)
2. What happens to a DS asset with no `source_url`/`documented_origin` (currently valid,
   forces `UNVERIFIED`) when registered with ARE, whose `AssetDescriptor` requires
   non-empty provenance? Refuse ARE registration, or synthesize a placeholder string that
   would misrepresent an honest "unknown" as a declared fact? (`COMPATIBILITY.md` item 2.)
3. How are DS's `RESTRICTED`/`CHANGES_REQUESTED` review decisions represented once ARE's
   core only has `APPROVED`/`REJECTED`? (`COMPATIBILITY.md` item 8.)
4. Who resolves `SCOPE_MISMATCH`/`PRINCIPAL_MISMATCH`-class errors into an HTTP status —
   does DS need a 403 code path it doesn't have today, or does everything still collapse
   to today's 404 convention? (`COMPATIBILITY.md` item 9, `SECURITY_REVIEW.md` §2 note.)
5. Backup/restore procedure for the new per-job ARE SQLite files — same procedure as
   `data/defence_studio.sqlite3` today, or a separate documented step? (Not yet written
   either way.)
6. Exact lifecycle of the per-job `Engine` instance (created on first asset-service call
   for a job? on job creation? cached in-process for how long, and is that cache safe
   across the API process restarting mid-job?).

## Required operation contract

For each operation: **trusted input** (what the caller must have already proven before
this call is reached), **validation**, **outcome**, **failure behavior**, **audit
requirement**. This section specifies the *contract* a real adapter must satisfy; it
implements none of it.

### 1. Registering an asset

- **Trusted input:** authenticated `owner_id` (from `require_owner`); `job_id` already
  ownership-checked; a descriptor body conforming to `AssetDescriptorV1` shape (or
  whatever shape the adapter settles on per Unresolved Question 1).
- **Validation:** contract-level validation (pydantic, as today) plus, if ARE is the
  target, `AssetDescriptor.__post_init__`'s stricter requirements (non-empty provenance —
  see Unresolved Question 2) must be satisfiable or the call must fail closed rather than
  substitute a fabricated value.
- **Outcome:** a new (or deduplicated, if DS's content-addressing is kept) asset record,
  `DRAFT`/equivalent, never auto-approved.
- **Failure behavior:** a provenance/validation failure must reject registration outright
  (422-equivalent), never register with silently downgraded honesty guarantees.
- **Audit requirement:** who registered it, when, and the exact descriptor content hash,
  durably recorded — matching DS's existing `created_by`/`created_at`/`sha256` columns.

### 2. Recording a rights revision

- **Trusted input:** authenticated `owner_id`; the target `asset_id` already resolved
  within the caller's own job.
- **Validation:** `RightsRecordV1` contract validation (as today); if routed through ARE,
  also `RightsRecord.__post_init__`'s validity-interval and permission-type checks, which
  are stricter in some ways (interval ordering) and looser in others (no exact-attribution
  match requirement) than DS's own — see `COMPATIBILITY.md` rights-revision row.
- **Outcome:** an immutable new rights revision; effective rights re-derived, never
  trusted from client input; a prior `APPROVED` review is invalidated back to
  `PENDING_REVIEW` (DS's existing rule, which must be preserved regardless of which engine
  stores the rights content).
- **Failure behavior:** validation failure rejects the whole call; no partial rights
  update.
- **Audit requirement:** the old and new rights-content hash, the re-derived verdict
  before/after, and that the prior approval was invalidated (not silently dropped).

### 3. Assigning an asset to an approved scene

- **Trusted input:** authenticated `owner_id`; `job_id`/`language`/`scene_id` already
  validated against the caller's own currently-approved scene plan (never a plan ID or
  hash supplied by the client and trusted as "the approved one" — DS's current code reads
  its own `scene_plans` table fresh inside the transaction, and this must remain true
  regardless of which engine stores the assignment).
- **Validation:** scene exists in the current approved plan; asset's re-derived effective
  rights permit the target use; no stale/duplicate assignment silently created.
- **Outcome:** a new, versioned assignment record pinned to the exact approved
  `plan_sha256`/`version`/`scene_id`; any prior `ACTIVE` assignment of the same asset to
  the same scene is superseded, never silently duplicated.
- **Failure behavior:** no approved plan -> 409; unknown scene -> 422; non-permitted
  rights -> 422 (DS's current behavior, which any adapter must preserve or explicitly
  document as changed).
- **Audit requirement:** the exact plan hash/version the assignment was bound to, so a
  later plan change is detectable as `stale_plan` without silent rebinding.

### 4. Removing an assignment

- **Trusted input:** authenticated `owner_id`; the assignment identified by
  `(job_id, language, scene_id, asset_id)` (DS's current composite key) or ARE's
  `assignment_id` if adopted (see `COMPATIBILITY.md` §4).
- **Validation:** the assignment must currently be `ACTIVE`.
- **Outcome:** the assignment is deactivated; the scene plan itself is never rewritten or
  touched (DS's existing, explicitly tested guarantee —
  `test_remove_assignment_does_not_touch_plan`).
- **Failure behavior:** removing a non-existent/already-removed assignment is a no-op
  (idempotent), not an error, matching DS's current `removed: 0` response shape.
- **Audit requirement:** who removed it and when; the removed assignment's prior content
  remains in history, never deleted.

### 5. Reviewing an asset

- **Trusted input:** authenticated `owner_id` used as the reviewer identity — **never** a
  client-supplied reviewer field (see `SECURITY_REVIEW.md` §4; this must hold true for
  whatever engine ultimately stores the decision).
- **Validation:** `reviewed_asset_sha256`/`reviewed_rights_sha256` must match the current
  stored content (stale -> 409); `expected_revision` must match the current revision
  (conflict -> 409); an `APPROVED` decision requires the re-derived effective rights to be
  `CLEAR`/`RESTRICTED`, never trusted from the request.
- **Outcome:** current review state updated and an append-only history row written in the
  same transaction.
- **Failure behavior:** a stale or conflicting request is rejected outright, never
  partially applied; a duplicate idempotency-key replay returns the existing decision
  without a second write.
- **Audit requirement:** reviewer identity, decision, note (non-blank, enforced today),
  the exact digests reviewed, and timestamp, immutable once written.

### 6. Retrieving assignment readiness

- **Trusted input:** authenticated `owner_id`; read-only, no state change.
- **Validation:** none beyond ownership — this is a report, not a mutation.
- **Outcome:** per-scene blocker list and an overall `ready` boolean; never a hidden
  mutation as a side effect of reading readiness.
- **Failure behavior:** no approved plan -> an honest `ready: false` with an explicit
  blocker, never a fabricated "ready" or a crash.
- **Audit requirement:** none beyond normal access logging (this is a read).

### 7. Reading revision and review history

- **Trusted input:** authenticated `owner_id`; scoped to the caller's own job/asset.
- **Validation:** none beyond ownership.
- **Outcome:** the complete, ordered append-only history for that asset — never
  truncated or filtered in a way that could hide a prior decision from its rightful
  owner.
- **Failure behavior:** an asset with no history returns an empty list, not an error.
- **Audit requirement:** this endpoint *is* the audit requirement for every other
  operation above; it must never be derivable as "lossy" relative to what was actually
  written.

## Phase 5D addendum — frozen integration boundary design

This section refines Option A around the decisions frozen in `DECISION_REGISTER.md`. It
is still a specification: no adapter module, route, dependency, or ARE import was added
by this phase. Every item below is explicitly labeled by its status so the reader never
has to guess whether something already works or is merely proposed.

### Existing verified behavior (already true today, unaffected by any future adapter)

- Authentication (`auth.py::authenticated_user`) and ownership (`_check_ownership`/
  `_check_ownership_locked`) happen before any service method body runs, for every asset
  route — re-confirmed this phase by re-reading `api/app.py:534-603` and
  `services/assets.py`.
- Every mutating `AssetService` method wraps its ownership re-check and its write in one
  `BEGIN IMMEDIATE` transaction — re-confirmed this phase, unchanged since Phase 5C.
- No code anywhere in `backend/defence_studio` imports `asset_rights_engine` — re-confirmed
  this phase (`grep -rln "asset_rights_engine" backend/defence_studio` → no matches).
- DS job IDs are `uuid.uuid4()` strings (`services/jobs.py:68`) — confirmed this phase,
  which is why Decision Register invariant 6 (tenant-isolation section) can require a
  full, untruncated UUID in any future ARE storage path rather than a lossy prefix.

### Proposed adapter behavior (not implemented; this is the design this phase freezes)

1. **Single import boundary.** One new module (e.g.
   `backend/defence_studio/services/asset_rights_adapter.py`) is the only place in the
   entire codebase permitted to `import asset_rights_engine`. No route, no other service
   module, and no test outside this module's own unit tests may import it directly. This
   must be enforceable by code review (and ideally an import-linting rule) once the
   module exists.
2. **Identity derivation happens only after ownership succeeds.** The adapter module
   receives `job_id` only from a caller (`AssetService`) that has already called
   `_check_ownership`/`_check_ownership_locked` for that exact `job_id` in the current
   transaction. The adapter itself performs no independent authentication or ownership
   check — it trusts its caller completely, the same way every other DS service trusts
   `owner_id` once it has passed through `require_owner`. This mirrors Decision Register
   Tenant Isolation invariant 1.
3. **Engine lifecycle: per-job, lazily constructed, optionally cached.** The adapter
   constructs (or retrieves from an in-process cache keyed by the exact `job_id` string)
   one `Engine(data_directory=f"{storage_root}/are/{job_id}/")` instance per job
   (Decision Register Option T1). The cache, if implemented, never substitutes for the
   per-call ownership check performed by the caller (invariant 3 of the tenant-isolation
   section) — it only avoids repeating `Engine.__init__`'s path setup and schema check.
4. **Reviewer/principal identity is always the authenticated `owner_id`.** Wherever the
   adapter constructs an ARE `ReviewRequest`/`HostPrincipal`, the `reviewer`/
   `principal_id` field is always exactly the `owner_id` already authenticated by
   `require_owner` — never a value read from the request body (there is none to read, per
   `SECURITY_REVIEW.md` §4) and never a value the adapter itself invents.
5. **Scene-plan authority stays with Defence Studio.** The adapter never asks ARE to
   determine whether a scene plan is approved; it reads DS's own `scene_plans` table
   (exactly as `AssetService._current_approved_plan` does today) and constructs whatever
   ARE-facing `SceneBinding`/`HostSceneContext` representation is needed *from* that
   already-authoritative DS read, inside the same transaction — never the reverse.
6. **Error translation is an explicit, enumerated table, not a catch-all.** Per Decision
   9, every ARE `DomainError` code the adapter can encounter is mapped to a specific DS
   `ReviewError` subclass; an unrecognized code (e.g. from a future ARE version) must
   raise a generic, clearly-logged 500-class failure rather than being silently absorbed
   into any existing 2xx/4xx path.
7. **Idempotency keys are not shared verbatim across the boundary.** Per
   `COMPATIBILITY.md` §6, DS's idempotency-key scope (`(job_id, asset_id, reviewer_id,
   key)`) and ARE's (shared across all review kinds) differ; the adapter must derive a
   distinct, deterministically-namespaced key for any ARE call it makes (e.g.
   `f"{job_id}:{key}"`) rather than forwarding DS's raw key, so a DS-side key reused in a
   different DS call-site context can never accidentally collide with an unrelated ARE
   call.
8. **Audit correlation.** Every adapter call that reaches ARE logs (at minimum, in
   whatever structured logging DS already uses) the DS `job_id`, authenticated `owner_id`,
   and the specific ARE operation invoked, so a future incident investigation can
   correlate a DS-side audit row with the corresponding ARE-side `review_history`/
   `rights_revisions` row without guessing.
9. **Recovery for ambiguous commits.** Because DS's transaction (its own SQLite file) and
   ARE's transaction (a separate SQLite file) cannot commit atomically together (confirmed
   in the original Option A/B comparison above), the adapter must define, for each
   operation, which side is allowed to commit first and what the retry behavior is if the
   second side's call fails after the first side already committed. The recommended
   default (not yet implemented): **DS commits first, then calls ARE; if the ARE call
   fails, the operation is reported as failed to the caller and the specific idempotency
   key is recorded as "ARE call pending" so a retry of the same idempotent request can
   complete the ARE-side call without repeating the DS-side write** (DS's existing
   idempotency-replay logic already returns the existing DS state on a key replay, so a
   retry naturally skips re-doing the DS write and only needs to re-attempt the ARE call).
   This requires a new, small piece of DS-side state (a "pending ARE call" marker) that
   does not exist today — explicitly flagged as a **required host framework change**, not
   something already present.
10. **No raw `Engine` access is reachable from an untrusted caller.** Consistent with
    `SECURITY_REVIEW.md` §8's conclusion: the adapter module's public functions are the
    only entry point; nothing resembling a debug endpoint, admin route, or test-only
    backdoor may construct an `Engine` pointed at a caller-influenced path outside the
    adapter's own job-id-derived convention.

### Required host framework changes (do not exist today; needed before the adapter can be built)

- The "pending ARE call" idempotency-retry marker described in item 9 above — a new,
  small, additive piece of state (e.g. one column or one small table), **not
  implemented by this phase** per the task's explicit no-migration restriction, and
  requiring its own schema-change proposal.
- A per-job `Engine` cache/lifecycle manager, if caching is adopted (item 3) — does not
  exist today; would be new code in the adapter module itself, not a dependency DS
  already has.
- An explicit ARE `DomainError`-code-to-DS-exception mapping table (item 6; Decision
  Register Decision 9) — does not exist today; must be written and reviewed before the
  adapter's first call into ARE.
- A stable, server-generated per-asset identifier distinct from DS's content-addressed
  `asset_id` (Decision Register Decision 1) — requires an additive schema change, not
  implemented by this phase.

### Unresolved external/product decisions (cannot be resolved by this document alone)

- Whether Decision 3 (rights-state ownership) resolves toward "ARE pure-evaluator only, no
  ARE persistence" or toward "ARE's stateful `Engine` persists rights/assignment state" —
  this single choice determines whether Decisions 5, 6, and 8 have any code path to apply
  to at all, and is the most consequential open question in `DECISION_REGISTER.md`.
- Whether Defence Studio's product owner wants the modification/distribution permission
  axes (Decision 4) badly enough to justify a future schema-change phase, or whether
  passing inert `False`/`False` to ARE indefinitely is acceptable.
- The backup/restore operational procedure for per-job ARE SQLite files under Option
  T1 — this phase recommends treating them the same as DS's existing per-job artifact
  directories, but no one has written or tested that procedure.

## Mandatory honesty statement (per task instruction, carried into the contract)

**Declared rights are not proof of legal ownership, and an asset's availability (or its
`REFERENCED_ONLY`/reachable-URL status) is not proof that its use is licensed.** Every
operation above operates on operator-asserted, human-reviewed claims about rights — never
on an automated verification of legal title. This is already how DS's M2.0 contract
module documents itself (`contracts/assets.py`'s module docstring: "Rights are operator
assertions reviewed by a human... nothing here promotes 'publicly accessible' to 'licensed
for reuse'") and how ARE documents itself ("a licence URL being reachable does not make
the terms true, and public accessibility never implies reuse rights" — `contracts.py`
module-level comments as reflected in `CONTRACTS.md`). Any real adapter must preserve this
property end to end, including in whatever UI eventually surfaces `effective_rights` to a
human reviewer.
