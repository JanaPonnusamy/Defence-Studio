> **Phase 5D gate: BLOCKED (2026-10-12).** A current-code readiness defect was
> reproduced on disposable SQLite below (F5D-01). The request requires a real code
> defect to be documented separately and work stopped. No integration design is
> frozen or approved by this session; preserved Phase 5C/pre-existing Phase 5D
> recommendations must not be treated as implementation authorization.

# Phase 5C Workstream C — authentication/authorization trace for the asset-service request path

Read-only review of the real request path from HTTP authentication through
`AssetService` execution. No product code was changed to produce this document. Every
control below cites the exact source location and, where a test demonstrates the
behavior, the exact test. Classifications: **Verified**, **Partially verified**, **Not
implemented**, **Requires integration testing**.

## Request path traced

```
HTTP request
  -> FastAPI route (api/app.py, asset routes lines 534-603)
     -> Depends(require_owner) = authenticated_user(secret, dev_config)   [auth.py:39-70]
        -> owner_id: str  (verified token subject, or dev-bypass owner)
     -> AssetService.<method>(job_id, owner_id, ...)                      [services/assets.py]
        -> self._check_ownership(job_id, owner_id)                        [services/review.py:88-95]
           -> self.jobs.get(job_id, owner_id)                             [services/jobs.py]
        -> (mutations) BEGIN IMMEDIATE; self._check_ownership_locked(...) [services/script.py:401]
        -> SQLite read/write; commit/rollback
```

## 1. Is the caller authenticated?

**Verified.** `authenticated_user(secret, dev_config)` (`auth.py:39`) returns a FastAPI
dependency. In production mode (`dev_config` absent or `dev_config.safe` false), a
missing bearer credential raises 401 (`auth.py:64`) and a present credential is verified
by `_verify_token` (`auth.py:15-36`): HS256 signature check via `hmac.compare_digest`,
explicit `alg == "HS256"` check, non-empty string `sub`, and an `exp` that must be in the
future. Any `ValueError`/`TypeError`/`KeyError`/`JSONDecodeError` from verification is
caught and re-raised as 401 (`auth.py:65-68`) — never silently passed through. Test:
`backend/tests/test_asset_service_m2_1.py::test_api_asset_routes_auth_and_errors` asserts
an unauthenticated `GET /api/jobs/job-x/assets` returns 401. The same `_verify_token`
path is shared by every other route family (F1-F5, Phase 2/3, scene, asset) — not
reimplemented per feature.

**Partially verified — dev-auth bypass widens the trust boundary for every asset route.**
`load_dev_auth_config()` (`dev_auth.py:56`) is fail-closed by default (`enabled=False`
unless **both** `DEFENCE_STUDIO_DEV_MODE=1` and `DEFENCE_STUDIO_DEV_AUTH_BYPASS=1` are
set, confirmed at `dev_auth.py:62`), and even when active, a bypass only fires for a
request with **no** Authorization header that originates from a genuine loopback address
with no proxy-forwarding header present (`request_is_loopback`, `dev_auth.py:72-87`,
checked at `auth.py:60-63`). This is a deliberate, narrowly-scoped development
convenience (see `project_status.md`'s "two-PC portability" change log entry), not
something this review found newly introduced for assets — `create_app()` wires the same
`dev_config` into the same `require_owner` dependency used by every route (`api/app.py:130,133`),
so the asset routes inherit exactly the same bypass behavior as every other route family,
no more and no less. **Classification is "partially verified" rather than "verified"
specifically because this phase did not independently re-run the dev-auth test suite
(`test_dev_auth.py`, if one exists) as part of this review** — it was out of scope
(auth.py/dev_auth.py predate this phase and were not touched), and this review relies on
reading the code rather than re-executing that suite. **This bypass must never be enabled
outside a trusted local development machine; it is not evaluated here as acceptable for
any production or multi-user deployment.**

## 2. Does the requested job belong to the authorized owner/tenant?

**Verified.** Every `AssetService` public method calls `self._check_ownership(job_id,
owner_id)` before any read (`services/assets.py` — confirmed at the top of
`register_asset` L121, `get_asset` L178, `list_assets` L196, `set_rights` L217,
`assign_asset` L284, `list_assignments` L379, `remove_assignment` L398,
`review_asset` L428, `asset_review_history` L506, `assignment_readiness` L519), which
delegates to `self.jobs.get(job_id, owner_id)` (`services/review.py:88-95`) and translates
a mismatch into `ReviewNotFound` (404) rather than ever returning another owner's data.
Every mutating method additionally re-checks ownership **inside** the transaction via
`_check_ownership_locked` (defined once at `services/script.py:401`, inherited through
`SceneService`/`AssetService`) immediately after `BEGIN IMMEDIATE` — closing the
time-of-check-to-time-of-use gap between the pre-transaction check and the write. Tests:
`test_register_cross_owner_denied`, `test_set_rights_cross_owner_denied`,
`test_assign_asset_cross_owner_denied`, `test_review_asset_cross_owner_denied`,
`test_list_assignments_cross_owner_denied`, `test_assignment_readiness_cross_owner_denied`
(all in `test_asset_service_m2_1.py`, lines 94-168) each assert a `ReviewNotFound` is
raised for a non-owning caller **and** that the rejected call left the real owner's state
unchanged (no partial mutation, no leaked read). Full suite run this session: all pass
(see Workstream B below and the final verification section).

**Note on HTTP status choice (flagged, not a defect):** cross-owner denial surfaces as
404, not 403 — the same convention every other F1-M2.1 service already uses (confirmed
consistent with `ReviewNotFound.status_code = 404`, `services/review.py:55-56`). This
avoids confirming to an unauthorized caller that a job ID exists at all, which is a
defensible choice, but means ARE's distinct `SCOPE_MISMATCH`/`PRINCIPAL_MISMATCH` codes
(see `COMPATIBILITY.md` §11) have no natural DS equivalent beyond "404 for everything."

## 3. Do the requested asset and scene belong to the same authorized scope?

**Verified** for the asset/job pairing: `asset_id` is always looked up with `WHERE
job_id=? AND asset_id=?` (e.g. `services/assets.py:180-182`, `:233-235`, `:309-311`,
`:440-442`), so an asset ID belonging to a different job never matches. **Verified** for
the scene/plan pairing: `assign_asset` reads the scene IDs from the **current job's**
approved plan only (`plan_row = self._current_approved_plan(connection, job_id,
language)` then `scene_ids = {s["scene_id"] for s in plan["scenes"]}`,
`services/assets.py:301-306`) and rejects an unknown `scene_id` with
`AssetValidationError` (422). Test: `test_assign_unknown_scene_rejected`
(`test_asset_service_m2_1.py:232-236`). Because both the asset lookup and the scene-plan
lookup are scoped by the same `job_id` that was already ownership-checked, a caller cannot
assign asset X from job A to scene Y of job B's plan — there is no code path that accepts
a scene plan or asset from a job other than the one in the URL.

## 4. Does reviewer identity come from trusted server-side authentication, not an arbitrary request field?

**Verified.** `AssetReviewBody` (`api/app.py:106-112`) has **no `reviewer`/`reviewer_id`
field at all** — only `decision`, `reviewed_asset_sha256`, `reviewed_rights_sha256`,
`expected_revision`, `note`. The route handler (`api/app.py:562-570`) passes `owner_id`
(from `Depends(require_owner)`, i.e. the authenticated subject) directly into
`AssetService.review_asset(job_id, owner_id, asset_id, body.decision, ...)`, and
`review_asset` writes that same `owner_id` as `reviewer_id` in both `asset_reviews` and
`asset_review_history` (`services/assets.py:487-493`). There is no code path by which a
client-supplied field becomes the recorded reviewer. This directly satisfies the
requirement the ARE-A2 host-adapter doc flags as a production prerequisite ("require
`request.reviewer` to equal `principal.principal_id`") — Defence Studio's existing design
already enforces the stronger version of that rule (the reviewer field doesn't exist on
the wire at all, so there is nothing to spoof) **for its own current implementation**,
independent of whether ARE is ever integrated. This is **not** itself a test of ARE's
`AdapterGateway`, which governs a different code path than exists today (see
`COMPATIBILITY.md` §6).

## 5. Are revoked or expired permissions enforced?

**Partially verified — token expiry only; no revocation mechanism exists in Defence
Studio to evaluate.** Token expiry: `_verify_token` rejects `exp <= time.time()`
(`auth.py:34-35`), i.e. a token simply stops working once past `exp`. **Not
implemented**: there is no token-revocation list, no session table, no "logged out"
state, and no owner-level deactivation anywhere in F1-M2.1 — a still-unexpired token
remains valid until its natural expiry with no way to invalidate it early. This is not
specific to the asset service; it is a property of the whole bearer-token design and was
not introduced or altered by Phase 5. **Requires integration testing** once a real
external token issuer exists (per F1's documented assumption: "F1 does not issue tokens
or register users... Configure... an external trusted token issuer") — revocation
necessarily lives on that issuer's side, not in this codebase, and cannot be tested until
one is chosen.

## 6. Is the approved-plan revision and digest checked at the correct point?

**Verified.** `assign_asset` re-reads the plan's `plan_sha256`/`version` **inside** the
same `BEGIN IMMEDIATE` transaction as the write (`services/assets.py:289-360`) — the
check and the use happen atomically, not as a separate pre-check that could race a
concurrent plan re-approval. If no `APPROVED` plan row exists at that instant,
`AssetStaleRevision` (409) is raised before any assignment row is written
(`services/assets.py:302-303`). Test: `test_assign_requires_approved_plan`
(`test_asset_service_m2_1.py:239-247`). Staleness *after* assignment (a later
re-approval) is separately, correctly handled as a read-time recomputation rather than a
stored flag that could go stale itself: `list_assignments` compares each assignment's
stored `scene_plan_sha256` against the **current** `_current_approved_plan` result at read
time (`services/assets.py:386-389`), confirmed by `test_assignment_flagged_stale_after_plan_reapproval`
(`test_asset_service_m2_1.py:259-268`).

## 7. Does the authorization and plan state remain valid throughout the operation?

**Verified** for single-operation atomicity: every mutating method's ownership check,
plan/scene lookup, and write occur inside one `BEGIN IMMEDIATE` transaction
(`services/assets.py` — e.g. `assign_asset` L290-363, `review_asset` L433-502,
`set_rights` L230-264), so nothing can change the authorization or plan state between the
check and the use within one call. `BEGIN IMMEDIATE` acquires SQLite's write lock up
front, serializing concurrent writers rather than allowing an interleaved read-modify-write.
**Requires integration testing** for the cross-call sequence (e.g. "plan is approved, read
by client A, re-approved by client B, then client A's stale assignment attempt") — this is
exactly what `test_assign_requires_approved_plan` and
`test_assignment_flagged_stale_after_plan_reapproval` demonstrate at the service level, but
no test in this suite drives two genuinely concurrent HTTP requests against a live server
to confirm the same guarantee holds under real network-level concurrency (as opposed to
sequential calls in one test process). The F2 worker-engine tests elsewhere in the suite
do exercise genuine process-level concurrency (a spawned, forcibly-terminated worker), but
that pattern has not been applied to the asset-review optimistic-concurrency path.

## 8. Can direct access to asset-service operations bypass any intended gateway?

**Verified as "yes, direct access is possible, and this is correct for the current
architecture."** There is no gateway between the FastAPI route and `AssetService` today —
the route handler calls the service method directly (`api/app.py:534-603`). This is not a
defect: Defence Studio has no "untrusted direct engine access" concern to defend against,
because `AssetService` **is** the trusted boundary (it owns authentication-derived
`owner_id` enforcement itself, via `require_owner` at the route layer). This question
becomes materially different, and genuinely risky, **only if ARE's raw `Engine` is ever
imported directly into code that can be reached without going through an equivalent
ownership check** — which is exactly the scenario ARE-A2's own documentation warns about:
"Do not expose the raw Engine or let end users choose providers, grants, mappings or
authenticated identity... direct Engine calls remain available for backward
compatibility." **Not implemented / not applicable today** — there is no ARE `Engine`
import anywhere in the Defence Studio backend (confirmed: `grep -r
"asset_rights_engine"` under `backend/defence_studio` returns nothing), so this control
has nothing to verify yet. It becomes a **mandatory Workstream D design requirement**: any
future adapter must ensure the only code path to `asset_rights_engine.Engine` is through a
host-owned boundary that has already performed the same authentication/ownership checks
`AssetService` performs today — never a route or module that imports `Engine` directly.

## 9. Can concurrent requests overwrite an accepted decision?

**Verified.** `review_asset` requires the caller to supply `expected_revision`, checked
against the current stored revision **inside** the `BEGIN IMMEDIATE` transaction
(`services/assets.py:456-464`); a mismatch raises `AssetRevisionConflict` (409) rather than
overwriting. A duplicate `(job_id, asset_id, reviewer_id, idempotency_key)` replay is
detected and returns the existing state without a second write or a second history row
(`services/assets.py:436-447`). Test: `test_review_pins_digests_and_conflict`
(`test_asset_service_m2_1.py:287-314`) exercises stale-digest rejection, a successful
decision, idempotent replay (asserts history length stays 1), and a stale
`expected_revision` conflict on a second, different decision attempt. SQLite's
`BEGIN IMMEDIATE` serializes concurrent writers at the database level, so "first
committer wins, second gets a conflict" is enforced by the database itself, not merely by
application-level timing — consistent with ARE's own documented policy ("Two competing
decisions produce one success and one conflict; which decision wins is intentionally
unspecified").

## Summary table

| # | Control | Classification |
|---|---|---|
| 1 | Caller authenticated | Verified (production path); Partially verified (dev-bypass scope, not independently re-tested this session) |
| 2 | Job belongs to authorized owner | Verified |
| 3 | Asset/scene belong to the same authorized scope | Verified |
| 4 | Reviewer identity from trusted server auth, not request field | Verified |
| 5 | Revoked/expired permissions enforced | Partially verified (expiry only; no revocation exists to test) |
| 6 | Approved-plan revision/digest checked at the correct point | Verified |
| 7 | Authorization/plan state remain valid throughout the operation | Verified (single-call atomicity); Requires integration testing (true concurrent-HTTP-request level) |
| 8 | Direct asset-service access vs. an intended gateway | Verified / Not applicable today — becomes a hard Workstream D requirement if ARE's `Engine` is ever imported |
| 9 | Concurrent requests cannot overwrite an accepted decision | Verified |

No control above is weakened, removed, or newly added by this review; it is a read of the
code as it already exists in the working tree plus the already-present regression tests
(see `ACCEPTANCE_PLAN.md`/final report for what remains to be exercised once an ARE
integration actually exists).

## Phase 5D separate code finding — F5D-01: obsolete approval accepted by readiness

**Status: reproduced; unfixed; design-freeze work stopped.** Severity: high integrity
risk (P1); a blocking review finding, not proof of publishing or external exploitation.
The user instruction says: "If a real code defect is discovered, document it separately
and stop rather than expanding the scope." This section is the separate finding within
an explicitly permitted deliverable; no production repair was attempted.

### Verified current behavior and evidence

`backend/defence_studio/services/assets.py::set_rights`, lines 253–259, changes a formerly
APPROVED asset to PENDING_REVIEW and updates its current descriptor/rights digests. It
retains the existing `asset_reviews` record pinned to the prior descriptor/rights. This
is legitimate retained history/current-review data which consumers must check for staleness.

`AssetService.assignment_readiness`, lines 516–566, loads current asset/review rows but
checks only the cached asset effective_rights == CLEAR (554–555) and review decision ==
APPROVED (556–558). It does not require the current asset review_status to be APPROVED or
compare the review's reviewed_asset_sha256/reviewed_rights_sha256 with current digests.
Therefore the old approval can authorize a readiness result after explicit invalidation.

A one-scene synthetic fixture was created in a new `tempfile.TemporaryDirectory` with
existing schema-v7 initialization, a newly generated job UUID, and a directly seeded
synthetic approved-plan row/artifact. No source/claim decision or live plan approval was
submitted. Actual current AssetService methods registered an asset, set rights, reviewed
it APPROVED, assigned it, and then set a different still-CLEAR rights record. This isolates
the readiness predicate; it is not an end-to-end research/publishing workflow test.

Observed results (asserted in the offline Python probe):

```json
{
  "before_ready": true,
  "after_ready": true,
  "after_blockers": [],
  "current_review_status": "PENDING_REVIEW",
  "rights_digest_changed": true,
  "review_pins_old_rights": true,
  "review_matches_current_rights": false,
  "current_effective_rights": "CLEAR",
  "source_or_claim_decisions_submitted": false,
  "temporary_database_only": true
}
```

Expected safety behavior: readiness must be false until current content has a matching
explicit approval; history must remain intact. Returning ready:true here contradicts
rights-change invalidation and the digest-pinned review contract. The stored assignment
remaining a historical point-in-time record does not justify a current readiness result
accepting approval of different rights content.

### Reproduction recipe (disposable fixture only)

The executed probe ran as a Python stdin script, with PYTHONPATH set to the backend and
`python -B -`. It imported existing database/services/contracts directly, not api.app,
not ARE, and used only a new TemporaryDirectory database and artifact root. Steps:

1. Initialize a fresh temporary schema-v7 database; create a new synthetic owner/job.
2. Seed one synthetic approved `scene_plans` row and a digest-checked plan artifact
   containing `scene-1`; create AssetService with that temporary database/root.
3. Register an image descriptor with documented_origin='Synthetic disposable fixture'.
4. `set_rights`: creator_owned, commercial_use=allowed, attribution_required=false,
   permitted_uses=['web']; retain returned asset/rights digests.
5. `review_asset`: APPROVED, those current digests, expected_revision=0, synthetic note/key.
6. `assign_asset`: ta/scene-1, target_use=web; assert readiness true.
7. `set_rights` again, changing permitted_uses to ['web', 'broadcast'] (still CLEAR).
8. Assert the current review_status is PENDING_REVIEW, the rights digest changed,
   and asset_reviews still pins the first rights digest.
9. Actual `assignment_readiness` returns ready:true with no blockers; assert this to
   reproduce the defect. All probe data is deleted on TemporaryDirectory exit.

### Narrow proposed repair and acceptance gate (not implemented)

A separately authorized repair should require approval for the current descriptor AND
rights digest and current approved lifecycle before declaring readiness. Preserve
append-only history, keep readiness read-only, and retain owner/job scoping. Define the
current-readiness snapshot consistently; do not solve this by rewriting old reviews or
quietly approving updated rights. No standalone ARE change or migration is justified by
this finding. Required future regressions:

- Approved assignment + changed still-CLEAR rights -> readiness false with a specific
  stale/unapproved-content blocker; review_status remains PENDING_REVIEW.
- Old review/assignment history is preserved; no implicit rebinding or approval.
- Explicit approval against current digests restores readiness when all other gates pass.
- Stale descriptor digest or rights digest blocks readiness independently.
- Wrong owner/job remains denied and competing rights/review writes cannot produce a
  readiness result from mismatched content snapshots.

These tests are **planned**, not added or passed in this phase.

### Relationship to ARE-A3.1 and remaining production risks

ARE-A3.1 is an unpublished candidate; its report records 140 passing source/installed
tests and complete-schema/trigger/JSON/reference validation. Accepted ARE 1.1.0 remains
unchanged and does not contain those repairs. No ARE import exists in this DS execution
path; standalone validation cannot repair DS's current readiness predicate and must not
be copied into DS to work around it. The report/source were inspected read-only.

Authentication, resource authorization, legal rights, media availability and privileged
tampering protection are not supplied by database validation. A3 G01–G07 remain
production gates: trusted identity/revocation, ownership/scope mapping, exclusive custody,
host reservations, durable intent/receipts/reconciliation, authenticated audit provenance,
and trusted storage/restore identity. No P0 was closed by this probe/documentation.

### Phase 5D execution and preservation record

- Branch before work: main. HEAD: 32798548750630c85114d13544425f054f64e0c5.
- Pre-existing Git changes: modified ACCEPTANCE_PLAN.md and INTEGRATION_SPEC.md;
  untracked DECISION_REGISTER.md, all under docs/asset_rights_integration/.
- This session changes only SECURITY_REVIEW.md, adding this finding/banner while
  preserving all original bytes. The other four design documents remain byte-identical
  to session start, including all pre-existing uncommitted work.
- Before editing, 105 DS production/test/frontend/integration-document files and 27
  standalone runtime/schema/migration/wheel/report/design files were hashed in memory.
  Preservation is checked after this edit; no verification files were added to either
  project. Persistent database contents and the protected research job were not accessed.
- Current verification: one disposable defect probe passed all assertions reproducing
  the bad readiness result. No pytest/full-suite run was performed. Existing asset tests
  were inspected: test_setting_rights_invalidates_prior_approval asserts only lifecycle
  invalidation; test_readiness_reports_specific_blockers covers the original approval.
  Neither composes rights change with a subsequent readiness assertion. Historical
  Phase 5C test claims are not represented as current test execution.
- Inspected: CLAUDE.md; core design §§1/1A; project_status.md relevant status/test notes;
  integration COMPATIBILITY/SECURITY_REVIEW/INTEGRATION_SPEC/ACCEPTANCE_PLAN and existing
  DECISION_REGISTER; backend pyproject/test conftest; asset contracts/service; auth/dev-auth;
  database initialization/asset/scene DDL; JobService; inherited ownership/artifact reads;
  app initialization/request-path source; relevant asset contract/service tests; and
  standalone ARE-A3.1 report. Project_Master_Document.md was not found in the workspace;
  CLAUDE.md names DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md as the authoritative master.
- Git after: the same three pre-existing entries plus modified SECURITY_REVIEW.md.
  No commit/push, production Python/frontend change, database-data change, new migration,
  ARE import or standalone edit occurred. QVault and Whisper Studio were not accessed.
- All nine compatibility choices remain unapproved; no new tenant/provenance policy is
  approved or design-frozen here. T1 and other pre-existing recommendations are preserved,
  not newly endorsed. Some require evidence corrections before a future freeze; this
  phase stops at the confirmed code finding as instructed.
