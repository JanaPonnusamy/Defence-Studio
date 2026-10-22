# Authentication session correction — 16 October 2026

Job dfe0b173-d9ff-458b-99b6-7b4ad3cccb8e.

Confirmed browser failure: the workspace showed e2e-operator and the retained job while protected loading returned Invalid bearer token / Authentication failed. Frontend persisted ds_review_token in localStorage, reused it indefinitely at boot, attached it correctly as Authorization: Bearer to same-origin /api calls, and did not clear/recover a rejected session. No refresh-token flow exists. A presence check was incorrectly treated as sufficient to display the signed-in shell.

Backend validates HS256 HMAC using the configured secret, nonblank sub and numeric future exp. It has no configured issuer/audience expectation. The configured loopback session endpoint mints the same normal signed bearer tokens for60minutes. Invalid supplied tokens remain401 even when the existing safe local dev configuration is enabled. The signing secret was not changed. The same running API and frontend use localhost8011 and scripts/run_dev.ps1 local environment loading.

The old browser credential was not exported, logged or copied. Its specific rejection cause (expired versus malformed/different signature) could not be established: backend intentionally returns the same generic401 and the supported read-only browser evaluation could not access localStorage. Expiry is plausible but not asserted as proven. Confirmed defect is the missing frontend response to a rejected persisted token. A fresh normal session validates against the current server; no key/issuer/audience or request-header mismatch was demonstrated for it.

Changes:
- frontend/js/api.js: shared request wrapper notifies on401 only for the token used by the rejected request, avoids clearing a newer login after a delayed old401, leaves403 and unauthenticated dev-session probing alone, and refuses protected calls without a token rather than retrying without credentials. No automatic retry or silent new login.
- frontend/js/app.js: clear only rejected token and show ordinary login with an explanatory message; preserve selected job/hash. Evidence, Script and Research mounts stop being current when the session is rejected. Explicit Sign out retains its established behavior.
- frontend/tests/session-auth.test.mjs: current401 invalidation/no anonymous retry, delayed401 versus newer token,403 and unauthenticated session probe regressions.
- This report and evidence-access.png only. Existing unrelated source changes remain intact.

Verification:
- Reproduced original error in actual browser.
- Used normal Sign out control; browser subsequently returned through the configured normal login/session flow as e2e-operator. The requested automated click on Enter development mode found no matching control because the page had already become authenticated; do not treat that failed click as a successful action. Fresh authenticated browser state was directly observed instead.
- Actual Sources screen: PIB ACQUIRED, review controls visible; source review changes appeared during this investigation through separate operator activity. The agent never clicked an approval button or entered a review explanation. Existing decisions/history were not reset.
- Actual Claims screen: persisted attributed rows present,17 pending/unverified claims accessible for review.
- Actual Evidence screen: Draft — not approved,1source/17claims/0eligible,0package approvals; Approve this revision and Export approved JSON disabled. Screenshot evidence-access.png.
- Live normal server-minted bearer: exact-job Sources/Claims/Evidence GETs all200; counts1/17. Synthetic invalid bearer still401. No protected request was repeated anonymously.
- node --test frontend/tests/*.test.mjs:72passed,0failed/skipped. Final narrow api/session tests after narrowing auth-current guards:6passed,0failed.
- C:/dstmp/next-clean-0945/Scripts/python.exe -m pytest backend/tests/test_dev_auth.py -q:14passed,0failed,1Starlette deprecation warning. Covers expiry/signature rejection and local auth boundaries.
- git diff --check passes (inherited normal CRLF advisories only).

Evidence review is now accessible. Job remains AWAITING_REVIEW. Source approval activity was observed but all17claims still require decisions and package approval remains absent. Next: Claims tab → select a claim → inspect passage → Review note / reason → justified decision. No auto-approval or AI draft generation was performed. If an old cached tab still displays the prior authentication failure, hard-refresh and use Enter development mode (or a valid issuer token); do not reuse the rejected credential. The frontend now preserves the job when rejecting a session.

No backend auth/security/environment changes, research retries, new jobs, database reset/deletion, staging, commits, pushes, deployment, TTS/rendering changes, process termination or shutdown. The full autonomous research-to-draft workflow is not claimed complete.
