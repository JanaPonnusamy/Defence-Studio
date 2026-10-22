# Exact review blocker — 16 October 2026

Job dfe0b173-d9ff-458b-99b6-7b4ad3cccb8e was inspected without retry, new job, evidence decisions or source/config edits.

## Verified current state

Initial live request failed with WinError10061. No process listened on 8011 and no Defence Studio Python process was present. Prior temporary process logs were unavailable (file absent); the cause of the prior process exit is unknown. Started only the existing web/API command: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_dev.ps1. Startup completed, PID10552, http://127.0.0.1:8011; left running. Research worker was not restarted because no research retry is needed at this review gate.

Actual authenticated HTTP200 reads:
- /api/jobs/{job_id}/review/sources: 1 ACQUIRED source, source_content artifact 83911 bytes; source ID 3e9c744833869a0bb3ab9752e712d50f6a73cb1e; canonical official PIB URL PRID2323814; publication date2026-10-15; normalization NORMALIZED; source review PENDING/null. Publisher metadata unknown; HTML declares hi while normalization detected en (visible metadata discrepancy, not a package blocker).
- /api/jobs/{job_id}/review/claims: 17 claims, all PENDING, unverified, non-stale; claim/source attribution and verbatim passages persisted. Detail GETs for the source and first claim also HTTP200.
- /api/jobs/{job_id}/package: 1 source, 17 claims, 0 eligible claims, ready=false, 0 approvals. Exactly19 blockers: 1 missing final source review with explanation, 17 missing final claim reviews with explanations, and No approved claims with approved source support. No missing/corrupt-byte or invalid-passage blocker returned. Limitations: publisher unknown; source support/approval is not proof; duplicates are not independent corroboration.
- /api/jobs/{job_id}/script/revisions: revisions=[]; no real AI draft/reopening verified.
- /api/discovery/providers: configured_search_api configured=false, manual_url/rss_feed configured=true. Missing search endpoint/key remains a discovery configuration blocker, but is not the immediate reason this already-acquired job cannot proceed.

Persisted claim ledger: foundation attempt1 and research discovery/acquisition/analysis attempts1 and2 COMPLETED; no current active claim or failed attempt. Last transitions are analysis RUNNING→COMPLETED, review PENDING→RUNNING→AWAITING_REVIEW at2026-10-16T13:46:19.562627+00:00. Stage6 script remains PENDING. Source/claim decision and evidence package ledger counts are all0.

AWAITING_REVIEW is the documented worker stop condition, not proof of complete workflow or a failed fetch. WORKER_CONTRACT.md says the consumer stops at that state; CONTRACTS.md review/approval contract requires current explained decisions and explicit revision-bound package approval. The job is correctly waiting for legitimate review.

## Browser limit and exact actions

Actual browser screens could not be re-observed: the browser tool rejected attachment to tab1 because its URL resolved to a disallowed protocol under browser security policy. No alternate surface/CDP/workaround was attempted. Consequently this run does not claim verified live Sources/Claims/Evidence display or a demonstrated UI defect.

The source-rendering implementation and actual API payloads support the following expected screens, but these are code/API findings rather than new visual confirmation: Sources lists the PIB Press Release source with ACQUIRED and PENDING; Claims lists17 PENDING/unverified rows; Evidence displays Draft — not approved, 1 source/17 claims/0 eligible and the19 required-review blockers, with Approve this revision disabled.

Operator steps, confirmed from existing UI code:
1. Open http://127.0.0.1:8011/; use the configured session, load the exact job ID.
2. Sources → click Press Release:Press Information Bureau. Inspect source, metadata and provenance. Under Review decision enter Review note / reason, then choose the justified decision (Approve for use, Reject, Needs more evidence or Mark conflict unresolved) and confirm. Do not approve automatically.
3. Claims → click each claim; inspect its supporting passage; enter Review note / reason and choose the justified decision. Final approval/rejection decisions are needed for package readiness; unresolved items remain blocked. Approving a source alone is insufficient.
4. Evidence → Required review. Resolve blockers; only once ready, enter Package approval explanation and use Approve this revision, if warranted. A package decision never certifies truth.
5. Script remains gated. Actual AI-provider configuration, live generation and saved-draft reopening are NOT verified by this investigation. Search API credentials are separate from draft-model configuration. Set the real deployment-supplied search URL/key privately in ignored backend/.env.local if general search is needed; no invented endpoint.

## Tests and changes

C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest backend/tests/test_review_f5.py backend/tests/test_evidence_package.py -q:48 passed,0 failed,1 Starlette deprecation warning,38.03s.
node --test frontend/tests/*.test.mjs:69 passed,0 failed/skipped.
After tests, exact-job source/claim details/package GETs still200; status AWAITING_REVIEW and updated_at2026-10-16T13:46:19.562627+00:00 unchanged; counts1/17/0 eligible,19 blockers,0 approvals unchanged.

No application/test/config source files changed; no regression added because no application UI/API defect was demonstrated. Only new evidence files in this directory (API responses and this report). Existing Git modifications/untracked files preserved. No direct database mutation, retry, approval, TTS/rendering work, staging, commit, push, deployment, shutdown or unrelated process termination. The autonomous end-to-end workflow remains incomplete.
