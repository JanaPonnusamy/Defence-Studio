# Defence Era — TTS implementation tracker

Development computer: the owner's home development PC (sole authorised development machine from 2026-11-01).
Machine identifiers, local paths, backup details and session details are kept in private notes, not in this repository.
Branch: `feature/home-tts-development` (separate worktree; the main working tree is not edited).
Labels: IMPLEMENTED / TESTED / INTEGRATED / LIVE-VERIFIED / ACCEPTED are not interchangeable.

## 1. Git synchronisation (2026-11-01)

| Item | Evidence |
|---|---|
| Remotes | `origin` = JanaPonnusamy/Defence-Studio (default branch `main`) · `axythic` = axythic-dev/Defence-Studio (default branch `develop`). Two **independent public repositories, not forks** (GitHub API `fork: false`, `private: false`) |
| PR numbering | PR numbers are per repository. axythic-dev PRs #1–#4: #3 (`develop → main`) and #4 (`main → develop`) were merged on 2026-11-01 and produced `4076e5d`. JanaPonnusamy PRs: #1 (Tamil voice picker) and #2 (this T0 work), both open |
| Remote for this work | `origin` only, by owner instruction for this task. `axythic` has no `feature/home-tts-development` branch. This deviates from the dual-remote rule in `automation/GIT_COMMIT_SYNC_RULE.md`; see owner decision §9.6 |
| Fetch / ancestry | `git fetch origin --prune`. Local `develop` `4076e5d` vs `origin/develop`: `0 0`. `ls-remote axythic`: `develop` = `main` = `4076e5d`. No divergence |
| Branch protection / CI | none on either repo; no GitHub Actions workflows |

## 2. Original working tree (preserved)

- The main working tree on `develop` `4076e5d` holds **66 pre-existing uncommitted entries**: 65 from earlier sessions plus the untracked home audit report. It is not edited by this work. They await selective review/migration.
- A private, hash-verified backup of those entries exists outside the repository (manifest, patches and file copies; secret scan clean). Its location and access controls are recorded privately.
- A clean baseline worktree at `4076e5d` is kept for failure comparisons.

## 3. Pull requests

| PR | Scope | Tests | Status |
|---|---|---|---|
| [JanaPonnusamy #1](https://github.com/JanaPonnusamy/Defence-Studio/pull/1) `feature/tamil-tts-acceptance → develop` | `753cfa3` Tamil voice picker + `9ab3f41` merge of develop. Net 7 files, +394/−4 | backend 34 passed / 1 skipped (live module); frontend voice-picker 4/4; audio-module suite not run (§6.4) | **OPEN**, not merged, awaiting owner approval |
| [JanaPonnusamy #2](https://github.com/JanaPonnusamy/Defence-Studio/pull/2) `feature/home-tts-development → develop` | T0 (§4) + T0 documentation cleanup (§5) + T1 design (§7) | §6 | **OPEN**, not merged, awaiting owner approval |

## 4. Phase T0

| Task | Status | Files |
|---|---|---|
| T0-A dataset defect (`datasets.py:262`) | **FIXED + TESTED.** Defect: clearing a transcript while setting `REVIEWED` fell back to the *stored* transcript, so an empty transcript became REVIEWED and training-eligible. Reproduced (HTTP 200 instead of 422), then fixed: a supplied transcript (even cleared) takes precedence | `voice_lab/datasets.py`, `tests/test_voice_lab.py::test_clearing_a_transcript_cannot_mark_it_reviewed` |
| T0-B Tamil text prep in Voice Lab | **IMPLEMENTED + TESTED.** `generation.prepare_text` reuses `services/tts_text.prepare` (real lexicon + designations). The model receives the spoken form; the approved text stays on the job. Provenance `text_preparation` (spoken, sha, replacements, warnings, lexicon sha); unknown terms appear as `pronunciation:` limitations; the Tamil request key includes the spoken sha (deterministic reuse, invalidated by table changes); broken Tamil → 422 `TAMIL_TEXT_INVALID`. English is unchanged (same key, verbatim text) | `voice_lab/generation.py`, 2 tests in `tests/test_voice_lab.py` |
| T0-C voice-conversion interface | **INTERFACE + GUARDS TESTED. No model.** `conversion.py`: `OwnerVoice`, `ConversionRequest`, `ConversionResult`, `VoiceConverter`, `UnavailableConverter`, `run_conversion` (consent scope GENERATION, missing source/owner audio, refuses CONVERTED without model identity or output) | `voice_lab/conversion.py`, `tests/test_voice_lab_identity.py` |
| T0-D speaker-verification interface | **INTERFACE + VERDICT RULES TESTED. No model.** `verification.py`: owner/reference similarity, margin, leakage verdict; identity is never PASS without measured scores and a validated calibration for the same model | `voice_lab/verification.py`, `tests/test_voice_lab_identity.py` |
| T0-E integration design | **PLAN WRITTEN** (not connected) | `docs/voice_lab/OWNER_VOICE_INTEGRATION_PLAN.md` |
| Docs | Voice Lab README module table; this tracker | `voice_lab/README.md`, `DEFENCE_ERA_TTS_IMPLEMENTATION_TRACKER.md` |

Readiness: T0 is **ready for code review**. T0-A/T0-B are IMPLEMENTED + TESTED (automated). T0-C/T0-D
have interface + guards TESTED; there are no models, so nothing is LIVE-VERIFIED. T0-E is a plan.
Nothing is INTEGRATED into RM-4/RM-5, and nothing is ACCEPTED by a human. Behaviour change to note:
Tamil Voice Lab request keys now include the spoken-form hash, so Tamil generations made before T0
regenerate once (English keys unchanged).

## 5. T0 documentation cleanup (2026-11-01)

| Item | Finding | Action |
|---|---|---|
| `DEFENCE_ERA_HOME_TTS_AUDIT.md` | Exposed hostname, hardware make/model, local account name, local repository/worktree/venv/office paths, local branch and worktree layout, and session details | **Removed from the branch.** The original remains privately (main working tree, untracked, and the private backup; SHA-256 identical). Not deleted anywhere |
| This tracker | Exposed a personal email address, hostname, local paths, backup location and ACL configuration, repository permission level, and agent process IDs/session timing | Removed; technical evidence, test results, limitations and decisions retained |
| Remote/PR references | "PRs #1–#4 were on axythic-dev" next to a JanaPonnusamy PR #1 (ambiguous numbering); the T0 PR had no number or link; a path contained a mangled escape | Corrected (§1, §3); paths removed |
| `OWNER_VOICE_INTEGRATION_PLAN.md` | Cited the audit file as its basis | Now cites the private audit generically and links the T1 design |
| Secrets scan | All PR files scanned for emails, drive paths, hostnames, account names, token patterns (`ghp_`, `github_pat`, `hf_`, `sk-`, `token=`, `password`) | **None found** in the remaining files. No recordings in the PR |
| **Limitation** | Both repositories are public. Commit `cee7222`, which contains the removed audit and the old tracker text, stays reachable in branch history. Removing it from history requires a force-push or a squash, which is not authorised | **Owner decision §9.7** |

## 6. Tests

Environment: backend test venv (Python 3.13.15, pytest 9.2.0), `--basetemp=<short path>` (§8), Node 24.19.0. cwd `backend/` unless noted.

### 6.1 T0 runs

| # | Command | Tree | Result |
|---|---|---|---|
| 1 | `pytest tests/test_voice_lab.py tests/test_voice_lab_gpu.py` (before any T0 edit) | T0 @ `4076e5d`, clean | **27 passed**, 0 failed |
| 2 | `pytest tests/test_voice_lab.py -k clearing_a_transcript` (new test, before the fix) | T0, test only | **1 failed** (`assert 200 == 422`): defect reproduced |
| 3 | `pytest tests/test_voice_lab.py -k "clearing_a_transcript or dataset"` (after the fix) | T0 | **3 passed** |
| 4 | `pytest tests/test_voice_lab.py -k "text_preparation or english_generation or generation_reuse or script_studio"` | T0 | **4 passed** |
| 5 | `pytest tests/test_voice_lab_identity.py` | T0 | **11 passed** |
| 6 | `node --test frontend/tests/voice.test.mjs` (cwd repo root; sanity, no frontend change) | T0 | **6 passed**, 0 failed |
| 7 | **Full suite** `pytest -q -p no:cacheprovider --basetemp=<short> -rfE tests` (4781.66 s), exit 1 | T0 (all edits) | **3 failed, 954 passed, 11 skipped**, 0 errors, 0 deselected = **968 collected**. Skip reasons not captured (`-rfE` only) |
| 8 | Same 3 node IDs (below) | baseline `4076e5d`, clean | **3 failed**, identical traceback |
| 9 | `python -m py_compile` on the 4 changed/new modules + new test file | T0 | OK. No linter/type checker is configured |

The 3 failures in runs 7/8 (before §6.2):

| Node ID | Cause (T0 and baseline identical) | Classification |
|---|---|---|
| `tests/test_p0_startup.py::test_entrypoint_secret_validation_in_fresh_process[None]` | the fresh subprocess (`PYTHONPATH` removed, `cwd=tmp_path`) raised `ModuleNotFoundError: No module named 'defence_studio'` | **ENVIRONMENTAL** |
| `...[short]` | same | **ENVIRONMENTAL** |
| `...[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]` | same | **ENVIRONMENTAL** |

### 6.2 Startup-test environment repair (2026-11-01)

Root cause: the backend package was not installed in the test venv. The test deliberately starts a fresh
interpreter outside the source tree, as an installed deployment would, so it needs the documented install.
Documented method: `backend/README.md` → from `backend/`, `pip install -e ".[test]"`. The same method is
used by the P0 clean-install evidence.

| # | Command | Result |
|---|---|---|
| 10 | `python -m pip install -e ".[test]" --dry-run` (cwd T0 `backend/`) | would install only `defence-studio-0.1.0`; every dependency already satisfied |
| 11 | `pip freeze` before/after + `python -m pip install -e ".[test]"` | exit 0; freeze diff = **one added line** (editable `defence_studio` from the T0 worktree); `pip check`: no broken requirements; import from `/` resolves to the T0 worktree |
| 12 | `pytest -q -p no:cacheprovider --basetemp=<short> "tests/test_p0_startup.py::test_entrypoint_secret_validation_in_fresh_process"` | **3 passed**, 1 warning (starlette TestClient deprecation), exit 0, 5.02 s |

- No production or test code was changed.
- Full suite not rerun. `test_p0_startup.py` is the only backend test that launches a fresh interpreter
  (`sys.executable`), and the editable install points at the same tree pytest already imports from. No
  other test's import resolution changes.
- Caveat: the venv is shared. The editable install points at the **T0 worktree**, so a fresh-process
  import from another worktree using this venv loads T0's package. Re-run `pip install -e` from the tree
  under test before testing another tree.

### 6.3 Skip reasons

Run 13: `pytest -q -p no:cacheprovider --basetemp=<short> -rsfE` over the 16 files that contain skip
markers (15 files + `test_p0_startup.py`; 193 collected), after §6.2. Result: **182 passed, 11 skipped,
0 failed**, exit 0, 991.19 s. Its 11 skips match the 11 skips of full-suite run 7 in number.

| Test location | Skip reason | Class |
|---|---|---|
| `test_assets_ingest_rm9.py:184` | real Visual Module not running | live service not running |
| `test_audio_rm4.py:258` | real Audio Module not running at `DEFENCE_STUDIO_AUDIO_MODULE_URL` | live service not running |
| `test_formats_rm10.py:53` | real Visual/Audio/Renderer modules not all running | live service not running |
| `test_render_rm5.py:509` | real Visual/Audio/Renderer modules not all running | live service not running |
| `test_visual_rm3.py:253` | real Visual Module not running at `DEFENCE_STUDIO_VISUAL_MODULE_URL` | live service not running |
| `test_factcheck_engine.py:488` | opt-in live RSS: set `DEFENCE_STUDIO_LIVE_FACTCHECK=1` | opt-in live network |
| `test_integration_live.py:32` | set `DEFENCE_STUDIO_LIVE_HTTP=1` | opt-in live network |
| `test_integration_live.py:45` | set `DEFENCE_STUDIO_LIVE_YOUTUBE=1` | opt-in live network |
| `test_live_content_pipeline.py:54` | opt-in live OpenAI; set `DEFENCE_STUDIO_LIVE_CONTENT_PIPELINE=1` | opt-in live paid API |
| `test_storage_lifecycle.py:249` | symlinks not permitted on this account | OS permission |
| `test_voice_integration.py:109` | no local database to copy | local data absent |

None of the skips is caused by a missing Python dependency. ffmpeg, Tamil fonts and the MAX_PATH guard
skips did not trigger. The audio-module live test (`test_audio_rm4.py:258`) needs the module running
as a service, which in turn needs its own venv (§6.4).

### 6.4 Audio-module test dependencies

`standalone/audio-module` documents its own isolated venv (`README.md`: `.venv\Scripts\python -m pip install -r requirements.txt`, then `.venv\Scripts\python -m pytest`). That venv does not exist on this PC. The backend venv lacks `sqlalchemy`, `soundfile`, `librosa`, `edge_tts`, `pyttsx3` and `pydantic_settings` (`numpy` present). Installing them into the backend venv would break the module's documented isolation, so nothing was installed. Running that suite needs owner approval to create the module venv (package downloads only, no models).

## 7. Phase T1 (design only)

`docs/voice_lab/T1_VOICE_IDENTITY_POC_DESIGN.md`: candidate VC, cloning and speaker-verification models
with licence evidence, the verification and calibration design, reference-delivery reuse and its gaps,
the POC experiment, hardware feasibility, and the owner decisions. **Nothing is installed, downloaded or
run. No recordings are used.**

## 8. Notes

- On this account, pytest's default temp dir pushes artifact paths over the app's 240-char Windows path limit, so all runs use a short `--basetemp`.
- The baseline worktree is kept for comparisons and can be removed with `git worktree remove` when no longer needed.

## 9. Owner approvals required

1. Merge PR #1 (Tamil voice picker) after review. There is no CI, and GitHub does not allow approving your own PR.
2. Review and merge PR #2 (T0 + cleanup + T1 design).
3. T1 execution decisions: see `T1_VOICE_IDENTITY_POC_DESIGN.md` §8 (models, gates, recordings, consent, impostor data, listeners).
4. Optional: create the audio-module venv to run its tests (§6.4).
5. Optional: accept the Indic Parler HF gate + local `HF_TOKEN`.
6. Decide whether `axythic` should be kept in sync (dual-remote rule) or `origin`-only for this branch.
7. Decide whether to remove commit `cee7222`'s audit/tracker text from public history (squash on merge, or an authorised force-push). Not done.
