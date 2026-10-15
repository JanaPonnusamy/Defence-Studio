// Populated frontend release acceptance. Real headless Chrome over CDP.
// Requires a running app (BASE) and JOB / EMPTY_JOB from seed_release_job.py.
// Does not call paid providers. Writes JSON and screenshots under OUT.
import { launch, sleep } from "./cdp.mjs";
import { mkdirSync, writeFileSync } from "node:fs";

const BASE = process.env.BASE || "http://127.0.0.1:8031";
const JOB = process.env.JOB;
const EMPTY = process.env.EMPTY_JOB;
const OUT = process.env.OUT || new URL("./out/release/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
mkdirSync(OUT, { recursive: true });
if (!JOB || !EMPTY) {
  console.error("JOB and EMPTY_JOB are required");
  process.exit(2);
}

const results = [];
const rec = (id, ok, note) => {
  results.push({ id, ok, note });
  console.log((ok ? "PASS" : "FAIL").padEnd(5), id, "-", note);
};

const b = await launch(Number(process.env.CDP_PORT || 9341));
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 10000, "dev enter");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="dashboard"]')`, 10000, "dashboard tab");

  const cards = await b.eval(`[...document.querySelectorAll('[data-job-card]')].map(c=>c.dataset.jobCard)`);
  rec("dashboard-lists-jobs", cards.includes(JOB) && cards.includes(EMPTY), `cards=${cards.length}`);
  await b.shot(OUT + "/dashboard-1440.png");

  await b.eval(`document.querySelector('[data-job-card="${JOB}"]').click()`);
  await b.waitFor(`location.hash.includes('sources') && !!document.querySelector('[data-source-id]')`, 10000, "sources");
  const sourceCount = await b.eval(`document.querySelectorAll('[data-source-id]').length`);
  rec("sources-populated", sourceCount >= 2, `source cards=${sourceCount}`);

  await b.click("[data-source-id]");
  await b.waitFor(`!!document.querySelector('#decision-note')`, 8000, "source decision");
  b.resetLog();
  await b.click('[data-decision="APPROVED_FOR_USE"]');
  await sleep(400);
  const toast = await b.eval(`document.querySelector('.toast')?.textContent || ''`);
  rec("source-review-requires-note", /explanation/i.test(toast), toast);
  await b.setValue("#decision-note", "Synthetic acceptance: both notices describe the same March drill.");
  await b.click('[data-decision="APPROVED_FOR_USE"]');
  await b.waitFor(`!!document.querySelector('.modal [data-action=cancel]')`, 4000, "confirm");
  await b.click(".modal [data-action=cancel]");
  await sleep(300);
  const stillPending = await b.eval(`document.querySelector('#detail-panel')?.innerText || ''`);
  rec("source-review-cancel", /PENDING|no decision/i.test(stillPending), "cancel leaves panel open");
  await b.click('[data-decision="APPROVED_FOR_USE"]');
  await b.waitFor(`!!document.querySelector('.modal [data-action=confirm]')`, 4000, "confirm again");
  await b.click(".modal [data-action=confirm]");
  await b.waitFor(`/Decision saved|APPROVED_FOR_USE/.test(document.body.innerText)`, 8000, "saved");
  rec("source-review-saved", true, "approval confirmed");
  await b.shot(OUT + "/source-review-1440.png");

  const before = await b.eval(`localStorage.getItem('ds_review_job_id')`);
  await b.send("Page.reload");
  await sleep(1500);
  await b.waitFor(`!!document.querySelector('[data-source-id]')`, 10000, "sources after refresh");
  const after = await b.eval(`localStorage.getItem('ds_review_job_id')`);
  const approved = await b.eval(`document.body.innerText.includes('APPROVED_FOR_USE')`);
  rec("refresh-persists-job", before === JOB && after === JOB, `before=${before} after=${after}`);
  rec("refresh-shows-saved-decision", approved, `approved visible=${approved}`);

  await b.click('[data-tab="claims"]');
  await b.waitFor(`!!document.querySelector('[data-claim-id]')`, 10000, "claims");
  const claimCount = await b.eval(`document.querySelectorAll('[data-claim-id]').length`);
  await b.click("[data-claim-id]");
  await b.waitFor(`!!document.querySelector('#detail-panel .passage') && !!document.querySelector('#decision-note')`, 8000, "claim detail");
  const passage = await b.eval(`document.querySelector('#detail-panel .passage')?.textContent || ''`);
  rec("claims-populated", claimCount >= 1 && passage.length > 10, `claim cards=${claimCount}; passage="${passage.slice(0, 80)}"`);
  b.resetLog();
  await b.click('#detail-panel [data-decision="APPROVED_FOR_USE"]');
  await sleep(300);
  const claimToast = await b.eval(`document.querySelector('.toast')?.textContent || ''`);
  rec("claim-review-requires-note", /explanation/i.test(claimToast), claimToast);
  await b.setValue("#decision-note", "Synthetic acceptance: the harbour notice supports this claim.");
  await b.click('#detail-panel [data-decision="APPROVED_FOR_USE"]');
  await b.waitFor(`!!document.querySelector('.modal [data-action=confirm]')`, 4000, "claim confirm");
  await b.click(".modal [data-action=confirm]");
  await b.waitFor(`/Decision saved/.test(document.querySelector('.toast')?.textContent||'')`, 8000, "claim saved");
  await sleep(500);
  rec("claim-review-saved", true, "non-conflict claim approval confirmed");
  await b.shot(OUT + "/claims-1440.png");

  await b.click('[data-tab="evidence"]');
  await b.waitFor(`/Evidence package|not approved|Draft|Loading evidence/.test(document.querySelector('#list-panel')?.innerText||'') && !/Loading evidence/.test(document.querySelector('#list-panel')?.innerText||'')`, 10000, "evidence");
  const evidenceText = await b.eval(`document.querySelector('#list-panel')?.innerText.slice(0,240) || ''`);
  rec("evidence-package-view", /Evidence package/.test(evidenceText), evidenceText.replace(/\s+/g, " "));
  await b.shot(OUT + "/evidence-1440.png");

  await b.click('[data-tab="script"]');
  await b.waitFor(`!!document.querySelector('#script-generate-form')`, 10000, "script form");
  const genDisabled = await b.eval(`document.querySelector('#gen-submit')?.disabled`);
  const blocked = await b.eval(`document.querySelector('.inline-blocked')?.innerText || ''`);
  rec("script-blocked-until-approval", genDisabled === true && /not ready|approved/i.test(blocked), blocked.replace(/\s+/g, " ").slice(0, 180));
  await b.shot(OUT + "/script-1440.png");

  await b.click('[data-tab="scene"]');
  await b.waitFor(`!!document.querySelector('#list-panel') && !/Loading scene/.test(document.querySelector('#list-panel').innerText)`, 10000, "scene");
  const sceneText = await b.eval(`document.querySelector('#list-panel')?.innerText || ''`);
  rec("scene-blocked-without-script", /No approved|script/i.test(sceneText), sceneText.replace(/\s+/g, " ").slice(0, 180));

  await b.click('[data-tab="production"]');
  await b.waitFor(`document.querySelectorAll('.prod-section').length >= 6`, 15000, "production sections");
  const sections = await b.eval(`[...document.querySelectorAll('.prod-section')].map(s=>s.dataset.section)`);
  const sectionErrors = await b.eval(`[...document.querySelectorAll('.prod-section .error-state')].map(e=>e.textContent.slice(0,120))`);
  const want = ["overview", "scenes", "render", "qc", "package", "credentials"];
  rec("production-sections", want.every((id) => sections.includes(id)), `sections=${sections.join(",")}`);
  rec("production-section-errors", sectionErrors.length === 0, sectionErrors.join(" | ") || "none");
  const subtitleCopy = await b.eval(`[...document.querySelectorAll('.prod-section')].some(s=>/subtitle/i.test(s.innerText))`);
  const packageCopy = await b.eval(`!!document.querySelector('[data-section="package"]')`);
  rec("subtitle-and-package-views", subtitleCopy && packageCopy, `subtitleCopy=${subtitleCopy} package=${packageCopy}`);
  const secretEcho = await b.eval(`[...document.querySelectorAll('[data-section="credentials"] input')].every(i=>i.type==='password' && i.value==='')`);
  rec("credentials-not-echoed", secretEcho, `password inputs empty=${secretEcho}`);
  await b.shot(OUT + "/production-1440.png");

  await b.click('[data-tab="discovery"]');
  await b.waitFor(`!!document.querySelector('#discovery-request-form, .candidate-list, .empty-state')`, 10000, "discovery");
  b.resetLog();
  const topic = await b.eval(`document.querySelector('#discovery-topic')`);
  if (topic) {
    await b.eval(`document.querySelector('#discovery-topic').value=''; document.querySelector('#discovery-request-form').requestSubmit()`);
    await sleep(400);
    const posted = b.log.http.filter((h) => h.req && h.method === "POST" && /discovery\/requests/.test(h.url)).length;
    const dtoast = await b.eval(`document.querySelector('.toast')?.textContent || ''`);
    rec("discovery-empty-topic", posted === 0 && /topic/i.test(dtoast), `posts=${posted} toast=${dtoast}`);
  } else {
    rec("discovery-empty-topic", false, "discovery form not shown on populated job");
  }
  await b.shot(OUT + "/discovery-1440.png");

  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(EMPTY)})`);
  await b.goto(BASE + "/#/sources");
  await b.send("Page.reload");
  await sleep(1200);
  await b.waitFor(`/No sources were acquired|No source has been acquired|No sources match/i.test(document.querySelector('#list-panel')?.innerText||'')`, 10000, "empty sources");
  rec("empty-job-sources", /No sources were acquired|No source has been acquired|No sources match/i.test(await b.eval(`document.querySelector('#list-panel')?.innerText||''`)), "empty source list");

  await b.eval(`document.querySelector('#job-id-field').value='not-a-real-job'; document.querySelector('#job-id-field').dispatchEvent(new Event('change',{bubbles:true}))`);
  await b.click("#load-job-btn");
  await b.waitFor(`/not found|error|Request error|Authentication/i.test(document.querySelector('#conn-indicator')?.innerText||document.querySelector('.error-state')?.innerText||'')`, 8000, "bad job");
  const conn = await b.eval(`(document.querySelector('#conn-indicator')?.innerText||'') + ' ' + (document.querySelector('.error-state')?.innerText||'')`);
  rec("bad-job-error", /not found|error|Request error/i.test(conn), conn.replace(/\s+/g, " ").slice(0, 180));
  await b.shot(OUT + "/bad-job-1440.png");

  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(JOB)})`);
  await b.goto(BASE + "/#/dashboard");
  await b.send("Page.reload");
  await sleep(1000);
  for (const [w, h, label] of [[1440, 900, "desktop"], [768, 1024, "tablet"], [390, 844, "mobile"]]) {
    await b.viewport(w, h);
    await b.click('[data-tab="dashboard"]');
    await sleep(400);
    const dash = await b.metrics();
    await b.click('[data-tab="production"]');
    await b.waitFor(`document.querySelectorAll('.prod-section').length>=6`, 12000, "prod " + label);
    const prod = await b.metrics();
    await b.shot(`${OUT}/production-${label}-${w}.png`);
    rec(`layout-${label}`, !dash.overflowX && !prod.overflowX, `dash ${dash.sw}/${dash.cw} prod ${prod.sw}/${prod.cw}`);
  }

  await b.viewport(1440, 900);
  await b.click("#sign-out-btn");
  await b.waitFor(`!!document.querySelector('#login-token-form')`, 5000, "login");
  rec("sign-out", true, "login form shown");
  await b.eval(`document.querySelector('#login-token-form').requestSubmit()`);
  await sleep(300);
  const loginErr = await b.eval(`document.querySelector('#login-error')?.textContent || ''`);
  rec("login-empty-token", /bearer token/i.test(loginErr), loginErr);

  const exceptions = b.log.exceptions.filter((e) => !/favicon/i.test(e));
  rec("no-js-exceptions", exceptions.length === 0, exceptions.slice(0, 3).join(" | ") || "none");
} catch (error) {
  rec("runner", false, String(error && error.stack || error).slice(0, 800));
} finally {
  writeFileSync(OUT + "/release-acceptance.json", JSON.stringify({ base: BASE, job: JOB, empty: EMPTY, results }, null, 2));
  const failed = results.filter((r) => !r.ok).length;
  console.log(`RESULT ${results.length - failed}/${results.length} passed, ${failed} failed`);
  b.close();
  process.exit(failed ? 1 : 0);
}
