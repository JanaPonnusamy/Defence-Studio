# Phase 5C Workstream E — integration acceptance plan

This is a plan for a *future* real adapter implementation (Option A per
`INTEGRATION_SPEC.md`). No adapter exists yet, so every scenario below is either
documented-only or backed by an existing test against the current (ARE-less)
`AssetService` — never claimed as "passed against a real integration" that does not
exist. Status column values:

- **Existing behavior verified** — a current test in this repository already exercises
  this exact scenario against the real `AssetService`/API, cited by file:test name.
- **Existing test coverage required** — no current test covers this; one should be added
  against the current system (does not require ARE to exist).
- **New host implementation required** — the scenario cannot be tested until the real
  adapter (host-side authentication/authorization/tenant mapping per
  `INTEGRATION_SPEC.md`) is built.
- **Integration environment required** — requires both the real adapter *and* a running
  ARE `Engine` wired end to end; cannot be exercised by either side's unit suite alone.

| # | Scenario | Status | Notes |
|---|---|---|---|
| 1 | Authentication failure (missing/invalid/expired bearer token) | **Existing behavior verified** | `test_asset_service_m2_1.py::test_api_asset_routes_auth_and_errors` — unauthenticated `GET /api/jobs/job-x/assets` -> 401. Token-expiry rejection is covered generically by `auth.py`'s `_verify_token` and exercised elsewhere in the suite (not asset-specific); no asset-specific expired-token test exists today. |
| 2 | Forged reviewer identity (client supplies a `reviewer`/`reviewer_id` field) | **Existing behavior verified, by absence of the field.** | `AssetReviewBody` (`api/app.py:106-112`) has no reviewer field to forge; `review_asset`'s reviewer is always the authenticated `owner_id` (`services/assets.py:487-493`, confirmed in `SECURITY_REVIEW.md` §4). A test asserting that a request body *containing* an extraneous `reviewer_id` key is ignored/rejected does not currently exist (the body schema uses `extra="forbid"`, so such a field would 422 rather than be silently accepted) — **existing test coverage required** to make this explicit: POST a review with an extra `reviewer_id` field and assert 422, not that it was accepted and ignored. |
| 3 | Cross-tenant and cross-owner access | **Existing behavior verified** | `test_set_rights_cross_owner_denied`, `test_assign_asset_cross_owner_denied`, `test_review_asset_cross_owner_denied`, `test_list_assignments_cross_owner_denied`, `test_assignment_readiness_cross_owner_denied`, `test_register_cross_owner_denied` (`test_asset_service_m2_1.py:94-168`) — each asserts `ReviewNotFound` for a non-owning caller and that the real owner's state is unaffected. "Cross-**tenant**" specifically has no test because Defence Studio has no tenant concept distinct from owner today (see `COMPATIBILITY.md` §7) — **new host implementation required** once a real tenant boundary (e.g. ARE's `HostScope.tenant_id`) is introduced. |
| 4 | Wrong scene or job | **Existing behavior verified** | `test_assign_unknown_scene_rejected` (`test_asset_service_m2_1.py:232-236`) covers wrong scene within the right job. "Wrong job" (asset ID that exists but belongs to a different job) is implicitly covered by every cross-owner test above (since job scoping and owner scoping are the same `WHERE job_id=?` filter) but there is no test using the *same* owner across two different jobs with an asset ID from job A presented against job B's URL — **existing test coverage required** to close that specific gap (same-owner, cross-job asset-ID confusion, distinct from cross-owner). |
| 5 | Unapproved scene plan | **Existing behavior verified** | `test_assign_requires_approved_plan` (`test_asset_service_m2_1.py:239-247`) — assignment against a DRAFT (unapproved) plan raises `AssetStaleRevision` (409). |
| 6 | Stale plan revision and changed digest | **Existing behavior verified** | `test_assignment_flagged_stale_after_plan_reapproval` (`test_asset_service_m2_1.py:259-268`) — a plan re-approval after assignment flags the existing assignment `stale_plan: true` without silent rebinding. |
| 7 | Revoked reviewer permission | **New host implementation required** | Defence Studio has no permission-revocation mechanism at all today (see `SECURITY_REVIEW.md` §5) — there is nothing to test until a real token issuer with revocation exists. Documented as a gap, not silently assumed covered. |
| 8 | Duplicate and conflicting review requests | **Existing behavior verified** | `test_review_pins_digests_and_conflict` (`test_asset_service_m2_1.py:287-314`) covers stale-digest rejection, idempotent duplicate replay (same key, no second history row), and a stale `expected_revision` conflict on a subsequent distinct decision. |
| 9 | Concurrent opposite decisions | **Existing test coverage required** | The existing conflict test is sequential (two calls in one test process, not two genuinely concurrent requests racing SQLite's `BEGIN IMMEDIATE` lock). A test spawning two real concurrent calls (threads or subprocesses) against the same asset with opposite decisions, asserting exactly one succeeds and the other gets `AssetRevisionConflict`, does not exist for the asset service specifically — though the *pattern* is already proven elsewhere in the suite (F2's worker-engine crash/retry tests use real concurrent/spawned-process scenarios). Recommend adapting that existing pattern to assets rather than inventing a new one. |
| 10 | Host and database failure | **Existing test coverage required** (DS side); **Integration environment required** (ARE side) | DS's existing `BEGIN IMMEDIATE`/rollback pattern (`services/assets.py`, every mutating method) is structurally sound but has no asset-specific test simulating a mid-transaction crash (F2's worker tests do this for the worker engine, not for `AssetService`). ARE-side host/DB failure recovery cannot be tested until a real adapter and a real ARE `Engine` wiring exist, per `INTEGRATION_SPEC.md`'s open question on transaction boundaries across two separate SQLite files. |
| 11 | Retry after partial failure | **Existing test coverage required** (DS side); **Integration environment required** (cross-engine) | DS's idempotency-key design (replay-safe `register_asset`/`assign_asset`/`review_asset`) is the retry-safety mechanism and is tested for the happy path (`test_register_idempotent_and_dedup`), but no test specifically simulates "the first attempt wrote the DS row but the (future) ARE-side call never completed, then a retry arrives" — this scenario does not exist as a code path today (no ARE call exists to partially fail), so it cannot be tested until Option A's adapter exists. |
| 12 | Backup and restore | **Existing test coverage required** (DS side, not asset-specific); **New host implementation required** (ARE-side procedure) | No backup/restore test exists for any Defence Studio SQLite file in this suite today (it is an operational procedure, not an automated test, per the project's conventions elsewhere). ARE's own acceptance report separately confirms `data/asset_rights.sqlite3`/`snapshot.sqlite3` hashes were unchanged and that its CLI's own snapshot/restore check passed (`A2_ACCEPTANCE_REPORT.md`: "Existing CLI on temporary SQLite \| Initialization, validation, snapshot, restore and cleanup passed") — that is ARE's own standalone verification, not a Defence-Studio-integrated backup/restore test, and must not be cited as if it were. |
| 13 | Audit identity provenance | **Existing behavior verified** | Every write path records `owner_id`/`reviewer_id` sourced from the authenticated dependency, never from request body content (`SECURITY_REVIEW.md` §4); `asset_review_history` is append-only and immutable once written, confirmed by `test_review_pins_digests_and_conflict`'s history-length assertions. |
| 14 | Migration and rollback | **Existing behavior verified** (DS v6->v7 only); **New host implementation required** (any future DS<->ARE migration) | `test_migration_v6_to_v7_additive_preserves_data_and_fails_closed` (`test_asset_service_m2_1.py:374-399`) verifies the existing additive migration preserves data and fails closed on an unsupported version. No migration between DS and ARE schemas is proposed by this phase (explicitly out of scope per the task instructions) and therefore has no test — this is a gap to close only when such a migration is actually designed. |
| 15 | Missing or changed media | **Existing behavior verified**, by design rather than by exercising real bytes | DS's M2.0/M2.1 is reference-only (`ContentStatus.REFERENCED_ONLY`/`UNAVAILABLE`, never a fabricated hash — `contracts/assets.py:104-113,202-226`); there is no byte-fetch code path to test failing, because none exists yet (byte ingestion is explicitly NOT_STARTED per `project_status.md`). ARE's own `MEDIA_AVAILABILITY_UNVERIFIED` warning (always emitted at readiness evaluation, per `CONTRACTS.md`) has no DS equivalent test because DS has no such warning field yet (`COMPATIBILITY.md` §3) — **existing test coverage required** only if/when DS adds that warning field; until then there is nothing to test. |
| 16 | Rights restrictions and review expiry | **Existing behavior verified** | `test_set_rights_reevaluates_states` (`test_asset_service_m2_1.py:180-192`) covers CLEAR/BLOCKED(non-commercial)/UNVERIFIED(unknown commercial use)/EXPIRED(past expiry) transitions deterministically. `test_non_clear_asset_cannot_be_assigned` and `test_cannot_approve_blocked_rights` cover the fail-closed assignment/approval refusal for non-CLEAR verdicts. |

## Explicitly not claimed as passed

Per the task's own instruction, nothing above marked **New host implementation required**
or **Integration environment required** has been executed — those scenarios have no code
path to exercise yet. Scenarios marked **Existing behavior verified** were confirmed by
reading the cited test and, for the ones most central to this phase (cross-owner
isolation, review conflict/replay, plan-approval gating, migration), by this session's
own full-suite run (see final report) rather than by trusting the citation alone.

## Recommended next test-writing work (does not require ARE; closes existing-system gaps)

These three gaps were identified while building this plan and can be closed against the
*current* system, independent of any future ARE adapter:

1. **Scenario 2 hardening** — an explicit test that a review request body carrying an
   extraneous `reviewer_id`/`reviewer` field is rejected (422, `extra="forbid"`) rather
   than silently accepted-and-ignored, making the "no forgeable reviewer field" guarantee
   observable in a test rather than only inferable from the schema.
2. **Scenario 4 gap** — a same-owner, cross-job asset-ID-confusion test: register an asset
   under job A, then attempt to reference that `asset_id` against job B (same owner, two
   different jobs), asserting `AssetNotFound` rather than a cross-job leak.
3. **Scenario 9** — adapt the existing F2 worker-engine concurrent/spawned-process test
   pattern to drive two genuinely concurrent `review_asset` calls (opposite decisions, same
   `expected_revision`) against one asset, asserting exactly one success and one
   `AssetRevisionConflict`.

These three are recommended, not performed, by this phase — Workstream B's own instruction
was to add tests only for gaps that still exist in the *named* methods
(`set_rights`/`assign_asset`/`review_asset`/`list_assignments`/`assignment_readiness`), and
that gap was found already closed (see the final report). These three are a different,
narrower set of gaps surfaced incidentally while building this acceptance plan; adding them
was not requested by this phase's Workstream B and is left for an explicitly authorized
follow-up, consistent with TOKEN SAVER MODE rule 3 (one task per prompt; no speculative
work).
