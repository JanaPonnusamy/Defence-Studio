// Search & Evidence browser acceptance (real headless Chrome over CDP, real server + worker).
// Env: BASE (default http://127.0.0.1:8030), FULL_JOB (job with policy run), BLOCKED_JOB (not-ready job),
//      Start Research is exercised live with a topic (no fixtures are injected).
import { launch, sleep } from "./cdp.mjs";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE || "http://127.0.0.1:8030";
const FULL = process.env.FULL_JOB, BLOCKED = process.env.BLOCKED_JOB;
const OUT = new URL("./out/", import.meta.url).pathname.replace(/^\//, "");
mkdirSync(OUT, { recursive: true });
const results = [];
const rec = (id, ok, note) => { results.push({ id, ok, note }); console.log((ok ? "PASS" : "FAIL").padEnd(5), id, "-", note); };

const b = await launch(9334);
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 8000, "dev enter");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="research"]')`, 8000, "research tab");

  // 1. validation: empty topic never reaches the API
  await b.eval(`localStorage.removeItem('ds_review_job_id')`);
  await b.goto(BASE + "/#/research"); await b.send("Page.reload"); await sleep(1200);
  await b.waitFor(`!!document.querySelector('#research-start')`, 8000, "start form");
  b.resetLog();
  await b.eval(`document.querySelector('#research-start').requestSubmit?.()`);
  await sleep(500);
  const posted = b.log.http.filter((h) => h.req && h.method === "POST" && /\/api\/jobs$/.test(h.url)).length;
  rec("start-validation", posted === 0, `POST /api/jobs calls with empty topic: ${posted}`);
  await b.shot(OUT + "research-start.png");

  // 2. real Start Research -> worker -> monitor
  await b.setValue("#rs-topic", "S-400 Triumf air defence system");
  await b.click("#research-start button[type=submit]");
  await b.waitFor(`!!document.querySelector('[data-state]')`, 20000, "monitor rendered after start");
  const first = await b.eval(`document.querySelector('[data-state]').dataset.state`);
  const states = new Set([first]);
  for (let i = 0; i < 40; i++) {
    await sleep(1500);
    states.add(await b.eval(`document.querySelector('[data-state]')?.dataset.state`));
    if (["COMPLETED", "FAILED", "AWAITING_READINESS", "CANCELLED"].includes([...states].pop())) break;
  }
  const jobHash = await b.eval(`location.hash`);
  const stagesDone = await b.eval(`document.querySelectorAll('.pipeline .stage--COMPLETED').length`);
  rec("start-research-live", states.size >= 1 && stagesDone >= 1, `states observed: ${[...states].join(" -> ")}; completed stages ${stagesDone}`);
  await b.shot(OUT + "research-live.png");
  const elapsed = await b.eval(`document.querySelector('.timing')?.innerText`);
  rec("timing-visible", /Elapsed/.test(elapsed || "") && /Estimated remaining/.test(elapsed || ""), (elapsed || "").replace(/\n/g, " | "));

  // 3. refresh persistence
  const before = await b.eval(`document.querySelector('h2')?.textContent`);
  await b.send("Page.reload"); await sleep(1500);
  await b.waitFor(`!!document.querySelector('[data-state]')`, 8000, "monitor after refresh");
  const after = await b.eval(`document.querySelector('h2')?.textContent`);
  rec("refresh-persists-job", before === after, `topic before/after refresh: ${before} / ${after}`);

  // 4. explorer on the full job
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(FULL)})`);
  await b.send("Page.reload"); await sleep(1800);
  await b.waitFor(`!!document.querySelector('#ex-filter')`, 10000, "explorer");
  const total = await b.eval(`document.querySelectorAll('tr[data-claim-id]').length`);
  await b.setValue("#ex-filter", "AUTO_ACCEPTED"); await sleep(300);
  const accepted = await b.eval(`document.querySelectorAll('tr[data-claim-id]').length`);
  const badgesOk = await b.eval(`[...document.querySelectorAll('tr[data-claim-id] .badge')].every(e=>e.textContent==='AUTO_ACCEPTED')`);
  rec("filter-accepted", accepted > 0 && accepted < total && badgesOk, `all ${total} -> accepted ${accepted}`);
  await b.setValue("#ex-filter", "NEEDS_REVIEW"); await sleep(300);
  rec("filter-unresolved", await b.eval(`document.querySelectorAll('tr[data-claim-id]').length`) > 0, "needs-review rows listed (not hidden, not usable)");
  await b.setValue("#ex-filter", "AUTO_ACCEPTED"); await sleep(200);
  await b.eval(`document.querySelector('tr[data-claim-id]').click()`);
  await b.waitFor(`!!document.querySelector('#detail-panel mark')`, 5000, "detail passage");
  const det = await b.eval(`({mark:document.querySelector('#detail-panel mark').textContent,
     h:document.querySelector('#detail-panel h2').textContent, txt:document.querySelector('#detail-panel').innerText})`);
  rec("claim-detail", det.mark.length > 10 && /Decision reasons/i.test(det.txt) && /Score components/i.test(det.txt) && /Unknown|\d{4}-\d{2}-\d{2}/.test(det.txt),
    `passage "${det.mark.slice(0, 60)}"; has reasons+components+date`);
  await b.shot(OUT + "research-explorer-1440.png");
  const links = await b.eval(`[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))`);
  rec("links-http-only", links.every((h) => /^(https?:\/\/|#|\/)/.test(h)), `${links.length} links checked`);
  const scripts = await b.eval(`document.querySelectorAll('#list-panel script, #detail-panel script').length`);
  rec("no-script-elements", scripts === 0, `script elements in rendered evidence: ${scripts}`);
  await b.click('#detail-panel [data-action="view-source"]');
  await b.waitFor(`/Extracted claims/i.test(document.querySelector('#detail-panel').innerText)`, 5000, "source detail");
  rec("source-detail", true, (await b.eval(`document.querySelector('#detail-panel h2').textContent`)));

  // exports: observe the real authenticated requests
  b.resetLog();
  await b.click('[data-action="export"][data-format="csv"]'); await sleep(1200);
  await b.click('[data-action="export"][data-format="json"]'); await sleep(1200);
  const exp = b.log.http.filter((h) => !h.req && /evidence\/report\.(csv|json)/.test(h.url));
  rec("exports", exp.length === 2 && exp.every((e) => e.status === 200), exp.map((e) => `${e.url.split("/").pop()}:${e.status}`).join(", "));

  // 5. responsive
  for (const [w, h, name] of [[1440, 900, "desktop"], [768, 1024, "tablet"], [390, 844, "mobile"]]) {
    await b.viewport(w, h); await sleep(400);
    const m = await b.metrics();
    const tableScrolls = await b.eval(`(()=>{const t=document.querySelector('.table-wrap');return t?{sw:t.scrollWidth,cw:t.clientWidth}:null})()`);
    await b.shot(OUT + `research-${name}.png`);
    rec(`responsive-${name}`, !m.overflowX, `page scrollWidth ${m.sw} / clientWidth ${m.cw}; table ${JSON.stringify(tableScrolls)}`);
  }
  await b.viewport(1440, 900);

  // 6. readiness-blocked and not-evaluated states
  if (BLOCKED) {
    await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(BLOCKED)})`);
    await b.send("Page.reload"); await sleep(1800);
    await b.waitFor(`!!document.querySelector('.readiness')`, 10000, "readiness banner");
    const t = await b.eval(`document.querySelector('.readiness').innerText`);
    rec("readiness-blocked", /Not ready/.test(t) && /min_accepted/.test(t), t.replace(/\n/g, " | ").slice(0, 200));
    await b.shot(OUT + "research-blocked.png");
  }
  rec("console-clean", b.log.exceptions.length === 0, `exceptions: ${b.log.exceptions.length}; console errors: ${b.log.console.length}`);
} catch (e) {
  rec("harness", false, String(e.message || e));
} finally { b.close(); }
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
