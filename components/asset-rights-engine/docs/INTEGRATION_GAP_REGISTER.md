# Integration gap register — ARE-A3

Target 1.1.0; no fixes made. P0 blocks production integration, P1 must be addressed before
the affected production operation is enabled, P2 is operational/documentation improvement.
Priority is a deployment risk judgment, not proof of an exploitable standalone vulnerability.
D entries are observed defects; G entries are missing capabilities/design limits. Relative
runtime file names below resolve to `src/asset_rights_engine/`. Evidence probe Pxx and test
Axx IDs are in A3_VERIFICATION_REPORT.md and INTEGRATION_ACCEPTANCE_PLAN.md respectively.

## Observed defects

| ID / priority | Risk and consequence | Boundary / source evidence | Recommended control | Required regression | Responsible layer | Dependency |
|---|---|---|---|---|---|---|
| D01 / P1 | Incomplete/corrupt DB metadata or JSON escapes structured domain error; service cannot consistently quarantine/report restoration failure | `engine.py:Engine._initialize` line 55 indexes a missing row; `validate_database` line 68 calls json.loads without translation; P05 TypeError, P08 JSONDecodeError | Explicit metadata presence/type checks and normalized parsing/contract failure DomainErrors; rollback/close unchanged | A25: missing migration row and malformed metadata/JSON fail with structured error, preserving bytes | engine | None; proposed next phase |
| D02 / P1 | `validate_database` falsely returns valid for missing required review_state table, so startup/restore health can pass an unusable DB | `engine.py:Engine.validate_database` 58–69; `schema.sql` line 5; P06 returns valid then get_rights DATABASE/no such table | Validate all required tables/columns/constraints and migration identity, not just physical integrity/version; cover validated contract content and references | A26: required table/column missing and invalid contract content rejected; good v1 remains compatible | engine | D01; G09 restore gate |
| D03 / P2 | README heading advertises 1.0.0 while metadata, install commands and accepted package are 1.1.0 | `README.md` line 3; `pyproject.toml` line 7; P10 | Correct heading in a separately authorized documentation update, not this read-only audit | A28 metadata/documentation consistency | operations | None |

D01 does not demonstrate approval bypass; it demonstrates error-contract failure under
malformed storage. D02 demonstrates incomplete validation, not cryptographic tamper detection.
P07 further proves that required trigger *names* are checked, not definitions; privileged
administrators can already remove every protection. Strengthening structural validation
would improve restore diagnostics, not make SQLite tamper-proof. No new standalone rights,
idempotency or transaction defect was demonstrated by the 94 passing baseline tests.

## Missing production capabilities and design limits

| ID / priority | Risk and consequence | Boundary / evidence | Recommended control | Tests | Responsible layer | Dependencies |
|---|---|---|---|---|---|---|
| G01 / P0 | Supplied reviewer IDs can impersonate a known fixture principal; no authenticated identity exists | `adapter_contracts.py:HostPrincipal`; `simulator.py:SyntheticHost.operation_context`; P02 | Host-session/service identity, trusted subject derivation, expiry/revocation; forbid fixture provider in production | A01,A02,A05 | host | Identity/offline policy decision |
| G02 / P0 | Cross-owner/tenant resource access if a provider relies only on caller scope; core has no durable ownership fields | `adapter_contracts.py:HostOperationRequest`; `schema.sql`; fixture-only registry | Trusted immutable ownership and scope-to-component mapping, operation/resource deny-by-default; explicit sharing policy | A03,A04,A06,A08 | host, adapter | G01,G07 |
| G03 / P0 | Raw library/CLI/alternate worker bypasses gateway and host approval policy | `engine.py:create_assignment/review_rights`; `host_adapter.py:AdapterGateway.__init__`; P01 | Exclusive custodian with process/filesystem isolation and restricted entry points; providers never chosen by clients | A07 | host, storage | G01,G02,G07 |
| G04 / P0 | Authorization/plan can change during operation; process-local DTO/Protocol cannot reserve external state | `host_adapter.py:HostAdapter.operation_context/_context`; `simulator.py` RLock; P04 | Stable authoritative host reservation across call, defined linearization/revocation/time semantics and lock order; fresh operative time | A05,A09–A14,A24 | host, adapter | G01,G02; human consistency decision |
| G05 / P0 | Host error/reply loss after SQLite commit yields ambiguous result, blind retry or wrong reversal | `engine.py:_connection/_review`; gateway context lifetime; P03 | Durable intent/fingerprint/receipt ledger, INDETERMINATE response, exact replay and fenced reconciliation; no implied distributed rollback | A15–A19 | host, adapter | G04,G06,G07 |
| G06 / P0 | History cannot prove decision-maker or correlate request to authenticated event | `contracts.py:ReviewEvent`; `schema.sql:review_history`; P02, field inventory | Host identity provenance, correlation ID, trusted time, instance/generation and Engine receipt sequence/hash in append-only host audit | A22,A27 | host, operations | G01,G05 |
| G07 / P0 | Valid wrong-scope/replaced storage or untrusted filesystem caller can bypass every boundary | `engine.py:__init__/create_database_snapshot`; no persisted scope/instance identity; P07 | Protected scope registry/private dirs, controlled service account/custody; snapshot manifest scope and external watermarks; refuse client paths | A04,A07,A20,A21 | storage, operations | Deployment custody decision |
| G08 / P1 | Expired rights appear eligible with caller-past time; absent/changed media remains rights-ready | `evaluator.py` lines 17–18; `engine.py:_readiness` warning; no media verification | Host-generated operative clock, final rights re-evaluation and immutable local media/digest check; historical results cannot authorize use | A23,A24,A27 | host, adapter | G04; media policy decision |
| G09 / P1 | Backup/rollback may resurrect obsolete host authorization or diverge from receipts; only initial migration shipped | `engine.py:_initialize/create_database_snapshot`; WAL tests; P05,P06 | Paired scope/checksum/generation/ledger-watermark manifests, quarantine restore, current authorization recheck and recovery drills; RPO/RTO | A20,A21,A25,A26 | storage, operations | D01,D02,G05,G07 |
| G10 / P1 | Threads-only tests do not verify process/distributed host locks or simultaneous assignment/review races | `tests/test_engine.py:test_concurrent_review`; fixture RLock; gateway separate get_assignment and Engine call | Single custodian per scope initially; controlled multi-process tests even for denied second-writer behavior; conflict/race fault tests | A12–A15 | adapter, operations | G03,G04,G05 |
| G11 / P1 | Host/DB outage, disk full and busy queues lack production response/health policy | `engine.py:_connection` timeout=10/SQLite errors; no service exists | Bounded queues/deadlines, sanitized errors, fail-closed health gating; reconcile unknown commits, never fallback-ready | A16–A19 | host, operations | D01,G05 |
| G12 / P1 | Non-review write retries have no generic receipt; only current revision getters make intervening-write recovery ambiguous | `engine.py:create_rights_revision/revise_assignment/get_rights/get_assignment` | Host exclusive resource fencing and intent receipts; conservative exact current-state match or quarantine. Add engine receipts/history API only if separately justified | A15,A18,A19 | host, adapter; engine only if extension chosen | G03,G05 |
| G13 / P1 | Evidence/licence strings can be false; explicit reviewer approval is still declared metadata | `engine.py:_review` evidence checks; `evaluator.py`; no evidence authenticity mechanism | Accountable evidence provenance/retention and manual review policy; never advertise ownership/legal proof | A22,A24,A27 | host, operations | G01,G06 |
| G14 / P1 | Audit/storage confidentiality and retention obligations unresolved; current SQLite is ordinary plaintext local storage | `schema.sql`, SECURITY.md privileged-access caveat | Choose retention, local ACL/encryption/key/backup custody policies and test them; do not claim current encryption | A20,A22 | storage, operations | G07; human policy decision |
| G15 / P2 | Capacity, journal mode and local/offline operational constraints are not deployment-tested | `engine.py:_connection`; no default WAL setup; no scale tests | Bound supported workload and pin tested filesystem/journal/durability settings; monitor locked/full/unavailable conditions | A17,A29 | operations | G10,G11 |

D03 is documentation drift, not a release version change request. G01–G15 are intentionally
unimplemented production capabilities or deployment limitations; their absence does not
retroactively claim ARE-A2 was a production adapter. Current security documentation mostly
states these limits accurately. Prior acceptance is standalone acceptance, not deployment approval.

## Order and owners

Smallest independent source repair: D01/D02 with dedicated temporary-database regression
checks. No host inspection or schema/API change is needed for the demonstrated defects.
Production design decisions next: custody/partitioning (G02/G07), identity (G01), reservation
semantics (G04), receipts/provenance (G05/G06). Remaining controls depend on these foundations.
Until all applicable P0s are implemented and their environment tests pass, production
integration remains blocked. See NEXT_PHASE_PROPOSAL.md; no work there is started in A3.
