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
