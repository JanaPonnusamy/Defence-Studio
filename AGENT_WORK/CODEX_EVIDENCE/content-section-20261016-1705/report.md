# Content Section diagnosis and repair — 16 October 2026

Starting/final HEAD: `47159881f39a477c37c6b2a91ffd103d34c5d593`, branch `main`. All changes remain **unstaged and uncommitted**. No commit, push, deployment, production database write, application shutdown, paid call or downstream media modification was performed. Previous untracked evidence remains intact.

## Topic and online research

Selected: **India's GSAT-7C and Varunastra contracts: Air Force communications and Navy anti-submarine capability**. Signing/announcement was **15 October 2026**, with current October 16 coverage. The bounded comparison included the October 16 INS Samarthak commissioning and India–UK naval cooperation. This is an evidence-led editorial selection, not a verified popularity ranking. Full source register, dates, links, facts, comparison, analysis and unknowns: [research.md](research.md).

Online research succeeded. The application's actual guarded HTTP provider also fetched the PIB release: **1 HTTP attempt, 1 candidate, OK**, declared date now **2026-10-15**, about 1 second. This proves live source access/extraction only. It does not prove a live AI call, automatic fact verification, independent corroboration or human approval.

## Execution path and demonstrated failures

The existing Content flow is **Search & Evidence → durable research worker → Sources/Claims review → Evidence package approval → Script generation → script_revisions/artifact storage → Script UI**. There is no separate framework or “Content” backend to replace.

1. **Research input/policy mismatch.** The research form sent only topic/language to discovery. Existing RSS sources are intentionally blocked by the controlled registry; the form could not pass verified article URLs even though the backend manual-URL provider already supports them. The current PIB URL uses `PressReleaseIframePage.aspx`, absent from the PIB allowlist. Baseline policy rejects this exact URL; the scoped correction permits it. No feed host or arbitrary domain was enabled.
2. **Publication-date handoff.** The live PIB release declares its date visibly in the body, but discovery/acquisition read only head metadata/time tags. Before the date fix, the successful candidate had `published_at: null`; after it, real guarded HTTP returns `2026-10-15`. Unknown dates were not replaced by today's date.
3. **Misleading generation success and missing draft display.** The Script UI printed “Draft generated” for any successful HTTP response, including persisted `VALIDATION_FAILED` revisions. It then rebuilt the page without selecting the returned revision, clearing the success text and leaving the operator to locate the draft manually.
4. **Duplicate/retry handling.** Research created a new job/key on each submission and had no single-flight guard. Script only disabled a button; repeated submit events could still issue calls. Both now fence concurrent submission. Research retains acknowledged job/request steps and its creation key for a retry in the same mounted view. Script retains a request key after a failed/unknown response and opens the acknowledged revision. These are not claims of durable browser-resume across a page reload before job creation/queue acknowledgment.
5. **Confirmed authorization defect in draft generation replay.** `ScriptService.generate` returned an idempotent replay before checking ownership. An isolated execution of actual HEAD code returned the owner's revision to a different owner using the known key; current code raises `ScriptNotFound` before replay. `baseline-reproduction.json` records both outcomes. Production jobs were not queried to reproduce this.

Missing paid-provider configuration was not assumed to be the root cause. The production owner's encrypted credentials/configuration were not inspected and no live paid generation was authorized. Existing provider abstractions and source-grounded prompts remain in use; no fake provider or research was installed in production.

## Product and test files changed

| File | Reason |
|---|---|
| `backend/config/source_policy.json` | Add only PIB's numeric-PRID article endpoint with bounded numeric `lang`/`reg` parameters; host/IP/redirect controls remain enforced. |
| `backend/defence_studio/acquisition/source_dates.py` (new) | Shared pure PIB-specific reader for explicitly labelled English/Hindi visible publication dates; invalid/ambiguous/non-PIB dates remain unknown. No network, storage or new dependency. |
| `backend/defence_studio/acquisition/providers/http_page.py` | Use the explicit-date fallback when HTML head metadata has no publication date. |
| `backend/defence_studio/discovery/providers.py` | Apply the same fallback to manual-URL discovery; existing metadata/time declarations retain priority. |
| `backend/defence_studio/services/script.py` | Authorize owner before the idempotent replay query. |
| `frontend/js/views/research-view.js` | Existing manual-source input, new-topic entry even with a selected job, single-flight/retry-safe submission, visible provider/no-source failures and links to the existing review/draft handoff. |
| `frontend/js/views/script-view.js` | Single-flight generation, stable retry key, select/open the saved revision, persistent accurate success/failure message. |
| `backend/tests/test_content_flow.py` (new) | Content-only real worker/service/storage chain with fixture HTTP, simulated review and mocked model response; attributed research reaches generation and survives a new service instance. |
| `backend/tests/test_discovery_manual_url.py` | Narrow endpoint/SSRF and explicit date regressions, including ambiguous/invalid/script-only/non-PIB dates. |
| `backend/tests/test_script_phase3.py` | Cross-owner replay regression using the already persisted owner's key. |
| `frontend/tests/research.test.mjs` | Verified-source payload, duplicate fencing, retry on the same job and visible search failure/review links. |
| `frontend/tests/script.test.mjs` | Invalid/malformed generation responses must not receive the saved-draft success label. |

All other new files are scoped research/evidence runners, logs, screenshots and reports under this directory. No TTS, voice, alignment, rendering, migration, provider-secret or unrelated workflow source changed. `git diff --check` passes (only normal LF/CRLF advisory output for an existing Windows file).

## Exact fresh tests and results

Commands ran from `E:/Jana Soft/DefenceStudio`. Python below is `C:/dstmp/next-clean-0945/Scripts/python.exe`.

| Command | Result |
|---|---|
| `python -m pytest backend/tests/test_script_phase3.py backend/tests/test_discovery_manual_url.py backend/tests/test_content_flow.py backend/tests/test_discovery_f1a.py backend/tests/test_acquisition_service.py -q` | **101 passed, 0 skipped, 1 warning, 108.08s, exit 0**. `backend-final-with-dates.log`. |
| `node --test frontend/tests/*.test.mjs` | **69 passed, 0 failed/skipped, 4.719s, exit 0**. `frontend-all-final.log`. |
| `python <evidence>/live_source.py` | **OK, 1 live candidate, 1 HTTP attempt, exit 0**. Before/after date JSON retained. No DB/AI call. |
| `python <evidence>/reproduce_baseline.py` | **exit 0**. HEAD policy rejects URL; current policy permits it. HEAD cross-owner replay returns a revision; fixed service denies it. Isolated fixture only. |
| `node <evidence>/browser_content.mjs` | **7/7 checks, exit 0**. Real Chrome and FastAPI, fixture research/review/model. Actual saved narration opened automatically; one model call on double submit; refresh retrieval; invalid/empty/provider-error failure messages; no JS exceptions. |
| `node <evidence>/browser_research.mjs` | **3/3 checks, exit 0**. Final research entry with an existing selected job, Evidence/Script links and invalid-source form error. |
| `python <evidence>/export_fixture_draft.py` | **exit 0**, read back saved English v1, 37 words, `AWAITING_REVIEW`. JSON includes actual persisted draft and source register. |

Backend warning: Starlette's deprecated httpx TestClient integration, not a test failure. Backend suites cover provider failures, malformed/empty output, attribution/references, approval/freshness gates, persistence/replay, guarded discovery and HTTP acquisition. No full unrelated media suite was repeated.

Earlier development failures are retained: test-only browser `window` shim was missing; an assertion was initially inserted into the wrong test; fixture used `text` instead of the existing `claim_text` field; a test incorrectly expected the literal source URL in the prompt even though the existing prompt binds claims/source IDs and the package stores URLs. These harness issues were corrected without changing production contracts to satisfy them. Initial final-backend-before-date run was 83 passed; it is superseded by 101 passed above. A follow-up browser attempt found the prior disposable CDP process unavailable; a fresh owned test browser then passed the final 3 checks. No failure is relabelled a pass.

## Evidence scope and actual persisted draft

`fixture-draft.json` / `fixture-draft.md` contain the **short controlled test draft**, not a claimed production-quality episode or live AI result. Research transport supplies paraphrased official-source facts; numeric procurement assertions are not promoted to independent corroboration. Reviews are explicitly simulated in the disposable DB. The mocked model uses the existing request/response contract and extracts supplied approved claims; it is not a live model evaluation. Its valid revision remains `AWAITING_REVIEW`; script approval was never granted. An intentionally invalid v2 is retained for inspection and shown as `VALIDATION_FAILED`.

Live guarded research and the mocked content chain are separate evidence. No complete live research→human approval→paid AI chain was run, and production provider availability is **UNKNOWN** until the operator checks their own authorized configuration. A real paid smoke test remains **NOT_RUN**, requiring explicit authorization. Existing evidence policy may require more permitted independent sources/claims; the patch does not weaken those requirements or approve a source merely because HTTP succeeded.

## Exact UI verification steps

On the app with the changed source loaded:

1. Sign in as the owner. Open **Search & Evidence**. If a job is already selected, expand **Start research on another topic**.
2. Enter `GSAT-7C and Varunastra defence contracts`, choose English, and paste `https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2323814&lang=2&reg=48` under **Verified source URLs**. Click **Start research** once. The existing research worker must be running; this task did not restart or reconfigure the user's applications.
3. Inspect discovery/provider results, Sources and Claims. Follow **Review the evidence package**. Complete the established human review and satisfy all readiness/corroboration blockers. A single article is not guaranteed to satisfy every policy threshold.
4. Once the current evidence package is approved, open **Script**, select English, and check model/budget settings. With an authorized configured provider, **Generate draft** is a paid-capable operation; no such operation was run here.
5. A valid response now says **Draft saved … Awaiting human review**, lists the saved revision and opens its narration/evidence mapping automatically. Refresh and select that English revision to verify persistence. Invalid output says **Generation failed validation** and remains inspectable; provider/empty-response failures show the actual error.

For the already-generated **test** draft only: isolated test app `http://127.0.0.1:8038`, DB/job/owner in `browser-state.json`; English v1 belongs to this fixture, not the production owner. Prefer opening `fixture-draft.md` or the screenshots instead of using test credentials in a normal app. The fixture server remains mocked and is not a deployment. It was left running, consistent with the instruction not to close applications.

## Preservation and remaining work

`protected-before.json` and `protected-after.json` show identical SHA-256 for both default and protected episode DB files. No production DB connections or review changes were used for this work. Current HEAD remains unchanged; all scoped source/tests/evidence are unstaged. No production application was restarted or closed; no shutdown occurred.

Remaining acceptance: human review of the actual topic's evidence and factual sufficiency, owner provider configuration verification, and an explicitly authorized live model run. The repair is **TESTED with live source access and controlled content/browser E2E**, with the live AI limitation above. No trend-rank, human-approval, deployment or paid-provider success claim is made.
