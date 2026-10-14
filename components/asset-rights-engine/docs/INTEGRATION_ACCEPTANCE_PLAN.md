# Integration acceptance plan — ARE-A3

This is a future test plan, not a record of planned tests passing. Independently rerun
current evidence: 94 installed-package tests passed with sockets denied; simulator 18/18.
A3 probe observations are evidence, not newly accepted regression tests. Details/commands:
A3_VERIFICATION_REPORT.md. Every row has one of the requested primary classifications;
compound tests are split so a fixture check is never mistaken for host authentication.

| ID | Test / required result | Classification | Existing evidence or implementation needed |
|---|---|---|---|
| A00 | Existing metadata, rights, revisions, digest, transactions, snapshots and optional core imports remain compatible/offline | ALREADY VERIFIED IN ARE-A1/A2 | Current A3 rerun of all 94; scripts/verify_release.py and accepted wheel comparisons |
| A01 | Missing, invalid and expired authenticated sessions cannot read/write; valid host session derives principal independently of request fields | REQUIRES HOST IMPLEMENTATION | No session system exists; constructing HostPrincipal is not this test |
| A02 | Forged reviewer fields, copied known ID, wrong issuer/service identity and revoked subject are rejected before Engine call, including retries | REQUIRES HOST IMPLEMENTATION | P02 accepted synthetic copy; existing equality/unknown-principal checks do not authenticate |
| A03 | Real tenant/owner policy denies unauthorized asset/job/scene/assignment access; equal IDs in different scopes remain distinct | REQUIRES HOST IMPLEMENTATION | Durable ownership registry and scope selection required |
| A04 | Separate component instances/backups cannot be selected/substituted across tenants/owners; restore manifest and recovery generation enforced | REQUIRES INTEGRATION ENVIRONMENT | OS/file custody, host registry and backup topology |
| A05 | Session/grant expires while queued: deny before engine; permission changes ordered against admitted operation; explicit policy for historical reviewer decisions | REQUIRES HOST IMPLEMENTATION | Current synthetic grants can be replaced but have no real revocation semantics |
| A06 | Deny-by-default for every operation/resource; rights reviewer, assignment reviewer, reader and administrator roles independently enforced | REQUIRES HOST IMPLEMENTATION | Production policy, actual identity and ownership resolution |
| A07 | Untrusted code/client/worker cannot call raw Engine, select providers, invoke admin utilities, mutate DB or choose paths | REQUIRES INTEGRATION ENVIRONMENT | Process/service/ACL perimeter; same-process Python is not a sandbox |
| A08 | Structural wrong scene/substituted asset and foreign synthetic scope are rejected before relevant Engine mutation | ALREADY VERIFIED IN ARE-A1/A2 | test_host_adapter.py:test_scene_substitution_before_engine, test_tenant_and_owner_boundaries_before_engine, asset/scene crosschecks |
| A09 | Missing/bad plan hash, nonpositive/Boolean revision and contradictory approval DTO rejected; draft/revoked host assertion blocks operations | ALREADY VERIFIED IN ARE-A1/A2 | AdapterContractTests; test_unapproved_rejected_before_engine, test_expected_approved_but_authoritative_unapproved |
| A10 | Stale expected revision/full context rejected before assignment; differing content digest detected | ALREADY VERIFIED IN ARE-A1/A2 | test_revision_mismatch_before_engine, test_digest_mismatch_before_engine, test_content_digest_change_detected |
| A11 | Previously approved assignment cannot reuse a changed plan digest/version; explicit revision requires new review | ALREADY VERIFIED IN ARE-A1/A2 | test_review_context_stale_blocks_even_replay, test_revise_assignment_requires_new_review; EngineTests.test_stale_scene |
| A12 | Two local opposing reviews: one success, one CONFLICT, one immutable event; exact duplicates do not append; changed payload same key conflicts | ALREADY VERIFIED IN ARE-A1/A2 | EngineTests.test_idempotency/test_concurrent_review; simulator opposing reviews and test_deterministic_report |
| A13 | Real authoritative plan/permission reservation survives competing changes across whole Engine call, consistent lock order, no commit under wrong policy epoch | REQUIRES INTEGRATION ENVIRONMENT | Fixture lock test does not prove deployed locking; P04 shows noncompliant provider can change state |
| A14 | Concurrent plan revision/assignment review/revise/readiness across actual worker boundaries never authorize stale render; second custodian fenced | REQUIRES INTEGRATION ENVIRONMENT | SQLite tests cover individual atomic calls, not host/use reservation |
| A15 | Host idempotency receipts namespace key to scope/operation/resource; duplicate non-review mutations do not create second revision; conflict stops retries | REQUIRES HOST IMPLEMENTATION | Engine reviews are idempotent; other operations need receipt/fencing policy |
| A16 | Host unavailable before authorization causes no engine effect; failure after engine commit returns indeterminate, not rollback; no cached fallback | REQUIRES INTEGRATION ENVIRONMENT | P03 is only safe synthetic exit-failure reproduction |
| A17 | DB locked/inaccessible/full/corrupt: sanitized unavailable outcome, bounded wait/queue, no ready fallback or leaked filesystem details | REQUIRES INTEGRATION ENVIRONMENT | SQLite timeout/error handling inspected; deployed disk/locking/durability tests missing |
| A18 | Crash at each host-intent, SQLite-commit, host-receipt and reply boundary reconciles without overwriting first decision; revoked retry denied | REQUIRES INTEGRATION ENVIRONMENT | Requires ledger, process fault injection and authority recovery policy |
| A19 | Reconciliation with unknown outcome and intervening revision either proves exact effect under exclusive custody or quarantines; never infers by ID alone | REQUIRES HOST IMPLEMENTATION | Host ledger and resource fence; current public getters expose only latest revisions |
| A20 | SQLite API snapshot/restore includes committed WAL data and preserves metadata/history; existing backup target rejected | ALREADY VERIFIED IN ARE-A1/A2 | EngineTests.test_snapshot_restore/test_wal_snapshot_includes_committed_rows; temporary CLI rerun |
| A21 | Cross-store restore/downgrade/rollback preserves scope, ledger watermarks and recovery generation; historical approvals cannot restore revoked permission | REQUIRES INTEGRATION ENVIRONMENT | Protected manifests, recovery runbook and paired snapshot drills |
| A22 | Audit proves host-derived identity issuer/subject/session or service actor, scope, policy epoch, correlation, request fingerprint and Engine receipt; privilege tampering externally detectable as specified | REQUIRES HOST IMPLEMENTATION | Engine history is application audit only; host provenance/retention mechanism absent |
| A23 | Missing local media, wrong size/hash or bytes changed after review block production use; check-to-use is on immutable version/read handle | REQUIRES INTEGRATION ENVIRONMENT | Engine intentionally does not open media or claim availability |
| A24 | Actual use at validity end is blocked; unknown/false permissions, territory/attribution/modification/distribution mismatches block declared eligibility | ALREADY VERIFIED IN ARE-A1/A2 | EngineTests.test_expired_by_date/test_restrictions/test_attribution and others; this verifies supplied-time metadata only |
| A25 | Missing migration row, malformed checksum metadata/JSON and DB contract parse failures consistently raise structured DomainError without modifying DB or leaking raw exceptions | REQUIRES ENGINE CHANGE | D01/P05/P08; add dedicated regression after explicit authorization |
| A26 | Missing required tables/columns, invalid stored contract content, wrong schema identity and weakened trigger definitions rejected by hardened validator; accepted v1 stays compatible | REQUIRES ENGINE CHANGE | D02/P06/P07; more semantic fixtures needed; no privileged tamper-proof claim |
| A27 | Host ignores caller-past evaluation/review timestamps for operative actions, links evidence to authenticated reviewer and enforces policy expiry/renewal; historical mode cannot grant use | REQUIRES HOST IMPLEMENTATION | Deterministic evaluator accepts arbitrary supplied time by design |
| A28 | README/package/wheel version labels agree without overwriting accepted artifacts | REQUIRES INTEGRATION ENVIRONMENT | D03 documentation correction/release review; no version change permitted in A3 |
| A29 | Deployment pins and tests local filesystem/journal mode, crash durability, retention, bounded concurrency and capacity assumptions | REQUIRES INTEGRATION ENVIRONMENT | No production topology/performance evidence |
| A30 | Actual future additive migration is fault-injected on copied data; unsupported downgrade rejected; compatible rollback uses validated backup/generation, not mutation of history | REQUIRES INTEGRATION ENVIRONMENT | Only schema migration 001 exists; initial rerun/unsupported version already tested, actual upgrade is planned |

A28's category denotes deployment/release documentation review, not a new Engine/API
requirement. ALREADY VERIFIED rows are limited to the stated standalone behaviors. Each
host/environment row remains planned even if A3 has a related synthetic probe.

## Execution rules and release gates

Use temporary scope-isolated component instances and synthetic evidence. Do not use real
jobs or production DBs. Start with existing regression suite and installed-wheel parity;
then the narrow engine validation tests (if authorized), then host unit tests with genuine
identity provenance verification, then isolated integration environment fault/recovery tests.
Do not access any live host merely because this plan names future tests.

Stop on unexpected unstructured exceptions, duplicate decisions, cross-scope access, ambiguous
success without a receipt, or missing authoritative state. Preserve failure artifacts and
quarantine affected resources. Authorization/outage failures must not default to readiness.

Deployment approval requires applicable P0 gaps closed and reviewed, affected P1 controls
implemented, all required tests actually executed with command/result artifacts, installed
binary provenance verified, restoration rehearsed, and independent review of identity and
custody. Sign-off must state deployment assumptions and residual risks. A3's document
completion or standalone tests cannot serve as production authorization.
