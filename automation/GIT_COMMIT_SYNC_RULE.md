# Git Commit and Sync Rule (permanent) + Commit/Sync Register

Authoritative for every agent (Claude, Codex, Cursor, others) on Defence Studio.
Discovered via `CLAUDE.md` (Claude) and `AGENTS.md` (Codex, Cursor). Do not copy this text elsewhere; reference it.

**Do not use `python -m automation github sync` for this workflow.** The NDF script stages *every* change
(including other agents' work and the dev database) and targets NEXORA config. Use explicit, path-limited git commands.

## Dual-remote, develop-branch rule (standing user instruction, 2026-10-27)

All code is pushed to **both** GitHub repositories, which are maintained **separately**:

| Remote name | URL | Role |
|---|---|---|
| `origin` | `https://github.com/JanaPonnusamy/Defence-Studio.git` | personal — source of truth; pull/fetch from here only |
| `axythic` | `https://github.com/axythic-dev/Defence-Studio.git` | office (axythic-dev) — **push-only** target |

- **`axythic` is push-only (user instruction, 2026-10-27):** never `git pull`, `git merge`, `git rebase` or cherry-pick from `axythic`, and never merge the two repos with each other. Only `git push axythic <branch>`. (`git fetch axythic` is allowed solely for the verification count below.)
- If a push to `axythic` is rejected as non-fast-forward (someone changed it directly), **stop and report** — do not pull, merge or force-push.

- **Working branch is `develop`.** Commit all new work on `develop`. `main` only changes when the user explicitly asks to merge/release.
- **Every push goes to both remotes:** `git push origin develop` and `git push axythic develop` (when `main` is updated, push it to both too).
- **Verify both:** after `git fetch origin` and `git fetch axythic`, both `git rev-list --left-right --count origin/develop...HEAD` and `git rev-list --left-right --count axythic/develop...HEAD` must be `0 0`.
- If one remote fails (auth/network/protection), still push the other, record the exact blocker in the register, and report it. Never force-push either remote to make them match.
- **Any machine (home or office) setup**, once — always clone from the personal repo:
  ```
  git clone https://github.com/JanaPonnusamy/Defence-Studio.git .
  git remote add axythic https://github.com/axythic-dev/Defence-Studio.git
  git switch develop                 # tracks origin/develop
  ```
  Before starting work: `git pull --ff-only origin develop` only (never pull from `axythic`; stop and report on conflicts).

## Mandatory end-of-task workflow

1. Finish the implementation; run the relevant tests.
2. Inspect the final diff (`git diff`, `git status --short`) for secrets, credentials, generated files, unrelated changes, accidental database changes.
3. Add a row to the register below (task, files, tests, commit, sync, blockers).
4. Stage only this task's files by explicit path. Never `git add -A` / `git add .` / `git commit -a`.
5. Commit only when verified, safe, and appropriate. Descriptive message.
6. Push `develop` to **both** remotes (`origin` and `axythic`, see the dual-remote rule above) using the existing authorized authentication (git credential setup). Never request, print, or write the token anywhere.
7. Verify both: `git fetch origin` / `git fetch axythic`, then `git rev-list --left-right --count <remote>/develop...HEAD` must be `0 0` for each.
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

> Follow the permanent Git Commit and Sync Rule registered under `automation` (`automation/GIT_COMMIT_SYNC_RULE.md`). At task completion, verify tests and diffs, update the commit/sync register, commit only the verified task-specific changes on `develop`, push to both remotes (`origin` and `axythic`) using the existing authorized credentials, and verify the final result. Follow all safety exceptions and never claim success without evidence.

## Commit / Sync Register (append-only)

| Date (IST) | Agent | Task | Files | Tests | Commit | Sync result | Blockers / left uncommitted |
|---|---|---|---|---|---|---|---|
| 2026-10-22 | Claude | Register permanent git commit/sync rule | `automation/GIT_COMMIT_SYNC_RULE.md`, `CLAUDE.md`, `AGENTS.md` | Docs only; no code tests | `2c27f21` | Pushed `4715988..2c27f21` to origin/main; fetch confirms `0 0` (2026-10-22) | ~90 unrelated modified/untracked files from other agents left untouched |
| 2026-10-22 | Claude | Cursor rule discovery (Cursor 3.24.18 installed; AGENTS.md auto-load not verifiable locally, so explicit rule added) | `.cursor/rules/git-commit-sync.mdc`, `automation/GIT_COMMIT_SYNC_RULE.md` | Docs only | `d889c55` | Pushed `78033e6..d889c55`; fetch `0 0` (2026-10-22) | ~92 unrelated paths untouched |
| 2026-10-22 | Cursor | Owner-approved narrow The Hindu article allowlist + policy tests | `backend/config/source_policy.json`, `backend/tests/test_source_policy_hindu_article.py`, `automation/GIT_COMMIT_SYNC_RULE.md` | `tests/test_source_policy_hindu_article.py` + `test_default_registry_file_loads...` + allowlist unit: 5 passed | `e63219d` / register `aa5b0ed` | Pushed `d778cc6..aa5b0ed` to origin/main; fetch confirms `0 0` (2026-10-22) | Disposable job acquisition stayed in temp DB. Policy file also included previously uncommitted PIB iframe + RSS entries already in that working file. Other concurrent paths unstaged. |
| 2026-10-22 | Claude | Content Search → final content pipeline audit/reconciliation (docs only) | `DEFENCE_ERA_PENDING_WORK.md`, `project_status.md`, `automation/GIT_COMMIT_SYNC_RULE.md` | None run (documentation audit) | `ffaca40` | Pushed to origin/main; fetch `0 0` (2026-10-22) | other agents' uncommitted work untouched |
| 2026-10-27 | Claude | Add `axythic` remote (axythic-dev/Defence-Studio), create `develop`, dual-remote rule | `automation/GIT_COMMIT_SYNC_RULE.md`, `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/git-commit-sync.mdc` | Docs only | `247d42c` (on `develop`) | Pushed new branch `develop` to origin (2026-10-27). axythic push **failed**: `403 Permission to axythic-dev/Defence-Studio.git denied to JanaPonnusamy` | BLOCKER: JanaPonnusamy needs Write access on axythic-dev/Defence-Studio (or sign in with an axythic-dev account), then push `develop` and `main` to axythic |
| 2026-10-27 | Claude | Make `axythic` push-only (no pull/merge; repos maintained separately) | `automation/GIT_COMMIT_SYNC_RULE.md`, `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/git-commit-sync.mdc` | Docs only | see git log (on `develop`) | origin: pushed; axythic: see final report | — |
