# Defence Era — TTS implementation tracker

Authoritative development computer: **Lenovo `DESKTOP-TPJPR62`** (sole authorised dev PC from 2026-11-01).
Development worktree: `D:\jana soft\DefenceEra-TTS-Development` · branch `feature/home-tts-development`.
Labels: IMPLEMENTED / TESTED / INTEGRATED / LIVE-VERIFIED / ACCEPTED are not interchangeable.

## 1. Git synchronisation (2026-11-01)

| Item | Evidence |
|---|---|
| Identity | hostname `DESKTOP-TPJPR62`; git user `Jana Ponnusamy <janaponnusamy@gmail.com>`; GitHub API login `JanaPonnusamy` |
| Remotes | `origin` = JanaPonnusamy/Defence-Studio (admin; default `main`) · `axythic` = axythic-dev/Defence-Studio (push; default `develop`). **Independent repos, not forks.** |
| Authoritative remote | **`origin`** (owner's project, as specified for this task). Note: PRs #1–#4 so far were on axythic-dev. Per this task, only `origin` was pushed; axythic is **not** updated (deviation from the dual-remote rule in `automation/GIT_COMMIT_SYNC_RULE.md`, by owner instruction) |
| Fetch | `git fetch origin --prune`; `ls-remote` axythic: both `develop` = `main` = `4076e5d` |
| Ancestry | local `develop` `4076e5d` vs `origin/develop`: `0 0`. Every tracked local branch `0 0`, except `feature/tamil-tts-acceptance` (see §4). `rebuild/develop` `249aff2` local-only (old; untouched). **No divergence.** |
| Fast-forward 18:12 | Another session fast-forwarded `develop` `c7f8d50 → 4076e5d` (PRs #3/#4) with the 65 changes intact (no conflicts) |
| Branch protection / CI | none on either repo; no GitHub Actions workflows |

## 2. Backup of the original working tree (65 entries + the home audit report)

| Item | Value |
|---|---|
| Location | `D:\jana soft\_migration_backups\DefenceEra_pre_sync_20261101\` (`BACKUP_MANIFEST.md`) |
| Content | staged.patch + unstaged.patch (binary), status, untracked list, 151 file copies with structure, 6 index versions of `MM` files, SHA-256 of all |
| Verification | 151/151 hashes match originals; staged.patch replays onto HEAD to the identical index tree `09b76eb2`; staged+unstaged replay == working tree. Not backed up: none. Secret scan: clean |
| Protection | ACL: owner, SYSTEM, Administrators only (inheritance removed) |
| Original tree | `D:\jana soft\DefenceEra` **unchanged** (status hash identical before/after worktree creation). Its 65 changes still await selective review/migration |

## 3. Worktrees

| Path | Branch / HEAD | Owner |
|---|---|---|
| `D:\jana soft\DefenceEra` | `develop` `4076e5d` + 65 uncommitted | preserved, do not edit |
| `D:\jana soft\DefenceEra-TTS-Development` | `feature/home-tts-development` from `origin/develop` `4076e5d` | **this TTS work** |
| `D:\jana soft\wt-tamil-tts-acceptance` | `feature/tamil-tts-acceptance` `9ab3f41` | created by another session 18:12; clean |
| `D:\rsw1` | detached `c7f8d50` | untouched |

## 4. Pull requests

| PR | Scope | Tests | Status |
|---|---|---|---|
| [#1 JanaPonnusamy/Defence-Studio](https://github.com/JanaPonnusamy/Defence-Studio/pull/1) `feature/tamil-tts-acceptance → develop` | `753cfa3` Tamil voice picker + `9ab3f41` merge of develop (register conflict resolved by keeping both rows). Net 7 files, +394/−4 | backend 34 passed / 1 skipped (live module); frontend voice-picker 4/4; audio-module suite not run (deps missing; untouched by PR) | **OPEN, mergeable (clean), 0 checks. Not merged: awaiting owner approval** |
| T0 PR (this branch) | see §5 | see §6 | see §7 |

## 5. Phase T0

| Task | Status | Files |
|---|---|---|
| T0-A dataset defect (`datasets.py:262`) | **FIXED + TESTED.** Defect: clearing a transcript while setting `REVIEWED` fell back to the *stored* transcript, so an empty transcript became REVIEWED and training-eligible. Reproduced (HTTP 200 instead of 422), then fixed: a supplied transcript (even cleared) takes precedence | `voice_lab/datasets.py`, `tests/test_voice_lab.py::test_clearing_a_transcript_cannot_mark_it_reviewed` |
| T0-B Tamil text prep in Voice Lab | **IMPLEMENTED + TESTED.** `generation.prepare_text` reuses `services/tts_text.prepare` (real lexicon + designations). The model receives the spoken form; the approved text stays on the job. Provenance `text_preparation` (spoken, sha, replacements, warnings, lexicon sha); unknown terms appear as `pronunciation:` limitations; the Tamil request key includes the spoken sha (deterministic reuse, invalidated by table changes); broken Tamil → 422 `TAMIL_TEXT_INVALID`. English is unchanged (same key, verbatim text) | `voice_lab/generation.py`, 2 tests in `tests/test_voice_lab.py` |
| T0-C voice-conversion interface | **INTERFACE + GUARDS TESTED. No model.** `conversion.py`: `OwnerVoice`, `ConversionRequest`, `ConversionResult`, `VoiceConverter`, `UnavailableConverter`, `run_conversion` (consent scope GENERATION, missing source/owner audio, refuses CONVERTED without model identity or output) | `voice_lab/conversion.py`, `tests/test_voice_lab_identity.py` |
| T0-D speaker-verification interface | **INTERFACE + VERDICT RULES TESTED. No model.** `verification.py`: owner/reference similarity, margin, leakage verdict; identity is never PASS without measured scores and a validated calibration for the same model | `voice_lab/verification.py`, `tests/test_voice_lab_identity.py` |
| T0-E integration design | **PLAN WRITTEN** (not connected) | `docs/voice_lab/OWNER_VOICE_INTEGRATION_PLAN.md` |
| Docs | Voice Lab README module table; this tracker; home audit copied | `voice_lab/README.md`, `DEFENCE_ERA_TTS_IMPLEMENTATION_TRACKER.md`, `DEFENCE_ERA_HOME_TTS_AUDIT.md` |

## 6. Tests (Python 3.13.15 `D:\jana soft\venvs\defence-backend`, pytest 9.2.0, `--basetemp=D:/pt_tmp`; Node 24.19.0)

| # | Command (cwd `backend/` unless noted) | Tree | Result |
|---|---|---|---|
| 1 | `pytest tests/test_voice_lab.py tests/test_voice_lab_gpu.py` (before any T0 edit) | T0 worktree @ `4076e5d`, clean | **27 passed**, 0 failed |
| 2 | `pytest tests/test_voice_lab.py -k clearing_a_transcript` (new test, before the fix) | T0, test only | **1 failed** (`assert 200 == 422`): defect reproduced |
| 3 | `pytest tests/test_voice_lab.py -k "clearing_a_transcript or dataset"` (after the fix) | T0 | **3 passed** |
| 4 | `pytest tests/test_voice_lab.py -k "text_preparation or english_generation or generation_reuse or script_studio"` | T0 | **4 passed** |
| 5 | `pytest tests/test_voice_lab_identity.py` | T0 | **11 passed** |
| 6 | `node --test frontend/tests/voice.test.mjs` (cwd repo root; sanity, no frontend change) | T0 | **6 passed**, 0 failed |
| 7 | **Full suite** `pytest -q -p no:cacheprovider --basetemp=D:/pt_tmp -rfE tests`, started 18:44:51, finished ~20:04:35 (4781.66 s), pytest exit 1 | T0 (all edits) | **3 failed, 954 passed, 11 skipped**, 0 errors, 0 deselected = **968 collected** (reconciles). Skip reasons not captured (`-rfE` reports only failures/errors) |
| 8 | Same 3 node IDs: `pytest "tests/test_p0_startup.py::test_entrypoint_secret_validation_in_fresh_process"`, 20:05:01–20:05:47, exit 1 | **baseline** `D:\jana soft\_baseline_develop_4076e5d` @ `4076e5d`, 0 status entries before and after | **3 failed**, identical traceback |
| 9 | `python -m py_compile` on the 4 changed/new modules + new test file | T0 | OK. No linter/type checker is configured (`backend/pyproject.toml` has no ruff/mypy/flake8 section; none installed) |

### Failure comparison (full suite)
| Node ID | T0 cause | Baseline | Classification |
|---|---|---|---|
| `tests/test_p0_startup.py::test_entrypoint_secret_validation_in_fresh_process[None]` | fresh subprocess (`PYTHONPATH` removed, `cwd=tmp_path`): `ModuleNotFoundError: No module named 'defence_studio'` | same failure, same traceback | **ENVIRONMENTAL** |
| `...[short]` | same | same | **ENVIRONMENTAL** |
| `...[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]` | same | same | **ENVIRONMENTAL** |

Evidence for ENVIRONMENTAL: `find_spec('defence_studio')` with the venv interpreter is True only from `backend/` and False from `/`. The package is not installed in `D:\jana softenvs\defence-backend`, so the test's fresh process cannot import it. T0 touches no startup or auth code. **Introduced regressions: none found.**

## 7. Commits and PR

- Branch `feature/home-tts-development`, one T0 commit on top of `origin/develop` `4076e5d`; hash and PR link in the PR / `git log`.
- Pushed to `origin` only (no force). `axythic` not pushed (owner instruction for this task).
- Merge: **not merged**; requires explicit owner authorisation.

### Readiness
T0 is **ready for code review**. IMPLEMENTED + TESTED (automated) for T0-A/T0-B; interface + guards TESTED for T0-C/T0-D (no models, so no LIVE-VERIFIED); T0-E is a plan. Nothing is INTEGRATED into RM-4/RM-5, and nothing is ACCEPTED by a human. Behaviour change to note: Tamil Voice Lab request keys now include the spoken-form hash, so Tamil generations made before T0 regenerate once (English keys unchanged).

## 8. Owner approvals required

1. Merge PR #1 (Tamil voice picker) after review. There is no CI; GitHub does not allow approving your own PR.
2. Review and merge the T0 PR.
3. Choose and approve one training-free voice-conversion model and one speaker-verification model (licence review, then install in an isolated venv). Nothing is installed.
4. Owner recordings (3–5 min, clean) + consent record (GENERATION scope).
5. Optional: accept the Indic Parler HF gate + local `HF_TOKEN`.
6. Decide whether `axythic` should be kept in sync (dual-remote rule) or `origin`-only from now on.
7. Native Tamil listener for acceptance.

## 9. Blockers / notes

- A second Claude Code session (PID 16332, started 18:10) made the 18:12 fast-forward, the Tamil worktree and the 18:26 push. The owner chose this session as the sole writer, and PID 16332 had exited by 19:21. Only this session (7916) was running at commit time.
- Baseline checkout `D:\jana soft\_baseline_develop_4076e5d` (detached, clean) kept for comparisons; remove it with `git worktree remove` when no longer needed.
- Environmental: `defence_studio` is not installed in the backend venv, so the 3 fresh-process startup tests fail on both baseline and T0 (`pip install -e backend` in that venv would be the fix; not done without approval).
- Environmental test issue on this account: pytest's default temp dir pushes artifact paths over the app's 240-char Windows limit, so all runs use `--basetemp=D:/pt_tmp`. That folder can be deleted by the owner.
- `standalone/audio-module` tests need sqlalchemy/soundfile/librosa, which are not installed in the backend venv.
