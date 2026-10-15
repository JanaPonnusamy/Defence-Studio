# Defence Studio --- Consolidated Production Readiness & Same-Day Execution Report

**Report date:** 15 October 2026\
**Evidence baseline:** `PROJECT_READINESS_AUDIT_20261015.md` and
associated readiness logs supplied by the project owner\
**Purpose:** Consolidate current known gaps, define a realistic same-day
release strategy, and coordinate Cursor, Codex, and Claude without file
collisions or loss of evidence.

## 1. Executive verdict

Defence Studio has a functioning local English production chain and
substantial implemented components. The audit verified a local chain
spanning English audio, visual planning, rendering, and subtitles. It
does **not** establish production readiness for unattended Tamil
production, independent automatic fact verification, or reproducible
fresh-machine installation.

The correct target for today is **a controlled, evidence-backed release
candidate**, not an unconditional claim that every feature is
production-ready. Complete the critical P0 fixes and run the release
gates today if the environment and agent throughput allow. Anything
requiring unavailable Tamil voices/fonts, a real alignment model,
provider credentials, OAuth, or human review must remain explicitly
blocked until actually verified.

No agent may declare a phase accepted based on implementation claims
alone. Phase IDs and existing history must be preserved.

## 2. Verified baseline from the supplied audit

### Core/backend

-   The audit reported **428 passed, 4 skipped** in the existing backend
    environment.
-   A high-impact defect was reproduced: rejected evidence was counted
    as corroboration for `VERIFIED_FACT`. Two grouped claims from two
    sources were counted as two corroborating claims even though only
    one claim was eligible and the other claim and source were rejected.
-   A clean editable backend installation failed due to ambiguous
    flat-layout package discovery: `config` and `defence_studio`.
-   `cryptography` was used by runtime services and tests but was
    missing from dependency declarations.
-   Bare test startup failed because `tests/conftest.py` imported
    `api.app` before setting the test secret, while `api.app`
    constructed an application at import time.
-   A run in the shared module audit environment failed because the
    installed Starlette `TestClient` did not accept `client=`. The audit
    identified this as a dependency mismatch, not proof of a product
    authentication defect.

### Component test evidence

  ---------------------------------------------------------------------------------
  Component/check                    Recorded result What it proves---and does not
                                                     prove
  --------------------- ---------------------------- ------------------------------
  Frontend Node tests                      30 passed Unit-level frontend checks; no
                                                     fresh rendered-browser
                                                     acceptance

  Standalone Audio                         31 passed Component tests and English
  Module                                             integration; not Tamil
                                                     capability or crash recovery

  Standalone Video             206 passed, 2 skipped Visual planning; not complete
  Visual Module                                      video rendering or
                                                     paid-provider operation

  Standalone Video             117 passed, 1 skipped Tested still-image/WAV
  Renderer                                           H.264/AAC render path; not a
                                                     complete branded episode

  Defence Align                 71 passed, 5 skipped Contract/source tests; real
                                                     model alignment not verified
                                                     and editable install fails

  Asset & Rights Engine                   140 passed Library tests; not proof of
                                                     production integration or
                                                     legal licence verification

  Three real HTTP                           3 passed English audio, visual
  integration checks                                 planning, and
                                                     audio/visual/render/subtitle
                                                     chain under tested conditions

  Live external HTTP                        1 passed The tested acquisition path
  acquisition                                        against
                                                     `https://example.com/`; not
                                                     defence-news extraction
                                                     quality

  Backend regression           428 passed, 4 skipped Existing environment
  suite                                              regression baseline; not
                                                     clean-install proof
  ---------------------------------------------------------------------------------

The historical skips included live HTTP, live YouTube, and
Tamil-font-dependent tests. The audit did not perform a complete
security assessment, vulnerability scan, load test, legal licensing
determination, or human editorial review.

## 3. Current readiness verdict by production gate

Status labels: **Verified** = directly supported by supplied logs;
**Open** = known gap; **Not verified** = no evidence in the supplied
audit; **Conditional** = requires configured external resources or human
acceptance.

  --------------------------------------------------------------------------------
  Gate                             Status                  Required action
  -------------------------------- ----------------------- -----------------------
  Local English                    Verified for the tested Re-run final smoke test
  audio/visual/render/subtitle     path                    after changes
  chain                                                    

  Evidence corroboration           Open --- P0             Fix eligibility-aware
  correctness                                              distinct-source
                                                           counting; add
                                                           regression tests

  Backend package discovery        Open --- P0             Explicit package
                                                           discovery/build
                                                           configuration

  Declared `cryptography`          Open --- P0             Declare runtime/test
  dependency                                               dependency in the right
                                                           groups

  Bare test startup                Open --- P0             Configure test settings
                                                           before app creation;
                                                           preserve production
                                                           secret validation

  Clean backend install and import Not verified            Verify in disposable
                                                           environment, including
                                                           import outside source
                                                           tree

  Human review history             Must be verified for    Assert no
  preservation                     remediation             review/history writes
                                                           and compare isolated
                                                           before/after records

  Tamil TTS voice capability       Open                    Provide a real
                                                           Tamil-capable
                                                           voice/provider; reject
                                                           unknown/incompatible
                                                           voice IDs

  Tamil font and shaping           Open                    Install/discover
                                                           appropriate font and
                                                           run Tamil render tests

  Tamil narration/script fidelity  Conditional             Real sample reviewed by
                                                           a Tamil speaker

  Accurate word-level alignment    Open/conditional        Install compatible
                                                           model/backend; verify
                                                           on real speech and
                                                           licensing

  Standalone Audio Module crash    Open                    Reconcile `RUNNING`
  recovery                                                 jobs and resubmit
                                                           eligible persisted
                                                           queue rows safely

  Defence Align clean installation Open                    Configure package
                                                           discovery and verify
                                                           wheel/editable install
                                                           outside source tree

  Independent standalone           Not present             Only build if required;
  fact-check service                                       first fix core
                                                           evidence-integrity
                                                           defect, then define
                                                           versioned contract

  YouTube captions/OAuth/private   Not verified            Separate opt-in
  upload                                                   live-provider
                                                           acceptance with test
                                                           channel and explicit
                                                           authorization

  OpenAI text/image generation     Not verified by this    Verify with configured
                                   audit                   provider; control costs
                                                           and avoid accidental
                                                           paid calls

  Browser/mobile acceptance        Not verified in this    Run populated UI
                                   audit                   acceptance on
                                                           representative flows

  Rights and attribution in final  Partial                 Verify approved asset
  output                                                   provenance, credits,
                                                           rights warnings, and
                                                           final packaging

  Security/load/licensing          Not performed           Do not claim these are
  assessment                                               complete

  Production                       Not established by this Define environment,
  deployment/rollback/monitoring   audit                   backup, rollback, logs,
                                                           health checks, and
                                                           operator runbook
  --------------------------------------------------------------------------------

## 4. Priority order for today

### P0-A --- Evidence integrity

Owner: **Codex** (single writer for backend integrity code and tests).

1.  Inspect current `script_integrity.py`, evidence package
    construction, authoritative review/eligibility policies, and tests.
2.  Reproduce the rejected-claim/source defect before editing.
3.  Count only current eligible claims backed by current approved
    sources; deduplicate by source identity.
4.  Ensure rejected, pending, stale, unknown, missing-source, and
    duplicate-source cases fail closed.
5.  Add tests for helper counts and externally observable
    `FACT_NOT_CORROBORATED` behavior.
6.  Prove human review decisions/history remain unchanged; no assessor
    writes to review tables.
7.  Run evidence/review/script-integrity regressions and the full
    backend suite.

### P0-B --- Reproducible backend installation

Owner: **Codex** (same backend owner; avoid simultaneous edits to
`pyproject.toml`, app initialization, or test setup).

1.  Configure explicit package discovery in `backend/pyproject.toml`.
2.  Declare `cryptography` as a runtime dependency and in test
    dependencies only where needed.
3.  Fix test initialization so a fresh test process works without a
    manually exported test secret.
4.  Do not weaken production secret validation or authentication.
5.  In a disposable virtual environment, run
    `pip install -e './backend[test]'`.
6.  Verify distribution metadata, imports, and test startup from a fresh
    process and outside the source directory.
7.  Run the complete regression suite and record exact command, exit
    code, pass/fail/skip counts, and logs.

### P1 --- Production-critical, but only after P0

Assign independent workstreams only where files do not overlap: -
**Claude:** Tamil readiness investigation and acceptance plan---voice
inventory/provider choices, font discovery, real speech sample, Tamil
reviewer checklist. Do not change backend core files unless explicitly
handed off. - **Cursor:** populated frontend/browser acceptance, release
UI defects, and production runbook draft. Do not edit backend
integrity/packaging files owned by Codex. - **Codex:** Defence Align
packaging and Audio Module queue recovery only after P0 is stable, or
keep these as separate commits/patches. - **Human/operator:** provider
credentials, YouTube OAuth, channel permissions, licensing decisions,
Tamil language review, and approval to publish.

Do not have all three agents implement overlapping fixes simultaneously.
Parallelize research, tests, documentation, and isolated modules---not
the same shared files.

## 5. Three-agent coordination design

### Single source of truth

Create or maintain these files at the repository root:

-   `AGENT_COORDINATION.md` --- coordinator-owned live board; phase
    status, file ownership, blockers, integration decisions, acceptance
    evidence, and last update.
-   `AGENT_WORK/CODEX.md` --- Codex append-only work log.
-   `AGENT_WORK/CURSOR.md` --- Cursor append-only work log.
-   `AGENT_WORK/CLAUDE.md` --- Claude append-only work log.
-   `AGENT_WORK/DECISIONS.md` --- append-only decisions, with date,
    decision owner, reason, and affected phase IDs.
-   `AGENT_WORK/ACCEPTANCE_MATRIX.md` --- phase/gate evidence ledger.

If the repository already has equivalent canonical files, extend those
instead of creating duplicate sources of truth. Preserve existing phase
IDs and history.

### Update protocol

Each agent must update its own work log: - At task start: phase ID,
owner, objective, exact files it expects to edit, baseline status. -
During work: discoveries, commands run, tests/results, blockers, files
changed. - Before handoff: final diff summary, exact verification
evidence, remaining risks, and next dependency.

Only the coordinator/integrator edits the shared `AGENT_COORDINATION.md`
status table. Agents submit a concise update in their own file; the
coordinator merges that into the shared board. This avoids three
processes overwriting one shared file.

Every update must include:
`Timestamp | Agent | Phase/Task | Status | Files changed | Tests/command | Evidence/log | Blocker/next action`

Allowed statuses: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`,
`IMPLEMENTED_NOT_VERIFIED`, `VERIFIED`, `ACCEPTED`, `REJECTED`.

Rules: 1. `IMPLEMENTED_NOT_VERIFIED` is not complete. 2. `VERIFIED`
requires reproducible command output or a test/log artifact. 3.
`ACCEPTED` requires the defined phase acceptance criteria and
human/integrator approval. 4. Preserve phase IDs; do not renumber or
silently mark old phases accepted. 5. Do not edit another agent's owned
files without an explicit handoff. 6. No agent may overwrite the whole
coordination file from an old snapshot. 7. Use append-only logs for
progress and a coordinator-owned board for current state. 8. If a
shared-file edit is unavoidable, stop concurrent edits and nominate one
writer.

### File ownership for the initial P0 window

  --------------------------------------------------------------------------------------------------------
  Agent                   Owns                                                     Must not touch during
                                                                                   P0
  ----------------------- -------------------------------------------------------- -----------------------
  Codex                   `backend/defence_studio/services/script_integrity.py`,   Frontend and standalone
                          relevant backend tests, `backend/pyproject.toml`,        modules not needed for
                          required app/test initialization files                   P0

  Cursor                  Frontend/browser test files, frontend-only release       P0 backend files and
                          issues, its own log                                      dependency
                                                                                   configuration

  Claude                  Tamil/provider/font research, Tamil acceptance           P0 backend files and
                          checklist, documentation in its own work log             shared status board

  Coordinator/operator    `AGENT_COORDINATION.md`, acceptance matrix, integration  Must not rewrite an
                          decisions, final merges                                  agent's patch without a
                                                                                   recorded handoff
  --------------------------------------------------------------------------------------------------------

Use separate Git worktrees or branches for each agent if they share a
Git repository. If worktrees cannot be used, enforce the file-ownership
table strictly. Never let agents simultaneously run formatters or broad
automated refactors over the whole repository.

## 6. Safe temporary-file cleanup policy

"Clear temporary files frequently" must not mean deleting files that are
still being used or are the only evidence for a test result.

### Safe cadence

-   Before a run: inspect available disk space and identify known temp
    output locations.
-   After each test/integration run: remove only that run's disposable
    temporary output after logs/results have been retained.
-   At phase completion: clear stale generated temp files and caches
    only when no related process is running.
-   At end of day: review the cleanup manifest and disk usage; retain
    the evidence needed to reproduce acceptance.

### Safe-to-clean candidates, after confirming they are not in use

-   Test-created temporary directories created by the current run.
-   Known renderer scratch files and disposable intermediate media.
-   Old `__pycache__`, `.pytest_cache`, and temporary build artifacts
    when no test/build process is using them.
-   Agent-generated scratch files that are explicitly marked disposable
    and not referenced by a task.

### Never delete automatically

-   `data/defence_studio.sqlite3` or any production/user database.
-   Human review/history records, evidence packages, source assets,
    research decisions, or approved outputs.
-   `AGENT_COORDINATION.md`, agent work logs, acceptance matrix, test
    logs, or audit reports.
-   Credentials, `.env`, signing keys, OAuth tokens, or device
    identities.
-   Untracked/uncommitted source files merely because they are
    untracked.
-   Any file under a live job's working directory, or any path whose
    ownership/purpose is uncertain.
-   Existing project virtual environments or downloaded models without
    explicit approval.

### Cleanup implementation requirements

Use an explicit allowlist of temporary directories and a dry-run mode by
default. Log every path that would be removed; require an explicit
`--apply` or equivalent for deletion. Use an age threshold for stale
files and exclude active job directories. Do not run recursive deletion
against the project root. Never clean during a build, render, test, or
agent write operation. Retain a cleanup log. If safe ownership cannot be
determined, skip the file.

## 7. Same-day execution schedule

This is a target sequence, not a guarantee of completion.

1.  **15--30 minutes --- coordination setup:** inspect Git status;
    identify canonical phase/status docs; establish ownership and agent
    logs; preserve uncommitted changes.
2.  **60--120 minutes --- P0 reproduction and fixes:** reproduce
    evidence defect; implement focused correction and tests; fix package
    discovery, dependency declarations, and test startup.
3.  **45--90 minutes --- clean-environment validation:** fresh virtual
    environment, editable install, package metadata/import checks, bare
    test startup.
4.  **As long as needed --- regression and preservation:** relevant
    regressions, full backend suite, review-history invariance,
    database-path checks, final diff review.
5.  **Parallel, non-overlapping work --- P1 evidence collection:**
    Cursor performs browser acceptance; Claude verifies Tamil
    prerequisites; Codex investigates alignment packaging/audio recovery
    only after P0 files are stable.
6.  **Final release gate:** rerun the core local chain, validate output
    and credits, review logs, check Git diff/status, and create the
    release candidate only if the gates below pass.

If the P0 changes or full regression suite take longer than expected, do
not skip the clean install or evidence-integrity checks to meet the
clock. Defer blocked production gates transparently.

## 8. Production release gates

Do not call the project production-ready until all gates relevant to the
intended deployment are evidenced:

-   [ ] Evidence-integrity regression fixed and accepted.
-   [ ] Clean backend installation and bare test startup pass.
-   [ ] Complete backend regressions pass or every deviation is
    explained and accepted.
-   [ ] Data, schema, credentials, and review-history preservation
    verified.
-   [ ] Each standalone component is tested in its own supported
    dependency environment.
-   [ ] Real Tamil TTS and Tamil font are available and verified if
    Tamil production is in scope.
-   [ ] Tamil script and narration reviewed by a Tamil speaker.
-   [ ] Real alignment backend tested if word-level timing is required.
-   [ ] Live text/image generation tested with configured providers and
    controlled costs.
-   [ ] YouTube caption acquisition and OAuth/private upload verified
    separately if publishing is in scope.
-   [ ] Populated browser acceptance passes.
-   [ ] Representative episode includes approved assets,
    attribution/credits, subtitles, branding, QC, and final packaging.
-   [ ] Deployment settings, secret management, health checks, logs,
    backup, rollback, and operator instructions are documented.
-   [ ] No unresolved critical/high security or factual-integrity issues
    are silently accepted.
-   [ ] Final phase statuses are backed by evidence and preserve the
    existing phase history.

## 9. Final report format required from the agents

At handoff, each agent reports: 1. Task/phase IDs and owner. 2. Baseline
finding. 3. Files changed. 4. Commands actually executed and exit codes.
5. Test totals, skipped tests, and failures. 6. Evidence log paths. 7.
Preservation checks. 8. Remaining blockers and risks. 9. Whether the
work is `IMPLEMENTED_NOT_VERIFIED`, `VERIFIED`, or proposed for
`ACCEPTED`.

The integrator then produces one release report with: - Completed and
accepted phases. - Implemented but unverified phases. -
Blocked/not-started phases. - All critical/high findings. - Test and
installation evidence. - Tamil/provider/YouTube/human-review
limitations. - Cleanup summary. - Git status and release/rollback
decision.

## 10. Final recommendation

Aim to finish the P0 remediation and produce a tested release candidate
today. Do not promise that every phase can be accepted today before the
actual gates run. The current audit explicitly leaves Tamil voice/font
readiness, real alignment, provider/OAuth flows, crash recovery, clean
installations, and full browser/episode acceptance unresolved.

The correct outcome is either **production release backed by all
applicable evidence** or a **release candidate with a precise blocker
list**. Do not convert missing evidence into a green status just to
finish today.
