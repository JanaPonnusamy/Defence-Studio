// Remaining workspace tabs on the already seeded acceptance job.
import { launch, sleep } from "./cdp.mjs";
import { mkdirSync, writeFileSync } from "node:fs";

const BASE = process.env.BASE;
const JOB = process.env.JOB;
const OUT = process.env.OUT;
mkdirSync(OUT, { recursive: true });
const results = [];
const rec = (id, ok, note) => { results.push({ id, ok, note }); console.log((ok ? "PASS" : "FAIL").padEnd(5), id, "-", note); };
const b = await launch(Number(process.env.CDP_PORT || 9343));
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 8000, "login");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="factcheck"]')`, 8000, "tabs");
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(JOB)})`);
  for (const tab of ["factcheck", "voice", "inspector", "research"]) {
    await b.goto(BASE + `/#/${tab}`);
    await b.send("Page.reload");
    await sleep(1500);
    await b.waitFor(`document.querySelector('#list-panel') && !/Loading/.test(document.querySelector('#list-panel').innerText)`, 12000, tab);
    const text = await b.eval(`document.querySelector('#list-panel')?.innerText.replace(/\\s+/g,' ').slice(0,180) || ''`);
    const err = await b.eval(`document.querySelector('#list-panel .error-state')?.textContent || ''`);
    await b.shot(`${OUT}/${tab}-1440.png`);
    rec(`${tab}-renders`, !err, err || text);
  }
  await b.viewport(390, 844);
  await b.click('[data-tab="factcheck"]');
  await sleep(500);
  const m = await b.metrics();
  rec("factcheck-mobile-overflow", !m.overflowX, `${m.sw}/${m.cw}`);
  rec("rest-no-js-exceptions", b.log.exceptions.length === 0, b.log.exceptions.slice(0, 2).join(" | ") || "none");
} catch (error) {
  rec("runner", false, String(error.stack || error).slice(0, 600));
} finally {
  writeFileSync(OUT + "/release-tabs.json", JSON.stringify(results, null, 2));
  b.close();
  process.exit(results.some((r) => !r.ok) ? 1 : 0);
}
