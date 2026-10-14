# Phase 5D — Decision Register: ARE 1.1.0 ↔ Defence Studio compatibility decisions

Resolves the nine "decision required" items Phase 5C's `COMPATIBILITY.md` identified and
froze without resolving. This document is the design freeze: it records a recommended
decision and its rationale for each item, but **does not implement any of them**. No
product code, migration, or dependency was added to produce this document.

Every entry below was checked against the current working tree (not re-derived from the
Phase 5C doc text alone): `backend/defence_studio/contracts/assets.py`,
`backend/defence_studio/services/assets.py`, and
`E:\DefenceStudio-Components\asset-rights-engine\src\asset_rights_engine\{contracts.py,engine.py,evaluator.py,adapter_contracts.py,host_adapter.py}`
were re-read for this phase. One correction to the Phase 5C record surfaced during this
re-check and is noted in Decision 5.

Status legend: **BLOCKED PENDING APPROVAL** — a product/security decision this document
recommends but does not have standing authorization to implement. Every entry in this
register is BLOCKED PENDING APPROVAL; none may be implemented from this document alone.

---

## Decision 1 — Asset identity model

**Current Defence Studio behavior.** `asset_id` is computed server-side as
`asset_digest(body)` — the SHA-256 of the canonical, id-less descriptor body
(`contracts/assets.py::asset_digest`, called from `services/assets.py:131`
`register_asset`). Two byte-identical descriptors collapse to the same `asset_id`; any
field edit (even a non-semantic one) produces a different `asset_id`, i.e. there is no
persistent identity across an edit — editing *is* creating a new asset in DS's model.

**Current ARE 1.1.0 behavior.** `asset_id` is a caller-supplied free identifier
(`contracts.py::AssetDescriptor.asset_id`, validated only as an `identifier()` pattern, no
digest relationship to content). `Engine.register_asset` (`engine.py:71-78`) requires a
**second** registration under the same `asset_id` to have byte-identical `canonical(asset)`
content (`require(existing[0]==canonical(asset), "Asset ID already registered with
different metadata")`) — i.e. ARE treats `asset_id` as a stable identity that content must
not silently diverge from, the opposite direction of DS's content-derives-identity model.

**Exact source files/functions.** DS: `contracts/assets.py::asset_digest`,
`services/assets.py::AssetService.register_asset` (L114-175). ARE:
`contracts.py::AssetDescriptor` (L52-85), `engine.py::Engine.register_asset` (L71-78).

**Available options.**
- **(a)** Keep DS's content-addressed ID as DS's own identity; synthesize an ARE
  `asset_id` as a stable, separate identifier (e.g. a server-generated UUID stored
  alongside the DS row) that does not change when the DS content hash changes on edit.
- **(b)** Use the DS content hash directly as the ARE `asset_id`. This makes an edited
  asset register as a *new* ARE asset under a new ID every time DS's descriptor changes,
  leaving the old ARE asset_id's rights/assignment history orphaned under a stale ID.
- **(c)** Treat DS's asset row as authoritative and never register per-edit content with
  ARE at all — only call `Engine.register_asset` once, at first creation, with a
  separately minted stable ID, and treat all subsequent DS-side content changes as DS-only
  (not reflected into ARE's `assets` table at all, only into ARE's rights/assignment
  tables which reference the asset by that stable ID).

**Recommended decision: (a).** Mint one stable, server-generated identifier per DS asset
at first registration (not derived from content), store it as a new column alongside the
existing content-addressed `asset_id` (DS's own identity is not replaced), and use the
stable identifier as the ARE-facing `asset_id`. **Rationale:** this preserves DS's
existing content-addressed dedup/idempotency behavior unchanged (zero regression risk to
the 22 passing asset-service tests) while giving ARE the persistent identity it structurally
requires. Option (b) would make every edit to an asset orphan its entire ARE-side rights
history, which is a worse user-facing outcome than adding one column.

**Security and data-integrity implications.** The stable ID must never be derived from or
influenced by client input (to avoid collision/takeover across jobs); it must be minted
server-side (e.g. `uuid4()`) at the same point `asset_id` is first computed, inside the
same `BEGIN IMMEDIATE` transaction, so a partial write cannot leave an asset with a content
hash but no stable ID. Because the stable ID crosses two separate SQLite files (DS's shared
DB, ARE's per-job file), it must be treated as sensitive-adjacent — knowledge of it should
not itself grant access (ownership checks remain the actual gate, as today).

**Backward-compatibility impact.** Purely additive on the DS side (one new column,
nullable until the real adapter backfills it); the existing `asset_id` field, its contract
validation, and every existing test remain unchanged. No existing API response shape
changes unless/until the adapter is built.

**Required tests (for the future adapter, not written now).** A test that editing an
asset's rights (which changes DS's content hash per `set_rights`) does **not** change the
stable ARE-facing identifier; a test that two distinct DS assets never collide on a stable
ID; a test that the stable ID is never present in the initial descriptor without having
been minted server-side.

**Blocked pending explicit approval.** Yes — adding the new column itself is a schema
change and out of scope for Phase 5D per the task's explicit "no DB migration" restriction.

---

## Decision 2 — Provenance-requiredness mismatch

**Current Defence Studio behavior.** `AssetProvenanceV1.source_url` and
`documented_origin` are both `Optional` (`contracts/assets.py:237,241`); a descriptor with
**neither** is valid at the contract level, and `evaluate_effective_rights` explicitly
handles that case by returning `UNVERIFIED` rather than rejecting registration
(`contracts/assets.py:385-389`: `has_provenance` check). Registration never fails for
missing provenance — only the derived rights verdict is affected.

**Current ARE 1.1.0 behavior.** `AssetDescriptor.__post_init__` requires
`provenance`/`original_source` to be non-empty, stripped strings for *every* asset
(`contracts.py:75`: `for v in (self.display_name,self.provenance,self.original_source):
require(isinstance(v,str) and bool(v.strip()),"Required text missing")`). There is no
"unknown provenance" representation in ARE's `AssetDescriptor` at all — registration
itself fails closed (raises `DomainError`) rather than succeeding with an honest-unknown
marker.

**Exact source files/functions.** DS: `contracts/assets.py::AssetProvenanceV1` (L229-247),
`evaluate_effective_rights` (L360-411). ARE: `contracts.py::AssetDescriptor.__post_init__`
(L70-85).

**Available options.**
- **(a)** Refuse to register with ARE at all when DS's provenance is empty; the asset
  remains DS-only (no ARE-side rights history) until provenance is supplied.
- **(b)** Synthesize a placeholder provenance string (e.g. `"unknown"`) to satisfy ARE's
  non-empty check.
- **(c)** Defer ARE registration until the first `set_rights` call, and require provenance
  to be non-empty as a **precondition of registering with ARE**, independent of DS's own
  (looser) registration rule — i.e. DS registration and ARE registration become two
  separate, independently-gated events rather than one combined operation.

**Recommended decision: (c), which subsumes (a).** Defence Studio's own registration
continues exactly as today (provenance optional, `UNVERIFIED` the honest consequence). A
separate, later step — "register this asset's rights with ARE" — becomes a distinct
operation gated on provenance being present, returning a clear, specific error
(`AssetValidationError`, not a generic 500) if attempted while provenance is still empty.
**Rationale: option (b) is explicitly prohibited by the task's own instruction** ("Do not
equate declared rights with legal ownership... do not treat a... locally stored file as
proof of a license") and by this phase's carried-forward mandate from Phase 5C's
`INTEGRATION_SPEC.md` ("never substitute a fabricated value") — synthesizing
`"unknown"` as ARE's required `provenance` string would misrepresent DS's honest
"we don't know" as a declared fact, which is exactly the kind of dishonesty both systems'
own documentation explicitly rejects. Deferring ARE registration (c) is the only option
that adds zero dishonesty at either layer.

**Security and data-integrity implications.** An asset that can never acquire provenance
can also never be registered with ARE and therefore can never reach ARE-evaluated
`CLEAR`/`RESTRICTED` rights — it remains permanently gated at DS's own `UNVERIFIED`
verdict, which is the correct fail-closed outcome (consistent with DS's existing rule that
unknown provenance forces `UNVERIFIED`).

**Backward-compatibility impact.** None on existing DS behavior; this only affects the
not-yet-built ARE registration step.

**Required tests (future).** Attempting ARE registration with empty provenance returns a
specific, documented error (not a crash); supplying provenance later and retrying succeeds
without having corrupted any DS-side state from the earlier failed attempt (idempotent
retry).

**Blocked pending explicit approval.** Yes — this decision shapes the adapter's error
contract and must be approved before any adapter code exists.

---

## Decision 3 — Rights-state ownership split

**Current Defence Studio behavior.** `RightsRecordV1` (`contracts/assets.py:250-287`) is
pure content with no state field. Lifecycle lives entirely outside it: `AssetReviewStatus`
on the `assets` row, plus the separate `asset_reviews` (current decision) /
`asset_review_history` (append-only) tables (`database.py:348-376`,
`services/assets.py::review_asset` L418-503). Rights content is keyed by content hash
(`asset_rights` table, `(job_id, asset_id, rights_sha256)`), not by a monotonic revision
counter.

**Current ARE 1.1.0 behavior.** `RightsRecord.state: RightsState` is a field **on the
rights content itself** (`contracts.py:88-115`); `Engine.create_rights_revision` rejects a
persisted `state==APPROVED` input (`engine.py:111`: `require(record.state!=RightsState
.APPROVED,"Approval requires review_rights")`) and approval is a *separate* call
(`review_rights`) that writes to `review_history`/`review_state` tables keyed by a
monotonic integer `revision` (`engine.py:109-123`). ARE's `_view` method
(`engine.py:90-101`) derives the effective status by joining the latest revision against
`review_state`, functionally similar to DS's "content + separate decision" split — **the
structural difference is smaller than Phase 5C's wording suggested**: ARE *also*
separates "what was reviewed" from "the review decision," it just keys the content side by
an integer revision instead of a content hash.

**Exact source files/functions.** DS: `contracts/assets.py::RightsRecordV1`,
`database.py` asset table DDL (L294-376), `services/assets.py::set_rights` (L211-266),
`review_asset` (L418-503). ARE: `contracts.py::RightsRecord`/`RightsState`,
`engine.py::create_rights_revision` (L109-119), `_review`/`review_rights` (L178-205).

**Correction to Phase 5C's record.** `COMPATIBILITY.md` row 2.1 described this as "ARE
couples state into the rights record itself" without noting that ARE's own `_view` logic
immediately re-derives the *effective* status from a separate `review_state` table at read
time (`engine.py:96-97`) rather than trusting the stored `state` field as final — this is
functionally the same "content-is-one-thing, decision-is-another" pattern DS uses, just
with a different physical key (integer revision vs. content hash). The *harder* mismatch
is the key type (revision counter vs. hash), not the state-ownership structure itself.

**Available options.**
- **(a)** DS's `asset_rights`/`asset_reviews` tables remain fully authoritative; ARE is
  used only as a stateless/pure evaluator fed DS's current rights content and review
  decision on each call (never persisting rights state in ARE's own tables at all).
- **(b)** ARE's rights tables become authoritative; DS's `asset_rights`/`asset_reviews`
  become a read-through cache or are retired, with DS's `set_rights`/`review_asset`
  becoming thin wrappers around `Engine.create_rights_revision`/`review_rights`.
- **(c)** Both persist independently, with DS's hash-keyed row as the source of truth for
  DS's own UI/export and ARE's revision-keyed row maintained in parallel purely to use
  ARE's `evaluate_effective_rights`/readiness-blocker-list output as an *additional*,
  non-authoritative, informational view.

**Recommended decision: (a).** Do not persist rights state in ARE's own tables at all;
call ARE's **pure** evaluator path only (note: ARE's own docs recommend `Engine
.evaluate_effective_rights`, not the raw pure function, for an "untrusted caller" —
but Defence Studio's adapter module is itself the trusted caller here, so it is the
component Phase 5C's `INTEGRATION_SPEC.md` already designated as the sole trust boundary;
it may legitimately choose to call ARE's pure `evaluator.evaluate_effective_rights`
directly without persisting anything to ARE's own SQLite file, since DS already owns a
complete, durable, audited persistence layer of its own). **Rationale:** maintaining two
independently-authoritative persistence layers for the same fact (option (c)) creates
exactly the "two divergent explanations" risk Phase 5C's `COMPATIBILITY.md` §9 flagged for
readiness blockers; option (b) discards DS's already-tested, already-working
`asset_reviews`/`asset_review_history` ledger for no documented benefit. Option (a) gets
ARE's richer blocker/warning-code output (Decision 5) without taking on a second
source-of-truth to keep synchronized.

**Security and data-integrity implications.** If (a) is adopted, ARE's `Engine
.register_asset`/`create_rights_revision`/`review_rights` (the stateful, persisting API)
are **never called** — only `evaluator.evaluate_effective_rights(record, context, ...)`
(the pure function) is. This has a direct consequence for Decision 1 and the tenant-
isolation decision (Decision 7): if ARE never persists anything, there is no per-job ARE
SQLite file to isolate at all, which **changes** Phase 5C's `INTEGRATION_SPEC.md`
assumption of "one `Engine` per job" into "no `Engine`/no per-job SQLite file needed for
this call shape." This is flagged explicitly in the Tenant Isolation section below as a
decision that depends on this one.

**Backward-compatibility impact.** None on DS's existing tables; this decision, if
adopted, means the adapter never needs ARE's persistence or migration story at all for
rights evaluation, substantially simplifying Decision 7 (and the whole Option A design) —
but see Decision 5 for why some of ARE's capability (readiness blockers specifically) still
needs the stateful `Engine.evaluate_assignment_readiness`, not just the pure evaluator.

**Required tests (future).** A test proving the adapter never writes to the ARE-side
`rights_revisions`/`review_history` tables when only the pure evaluator path is used (i.e.
ARE's own SQLite file, if created at all for other reasons, never grows from a rights-only
call); a test that DS's existing rights-invalidate-on-change rule
(`test_setting_rights_invalidates_prior_approval`) continues to pass unmodified, since
rights persistence stays entirely DS-owned under this decision.

**Blocked pending explicit approval.** Yes — this is the single most consequential
decision in this register; it determines whether the adapter needs ARE's stateful
`Engine` at all for the rights axis, or only for the assignment-readiness axis (see
Decision 5).

---

## Decision 4 — Missing modification/distribution permission fields in Defence Studio

**Current Defence Studio behavior.** `RightsRecordV1` has `commercial_use: CommercialUse`
only; there is no structured field for "may this asset be modified" or "may this asset be
redistributed" — only free-text `restrictions: list[str]` (`contracts/assets.py:269`),
which `evaluate_effective_rights` never inspects at all (confirmed: `restrictions` does
not appear anywhere in `evaluate_effective_rights`'s body, L360-411 — it is stored but not
evaluated).

**Current ARE 1.1.0 behavior.** `RightsRecord` has three independent tri-state Booleans:
`commercial`, `modification`, `distribution` (`contracts.py:94`), each individually
checked in `evaluator.evaluate_effective_rights` against the corresponding `UseContext`
flag (`evaluator.py:12-13`: `for field in ("commercial","modification","distribution"):
if getattr(context,field) and getattr(record,field) is not True:
blockers.append(field.upper()+"_NOT_PERMITTED")`).

**Exact source files/functions.** DS: `contracts/assets.py::RightsRecordV1` (L250-287).
ARE: `contracts.py::RightsRecord`, `evaluator.py::evaluate_effective_rights` (L12-13).

**Available options.**
- **(a)** Extend DS's own `RightsRecordV1` with `modification_use`/`distribution_use:
  CommercialUse`-shaped tri-state fields, mirroring `commercial_use`'s existing pattern.
  This is a genuine schema/contract change.
  **This is a schema change and is documented here as a separate proposal, not
  implemented in Phase 5D** per the task's explicit instruction.
- **(b)** Leave DS's contract as-is; when calling ARE's evaluator, always pass
  `UseContext(modification=False, distribution=False)` — i.e. never request a permission
  DS has no way to know the answer to, treating "unrequested" as never a blocker
  (`evaluator.py` only flags `*_NOT_PERMITTED` when the *context* requests `True` and the
  record doesn't permit it — requesting `False` never blocks).
- **(c)** Parse DS's free-text `restrictions` list for known phrases (e.g. "no derivative
  works") and map them heuristically into ARE's Boolean fields.

**Recommended decision: (b) now; (a) as a separate, explicitly-proposed future schema
change.** **Rationale:** (c) is explicitly the kind of "invent an equivalence between
concepts whose meanings differ" this phase's own instruction forbids — free text was
never structured data and heuristic parsing would fabricate a permission DS never actually
recorded. (b) costs nothing and is honest: Defence Studio today has no product requirement
for per-asset modification/distribution gating (no caller ever asks for it), so passing
`False` for both simply means ARE never blocks on an axis DS doesn't yet use — it does not
misrepresent anything, because DS is not currently claiming to need or support that axis.
If and when a product requirement for modification/distribution restrictions emerges,
(a) should be proposed and scoped as its own schema-change phase, including a migration.

**Security and data-integrity implications.** Under (b), an asset DS considers safe for
`web`/`broadcast` could still be modified or redistributed by a downstream consumer in a
way its rights holder never actually authorized — but this is not a *new* gap introduced
by this decision; it is DS's **existing** gap (the free-text `restrictions` field is
already unenforced today, before any ARE integration). Choosing (b) does not make this
worse; it also does not make it better. This should be flagged to the product owner as a
standing, pre-existing limitation, not something ARE integration created.

**Backward-compatibility impact.** (b) is fully backward compatible (no schema change).
(a), if later approved, would be additive (new nullable/defaulted columns) consistent with
every prior DS schema bump's convention.

**Required tests (future).** A test confirming that calling ARE's evaluator with
`modification=False, distribution=False` never produces a `MODIFICATION_NOT_PERMITTED`/
`DISTRIBUTION_NOT_PERMITTED` blocker regardless of the DS rights record's content (proving
(b) is inert, not silently restrictive).

**Blocked pending explicit approval.** The immediate decision (b) requires no
implementation and is low-risk; however it still requires explicit product-owner sign-off
because it is a decision about what Defence Studio is and isn't claiming to enforce, which
this document is not authorized to decide unilaterally. (a) is explicitly deferred to a
separate future proposal and is not to be implemented from this register.

---

## Decision 5 — Effective-rights output granularity

**Current Defence Studio behavior.** `evaluate_effective_rights` returns one of 5 enum
values (`CLEAR`/`RESTRICTED`/`UNVERIFIED`/`EXPIRED`/`BLOCKED`,
`contracts/assets.py:165-173`). `assignment_readiness` (`services/assets.py:516-563`)
separately builds its own ad-hoc blocker strings per scene by inspecting that single enum
value plus review/assignment state — it does not consume any structured blocker-code list,
because DS's evaluator never returns one.

**Current ARE 1.1.0 behavior.** `evaluator.evaluate_effective_rights` returns an
`Evaluation` with `eligible: bool`, a tuple of named blocker codes (e.g.
`RIGHTS_NOT_APPROVED`, `INCOMPLETE_RIGHTS_EVIDENCE`, `COMMERCIAL_NOT_PERMITTED`,
`ATTRIBUTION_MISSING_OR_MISMATCHED`, `TERRITORY_RESTRICTED`, `RIGHTS_NOT_YET_VALID`,
`RIGHTS_EXPIRED` — `evaluator.py:7-18`), `warnings` (always empty from the pure evaluator;
`Engine._readiness`, L172, is the one that always appends
`"MEDIA_AVAILABILITY_UNVERIFIED"`), and `review_actions`. `Engine._readiness`
(`engine.py:156-172`) additionally layers on `STALE_RIGHTS_REVISION`,
`STALE_SCENE_BINDING`, `USE_CONTEXT_CHANGED`, `ASSIGNMENT_BLOCKED`,
`ASSIGNMENT_NOT_APPROVED`, `ASSIGNMENT_REVIEW_AFTER_EVALUATION` — a materially richer,
named-code vocabulary than DS's single enum.

**Exact source files/functions.** DS: `contracts/assets.py::EffectiveRights`/
`evaluate_effective_rights` (L165-173, L360-411), `services/assets.py
::assignment_readiness` (L516-563). ARE: `evaluator.py::evaluate_effective_rights`,
`engine.py::Engine._readiness`/`evaluate_assignment_readiness` (L156-176).

**Available options.**
- **(a)** Keep DS's single-enum verdict as the sole externally-visible rights status;
  never surface ARE's blocker-code list anywhere.
- **(b)** Replace DS's ad-hoc readiness-blocker strings with ARE's structured blocker
  codes, exposed as a new (additive) field alongside the existing enum/free-text — i.e.
  DS's enum verdict remains the primary gate (unblocks nothing new), but the readiness
  endpoint's response grows a `blocker_codes: list[str]` field sourced from ARE.
- **(c)** Fully replace DS's own blocker-string construction with calls into ARE's
  `Engine.evaluate_assignment_readiness`, discarding DS's own ad-hoc string-building
  entirely.

**Recommended decision: (b).** **Rationale:** DS's existing enum verdict is already
depended upon by the entire assignment/approval gating logic
(`assign_asset`/`review_asset`'s fail-closed checks against `EffectiveRights.CLEAR`/
`RESTRICTED`) and 22 passing tests — replacing it (option (c)) risks a behavior change in
exactly the gating logic this document is required not to weaken. Adding ARE's richer
blocker codes as a **second, additive, read-only** field gives human reviewers (and a
future UI) a more specific "why" without touching the enforcement path at all. This also
directly uses ARE's `Engine.evaluate_assignment_readiness` (the **stateful** method, not
the pure evaluator) — which means, contrary to Decision 3's "no ARE persistence needed"
conclusion for rights-only evaluation, **readiness specifically does need a real,
persisted ARE `Engine` instance**, because `evaluate_assignment_readiness` reads
`review_history`/`assignment_revisions` rows that only exist if `create_assignment`/
`review_assignment` were actually called against that `Engine`. This is noted explicitly
because it means Decision 3 and Decision 5 are **not independent**: choosing "pure
evaluator only, no ARE persistence" for rights (Decision 3) while also wanting ARE's
richer readiness blocker codes (Decision 5) requires maintaining a parallel,
ARE-persisted assignment/review history *in addition to* DS's own — reintroducing the
dual-source-of-truth risk Decision 3 specifically tried to avoid. **This dependency must
be resolved together, not separately**, before any adapter is built.

**Security and data-integrity implications.** None beyond what Decision 3 already
analyzes — adding a read-only informational field changes no gate.

**Backward-compatibility impact.** Additive only (a new optional response field); no
existing field, enum value, or test assertion changes.

**Required tests (future).** A test that the new `blocker_codes` field never changes
`ready`/the enum verdict's gating behavior even when present; a test that its absence
(e.g. ARE unavailable) does not break the existing readiness response shape.

**Blocked pending explicit approval.** Yes, and specifically **contingent on resolving
the Decision 3 / Decision 5 dependency above** — approval of Decision 5 without first
deciding whether ARE's `Engine` persists anything at all would leave the adapter design
internally inconsistent.

---

## Decision 6 — Role versus target-use semantics

**Current Defence Studio behavior.** `AssetAssignmentV1.target_use: PermittedUse | None`
(`broadcast`/`web`/`derivative`/`thumbnail`, `contracts/assets.py:147-151,344`) describes
*how* an assignment's asset may be used; there is no "role" concept (e.g. "primary visual"
vs. "background b-roll") anywhere in the assignment model.

**Current ARE 1.1.0 behavior.** `AssignmentDescriptor.role: str`
(`contracts.py:191-206`) is a free identifier with no fixed vocabulary, validated only as
`identifier()`; `UseContext` (separately) carries `commercial`/`modification`/
`distribution`/`territory`/`attribution_text` — notably, **ARE's `UseContext` has no
"target use" field equivalent to DS's `broadcast`/`web`/`derivative`/`thumbnail`
vocabulary at all.** Role and use-context are genuinely two different axes in ARE, neither
of which maps directly onto DS's single `target_use` field.

**Exact source files/functions.** DS: `contracts/assets.py::PermittedUse`/
`AssetAssignmentV1` (L147-151, L318-354). ARE: `contracts.py::AssignmentDescriptor`/
`UseContext` (L117-129, L190-206).

**Available options.**
- **(a)** Map DS's `target_use` to ARE's `role` (lossy — loses the ability to have one
  role with multiple valid target uses, and loses `UseContext`'s commercial/modification/
  distribution dimensions, which DS's `target_use` was never meant to carry).
- **(b)** Treat the two as orthogonal: synthesize a fixed, deterministic `role` string per
  assignment (e.g. always `"primary"`, since DS does not currently have a multi-role-per-
  scene concept), and separately construct `UseContext` from DS's `effective_rights`
  derivation inputs (`for_commercial`, and `False`/`False` for modification/distribution
  per Decision 4), leaving `target_use` as a DS-only concept with no ARE equivalent at all.
- **(c)** Do not call any ARE API that requires a `role` parameter (i.e. restrict the
  adapter to rights-evaluation calls only, never `create_assignment`/`revise_assignment`),
  sidestepping the mapping question entirely.

**Recommended decision: (b).** **Rationale:** (a) actively invents an equivalence between
concepts the Phase 5C compatibility review already identified as *not* the same axis —
exactly what this phase's own instruction forbids. (b) is honest: it acknowledges `role`
is an ARE-only concept DS doesn't have a real answer for, and supplies a safe, inert
constant rather than fabricating meaning. (c) is the simplest and lowest-risk option and
should be the default unless/until there is a concrete product need for ARE's
assignment-persistence APIs specifically (see the Decision 3/5 dependency above — if
Decision 3 resolves toward "pure evaluator only," option (c) becomes the natural
consequence for the assignment axis too, and (b) only becomes relevant if Decision 5's
richer readiness blockers are pursued).

**Security and data-integrity implications.** None — `role` has no bearing on any
authorization or rights-gating decision in either system.

**Backward-compatibility impact.** None; DS's `target_use` field and its existing
validation/behavior are unaffected either way.

**Required tests (future).** If (b) is adopted: a test that the synthesized `role`
constant never varies per call (deterministic) and that `target_use`'s existing DS-side
enforcement (`assign_asset`'s `tu = PermittedUse(target_use)` handling) is unaffected by
whatever is passed to ARE's `role` parameter.

**Blocked pending explicit approval.** Yes, and deferred in practice until the Decision
3/5 dependency is resolved (see Decision 5's note) — if the adapter never calls
`create_assignment`, this decision has no code to apply to yet.

---

## Decision 7 — Tenant-isolation model

See the dedicated **Tenant Isolation — Mandatory Security Decision** section below. This
decision is the task's own mandatory security decision and is analyzed there in full
depth (Options T1/T2/T3) rather than in this register's shorter format, because of its
scope and because the task requires independently verifying ARE's connection lifecycle
before any recommendation — done in that section via a direct re-read of `engine.py`.

**Blocked pending explicit approval.** Yes.

---

## Decision 8 — Review-decision vocabulary

**Current Defence Studio behavior.** `decision` accepts 4 values:
`{APPROVED, REJECTED, RESTRICTED, CHANGES_REQUESTED}` (`services/assets.py:83`
`REVIEW_DECISIONS`, enforced in `review_asset` L429-430). `RESTRICTED` maps to
`AssetReviewStatus.RESTRICTED` (usable only under recorded constraints);
`CHANGES_REQUESTED` maps back to `PENDING_REVIEW` (`services/assets.py:474-477`
`status_map`) — i.e. it is not a terminal rejection, it is a request to redo the
submission.

**Current ARE 1.1.0 behavior.** `ReviewRequest.decision` is restricted to exactly
`"APPROVED"`/`"REJECTED"` (`contracts.py:155`:
`require(self.decision in ("APPROVED","REJECTED"),"Invalid review decision")`). There is
no ARE-side representation of "approved with restrictions" or "needs changes, not
rejected."

**Exact source files/functions.** DS: `services/assets.py::REVIEW_DECISIONS` (L83),
`review_asset` (L418-503). ARE: `contracts.py::ReviewRequest.__post_init__` (L153-157).

**Available options.**
- **(a)** Map DS's `RESTRICTED` → ARE `"APPROVED"` (since `RESTRICTED` *is* a form of
  approval-with-constraints) and `CHANGES_REQUESTED` → simply never call ARE's
  `review_rights`/`review_assignment` for that decision at all (since it is not a terminal
  state and nothing in ARE needs to know about an in-progress, non-final review round).
- **(b)** Map both `RESTRICTED` and `CHANGES_REQUESTED` → ARE `"REJECTED"`, treating
  anything short of unconditional approval as a rejection from ARE's point of view.
- **(c)** Do not call ARE's review APIs at all for decisions outside
  `{APPROVED, REJECTED}`; DS's own `asset_reviews`/`asset_review_history` remain the sole
  record of `RESTRICTED`/`CHANGES_REQUESTED` decisions, with ARE only ever informed of a
  clean binary outcome when one is eventually reached.

**Recommended decision: (a) for `RESTRICTED`, explicit non-call for
`CHANGES_REQUESTED`** (i.e. a hybrid of (a) and (c), applied per-decision rather than
uniformly). **Rationale:** mapping `RESTRICTED` to ARE's `"REJECTED"` (option (b)) would
misrepresent a genuinely-approved-with-constraints asset as rejected, which is a stronger
and incorrect claim; `RESTRICTED` is substantively an approval (DS's own
`assign_asset` logic already treats `RESTRICTED` effective-rights the same as `CLEAR` for
assignment purposes — see `services/assets.py:317` `if effective not in (EffectiveRights
.CLEAR, EffectiveRights.RESTRICTED)`), so mapping it to ARE's `"APPROVED"` is the more
accurate translation. `CHANGES_REQUESTED` has no approval-adjacent meaning at all (it is a
"not yet decided, send back" signal) and should simply not be forwarded to ARE as either
outcome — forwarding it as either `"APPROVED"` or `"REJECTED"` would fabricate a decision
that was never actually made.

**Security and data-integrity implications.** This mapping only matters if Decision 3
resolves toward persisting review decisions in ARE's own tables at all; if Decision 3
resolves toward "pure evaluator only, DS remains sole review record," this decision has no
code path to apply to (consistent with the cross-decision dependency already flagged
under Decision 5).

**Backward-compatibility impact.** None on DS's existing 4-value vocabulary or its
existing tests; this only affects a not-yet-built ARE-forwarding step.

**Required tests (future).** A test that a `RESTRICTED` decision, if forwarded to ARE,
results in ARE's own effective-rights evaluation reflecting an approved (not rejected)
state; a test that `CHANGES_REQUESTED` never produces any ARE-side review-history row at
all (no call made).

**Blocked pending explicit approval.** Yes, and contingent on the Decision 3 resolution
exactly as noted above.

---

## Decision 9 — Error/status-code mapping

**Current Defence Studio behavior.** The `ReviewError` family carries a `status_code`
class attribute consumed by one shared FastAPI exception handler
(`services/review.py:51-76`; `AssetNotFound`→404, `AssetValidationError`→422,
`AssetStaleRevision`/`AssetRevisionConflict`→409, all in `services/assets.py:63-80`).
Cross-owner denial is 404 (never 403) by deliberate design, confirmed in
`SECURITY_REVIEW.md` §2 — this avoids confirming a job ID's existence to an unauthorized
caller.

**Current ARE 1.1.0 behavior.** A single `DomainError(code: str, message: str)` for
every failure (`contracts.py:7-10`); ARE's own codes observed in this phase's re-read of
`engine.py`/`host_adapter.py` include: `VALIDATION`, `FILESYSTEM`, `DATABASE`,
`NOT_FOUND`, `CONFLICT`, `SNAPSHOT` (engine-core), plus `SCENE_MISMATCH`,
`PLAN_NOT_APPROVED`, `STALE_PLAN`, `RESOURCE_MISMATCH`, `PRINCIPAL_MISMATCH`
(host-adapter layer, `host_adapter.py:35-77`). ARE has no HTTP status concept at all —
translation to an HTTP code is entirely the host's responsibility.

**Exact source files/functions.** DS: `services/review.py::ReviewError` family (L51-76),
`services/assets.py` exception subclasses (L63-80). ARE: `contracts.py::DomainError`
(L7-10), `engine.py` (multiple `DomainError(...)` call sites), `host_adapter.py::
AdapterGateway._context`/`_review_identity` (L30-77).

**Available options.**
- **(a)** Map every ARE `DomainError` code to one of DS's existing 4 status codes
  (404/409/422/plus a generic 400), accepting the loss of granularity Phase 5C already
  flagged (e.g. `SCOPE_MISMATCH`/`PRINCIPAL_MISMATCH` both collapsing to DS's existing 404
  cross-owner convention, consistent with not confirming resource existence).
- **(b)** Introduce a new DS exception class deriving from `ReviewError` with
  `status_code = 403`, used specifically for ARE's `PRINCIPAL_MISMATCH`/`SCOPE_MISMATCH`,
  diverging from DS's existing 404-for-everything cross-owner convention.
- **(c)** Wrap every ARE call in a `try/except DomainError` that re-raises as DS's
  existing `AssetValidationError` (422) regardless of the specific ARE code, discarding
  code-level granularity entirely.

**Recommended decision: (a).** **Rationale:** introducing a new 403 code path (option
(b)) for only the ARE-sourced subset of cross-owner-shaped errors would create an
inconsistency where cross-owner denial means 404 everywhere else in the API but 403 for
this one integration — a confusing, inconsistent API surface. Preserving DS's existing,
tested 404-for-cross-owner convention (option (a)) is simpler and matches
`SECURITY_REVIEW.md` §2's "defensible choice" judgment on the existing design. Option (c)
is rejected because it discards information (`VALIDATION` vs `NOT_FOUND` vs `CONFLICT`
are genuinely different situations DS's own error family already distinguishes; collapsing
them to one code would be a regression relative to DS's current precision, not merely a
simplification).

**Security and data-integrity implications.** The explicit mapping table (to be written
alongside the real adapter, not here) must be reviewed to confirm no ARE code is mapped to
a *more* permissive DS status than DS's own equivalent failure would produce (e.g. ARE's
`CONFLICT` must map to 409, never silently swallowed as a 200).

**Backward-compatibility impact.** None on DS's existing exception hierarchy; this adds a
translation function in the (not-yet-built) adapter module only.

**Required tests (future).** One test per ARE `DomainError` code enumerated above, each
asserting the adapter translates it to the specific DS status code this decision assigns,
with no silent catch-all that could mask an unexpected new ARE error code appearing in a
future ARE version as a misleadingly generic success or wrong status.

**Blocked pending explicit approval.** Yes — the exact mapping table is itself a security-
relevant artifact that should be reviewed before any adapter code is written, not derived
ad hoc during implementation.

---

## Summary

| # | Decision | Recommended default | Blocked pending approval |
|---|---|---|---|
| 1 | Asset identity model | Mint a stable, server-generated ARE-facing ID alongside DS's existing content-addressed `asset_id` | Yes |
| 2 | Provenance requiredness | Defer ARE registration until provenance is non-empty; never synthesize a placeholder | Yes |
| 3 | Rights-state ownership | DS's tables remain sole source of truth; call ARE's pure evaluator only, no ARE-side persistence for rights | Yes — and gates Decisions 5, 6, 8 |
| 4 | Missing modification/distribution fields | Pass `False`/`False` to ARE (inert); propose a DS schema extension separately, not now | Yes |
| 5 | Effective-rights output granularity | Add ARE's blocker codes as an additive, read-only field; do not replace DS's gating enum | Yes — contingent on Decision 3 |
| 6 | Role vs. target-use | Do not call ARE's assignment-persistence APIs at all unless Decision 3/5 require it; if required, synthesize an inert constant role | Yes — contingent on Decision 3/5 |
| 7 | Tenant isolation | See dedicated section below | Yes |
| 8 | Review-decision vocabulary | `RESTRICTED`→ARE `"APPROVED"`; `CHANGES_REQUESTED`→ no ARE call | Yes — contingent on Decision 3 |
| 9 | Error/status-code mapping | Map every ARE code onto DS's existing 404/409/422 family; no new 403 path | Yes |

**None of these decisions were implemented.** Decisions 3, 5, 6, and 8 are explicitly
interdependent and must be approved together, not piecemeal, because approving 5/6/8
without first resolving 3 would leave the design internally inconsistent (see each
entry's "Security and data-integrity implications").

---

## Tenant Isolation — Mandatory Security Decision

The task requires verifying ARE's actual connection lifecycle before recommending an
isolation model, not assuming "one `Engine` per job" is automatically secure. This section
does that verification first, then evaluates Options T1/T2/T3.

### How `Engine` actually opens, owns, and closes its connection

Re-read in full this phase: `E:\DefenceStudio-Components\asset-rights-engine\src\
asset_rights_engine\engine.py`.

- **No persistent connection is held.** `Engine.__init__` (`engine.py:11-18`) does not
  open a connection at construction — it only resolves/creates the `data_directory` path
  and calls `self._initialize()`, which itself opens and closes a connection via the
  `_connection()` context manager. Every subsequent public method (`register_asset`,
  `get_asset`, `create_rights_revision`, etc.) independently calls `self._connection(...)`,
  which does `sqlite3.connect(self.path, timeout=10)` **fresh, every call**
  (`engine.py:20-37`), and unconditionally `con.close()` in the `finally` block
  (`engine.py:37`). There is no connection pool, no cached handle, no in-process lock
  object guarding `self.path` beyond what SQLite itself provides.
- **Locking is entirely SQLite's, not `Engine`'s.** Writes use `BEGIN IMMEDIATE`
  (`engine.py:27`), which acquires SQLite's reserved lock immediately rather than
  deferring it to the first write statement — this serializes concurrent writers **at the
  file level**, exactly like Defence Studio's own `BEGIN IMMEDIATE` pattern. Reads use
  plain `BEGIN` (`engine.py:27`, the `else` branch), which does not take a reserved lock.
  `sqlite3.connect(..., timeout=10)` gives a 10-second busy-wait before a lock contention
  raises `sqlite3.OperationalError` (caught and re-raised as `DomainError("DATABASE", ...)`
  at `engine.py:30-32`).
- **`Engine.__init__` does path containment checks, not identity checks.**
  `require(self.path.resolve().parent==self.root,"Database escapes configured
  directory")` (`engine.py:17`) only prevents `database_name` from containing a path
  traversal component (e.g. `"../other/asset_rights.sqlite3"`) relative to its *own*
  `data_directory` argument — it does **not** check whether some *other* already-existing
  `Engine` instance (in the same process, a different process, or a different job's
  adapter call) is simultaneously pointed at the exact same `(data_directory,
  database_name)` pair. **Two `Engine` instances constructed with the same path argument
  will transparently share the same SQLite file and the same data, with no error, no
  warning, and no cross-instance awareness of each other** — `Engine` has no concept of
  "this path is already owned by another live instance." The only thing preventing data
  merge between two jobs is **whichever code decides what path string to pass into
  `Engine(data_directory=...)`** — entirely external to `Engine` itself.
- **Schema versioning is a shared-file safety net, not a tenant boundary.**
  `_initialize()` (`engine.py:39-56`) checks `PRAGMA user_version` and a stored schema
  checksum (`engine.py:42-55`) — this protects against opening a file with an
  incompatible/foreign schema, and would in fact catch *some* accidental misuse (e.g.
  pointing `Engine` at a file that isn't an ARE database at all, via the "Refusing to
  initialize existing unversioned database" check at `engine.py:46`). But it provides
  **no protection at all** against two different jobs' adapter instances both correctly
  constructing `Engine` against the *same* ARE-schema-versioned file — that scenario looks
  identical to "the same job calling its own `Engine` twice" from `Engine`'s point of
  view, because `Engine` has no job/tenant-identity concept whatsoever (consistent with
  `COMPATIBILITY.md` §7's finding).

**Conclusion of this verification: "one `Engine` per job" is a property the *caller*
(Defence Studio's adapter) must enforce by construction — by always deriving the file
path deterministically and exclusively from the authenticated, ownership-checked
`job_id`, never from any other input. `Engine` itself supplies zero isolation guarantee on
its own.** This directly satisfies, and is required by, the task's fail-closed rule: no
integration design may rely solely on a caller-supplied identifier — the `job_id` used to
construct a path must be the same `job_id` that has *already* passed
`_check_ownership`/`_check_ownership_locked` in the calling `AssetService` method, not a
raw value taken fresh from the request at the point the `Engine` is constructed.

### Option T1 — One ARE Engine and separate component-owned database per Defence Studio job

One `Engine(data_directory=.../jobs/<job_id>/are/)` instance per `job_id`, constructed
(or retrieved from a short-lived cache) only inside an already-ownership-checked
`AssetService`/adapter method.

### Option T2 — One ARE Engine and database per authorized tenant/owner scope, with job-level resource authorization in the trusted adapter

One `Engine` per `owner_id` (coarser than per-job), with the adapter enforcing `job_id`
scoping *inside* that shared-per-owner file via its own resource-authorization logic
(since `Engine` has no `job_id` column at all — the adapter would need to encode `job_id`
into every ARE-side identifier it constructs, e.g. prefixing `asset_id`/`assignment_id`
strings with the job, and independently verify that prefix on every read).

### Option T3 — A shared ARE database with independently enforced tenant-aware storage and authorization

One `Engine` for the entire Defence Studio deployment, with tenant/job isolation enforced
entirely by the adapter layer filtering/authorizing by a job/tenant identifier encoded
into ARE's own identifier strings (asset_id, assignment_id), never by separate files.

### Comparison

| Dimension | T1 (per-job Engine/DB) | T2 (per-owner Engine/DB) | T3 (shared Engine/DB) |
|---|---|---|---|
| **Cross-tenant leakage risk** | Lowest — a bug in the adapter's job-scoping logic cannot leak data between jobs at the storage layer, because two jobs' data physically cannot be in the same file unless the adapter's *path-construction* code itself is buggy (a single, narrow, easily-reviewed code path). | Medium — a bug in the adapter's job-scoping-within-a-shared-owner-file logic (the per-job identifier prefixing) directly causes a leak between two jobs of the *same* owner, since nothing at the storage layer distinguishes them. Cross-*owner* leakage still requires a path-construction bug, same as T1. | Highest — any bug in the adapter's job/tenant-filtering logic leaks across every job and every owner, since all data lives in the one file ARE itself treats as fully unscoped (confirmed above: `Engine` has no tenant/job concept at all). This is the option that most directly contradicts ARE's own documented production prerequisite ("dedicated engines/storage per scope"). |
| **Cross-job isolation (same owner, two jobs)** | Enforced by file separation — automatic, not logic-dependent. | **Not enforced by storage at all** — must be fully reimplemented by the adapter via identifier-prefixing/filtering, which is exactly the kind of bespoke tenant-aware logic ARE's own docs say it does not provide and a host must build independently. | Same weakness as T2, but for every owner, not just within one owner's jobs. |
| **Connection/lifecycle management** | One `Engine` object per active job; given the verified fact that `Engine` opens/closes a fresh connection per call anyway (no persistent handle), there is no meaningful *connection pooling* cost difference between T1/T2/T3 — the cost difference is purely in *how many distinct SQLite files* exist and how many `Engine` Python objects are constructed/cached. A per-job cache (e.g. an LRU keyed by `job_id`, evicted after inactivity) is a reasonable, low-risk addition since `Engine.__init__` itself is cheap (path resolution + one `_initialize()` call) relative to a long-lived job. | Fewer `Engine` objects than T1 (one per owner instead of one per job), marginally less construction overhead, but requires the adapter to track job scoping as additional in-memory or in-ARE-identifier state per call. | Fewest `Engine` objects (one, process-wide) but the *most* per-call scoping logic, since every single call must filter by both owner and job using string-encoded identifiers rather than file separation. |
| **Database growth / backup / restore** | Many small files (one per job); aggregate size is the same as any other option, but backup/restore can be done **per job** (e.g. restoring one job's ARE file without touching any other job's), which matches Defence Studio's existing per-job artifact-directory convention (`jobs/<job_id>/...`) exactly. | Medium file count (one per owner); a restore granularity of "per owner" rather than "per job" — coarser than Defence Studio's existing per-job backup habits elsewhere, a real operational mismatch. | One large, ever-growing file; a restore of *any* data requires restoring *all* tenants' data simultaneously — the worst option for selective recovery, and the one most likely to turn a single restore operation into a multi-tenant incident. |
| **Migrations and upgrades** | `Engine._initialize()`'s schema-checksum check (`engine.py:42-55`) runs independently per file; a schema upgrade must be applied file-by-file, but a failure in one job's file cannot block or corrupt any other job's file — failures are naturally isolated. | A schema upgrade failure on one owner's shared file blocks/risks every job that owner has, a strictly larger blast radius than T1 for the same bug. | A schema upgrade failure blocks or risks literally every job and every owner simultaneously — the largest possible blast radius. |
| **Concurrent access** | SQLite's `BEGIN IMMEDIATE` serializes writers **within one job's file only** — two different jobs' concurrent writes never contend with each other at all, since they are different files; this gives Defence Studio's existing per-job concurrency model (already true for DS's own shared file, scoped by `WHERE job_id=?` rather than by separate files) an *additional*, storage-level guarantee that two jobs' ARE-side writes can never block one another. | Two different jobs **of the same owner** now contend for the same SQLite write lock even though they are logically unrelated jobs — a job with heavy asset/rights activity could measurably slow down an unrelated job sharing the same owner, a real (if usually minor) performance isolation loss T1 does not have. | Every job and every owner contends for one write lock — the worst concurrency profile, and the one most likely to surface SQLite's 10-second busy-timeout (`engine.py:24`) under real concurrent load, which fails as a 409-class `DomainError("DATABASE", ...)` rather than succeeding slowly. |
| **Host shutdown/restart behavior** | Because no connection is held open across calls (verified above), an ungraceful host shutdown mid-call leaves at most one job's one in-flight SQLite transaction to recover via SQLite's own WAL/rollback-journal crash recovery (standard SQLite guarantee, not something `Engine` adds) — recovery scope is naturally bounded to one file. | Same per-call recovery mechanism, but a crash mid-call now has the *theoretical* (if practically tiny) chance of being mid-transaction on a file shared by other jobs of the same owner — SQLite's own crash recovery still protects the file's integrity, but "which job was affected" is less immediately obvious from the file alone. | Same SQLite-level crash safety, but any crash recovery investigation must consider the entire deployment's data, not one job — operationally worse for incident response, though not actually less *safe* at the SQLite level (SQLite's crash recovery is file-scoped regardless of how many logical tenants share that file). |
| **Audit-history retention** | `Engine.get_review_history()`/`list_assignments()` (`engine.py:209-212`) return **everything in that file** — for T1, that is naturally exactly one job's history, no adapter-side filtering required to get a correct, non-leaking answer. | For T2, `get_review_history()` returns **every job of that owner's** history mixed together — the adapter **must** filter the returned tuple by its own job-encoded identifier convention before returning anything to a caller, or it leaks one job's review history into another job's read (of the same owner) — a real, must-not-forget filtering requirement T1 does not have. | For T3, the same method returns **the entire deployment's** history — the adapter's filtering requirement becomes the single most safety-critical piece of code in the whole integration, since forgetting to filter here leaks across every tenant, not just within one owner. |
| **Operational complexity** | Many small files to enumerate/monitor, but each is simple, independently reasoned about, and matches DS's existing per-job artifact-directory convention — low *conceptual* complexity despite a higher file count. | Fewer files, but the adapter must implement and maintain its own job-scoping/filtering logic for every single ARE call that returns or accepts a collection (`get_review_history`, `list_assignments`) — genuinely more code to get right and keep right across future ARE version upgrades. | Fewest files, but the *most* adapter-side scoping logic, applied to the *most* security-critical boundary (tenant and job isolation simultaneously) — this is the option where a single missed filter has the largest blast radius. |

### Recommended default: **T1 — one ARE Engine and database per Defence Studio job.**

This matches Phase 5C's `INTEGRATION_SPEC.md` original recommendation and this
verification confirms it is the correct choice **once the caller enforces path
construction correctly** — T1 is the only option where cross-job and cross-owner
isolation is a property of the *storage layer* (file separation) rather than of
bespoke adapter-side filtering logic that must be perfectly correct on every call,
forever, across every future ARE and Defence Studio version. T2 and T3 both convert a
storage-level guarantee into an application-level one for no documented benefit (no
stated requirement exists for a shared ARE file across jobs or owners), which is exactly
the kind of avoidable risk this phase's fail-closed mandate is meant to catch.

### Exact invariants the future adapter must enforce (T1)

These are binding requirements on any future implementation, not suggestions:

1. **The `Engine` path must be a deterministic, pure function of the already-
   ownership-checked `job_id` only** — e.g. `data/are/<job_id>/`, constructed *after*
   `_check_ownership`/`_check_ownership_locked` has already succeeded for that `job_id` in
   the calling `AssetService` method, never before, and never from any other request field
   (query parameter, header, or body value). This is the direct application of the task's
   fail-closed rule to this specific design.
2. **No code outside the adapter module may construct an `Engine` instance.** Consistent
   with `SECURITY_REVIEW.md` §8's conclusion and `INTEGRATION_SPEC.md`'s existing
   recommendation — this must remain a single, narrow, reviewable import boundary.
3. **A per-job `Engine` cache (if used for performance) must be keyed by the exact
   ownership-checked `job_id` string, never by a value derived from unvalidated input,
   and must not outlive the ability to re-verify ownership on a subsequent call** — i.e.
   caching the `Engine` object must not become a shortcut that skips re-checking
   `_check_ownership` on every call; the cache only saves the cost of re-running
   `Engine.__init__`'s path setup and schema check, never the ownership check itself.
4. **`Engine.get_review_history()`/`list_assignments()` results may be returned directly
   without additional adapter-side job filtering under T1** (since the file itself is
   already job-scoped) — but this exact property is what makes T1 safer than T2/T3, and
   must be re-verified if the adapter is ever changed to share a file across jobs for any
   reason; if that ever happens, this invariant is void and T2/T3's mandatory filtering
   requirement applies instead.
5. **The job-to-path mapping itself must be auditable** — i.e. there must be a way to
   confirm, after the fact, which `job_id` a given ARE SQLite file on disk corresponds to
   (satisfied automatically by T1's deterministic, job-id-derived directory naming
   convention — e.g. the directory name *is* the job ID — so this invariant requires no
   separate index as long as invariant 1 is followed exactly).
6. **No two `job_id` values may ever resolve to the same path.** This sounds trivial but
   must be explicitly tested: if the path-construction function ever normalizes,
   truncates, or hashes `job_id` in a lossy way (e.g. for filesystem-safety reasons, akin
   to the existing `rights-{r_sha[:16]}.json` truncation pattern already used elsewhere in
   `services/assets.py:227` for Windows `MAX_PATH` safety), a collision between two
   different `job_id`s mapping to the same truncated path would silently merge two jobs'
   ARE data — the single most severe possible failure mode this design must guard
   against. Any truncation/hashing scheme used for the ARE path must be proven
   collision-resistant for `job_id`'s actual ID space (DS job IDs are UUIDs; a full UUID,
   not a truncated prefix, should be used in the path specifically to avoid repeating the
   MAX_PATH-driven truncation pattern in a context where truncation would be unsafe rather
   than merely space-saving).
