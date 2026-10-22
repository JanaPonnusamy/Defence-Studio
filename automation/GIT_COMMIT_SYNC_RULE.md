# Git Commit and Sync Rule (permanent) + Commit/Sync Register

Authoritative for every agent (Claude, Codex, Cursor, others) on Defence Studio.
Discovered via `CLAUDE.md` (Claude) and `AGENTS.md` (Codex, Cursor). Do not copy this text elsewhere; reference it.

**Do not use `python -m automation github sync` for this workflow.** The NDF script stages *every* change
(including other agents' work and the dev database) and targets NEXORA config. Use explicit, path-limited git commands.

## Mandatory end-of-task workflow

1. Finish the implementation; run the relevant tests.
2. Inspect the final diff (`git diff`, `git status --short`) for secrets, credentials, generated files, unrelated changes, accidental database changes.
3. Add a row to the register below (task, files, tests, commit, sync, blockers).
4. Stage only this task's files by explicit path. Never `git add -A` / `git add .` / `git commit -a`.
5. Commit only when verified, safe, and appropriate. Descriptive message.
6. Push to the configured upstream using the existing authorized authentication (git credential setup). Never request, print, or write the token anywhere.
7. Verify: `git fetch origin` then `git rev-list --left-right --count origin/<branch>...HEAD` must be `0 0`.
8. Update the register row with the real commit hash, push result, timestamp, final status. Same commit if practical; otherwise a clearly linked follow-up commit, also pushed.
9. Report task, tests, commit hash, sync result, and anything left uncommitted.

## Safety rules

- Never claim a commit or push succeeded without command output proving it.
- Failing tests: investigate; never commit broken work to satisfy this rule.
- Auth/network/conflict/branch-protection failure: record the exact blocker and report; do not work around it.
- Never force-push, reset history, overwrite remote work, discard user changes, or resolve conflicts blindly.
- Never stage unrelated files, `.env*`, keys, tokens, caches, venvs, generated artifacts, `*.sqlite3` / production databases, or another active agent's incomplete work.
- An explicit user instruction not to commit/push for a task overrides this rule.
- Migrations, production-data changes, deployments, paid/provider calls and destructive operations keep their own authorization; git sync authorizes none of them.
- Do not stop unrelated processes or shut down the PC.

## Prompt reference (include in task prompts)

> Follow the permanent Git Commit and Sync Rule registered under `automation` (`automation/GIT_COMMIT_SYNC_RULE.md`). At task completion, verify tests and diffs, update the commit/sync register, commit only the verified task-specific changes, synchronize with the configured upstream using the existing authorized credentials, and verify the final result. Follow all safety exceptions and never claim success without evidence.

## Commit / Sync Register (append-only)

| Date (IST) | Agent | Task | Files | Tests | Commit | Sync result | Blockers / left uncommitted |
|---|---|---|---|---|---|---|---|
| 2026-10-22 | Claude | Register permanent git commit/sync rule | `automation/GIT_COMMIT_SYNC_RULE.md`, `CLAUDE.md`, `AGENTS.md` | Docs only; no code tests | pending | pending | ~90 unrelated modified/untracked files from other agents left untouched |
