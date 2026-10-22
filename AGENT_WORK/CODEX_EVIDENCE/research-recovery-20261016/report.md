# Research recovery — verified 16 October 2026

Job: `dfe0b173-d9ff-458b-99b6-7b4ad3cccb8e`. Same ID/topic/language; no duplicate job.

## Root causes

- The web launcher does not start the separate durable research consumer. It was restored in the previous investigation; this task restarted only owned API/worker processes to load the scoped changes.
- Default RSS configuration and source registry disagreed: all four feed endpoints were absent from the enabled registry. Guarded live probes now returned HTTP 200 and parseable XML for all four, without redirects. `feed-probes.json` contains counts and sample links.
- The optional search API is a generic deployment-supplied bearer-authenticated GET endpoint (q/lang; JSON results list), not a named vendor integration. Both `DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL` and `DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY` are absent from local configuration and the runtime reports configured=false. No URL/key invented. The local launcher loads backend/.env.local; provider construction reads those process variables. URL validation is performed by the existing guarded fetcher at execution, including registry/DNS/redirect checks.
- Existing rerun creates a new job. Completed empty attempts had no safe in-place recovery. Discovery/acquisition results used fixed paths that would overwrite previous bytes. The monitor paired first-attempt start with latest-attempt completion.

## Changes made in this task

- `backend/config/source_policy.json`: retained inherited PIB rule; added four enabled feed-only entries, exact hosts and anchored paths/queries. No subdomains, arbitrary article paths or global bypass. Reporting-source trust remains unattributed/unapproved; numeric claims are attributed-only. Enabled: feeds.bbci.co.uk/news/world/rss.xml (the configured canonical hostname, not feeds.bbc.co.uk); www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml; breakingdefense.com/feed/; www.thehindu.com/news/national/feeder/default.rss.
- `backend/defence_studio/services/discovery.py`: bounded owner-authenticated same-job empty-run retry under a database write lock; preserve topic/language, enforce request-key replay, reject active/populated/reviewed/downstream runs, retain attempt limits and append retry artifact/transitions. No prior records deleted.
- `backend/defence_studio/api/app.py`: POST /api/jobs/{job_id}/discovery/retry-empty with existing discovery body and required Idempotency-Key; existing rerun unchanged.
- `backend/defence_studio/discovery/handler.py` and `backend/defence_studio/acquisition/handler.py`: claim-specific result paths preserve prior attempt bytes. Analysis already uses content-addressed output.
- `backend/defence_studio/services/research_monitor.py`: current-attempt stage timings; pending resets no longer retain an old stage start.
- `frontend/js/views/research-view.js`: retained all inherited work; removed obsolete assertion that the backend has no retry capability. No new retry button added; recovery uses authenticated API.
- `backend/tests/test_research_recovery.py`: recovery identity/history/hashes, auth/key replay, nonempty rejection, exact feed policies, private addresses and blocked redirects; mocked transport only.
- `scripts/run_research_worker.ps1`: repeatable existing worker launcher, matching the web launcher's local env and Python selection. No changes to the web launcher.
- `backend/README.md`: local consumer setup, search contract/configuration and bounded retry API.
- This evidence directory and append-only AGENT_WORK/CODEX.md entry.

## Real execution

Supported API returned HTTP 202 for retry key research-recovery-20261016-live-1. `retry-request.json` retained the topic/language and supplied the previously verified official PIB article URL: https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2323814&lang=2&reg=48 . No search terms or feed article permissions were fabricated to force irrelevant matches. Seeded requests select manual_url alone by existing design.

Discovery: manual_url OK, 1 actual HTTP attempt, 1 candidate. Acquisition: requested=1, succeeded=1, failed=0, ACQUIRED official source persisted with actual bytes. Analysis: sources_total=1, sources_normalized=1, claims_total=17, COMPLETED. API readback confirms 1 source and 17 claims. No model/mock installed in the running application, no AI generation or approval performed. HTTP success and deterministic extraction do not establish truth or corroboration.

Separately, the actual RssFeedProvider through DiscoveryService/production registry made 4 HTTP requests: COMPLETED_NO_RESULTS, 0 candidates for the exact original topic phrase. This read-only provider probe did not alter the job or overwrite its discovery artifact. Feed access works; broad daily-news/semantic search is not demonstrated. The sample India-matching items were unrelated to defence and their article paths remain unapproved. Search API still unavailable pending owner endpoint/key configuration.

Current job: AWAITING_REVIEW, 4/13 completed, research stages attempt 2/3. Browser shows fetched=1, failed=0 and PIB ACQUIRED; no evidence approval. Current stage durations: discovery 1.061s, acquisition 0.366s, analysis 0.059s. Current API PID5360, research consumer PID13572, localhost:8011; both left running. No unrelated process stopped.

## Verification and preservation

- C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest backend/tests/test_research_recovery.py backend/tests/test_research_pipeline.py backend/tests/test_acquisition_service.py backend/tests/test_discovery_f1a.py backend/tests/test_discovery_manual_url.py backend/tests/test_worker_engine.py backend/tests/test_content_flow.py -q: **94 passed**, exit0 (backend-tests.log).
- After monitor fix: same Python -m pytest backend/tests/test_research_recovery.py backend/tests/test_research_monitor.py -q: **21 passed**, exit0 (monitor-final-tests.log). Includes 9 overlapping recovery tests; do not sum counts as unique.
- node --test frontend/tests/*.test.mjs: **69 passed**, 0 failed/skipped, exit0 (frontend-tests.log).
- OpenAPI HTTP200; actual source/claims/monitor API reads HTTP200; browser refreshed real job. git diff --check exit0 (normal inherited CRLF advisory only).
- Initial development failures were a nonexistent job_id column in verification_review_history, a wrong list-return expectation, and the wrong create_app keyword in a new test. Corrected before live recovery; no failed run is relabelled a pass. Broader passing run supersedes earlier 2 failed/63 passed development run.
- `preservation-result.json`: all 4 original artifact bytes still match their registered SHA256. Checked other-job workflow, claim, source-review and evidence-package row hashes unchanged. Old request/result/analysis versions and history remain; new references/claims/transitions appended normally through worker/service execution.
- `data/defence_studio.sqlite3` was already modified at task start and now includes authorized retry/execution records; new artifact files are under its existing storage. No schema changes in this task. All inherited source/evidence preserved; no staging, commits, pushes, deployment, reset or deletion.

## Next action

Open http://127.0.0.1:8011/#/research with the affected job loaded, then Sources → Claims → Evidence. Review the real source, source date and extracted claims; supply further permitted independent sources if required. Do not approve simply because extraction succeeded. One source is not independent corroboration. Script remains gated by evidence approval. For general search, privately edit the ignored backend/.env.local with the actual compatible endpoint and API key and narrowly register its host/path; restart the API/consumer. The application has no vendor endpoint to infer.
