# Cursor append-only work log

Owner: Cursor (frontend acceptance and release readiness)
Scope: frontend tests, browser acceptance, isolated frontend defects, frontend release checklist. No edits to Codex-owned backend files (`script_integrity.py`, `backend/pyproject.toml`, backend test initialization, subtitle font resolution, databases, human review history). No edits to `AGENT_WORK/CLAUDE.md`, `AGENT_WORK/CODEX.md`, or `AGENT_COORDINATION.md`.

2026-10-15T19:07:00+05:30 | Cursor | P1 frontend acceptance / X4 | IN_PROGRESS | Ownership: frontend tests, browser scripts, AGENT_WORK/CURSOR.md, AGENT_WORK/CURSOR_EVIDENCE | Read consolidated plan, project_status.md, DEFENCE_ERA_PHASE_STATUS.md, AGENT_WORK/CLAUDE.md, AGENT_WORK/CODEX.md, frontend/package.json, scripts/run_dev.ps1 | No shared board edits. Codex P0 files and Tamil font work left untouched. HEAD not moved. Production DB not opened for writes. | Next: npm test, disposable seed, populated browser pass.

2026-10-15T19:10:00+05:30 | Cursor | P1 frontend acceptance / X4 | IN_PROGRESS | frontend/tests/browser/seed_release_job.py, release.mjs | `npm test` in frontend -> exit 0, 55 passed, 0 failed, 0 skipped. Seed: python frontend/tests/browser/seed_release_job.py with PYTHONPATH=backend and disposable DB/storage -> exit 0. | AGENT_WORK/CURSOR_EVIDENCE/disposable/ | No frontend build script exists. App is static files served by FastAPI. Did not use scripts/run_dev.ps1 because it loads backend/.env.local and the project database.

2026-10-15T19:12:00+05:30 | Cursor | P1 frontend acceptance / X4 | IMPLEMENTED_NOT_VERIFIED | frontend/js/views/claims-view.js, frontend/js/app.js, frontend/tests/api.test.mjs | First browser run exit 1: claim detail never mounted a review form. Cause: the panel was rendered only when claim.has_conflict, while the evidence package requires a current review on every pending claim. | AGENT_WORK/CURSOR_EVIDENCE/browser/release-acceptance.json (first run, 7/8 before the runner stopped) | Fix limited to showing the existing decision panel and history for every claim. No API change.

2026-10-15T19:15:27+05:30 | Cursor | P1 frontend acceptance / X4 | VERIFIED | Same frontend files plus frontend/tests/browser/release-tabs.mjs | Disposable server: python -m uvicorn defence_studio.api.app:app --host 127.0.0.1 --port 8031 --app-dir backend, DEV_MODE=1, DEV_AUTH_BYPASS=1, DEV_AUTH_OWNER=e2e-operator, AUTH_SECRET a 32-byte test secret, DB/storage under CURSOR_EVIDENCE/disposable. OpenAI key not loaded. `node tests/browser/release.mjs` -> exit 0, 26/26. `node tests/browser/release-tabs.mjs` -> exit 0, 6/6. Final `npm test` -> exit 0, 55 passed. Server on port 8031 stopped after the runs. | AGENT_WORK/CURSOR_EVIDENCE/browser/release-acceptance.json, release-tabs.json, screenshots | Persisted reviews on job 6a7434de-aa58-4d93-a9f4-f98c31d41293: source src-a APPROVED_FOR_USE, claim claim-bcb9d5f7bb41b04f APPROVED_FOR_USE. Remaining claims and src-b still PENDING, so the package stayed a draft and script generation stayed disabled. Not ACCEPTED. Not production-ready.

## Frontend release checklist (2026-10-15)

Separate implementation from acceptance. This session did not accept any phase.

### Build and tests
- Frontend has no compile/build step. `frontend/package.json` script is `node --test tests/*.test.mjs` only.
- Supported local start: `scripts/run_dev.ps1` (loads `backend/.env.local`, serves API+static UI on 127.0.0.1:8011, project DB). Not used for this pass.
- Acceptance start: uvicorn on 127.0.0.1:8031 against a disposable database. Stopped after the run.
- `npm test`: 55 passed, 0 failed, 0 skipped. Exit 0. Twice (before and after the claim-panel fix).

### Browser workflows actually exercised
Headless Chrome, viewports 1440, 768, and 390. Synthetic job "Synthetic Northwind harbour drill" plus an empty job. Evidence: `AGENT_WORK/CURSOR_EVIDENCE/browser/`.
- Dashboard: both jobs listed with real status. Opening a card selects it.
- Sources: two acquired sources. Empty note blocked. Cancel left the decision uncommitted. Approve persisted and survived reload.
- Claims: four claims. Review form present without a conflict flag. Empty note blocked. One approval persisted (confirmed by GET `/review/claims`).
- Evidence: draft package, not approved, blockers listed.
- Script: generate control disabled until the package is approved. No draft was edited.
- Scene: blocked, no approved script.
- Production: overview, scenes, visual, audio, render/subtitles, QC, package, YouTube, usage, credentials. No section error. Pipeline stages shown as NONE. Render, QC, and package controls are the empty/disabled states.
- Discovery: empty topic does not POST.
- Search & Evidence, Fact check, Voice, Inspector: each tab rendered for this job. Fact-check package empty. Voice upload form shown, no file submitted. Inspector showed the local pipeline trace.
- Settings: credential inputs are password fields and were empty. No key was saved.
- Errors: unknown job id shows "Job not found or not yours". Sign-out returns to login. Empty token is rejected in the form.
- Empty job: "No sources" state.
- Refresh: job id restored from local storage; approved source decision still visible.
- Layout: no horizontal page overflow at 1440, 768, or 390 on dashboard and production. Fact check at 390 also had no page overflow. The tab strip scrolls when it does not fit.

### Persistent job state
Verified for the disposable job: local storage job id, source approval, and claim approval survive reload and a fresh GET. Scene, script, render, QC, and package had nothing persisted to restore.

### API / backend dependencies
The UI is served by the FastAPI app and reads live `/api` routes. This pass did not change contracts. Script generation, scene plans, visual/audio/render, QC, and packaging stay blocked on server-side approvals and on the Audio, Visual, and Renderer modules. Codex owns the uncommitted P0 integrity/packaging changes; this session did not retest them.

### Error handling and recovery
Verified in the browser: missing review note, cancelled confirm, unknown job, empty discovery topic, empty token, unsigned-out shell. Not retested here: worker crash recovery, render cancel/retry, voice cancel/retry/discard, fact-check human correction.

### Unverified external-provider features
OpenAI script generation, hosted search, reverse image, OCR, object detection, YouTube OAuth/upload, Tamil TTS, forced alignment. Credential fields were displayed and not submitted. No paid call was made.

### Known defects and release blockers
- Fixed in this session, verified in the browser: claim review was unavailable unless the claim was flagged as a conflict, so required package reviews could not be recorded. Files: `frontend/js/views/claims-view.js`, `frontend/js/app.js`.
- Release blockers outside this fix: evidence package still not approvable until the remaining synthetic reviews are done; no approved script; no scene, asset, audio, render, subtitle burn-in, QC, or publishing package on this job; Tamil narration; live providers; YouTube; human review. Do not treat the 26 browser checks as production acceptance.
- Codex P0 evidence-integrity and install work, and the Tamil font handoff, are recorded in `AGENT_WORK/CODEX.md`. This session did not accept them.

## Proposed AGENT_COORDINATION.md update (not applied)

Timestamp 2026-10-15T19:15:27+05:30 | Cursor | P1 / X4 frontend acceptance | VERIFIED for the browser pass above, not ACCEPTED | Files: claims-view.js, app.js, api.test.mjs, frontend/tests/browser/release.mjs, release-tabs.mjs, seed_release_job.py, AGENT_WORK/CURSOR.md, AGENT_WORK/CURSOR_EVIDENCE/ | Tests: npm test 55/55; release.mjs 26/26; release-tabs.mjs 6/6 | Blocker: populated script edit, scene approval, QC, subtitles, and packaging still need an approved evidence package and the real module chain. Production readiness is not claimed.

2026-10-15T19:48:54+05:30 | Cursor | Manual source URL entry / evidence gate | VERIFIED | frontend/js/views/discovery-view.js, frontend/js/app.js, frontend/js/views/sources-view.js, frontend/tests/discovery.test.mjs, frontend/tests/browser/manual-url.mjs, release.mjs, release-tabs.mjs | Contract confirmed before edit: POST `/api/jobs/{id}/discovery/requests` body field `seed_urls` (list, max 20). Server validates via `request_from_dict` / `normalize_url`. Fetch runs only in the research worker through `manual_url`, not in the browser. `npm test` exit 0, 58 passed. `node tests/browser/manual-url.mjs` exit 0, 13/13. `release.mjs` exit 0, 26/26. `release-tabs.mjs` exit 0, 6/6. Disposable DBs under AGENT_WORK/CURSOR_EVIDENCE/manual-url/. Server on 8032 stopped. Previous CURSOR_EVIDENCE/disposable and browser screenshots were not deleted. | Evidence: AGENT_WORK/CURSOR_EVIDENCE/manual-url/browser/manual-url.json | Not ACCEPTED. Not production-ready.

Manual URL result:
- Discovery form now has a Source URLs field and Save source URLs. Empty, malformed, and credentialed URLs are rejected before any request. A double click sends one POST. The textarea is kept on error. Saving does not approve anything and does not call OpenAI.
- A valid `https://example.com/` save succeeded. After queue, the research worker ran. The guarded fetcher refused the host: SOURCE_NOT_ALLOWLISTED (example.com is not in config/source_policy.json). The Discovery banner shows that error. The Sources list stays empty and says nothing was approved. No source card was invented.
- Existing acquired sources on the synthetic job still render with the source-card list. Generate stays disabled, and no `/script/generate` request was sent, while the package is an unapproved draft. The package approve button stays disabled while blockers remain. An approved package that would enable Generate was not reached: the synthetic package still has pending reviews and zero eligible claims.
- Network: one controlled fetch attempt of https://example.com/ per queued manual-URL job, made by the existing Python worker. No paid provider.

## Proposed AGENT_COORDINATION.md update (not applied)

Timestamp 2026-10-15T19:48:54+05:30 | Cursor | Manual source URL frontend | VERIFIED, not ACCEPTED | Files: discovery-view.js, app.js, sources-view.js, discovery.test.mjs, manual-url.mjs | Tests: npm 58/58; manual-url.mjs 13/13; release.mjs 26/26; release-tabs.mjs 6/6 | Blocker: example.com is rejected by the source allowlist, so a saved URL does not become an acquired source until an enabled registry entry exists. Generate stays disabled until the backend package is actually approved. Do not mark production-ready.

2026-10-15T20:06:07+05:30 | Cursor | English episode browser acceptance | Browser stages below are VERIFIED or NOT_RUN. Episode-level acceptance is not reached. | No product source edits. New harness only: frontend/tests/browser/episode.mjs, episode-review.mjs, episode-script.mjs, episode-confirm.mjs. Source policy was not edited. | Disposable episode server 127.0.0.1:8033, DB `AGENT_WORK/CURSOR_EVIDENCE/episode/ds.sqlite3`, storage `AGENT_WORK/CURSOR_EVIDENCE/episode/storage`, owner e2e-operator, `DEFENCE_STUDIO_SCRIPT_PROVIDER=offline`, OpenAI key unset. Job `522dc4ce-16d0-4680-a450-fcd841180c69`, topic "S-400 Triumf English acceptance", language en. Allowlisted URLs already in `backend/config/source_policy.json`: `https://missilethreat.csis.org/defsys/s-400-triumf/` (source `148827db7320d204c6c3f4e53296339538cf636f`) and `https://www.rusi.org/explore-our-research/publications/rusi-defence-systems/russias-air-defence-challenge-syria` (source `9310b0a55b625410bad0c205edc3a8cf18273981`). Servers on 8033 and 8034 stopped after the runs. Prior evidence directories were not deleted. | Not ACCEPTED. Not production-ready.

Episode job `522dc4ce-16d0-4680-a450-fcd841180c69`. Environment: headless Chrome against the disposable API above. UI review notes say "Disposable browser acceptance 2026-10-15. UI decision path only; not an editorial approval for publication." Those clicks are operator decisions in the disposable database. They are not an editorial or publication approval.

| Stage | Status | Action | Result |
| --- | --- | --- | --- |
| Discover and save permitted sources | VERIFIED | UI Save source URLs, then queue. `episode.mjs` | Saved 2 URLs. Nothing approved by the save. |
| Fetch | VERIFIED | Existing research worker `manual_url` | 2 candidates. `manual_url: OK · 2 result(s) · 4327 ms`. |
| Source record and provenance | VERIFIED | Sources tab, open CSIS card | Acquired. Publisher Missile Threat. Published unavailable. Canonical URL is the CSIS page. |
| Source review | VERIFIED | UI Approve for use on both cards, with the disposable note and confirm dialog | Both sources `APPROVED_FOR_USE`. |
| Claim review | VERIFIED | UI Approve for use on all 43 pending claims, same note | 43/43 recorded. 0 conflicts in the analysis artifact. |
| Evidence package | VERIFIED | Evidence tab, then Approve this revision after the package reported no blockers | Revision `9804ea8455e939d863b5a55f97d8ad55489d00f9989f478ce917fb7f3ec1e7cf`. 2 sources, 43 claims, 43 eligible. UI: "Approved current revision". Limitations remain, including unavailable publication dates. |
| English script | VERIFIED | Script tab, language English, Generate draft. Offline provider only | Version 1, status `AWAITING_REVIEW`, provider `offline-extractive`, measured cost ₹0, 43 sections, each `ATTRIBUTED_CLAIM`. Validation passed with a warning that claim `claim-05608baa58e4c016` has no source publication date. Script was not approved. The form's model field still shows `gpt-4o-mini`; usage records the offline provider and no tokens. No external generation host was called. |
| Scene plan, assets, audio, visual timeline, subtitles, render, QC, publish package | NOT_RUN | Production tab read only | Pipeline: approved evidence package, script v1 `AWAITING_REVIEW`, scene plan NONE, assets NONE, audio NONE, visual NONE, render NONE, QC NONE, final approval NONE, publish package NONE. Scene generation requires an approved script. Audio, render, and packaging modules were not configured and were not started. |
| Refresh | VERIFIED | Reload | Job id `522dc4ce-16d0-4680-a450-fcd841180c69` stayed selected. |
| Layout 390 px | VERIFIED | Sources and script viewports | 390/390, no horizontal overflow. |

The first `episode-stages.json` recorded sources and claims as BLOCKED because the list was read before acquisition finished. Later UI passes on the same job supersede that file: `episode-review.json`, `episode-script.json`, `episode-confirm.json`.

Browser acceptance and episode acceptance are separate. The browser completed discovery, acquisition, explicit review, package approval, and an offline English draft on this disposable job. A finished episode was not produced. No audio, scene plan, subtitle file, render, QC report, or publish package exists.

Regression after the episode harness, on a separate disposable DB `AGENT_WORK/CURSOR_EVIDENCE/regression/ds.sqlite3` at 127.0.0.1:8034. Populated job `9ab10d60-8bf3-4a55-a04f-89711cd2a6ac`, empty job `64152421-bfec-49c1-a8c3-1589a434b5d8`.

- `npm test` in `frontend`: exit 0, 58 passed.
- `node tests/browser/manual-url.mjs`: exit 0, 13/13. `https://example.com/` is still `SOURCE_NOT_ALLOWLISTED`. Pending reviews still disable Generate, with zero `/script/generate` posts.
- `node tests/browser/release.mjs`: exit 0, 26/26.
- `node tests/browser/release-tabs.mjs`: exit 0, 6/6, including 390 px.

## Proposed AGENT_COORDINATION.md update (not applied)

Timestamp 2026-10-15T20:06:07+05:30 | Cursor | English episode browser acceptance | Browser workflow VERIFIED through an offline English script draft. Episode output NOT_RUN. Not ACCEPTED | Job `522dc4ce-16d0-4680-a450-fcd841180c69` on disposable DB `AGENT_WORK/CURSOR_EVIDENCE/episode/`. Allowlisted CSIS and RUSI URLs acquired. 2 sources and 43 claims reviewed in the UI with a disposable note, not an editorial approval. Evidence revision `9804ea8455e939d863b5a55f97d8ad55489d00f9989f478ce917fb7f3ec1e7cf` approved in the UI. English script v1 is `AWAITING_REVIEW` from `offline-extractive` at ₹0. Scene, audio, subtitles, render, QC, and packaging were not run. Regression: npm 58/58, manual-url 13/13, release 26/26, tabs 6/6. Source policy unchanged. Do not mark production-ready.

2026-10-15T20:13:13+05:30 | Cursor | Script review and downstream stage visibility | UI review flow VERIFIED on a database copy. Original episode script remains `AWAITING_REVIEW`. Episode output NOT_RUN. Not ACCEPTED | Files: `frontend/js/views/script-view.js`, `scene-view.js`, `production-view.js`, `frontend/tests/script.test.mjs`, `production.test.mjs`, `frontend/tests/browser/script-review.mjs`. | The acceptance database `AGENT_WORK/CURSOR_EVIDENCE/episode/ds.sqlite3` was not written. Mutating review ran against a copy at `AGENT_WORK/CURSOR_EVIDENCE/script-review/ds.sqlite3` on 127.0.0.1:8035. Same job id `522dc4ce-16d0-4680-a450-fcd841180c69`. Offline script provider. OpenAI key unset. Servers stopped. | Not ACCEPTED. Not production-ready.

Original job `522dc4ce-16d0-4680-a450-fcd841180c69` in `AGENT_WORK/CURSOR_EVIDENCE/episode/ds.sqlite3` is still English script v1 `AWAITING_REVIEW`. The copy's script is `CHANGES_REQUESTED` after one explicit UI decision. That decision is not an approval and not an editorial acceptance.

| Stage | Status | Where | Result |
| --- | --- | --- | --- |
| Open English script v1 | VERIFIED | Copy, Script tab | Title, status `AWAITING_REVIEW`, full narration, and claim links are visible. |
| Claim warning `claim-05608baa58e4c016` | VERIFIED | Copy, revision detail | Validation lists `WARNING: Claim claim-05608baa58e4c016: source publication date is unavailable (section s001)`. |
| Source attribution | VERIFIED | Copy, evidence mapping | Each section shows its `source_ids`. Section s001 cites `148827db7320d204c6c3f4e53296339538cf636f`. |
| Offline draft vs provider script | VERIFIED | Copy, usage block | Provider `offline-extractive`, measured cost ₹0, and the line "Offline extractive draft. Each narration section repeats approved claim text. Recorded token usage: none." A provider value other than `offline-extractive` does not get that line. |
| Approve control | VERIFIED as a gate. Approval NOT_RUN | Copy | Empty note is refused. Confirm dialog opens only after a note. Cancel sends no `/approve` POST. Status stays `AWAITING_REVIEW` until a different decision. |
| Request changes | VERIFIED | Copy, explicit UI | Empty note is refused. One POST records `CHANGES_REQUESTED`. Refresh keeps the status, the note, and the job id. Approve and export stay disabled. |
| Scene plan | BLOCKED | Copy, Scene tab and Production | "No approved English script revision." Generate plan is disabled. No plan was created. |
| Assets | NOT_RUN | Production overview | Status NONE. No asset artifact. |
| Narration audio | BLOCKED | Production | "An approved scene plan is required." Generate narration audio is disabled. No audio was started. |
| Subtitles and render | NOT_RUN | Production | No render. Start render is disabled until audio and a measured visual timeline exist. |
| QC | NOT_RUN | Production | No QC report. Run QC is disabled. |
| Publishing package | NOT_RUN | Production | No package. Build is disabled until a final QC approval. |
| Layout 390 px | VERIFIED | Script detail and Production | 390/390, no horizontal overflow. |

UI correctness and episode completion are separate. The UI shows the real persisted statuses and keeps the next actions disabled. No scene plan, audio, subtitle file, render, QC report, or publishing package was produced.

Tests: `npm test` exit 0, 59 passed. `node tests/browser/script-review.mjs` exit 0. `release.mjs` exit 0, 26/26, and `release-tabs.mjs` exit 0, 6/6, on a new database `AGENT_WORK/CURSOR_EVIDENCE/script-review/release-db/` (job `08896542-d999-40f7-bfa5-4c8949c76776`). Manual URL suite was not repeated; discovery files were not changed. Artifacts: `AGENT_WORK/CURSOR_EVIDENCE/script-review/browser/script-review.json` and the screenshots beside it.

## Proposed AGENT_COORDINATION.md update (not applied)

Timestamp 2026-10-15T20:13:13+05:30 | Cursor | Script review visibility | UI VERIFIED on a copy. Original English script remains AWAITING_REVIEW. Episode output NOT_RUN. Not ACCEPTED | Script detail now shows source ids and labels an offline-extractive draft. Scene and audio generation controls stay disabled until the server prerequisite exists. Copy job `522dc4ce-16d0-4680-a450-fcd841180c69` was marked CHANGES_REQUESTED through the UI. The episode database was not modified. Tests: npm 59/59, script-review browser pass, release 26/26, tabs 6/6. Do not mark production-ready.

2026-10-15T20:59:28+05:30 | Cursor | Fact-check, TTS, and video-renderer audit | Fact-check VERIFIED WITH LIMITATIONS. TTS VERIFIED WITH LIMITATIONS. Video renderer VERIFIED WITH LIMITATIONS. Three-module workflow is not production-ready. | Files changed: `backend/defence_studio/verification/claims.py`, `backend/tests/test_factcheck_engine.py`. Standalone audio and renderer source were not modified. Episode job `522dc4ce-16d0-4680-a450-fcd841180c69` was not opened for writing; it remains English script v1 `AWAITING_REVIEW`, 43 claim reviews, 2 source reviews. | Evidence: `AGENT_WORK/CURSOR_EVIDENCE/modules-e2e/`. `git diff --check` exit 0.

Fact-check: `python -m pytest -q tests/test_factcheck_engine.py tests/test_factcheck_gates_audit.py` exit 0, 19 passed, 1 skipped (opt-in live RSS). Defect fixed: retracted sources, sources with `retrieval_error`, and sources with `review_decision` REJECTED or INVALID were still eligible as independent support. They are now excluded and listed on `excluded_sources`. Identical copies still share one independence group. The engine does not call an AI provider. `assessment.py` writes `claim_assessments` and does not reference `claim_reviews`. Disposable result: `modules-e2e/factcheck/claims-result.json`, sha256 `391880d82c09f744d375ca34482dbc749ff91f41bf4151499209d4d4e10c1dc8`, verdict PARTIALLY_SUPPORTED with one support group after the rejected and retracted copies were excluded.

TTS: eSpeak NG is not on PATH. The audio-module tree has no `.venv`. Disposable venv ran `pytest -q tests/test_tts_adapters.py tests/test_job_queue_unit.py tests/test_validate.py` exit 0, 9 passed. Align, analyze, and API end-to-end tests were not run: they import librosa, which is not installed, and the API end-to-end path uses mock-tts. Real synthesis: `local-pyttsx3` through SAPI. Voices present: Microsoft Hazel (en-GB) and Microsoft Zira (en-US). No Tamil voice. Output `modules-e2e/tts/english.wav`, sha256 `0af6d2deae0bd7df73bcfe71524f17a6a47ea43701eba4a10fc9999d16616ded`, 243382 bytes, pcm_s16le, 22050 Hz, mono, 5.518 s, mean volume -20.9 dB, max -5.0 dB. Provider `local-pyttsx3`, `is_mock` false. `estimated_cost` 0.0 is an unmetered local estimate, not a measured bill. Rate and volume are applied. Pitch and emotion are not implemented. mock-tts stays labeled `is_mock`. Tamil synthesis and eSpeak NG are BLOCKED.

Video renderer: ffmpeg 8.1.1 with libx264, aac, and libass on PATH. `python -m pytest -q` in `standalone/video-renderer` exit 0, 117 passed, 1 skipped, 1 Starlette deprecation warning, 221.22 s. Real retained render used the speech WAV as narration: `modules-e2e/render-out/output.mp4`, sha256 `90945580954a5490ebfef7f8fdfb2e518fad82060def6046fbea73f78f3788a1`, 99352 bytes, H.264 320x180 12 fps, AAC 48000 Hz mono, container duration 5.518 s, full decode 0 errors, QC passed. Subtitles, captions, and Tamil text are outside the R1 contract and were not produced.

## Proposed AGENT_COORDINATION.md update (not applied)

Timestamp 2026-10-15T20:59:28+05:30 | Cursor | Fact-check, local TTS, and standalone renderer | Each module VERIFIED WITH LIMITATIONS. Combined workflow not production-ready | Fact-check no longer counts retracted, failed, or rejected sources as corroboration. Tests 19 passed, 1 skipped. Real pyttsx3 English WAV decoded; eSpeak NG and Tamil voice are absent. Renderer tests 117 passed, 1 skipped; real MP4 from that WAV passed ffprobe and a full decode. Subtitles are not in the standalone R1 contract. Episode job was not modified. Do not mark production-ready.

2026-10-16T03:35:00+05:30 | Cursor | QVault vs TTVC vs Defence Studio video-generator reuse audit | VERIFIED as read-only inspection | No QVault/TTVC/Defence Studio product source edited. No install, download, generate, paid call, git mutation, `.env` read, or DB/job write. QVault uncommitted GK-scraper files left intact. YouTube kwG9uCDeSk4 WebFetch 403 — no delivery analysis. Tamil narration remains BLOCKED. | Report: `AGENT_WORK/CURSOR_EVIDENCE/video-generator-reuse-audit-20261016.md`

QVault is FastAPI+React with a **quiz** Video Generation Engine (`POST /api/videos/generate`, Edge TTS default, WordBoundary timings, Pillow→ffmpeg). `output/videos` empty — no real quiz MP4. TTVC is **Python/Tkinter**, not VB.NET; Edge Neerja/Pallavi + pydub; `Output/final_output.mp3` ffprobe 132.336 s; no MP4. Best reuse: **adapter** QVault Edge `TTSProvider` onto DS `TTSAdapter`, not copy quiz/MoviePy. No new TTS model recommended.


