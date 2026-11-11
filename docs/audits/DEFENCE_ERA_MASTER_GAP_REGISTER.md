# Defence Era — Master Gap Register

Audit date: 2026-11-06. Workspace: `D:\jana soft\DefenceEra`. Branch: `develop`. HEAD: `fafc7247efaff6eba39f246dd285dd1d41838d39` (`origin/develop`). Remotes: `origin` = JanaPonnusamy/Defence-Studio, `axythic` = axythic-dev/Defence-Studio.

This register is from the source tree on that date, including uncommitted work that was already in the working tree. It does not treat older phase rows as current when later code contradicts them. Runtime suites were not re-run for every module in this pass. A status of TESTED means a named test file exists and a prior recorded run passed; where this pass did not re-execute that suite, column B says **not re-run 2026-11-06**.

## How to read a row

| Column | Meaning |
|---|---|
| A | Implementation present in source |
| B | Automated tests exist and their last recorded result |
| C | Modules wired together in this repository |
| D | A real external provider or device was exercised |
| E | A person still has to accept the result |

Status values: `COMPLETE` · `PARTIAL` · `BLOCKED` · `NOT_IMPLEMENTED` · `NEEDS_VERIFICATION`.

`COMPLETE` requires A–D for the module's agreed scope, or an explicit statement that D does not apply because the module has no external provider. Mocks are never column D. `human-only-approval.v1` stays in force: automated checks may recommend; they do not approve evidence, scripts, or publishing.

## Working tree at audit time

HEAD is clean relative to `origin/develop`. The working tree is not. Uncommitted work from earlier sessions is preserved and is not part of this audit's commit:

- Modified shared files include `api/app.py`, `config.py`, `services/{script,audio,render,worker,tts_text,speech_reference*}.py`, `discovery/providers.py`, `image_research/service.py`, `providers/openai_text.py`, `frontend/js/app.js`, `frontend/index.html`, `project_status.md`.
- Untracked modules include `backend/defence_studio/system_resources/`, speech-reference routes, Voice Lab UI (`frontend/js/voice-lab/`, `voice-lab-view.js`), system-resources UI, and acceptance-sprint analysis.

`DEFENCE_ERA_PHASE_STATUS.md` still describes the 2026-10-14 baseline (P8/P9/X1–X3 `NOT_STARTED`). `project_status.md` and the route table supersede those rows. Schema in `database.py` is **v16**. Voice Lab and system resources use their own `vl_` / `rs_` tables.

## Critical blockers

| ID | Priority | Blocker | What it stops |
|---|---|---|---|
| K1 | P1 | No Google OAuth client recorded in the environment | Live YouTube connect and private upload |
| K2 | P1 | Indic Parler-TTS gated; `HF_TOKEN` not accepted for that model | Tamil Voice Lab speech |
| K3 | P1 | No CUDA GPU on this PC (RX 560X + UHD 630 only) | Voice Lab training |
| K4 | P1 | Hosted search URL and key unset | Topic search beyond RSS keyword match |
| K5 | P2 | Tesseract absent; reverse-image gateway unset | Fact-check OCR and live reverse image |
| K6 | P2 | Asset Rights Engine has no host adapter | Clearance from the standalone rights engine |
| K7 | P1 | OpenAI spend is owner-gated | A new live script run was not started in this audit |
| K8 | P2 | Full backend/frontend suites not re-run on 2026-11-06 | Current dirty tree is `NEEDS_VERIFICATION` as a whole |

## Module register

| # | Module | Status | Intended behaviour | What exists | Missing | A | B | C | D | E |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Main application and dashboard | PARTIAL | Job list, stage navigation, responsive shell | `frontend/js/views/dashboard-view.js`, tab shell in `app.js`, `/api/jobs` | Populated-data browser pass not re-run this audit. Dirty `app.js` / `index.html` include uncommitted tabs | Yes | Frontend tests exist; not re-run 2026-11-06 | Shell mounts research through production | Local UI only | Owner visual acceptance |
| 2 | Authentication and user management | PARTIAL | Authenticated owner, no cross-owner access | `auth.py` HS256 bearer; `dev_auth.py` double-flag loopback bypass; owner checks on jobs | No user directory, roles, or password store. Dev bypass is local-only | Yes | Auth tests exist; not re-run 2026-11-06 | Every API route uses `require_owner` | Loopback dev session historically used | Production identity provider not chosen |
| 3 | AI assistant and orchestration | PARTIAL | One durable job across research, script, scene, voice, render | `services/worker.py` leases; stage handlers; pipeline trace | No chat assistant. Ollama is not a product worker in this repo. Script Studio OpenAI calls are not on the resource scheduler | Yes | Worker crash/lease tests exist | Stages share `workflow_jobs` | Restart recovery tested historically | — |
| 4 | Content research | PARTIAL | Guarded fetch, claims, evidence package | `acquisition/`, `analysis/`, `services/evidence.py`, research worker | Live HTTP/YouTube opt-in tests still skippable. Research package has no image/rights field (G3). Syndication is text similarity (G6) | Yes | Named research tests exist | Discovery feeds the package | BrahMos job `9530b8c6` recorded 2026-11-01: 6 acquired, not approved | Human review of that package |
| 5 | Topic discovery and management | PARTIAL | Feeds plus hosted search, taxonomy, history | `discovery/providers.py`: RSS, configured search API, manual URL. Taxonomy and job-derived history | Hosted search `NOT_CONFIGURED` (K4). No X/social/YouTube-search provider (G1, unspecified) | Yes | `test_discovery_f1a` and manual-url tests exist | Wired to acquisition | RSS live-verified 2026-10-14 on four feeds; not re-run here | — |
| 6 | Script generation | PARTIAL | Source-grounded Tamil and English scripts, 900–1,300 spoken words, human approval | `services/script.py`, `script_studio.py`, `script_integrity.py` (`NARRATION_LENGTH_OUT_OF_RANGE`), `providers/openai_text.py` | Live generation not repeated this audit (K7). Native-speaker Tamil review open | Yes | `test_narration_length.py` covers 899/900/1300/1301; `test_script_phase3` exists | Requires a human-approved package | Historical OpenAI calls recorded (Tamil job 8670fb03; BrahMos extraction). Not repeated | Human script approval |
| 7 | Tamil TTS and voice cloning | BLOCKED | Owner-voice Tamil narration | Production path: approved scene → `services/audio.py` → Audio Module `edge-neural` (`ta-IN-PallaviNeural`) with OneCore fallback. Voice Lab Parler path is separate | Indic Parler-TTS blocked (K2). No owner-voice clone. Training blocked (K3). Edge Pallavi sample is not a full-pipeline render | Partial | Audio module and TTS designation tests exist | Audio Module HTTP adapter | One Edge Pallavi clip recorded 2026-11-04 (3.12 s). English Parler CPU benchmark 2026-11-01 | Tamil listening review |
| 8 | Speech configuration and Voice Lab | PARTIAL | Narrators, datasets, references, style, mixer | `backend/defence_studio/voice_lab/` (~74 routes). UI in untracked `frontend/js/voice-lab/` calls hardware, training, and script-generation | Browser mixer is local. Server `/projects` not the UI store. Real datasets and consent absent. Music separation `NOT_AVAILABLE` | Yes, backend | `test_voice_lab.py`, `test_voice_lab_gpu.py` recorded 2026-11-01 | Script Studio API exists; Script Studio UI does not call it for narration | English evaluation model CPU RTF ~12.5. DirectML Parler recorded not runnable | Listening evaluation not done |
| 9 | Audio processing and alignment | PARTIAL | Measured duration and word timing | Audio artifacts hashed; approximate alignment stored. `components/defence-align/` standalone | Forced alignment not in the production path. MMS-FA is CC-BY-NC | Partial | Audio module tests; aligner 76 passed standalone historically | Duration feeds subtitles | English local voice and one Tamil Edge path | — |
| 10 | Visual generation | PARTIAL | Scene plan to a renderer-neutral timeline | `services/visual.py` calls the Visual Module. Wikimedia fill for open licences | AI image generation stays off. Shots without cleared assets stay gaps. Tamil model images not a product path | Yes | Visual module suite recorded later than the Oct audit | HTTP integration | Real module used on the English chain and the 2026-10-28 Tamil short | Rights review per asset |
| 11 | Media and rights management | PARTIAL | Provenance and a visible rights status on every asset | Core asset routes; image research (`/api/image-research/*`) with human accept/reject. Standalone Asset Rights Engine under `components/asset-rights-engine/` | ARE adapter not started (K6). `crop_detection` returns `NOT_IMPLEMENTED`. Image research files are dirty | Partial | `test_image_research.py` recorded; not re-run 2026-11-06 | Image research is a separate job, not the scene asset ledger | Rafale image-research browser session 2026-11-01 on a temp DB | Human rights decision per image |
| 12 | Video rendering | PARTIAL | Approved picture, sound, and subtitles to an MP4 | `services/render.py`, renderer HTTP API, libass subtitles | Asset-byte gaps still render as visual-gap cards when rights are missing. Renderer sample is not a substitute for a new topic | Yes | Renderer suite recorded 2026-11-04 (118 passed / 1 skipped) | Chained after measured visual + audio | English three-module chain and one unpublished Tamil short (job 8670fb03) | — |
| 13 | Content assembly | PARTIAL | Intro, main, outro, thumbnail, metadata, manifest | `services/package.py`, branding integration, format presets including a vertical short | Package stays private. Duplicate-title check exists; it is not a channel analytics product | Yes | Package tests exist | Requires QC approval | Real FFmpeg package builds recorded | Human package review |
| 14 | Human review and approval | PARTIAL | People approve evidence, scripts, QC, and publish jobs | `services/human_approval.py` (`human-only-approval.v1`). Evidence, script, scene, QC, and YouTube approve routes refuse automated actors. Legacy `AUTOMATED_*` rows stay in history and do not make a package eligible | This audit's UI change shows policy `recommendation`, waiting state, legacy history, and missing items on the evidence screen. QC/YouTube screens were not redesigned. Publishing is still a separate human step | Yes | `approval-flow.test.mjs` updated and run this pass (see below). Backend approval suite not re-run | Gates script, scene, render, package, and YouTube | Policy recommendation verified in source (`evidence_policy.run` never writes an approval) | Every production approval |
| 15 | YouTube OAuth | BLOCKED | Web-server OAuth, channel confirm, encrypted tokens | `services/youtube.py`, `/api/youtube/connect/*` | Live Google behaviour unverified (K1). Mock tests are not column D | Yes | `test_youtube_rm8` mocked | Production view has connect controls | None | Channel owner must authorize |
| 16 | YouTube publishing | BLOCKED | Private upload, read-back, thumbnail and captions | Resumable upload state machine behind the same service. Upload requires a human approve on the YouTube job | No video ID from Google (K1). Automatic publishing is not implemented and must stay off | Yes | Mocked | Starts only from an approved package | None | Human approve, then a real private upload |
| 17 | Publishing history and analytics | PARTIAL | Reopen prior videos and see what was published | YouTube job list and duplicate-title / script-repetition checks | No YouTube Analytics API. History cannot show real watch data | Partial | Repetition tests exist | Reads local job records | None | — |
| 18 | Ollama integration | NOT_IMPLEMENTED | Not a Defence Era product module | No `ollama` package or route in `backend/defence_studio` | Engineering use of Ollama, if any, stays outside this repository | No | — | — | — | — |
| 19 | Claude review workflow | NOT_IMPLEMENTED | External engineering process, not an application feature | `docs/AI_PARALLEL_WORK_CONTROL.md` records multi-agent claims | No in-app Claude reviewer | No | — | — | — | — |
| 20 | OpenAI integration | PARTIAL | Budget-guarded text generation and inspection | `providers/openai_text.py`, inspector routes, per-call cost fields | A new paid call was not made (K7). Transport retries remain | Yes | Provider tests use fakes | Script and optional extraction | Historical live calls; none in this audit | Budget owner |
| 21 | Provider configuration and model selection | PARTIAL | Choose providers without leaking secrets | Env settings, encrypted per-owner credential store (RM-9), Voice Lab model licence file, system-resource provider preference | Hosted search, reverse image, and YouTube credentials unset. Voice Lab production flag is false for every model | Yes | Credential redaction tests exist | Settings are per feature, not one screen | Voice Lab hardware report is local | Owner supplies keys |
| 22 | System resource management | NEEDS_VERIFICATION | One scheduler for heavy work | Uncommitted `system_resources/` plus routes and a System Resources tab. Workloads wrap worker, render, and image research | Voice Lab generation is not wrapped yet (coordination note). This audit did not re-run the 17-job acceptance | Code present, mostly untracked | `test_system_resources.py` claimed 45 passed on 2026-11-01 | Wired where the uncommitted hunks are loaded | Acceptance JSON dated 2026-11-01; not re-run | — |
| 23 | Settings and configuration | PARTIAL | Operator-editable policy and limits | Retention API, resource-limit API, chapter templates JSON, discovery feed JSON | No single Settings screen. Several controls are JSON files | Partial | Storage and resource tests exist for their own APIs | Each module reads its own config | — | Policy edits need owner review |
| 24 | Logging, monitoring, and diagnostics | PARTIAL | Trace a job and see failures | Pipeline trace, script inspection, worker errors, system sampler | No central log product or alerting | Partial | Inspector frontend tests exist | Inspector reads stored exchanges | Mocked provider only for the inspector browser pass | — |
| 25 | Security and permissions | PARTIAL | Auth, SSRF guard, path containment, human approval | `url_guard.py`, project-root database/storage rejection, human approval, owner isolation | Dev-auth bypass exists behind two flags and loopback. Fact-check object detection unsupported. Legal review of FFmpeg/GPL still open | Yes | Path tests recorded 17 passed / 1 skipped (symlink). Approval code inspected | Applied on API and acquisition | Containment checked against a disposable API on 2026-11-04 | — |
| 26 | Backup, recovery, and data management | PARTIAL | Restart recovery and controlled cleanup | Worker leases; storage registry v15; dry-run cleanup with an exact confirmation phrase | No operator backup/restore screen. Production database migration to v16 was not applied by the 2026-11-01 sprint (control doc). Confirm before relying on a production file | Partial | Storage lifecycle tests recorded | Cleanup refuses referenced evidence and active jobs | Dry-run on a copy of real storage, 2026-11-01; production cleanup not run | Owner confirms any delete |

## Priority of remaining work

**P0.** No failing test was executed in this pass. Do not weaken `human-only-approval.v1`. Do not commit the foreign dirty tree as part of an unrelated task.

**P1.** K1 YouTube credentials and one private upload. K2 Tamil model access. K4 hosted search if the owner chooses a vendor. K7 one budgeted script run when the owner authorizes spend. Voice Lab UI persistence to server projects. Confirm whether `data/defence_studio.sqlite3` is still below schema v16 before a production research run.

**P2.** K5 OCR and reverse image. K6 rights-engine adapter. G1 social/YouTube discovery only after an owner decision. G3 images inside the research package. G6 syndication fixtures. Scene editing depth. `crop_detection`. Native-speaker Tamil review.

**P3.** GPU scheduler polish, renderer benchmarks, QVault interface note, FFmpeg lookup unification.

## This pass

| Item | Result |
|---|---|
| Evidence screen | Shows `recommendation`, waiting-for-a-person, legacy automatic history, and `missing_information` from `GET /api/jobs/{id}/evidence/policy`. The approve button still requires `package.ready` and a note. No approval route was added or changed. |
| Tests | `frontend/tests/approval-flow.test.mjs` |
| Left untouched | Approval service, YouTube upload, publishing automation, schema, and every foreign dirty file |

## Acceptance still open

A module leaves this register only when its row can say A implemented, B passed on the current tree, C integrated, D recorded with a real provider or marked not applicable, and E either signed or explicitly not required. None of the 26 rows meet that bar today.
