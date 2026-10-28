// Studio tab (Phase 1): stage rail renders from server state, selection,
// navigation to the owning tab, resume after reload, and layout at 3 widths.
// Run against a disposable seeded db: BASE=<url> JOB=<job id> OUT=<dir> node tests/browser/studio.mjs
import { launch, sleep } from "./cdp.mjs";
import { mkdirSync, writeFileSync } from "node:fs";

const BASE = process.env.BASE;
const JOB = process.env.JOB;
const OUT = process.env.OUT;
mkdirSync(OUT, { recursive: true });
const results = [];
const rec = (id, ok, note) => { results.push({ id, ok, note }); console.log((ok ? "PASS" : "FAIL").padEnd(5), id, "-", note); };
const ready = `document.querySelector('#list-panel .studio-rail') || document.querySelector('#list-panel .error-state')`;
const b = await launch(Number(process.env.CDP_PORT || 9344));
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 8000, "login");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="studio"]')`, 8000, "tabs");
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(JOB)})`);
  await b.goto(BASE + "/#/studio");
  await b.send("Page.reload");
  await b.waitFor(`!!(${ready})`, 12000, "studio rail");
  const err = await b.eval(`document.querySelector('#list-panel .error-state')?.textContent || ''`);
  rec("rail-renders", !err, err || "rail present");
  const stages = await b.eval(`[...document.querySelectorAll('.studio-stage')].map(n => n.dataset.stage + ':' + n.dataset.state).join(' ')`);
  rec("nine-stages-in-pipeline-order", stages.split(" ").length === 9 && stages.startsWith("research:"), stages);
  const current = await b.eval(`document.querySelector('[aria-current="step"]')?.closest('.studio-stage')?.dataset.stage || ''`);
  rec("one-current-stage", !!current && (await b.eval(`document.querySelectorAll('[aria-current="step"]').length`)) === 1, current);
  rec("detail-shows-current", (await b.eval(`document.querySelector('#detail-panel h2')?.textContent || ''`)).length > 0,
    await b.eval(`document.querySelector('#detail-panel')?.innerText.replace(/\\s+/g,' ').slice(0,160) || ''`));
  await b.shot(`${OUT}/studio-1440.png`);

  await b.click('[data-select="render"]');
  await sleep(200);
  rec("select-other-stage", (await b.eval(`document.querySelector('#detail-panel h2')?.textContent`)) === "Render"
    && (await b.eval(`document.querySelector('[data-select="render"]').getAttribute('aria-pressed')`)) === "true", "Render selected");

  await b.send("Page.reload");
  await b.waitFor(`!!(${ready})`, 12000, "studio after reload");
  const after = await b.eval(`document.querySelector('[aria-current="step"]')?.closest('.studio-stage')?.dataset.stage || ''`);
  rec("resume-after-reload", after === current && (await b.eval(`location.hash`)) === "#/studio", `${after} ${await b.eval("location.hash")}`);

  await b.click(`[data-select="script"]`);
  await sleep(200);
  await b.click("[data-open-tab]");
  await sleep(800);
  rec("open-navigates-to-owning-tab", (await b.eval(`location.hash`)) === "#/script", await b.eval(`location.hash`));

  for (const [w, h] of [[768, 1024], [390, 844]]) {
    await b.viewport(w, h);
    await b.goto(BASE + "/#/studio");
    await b.waitFor(`!!(${ready})`, 12000, `studio ${w}`);
    await sleep(300);
    const m = await b.metrics();
    await b.shot(`${OUT}/studio-${w}.png`);
    rec(`no-horizontal-overflow-${w}`, !m.overflowX, `${m.sw}/${m.cw}`);
  }
  rec("no-js-exceptions", b.log.exceptions.length === 0, b.log.exceptions.slice(0, 2).join(" | ") || "none");
} catch (error) {
  rec("runner", false, String(error.stack || error).slice(0, 600));
} finally {
  writeFileSync(OUT + "/studio.json", JSON.stringify(results, null, 2));
  b.close();
  process.exit(results.some((r) => !r.ok) ? 1 : 0);
}
