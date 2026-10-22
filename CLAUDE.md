# Defence Studio — Claude project instructions

**Authoritative plan:** `DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md`. **Current state:** `project_status.md`.
Read those two first; the core design §1 and §1A govern how to work here.

## TOKEN SAVER MODE (always on)

Applies to every phase, component, bug fix, and review. Full text in
`DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` §1A.

1. **Read minimally** — `project_status.md` + the specific phase contract first; more files only to resolve a concrete dependency. No full-repo rescans or repeated Phase 0 discovery without a reason.
2. **Trust verified handoffs** — start from documented completed work/tests; verify only what the task touches; do not redo completed phases.
3. **One task per prompt** — only the named phase or requested fix; no speculative work, refactors, redesigns, or extra features.
4. **Inspect before editing** — check source + Git diff; smallest safe change; never rewrite working files wholesale.
5. **Targeted tests first** — smallest relevant tests after a change; full regression once at the review gate or on real regression risk.
6. **Avoid repeated tool calls** — no re-reading files, rerunning unchanged tests, regenerating reports, or duplicate searches; combine read-only checks.
7. **Keep outputs concise** — no pasted whole files, long logs, or inventories; summarize, show only relevant errors, reference paths.
8. **Limit documentation changes** — update only the authoritative documents affected; don't duplicate status across docs unless the convention requires it.
9. **Preserve architecture and boundaries** — don't touch unrelated projects, modules, databases, secrets, or completed phases; no mock production data; no downstream work before the review gate.
10. **Stop when done** — report changed files, tests run + results, blockers, and the next task; don't advance phases without authorization.
11. **Never sacrifice correctness for token savings** — don't skip security checks, migration validation, crash-recovery tests, or acceptance criteria; if context is insufficient, inspect only the missing information.

**Before every tool call ask:** "Will this materially help complete or verify the current task?" If not, skip it.

## Project conventions

- **Component registry:** every component we build is kept as one canonical copy with a README and registered in `component_registry/` (`REGISTRY.md`). UI primitives (input box, date picker, …) are built once and reused, never re-implemented per page.
- **Phased order is locked** (core design §3). Begin at the current task in `project_status.md`; do not skip the review gate.
- **Single-writer rule:** only one agent/session edits the working tree at a time. Before editing, check `git status --short`; never overwrite or revert changes you did not make. Commit/push only per the Git rule below.
- **Git commit and sync (standing user authorization, 2026-10-22):** at the end of every coding task follow `automation/GIT_COMMIT_SYNC_RULE.md` — verify tests/diffs, update its register, commit only this task's files by explicit path, push to upstream with the existing authorized credentials, verify, never claim success without evidence. Its safety exceptions and any task-specific "do not commit" instruction override it. Do not use `python -m automation github sync` (stages everything).
- **Status trackers:** `project_status.md`, `DEFENCE_ERA_PHASE_STATUS.md`, `DEFENCE_ERA_PENDING_WORK.md`, `DEFENCE_ERA_IMPLEMENTATION_ROADMAP.md` are updated from verified evidence only. Labels: IMPLEMENTED / TESTED / INTEGRATED / LIVE-VERIFIED / ACCEPTED are not interchangeable.
