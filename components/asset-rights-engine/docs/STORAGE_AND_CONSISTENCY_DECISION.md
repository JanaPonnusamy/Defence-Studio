# Storage and consistency decision — ARE-A3

Status: proposed, not implemented or approved for production. Evidence target: 1.1.0,
schema v1. This review has no observed host deployment requirements beyond a future
local/offline video-production integration. No host database was inspected.

## Options and decision boundaries

Option A describes database custody: a trusted host-side custodian is the only permitted
Engine caller and owns component SQLite. Option B describes an authority split: host
owns identity, authorization, scenes and authoritative approvals while component records
are reconciled. These options overlap for identity and scene authority; neither means
copying a host schema into Engine. For comparison, B means independently mutable host
approval state plus component records requiring explicit reconciliation, rather than
engine review events being the single authority for declared-rights review decisions.
Option C would replace the persistence/topology (e.g. another database/distributed engine),
which is unsupported by the current implementation and not justified for this first phase.

| Dimension | A: dedicated component SQLite custodian | B: dual authoritative approval workflows + reconciliation | C: alternative/distributed persistence |
|---|---|---|---|
| Security isolation | Explicit service/process and directory ACL boundary; Engine itself has no authentication | Host can centralize policy, but mappings/reconciliation credentials expand boundary | New access-control/storage behavior would require implementation and audit |
| Tenant/owner separation | One dedicated Engine per (host, tenant, owner) initially; trusted registry only | Host mappings must fence every mirror and restore; schema v1 has no tenant columns | Could add native partitioning, currently absent |
| Transactions | Engine writes atomic within SQLite; host receipt remains separate | Two sources of mutable approvals and no common transaction | Requires new transaction model and compatibility work |
| Concurrency | Single custodian queue/lock; SQLite BEGIN IMMEDIATE; current fixture lock is process-local | Cross-host version reservations, ordering and retries required | Multi-worker consistency entirely unverified |
| Partial failure | Host intent/receipt ledger + engine authoritative result; no blind reversal | Explicit conflict/merge policy for divergent approval authorities | New consensus/outbox/receipt design still required |
| Backup/restore | SQLite backup API plus scope/host-ledger manifest and watermark | Coordinated recovery of both authorities needed | New backup tooling and drills |
| Migration | Keep schema v1; future additive upgrades only after rehearsal | Host and component formats must be coordinated | Database rewrite/import/migration risk |
| Operational support | Small local service; protected files, serialized writes, bounded queue | Reconciliation jobs and operational ownership on both sides | More services and expertise |
| Local/offline | Fits existing standard-library package and no runtime network dependence | Possible if host authority/ledger is local and trustworthy | No requirement justifies extra distributed infrastructure |
| Multiple processes/instances | Prohibit shared active scope writers and network-filesystem SQLite initially | Must fence stale instances and synchronize policy/plan state | Not supported today |

## Recommendation

Choose **Option A for the first production topology**, retaining host authority over
sessions, policy, job/scene plans and final use authorization. Engine is authoritative for
its declared-rights/assignment revisions and review events; the host ledger records receipts,
not a competing copy of those decisions. This avoids dual mutable review authorities.
Host plan approval always remains host authority, even under A. This is a conditional
architecture recommendation, not evidence that a trusted host service exists.

Assumptions: one local machine, local protected filesystem, one custodian process per
scope, no arbitrary untrusted plugins in that process, bounded workload and no shared
cross-owner assets at launch. A controlled host restart must be possible. If deployment
requires multi-machine active writers, network-share SQLite or immediate cross-instance
revocation, reject these assumptions and reopen the decision before implementation.

## Authoritative data and consistent operation

1. Host registry binds (host, tenant, owner) to a configured component instance and private
   data directory. Client metadata never chooses a database path. A scope-specific component
   instance ID/recovery generation is host-managed metadata; no such Engine column exists.
2. Trusted host authenticates subject and authorizes the operation/resources. Resolve stable
   job/scene identity and the current approved plan; construct DTOs from that result.
3. Persist a host operation intent/fingerprint, then enter an authorization/plan reservation
   with a consistent lock order. Recheck current identity/policy inside it before Engine use.
4. Engine commits one operation inside its SQLite transaction. Persist host receipt containing
   instance/generation, immutable revision/hash and review sequence where applicable.
5. Only a reconciled receipt is a completed host workflow. A reply/context-exit failure after
   SQLite commit is INDETERMINATE, not proof that the engine rolled back (probe P03).

A host database transaction cannot roll back a committed SQLite transaction. Do not create
that illusion with a context manager. For the initial topology, unresolved writes block later
writes to that resource until reconciled. Review retries reuse exact keys/payloads and recheck
host authorization; revoked callers do not retrieve prior results through an unauthorized retry.
A privileged recovery worker may reconcile under its separately audited authority.

Non-review writes have no generic Engine request receipt. register_asset has exact metadata
replay; create_assignment returns CONFLICT when an ID exists; revision methods use expected
current revision and cannot blindly repeat an increment. Under exclusive writes, the host
ledger may compare expected+1 and the exact current payload/digest to establish an ambiguous
operation's effect. If later writes intervened or evidence is ambiguous, quarantine; do not
infer success from ID existence alone. There is no public historical revision getter beyond
current views, so broader automatic reconciliation would need a separately reviewed extension.

## Backup, restore and migration policy

Use create_database_snapshot, not a raw copy of a live WAL database. SQLite's backup API
already includes committed WAL data (verified test). Engine does not enable WAL by default;
connection timeout is 10 seconds. Pin/test deployment journal, durability, locking and filesystem
behavior explicitly before operation. Do not claim a multi-file host/engine snapshot is atomic.

Pause new work and drain/reconcile pending intents; produce a completed component snapshot
and record a matching scope, instance/generation, wheel hash, schema version, host-ledger
watermark and checksum in a protected backup manifest. Retain identity/policy audit provenance
separately. These controls are proposed, not currently implemented. Set RPO/RTO and retention
through human operational decisions. Encryption/key custody is also unresolved.

Restore into a quarantined temporary/dedicated location. Verify manifest scope/generation,
physical integrity, required schema/revisions/triggers and known validation defects D01/D02
before trusting results. Reconcile receipts and increment the host recovery generation to
prevent silently continuing old queued work. Recheck current host approval/permission/rights
validity and media; restoring old APPROVED rows never restores host authorization. Switch the
registry to restored storage only after a controlled recovery decision. Keep the original
snapshot immutable and rollback by swapping a validated compatible deployment generation.

No new migration is provided in A3. Schema 001 is immutable. Future upgrades need additive
numbered migrations, dry-run on copies, fault injection and compatible-binary rollback. Never
run an older binary against an unsupported schema or subtract historic review decisions.

## Unresolved decisions

Human approval is required for custody/topology, per-owner partitioning/sharing semantics,
identity provider and offline-session policy, admission-time versus strict commit-time
revocation semantics, lock/deadline rules, RPO/RTO, backup custody and audit retention,
recovery-worker authority, media storage guarantees and whether performance needs exceed
local SQLite. D01/D02 validation fixes precede a production restore gate. No choice here
authorizes host access or production implementation.
