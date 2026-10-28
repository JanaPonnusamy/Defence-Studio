# Git Commit and Sync Rule (permanent) + Commit/Sync Register

Authoritative for every agent (Claude, Codex, Cursor, others) on Defence Studio.
Discovered via `CLAUDE.md` (Claude) and `AGENTS.md` (Codex, Cursor). Do not copy this text elsewhere; reference it.

**Do not use `python -m automation github sync` for this workflow.** The NDF script stages *every* change
(including other agents' work and the dev database) and targets NEXORA config. Use explicit, path-limited git commands.

## Dual-remote, develop-branch rule (standing user instruction, 2026-10-27)

All code is maintained in **both** GitHub repositories, kept **identical** — pull from both and push to both, on every machine (user instruction, 2026-10-27; supersedes the earlier same-day "push-only" variant):

| Remote name | URL | Role |
|---|---|---|
| `origin` | `https://github.com/JanaPonnusamy/Defence-Studio.git` | personal |
| `axythic` | `https://github.com/axythic-dev/Defence-Studio.git` | office (axythic-dev) |

- **Before starting work:** `git pull --ff-only origin develop` then `git pull --ff-only axythic develop`. If either is not a fast-forward (the repos diverged), **stop and report** — do not merge blindly or force-push.
- If one repo received commits the other lacks, the next push to both brings them back in line.

- **Working branch is `develop`.** Commit all new work on `develop`. `main` only changes when the user explicitly asks to merge/release.
- **Every push goes to both remotes:** `git push origin develop` and `git push axythic develop` (when `main` is updated, push it to both too).
- **Verify both:** after `git fetch origin` and `git fetch axythic`, both `git rev-list --left-right --count origin/develop...HEAD` and `git rev-list --left-right --count axythic/develop...HEAD` must be `0 0`.
- If one remote fails (auth/network/protection), still push the other, record the exact blocker in the register, and report it. Never force-push either remote to make them match.
- **Any machine (home or office) setup**, once:
  ```
  git clone https://github.com/JanaPonnusamy/Defence-Studio.git .
  git remote add axythic https://github.com/axythic-dev/Defence-Studio.git
  git fetch axythic
  git switch develop                 # tracks origin/develop
  ```

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
| 2026-10-27 | Claude | Make `axythic` push-only (no pull/merge; repos maintained separately) | `automation/GIT_COMMIT_SYNC_RULE.md`, `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/git-commit-sync.mdc` | Docs only | `c259577` | Pushed to origin/develop; fetch `0 0`. axythic 403 (no access yet) | superseded by next row |
| 2026-10-27 | Claude | Revert to two-way sync: pull from and push to both remotes, kept identical | `automation/GIT_COMMIT_SYNC_RULE.md`, `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/git-commit-sync.mdc` | Docs only | `d7638d6` | Pushed to origin/develop; fetch `0 0`. axythic 403 at that time | resolved by next row |
| 2026-10-27 | Claude | First push to axythic after JanaPonnusamy got Write access | `automation/GIT_COMMIT_SYNC_RULE.md` (register) | Docs only | `d7638d6` + this register commit | Pushed new branches `develop` and `main` to axythic; fetch: origin/develop `0 0`, axythic/develop `0 0`, origin/main vs axythic/main `0 0` (2026-10-27) | none |
| 2026-10-28 | Claude | Script/scene approval advance their workflow stage; AI images only with owner-authorized budget | `backend/defence_studio/services/{stage_sync,script,scene,visual}.py`, `backend/defence_studio/integrations/visual_module.py`, `backend/tests/{test_stage_sync,test_visual_rm3}.py` | stage/visual/script*/scene* tests: 153 passed, 1 skipped | see git log | see final report | DB + job storage committed separately; a peer session's stash (`pre-refactor baseline`) had removed this work, re-applied |
| 2026-10-28 | Claude | Tamil narration via installed Windows OneCore voice (Valluvar); opt-in photo fallback for data intents; Wikimedia Commons open-licence image filler | `standalone/audio-module/app/tts/pyttsx3_worker.py`, `standalone/audio-module/tests/test_tts_safety.py`, `standalone/video-visual-module/app/{api/visual_plans,pipeline/shot_planner}.py`, `standalone/video-visual-module/tests/test_planning.py`, `backend/scripts/fill_open_images.py` | audio tts_safety 17 passed; visual module full suite 207 passed / 2 skipped | see git log | see final report | audio `test_errors_and_isolation::test_tts_unknown_provider_rejected` fails on missing `jobs` table (test DB setup, pre-existing, unrelated) |
| 2026-10-28 | Claude | Tamil neural voice (edge-neural/Pallavi), short-format evidence profile + script schema, Tamil visual matching + fill-frame photos, natural-Tamil prompt + whole-sentence subtitle cues; 1-minute DAC video (job 8670fb03, unpublished) | `standalone/audio-module/{app/tts/edge_neural.py,app/tts/registry.py,app/config.py,requirements.txt,tests/test_tts_safety.py}`, `standalone/video-visual-module/{app/api/visual_plans.py,app/pipeline/{intent,motion,shot_planner}.py,tests/test_planning.py}`, `backend/config/{evidence_policy,source_policy}.json`, `backend/defence_studio/{contracts/discovery.py,integrations/subtitles.py,services/{audio,evidence_policy,evidence_readiness,script,script_prompt,visual}.py}`, `backend/scripts/fill_open_images.py`, `backend/tests/{test_content_pipeline_fixes,test_evidence_policy_scoring,test_render_rm5}.py`, `project_status.md` | Backend full suite 741 passed / 14 failed, all environmental on this Windows account (no pywin32 `pywintypes`, fresh subprocesses without venv path, ffmpeg not on PATH; voice tests pass with ffmpeg); audio module 64 passed; visual module 208 passed / 1 env failure | `9a6639a`, `18008c1` (+ this register commit) | NOT PUSHED: this Windows account ("Jana Local Account") has no GitHub sign-in; Git Credential Manager waited for interactive login (push stopped after 300 s). Both remotes were `0 0` with `fd1aa6d` before the commits. | Runtime `data/` DB/storage left uncommitted (pre-existing). Push needs an interactive `git push origin develop` + `git push axythic develop` by the user. |
| 2026-10-28 | Claude | Adopt global Tamil script writing rules as mandatory default; extend Tamil script-drafting prompt | `docs/TAMIL_SCRIPT_RULES.md`, `backend/defence_studio/services/script_prompt.py`, `CLAUDE.md`, `automation/GIT_COMMIT_SYNC_RULE.md` | content_pipeline_fixes + openai_structured_output + script_final_validation + script_inspection: 40 passed, 30 errors = env `StoragePathError` (temp path >240 chars on this account; identical on baseline) | see git log | see final report | Push depends on GitHub sign-in for this Windows account |
