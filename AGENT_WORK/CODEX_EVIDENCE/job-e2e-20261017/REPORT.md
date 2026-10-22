# Defence Studio job E2E verification — 17 October 2026

**Result: the seeded research workflow works end to end through the evidence review gate. The full script, narration, video and publishing workflow has not been demonstrated by this job.** Three verified defects were fixed. All 19 final live verification checks passed after restarting the API.

Exactly one English job was created through the application UI: `ff16816c-7af1-477a-b5f7-888ebd42f21a`, **India defence developments: INS Samarthak commissioning**. The user selected India's defence developments in English, with the run stopping at its review gate.

The actual path exercised was UI creation, durable queue consumption, foundation, discovery with supplied source URLs, real HTTP acquisition, extraction, automated evidence assessment, and persisted `AWAITING_REVIEW`. The first four stages completed; evidence review is waiting; stages 6–13 remain pending. This establishes the seeded path, not the reliability of unrestricted discovery or every external provider.

Two PIB documents were acquired successfully:

- [Commissioning release, PRID 2324581](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2324581&lang=1&reg=3)
- [Related release, PRID 2324319](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2324319&lang=1&reg=3)

The current package contains 27 claims: 17 `SUPPORTED`, 8 `INSUFFICIENT_EVIDENCE`, and 2 `NEEDS_MORE_SOURCES`. These are automated policy outcomes, not human fact verification. Both documents belong to the same `gov-india` independence group. Readiness correctly requires two independent groups and observes only one. An additional independent eligible source and completion of the evidence review requirements are necessary before generation.

**Fixes delivered**

| Verified problem | Change | Evidence |
| --- | --- | --- |
| The development launcher started the API without a research consumer, leaving newly queued jobs unprocessed. | Development launchers enable the research worker by default. The API lifespan starts it only when configured, shuts it down, and drains its current stage. Explicit worker disablement remains available. | Before-fix lifecycle regression failed with a queued job; the enabled lifecycle, restart and disabled-worker checks pass. The live job reached review. |
| The HTML normalizer discarded a whole ASP.NET form and then restored hidden print panels and encoded content through a tag-stripping fallback. | Preserve visible article text inside forms, exclude hidden content and textarea/script/style content, and fail closed when no visible content remains. Normalizer version advanced to v3. Hidden article roots no longer cause later page chrome to be included. | Live extraction changed from 65 claims, including 30 exact duplicates, to 27 claims with no duplicate text within either source. Source offsets were checked. Original raw HTML and earlier artifacts were retained. |
| The monitor showed a completion countdown while a job was awaiting review or paused. | Suppress the remaining-time estimate for these states and display the action needed in the frontend. | Backend monitor and frontend regressions pass; live monitor reports no false ETA. |

Only the newly created job was reanalyzed after the normalization fix. Existing repeated-visible-span policy behavior was preserved; no extractor deduplication change remains in this work.

**Verification**

| Check/run | Result | Evidence file |
| --- | --- | --- |
| Final actual API, persistence, integrity, authorization and preservation checks | 19/19 passed | `verification-final.json`, `verify_final.py` |
| Startup, worker lifecycle and related regressions | 34 passed | `startup-tests.log` |
| Broad affected backend run | 225 passed, 1 failed; the failure was the old default-ETA test using a paused fixture | `backend-final.log` |
| Final monitor suite after correcting that fixture to RUNNING | 14 passed | `monitor-final.log` |
| Final normalization, boilerplate and content-flow suites | 34 passed | `normalization-final.log` |
| Final frontend suite | 75 passed, 0 failed | `frontend-verified.log` |

These test sets overlap and must not be added together as unique test counts. The broad backend run was not repeated after its fixture correction; its failure log is retained. The affected suites were rerun successfully. An earlier experimental extractor deduplication change broke a deliberate policy fixture and was removed; its intermediate log is retained as unsuccessful evidence. The unrelated full audio/video/provider suites were not rerun for this task. Backend runs report a non-failing Starlette/httpx deprecation warning.

Final live checks verified that the review gate and stage states survive restart; the assessment is current; the independent-source requirement is enforced; no human review or script revision was added; invalid bearer, proxied anonymous and cross-owner access are rejected; and script generation returns HTTP 409 while readiness is blocked. The API response was: `Script generation requires a ready, currently approved evidence package`.

The database contains exactly one additional job (three before, four after). Every original database row and all 35 baseline artifact files were preserved. SQLite integrity and the new job's artifact checksums passed. See `preservation-before.json`, `job-reanalysis.json` and `verification-final.json`. The baseline database backup is in the ignored task directory `tmp/job-e2e-20261017/before.sqlite3`.

Browser inspection confirmed the created job, two sources, 27 claims, unapproved package and the independent-source blocker. The browser transport subsequently closed during an attempted screenshot, so no final screenshot was captured. Script blocking was verified through the real HTTP API; it is not claimed as a browser-tested generation click.

**Handoff**

The API and research worker are left running at [Defence Studio](http://127.0.0.1:8011/#/evidence), with the new job selected in the existing browser session. No human approval, paid generation, narration, render or publication was performed. No commit or push was made. Inherited repository changes were preserved.

This job is ready for additional evidence and review. It is not evidence that the entire production pipeline is complete or that all remaining project gaps are closed.
