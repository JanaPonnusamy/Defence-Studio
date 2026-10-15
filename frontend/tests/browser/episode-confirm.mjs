// Read the script revision the previous UI generate already stored. Do not generate again.
import { mkdirSync, writeFileSync } from "node:fs";
import { launch, sleep } from "./cdp.mjs";

const BASE = process.env.BASE;
const OUT = process.env.OUT;
const JOB = process.env.JOB;
mkdirSync(OUT, { recursive: true });
const stages = [];
const stage = (name, status, detail) => {
  stages.push({ name, status, detail });
  console.log(status.padEnd(10), name, "-", String(detail).replace(/\s+/g, " ").slice(0, 320));
};
const b = await launch(Number(process.env.CDP_PORT || 9367));
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 10000, "login");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="script"]')`, 10000, "tabs");
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(JOB)})`);
  await b.goto(BASE + "/#/script");
  await b.send("Page.reload");
  await b.waitFor(`!!document.querySelector('[data-open-revision="en:1"]')`, 20000, "english revision");
  await b.click('[data-open-revision="en:1"]');
  await b.waitFor(`/offline-extractive/.test(document.querySelector('#detail-panel')?.innerText||'')`, 15000, "provider");
  const facts = await b.eval(`(() => { const t = document.querySelector('#detail-panel').innerText; return { awaiting: /status AWAITING_REVIEW/.test(t), provider: /Provider: offline-extractive/.test(t), attributed: /Attributed claim/.test(t), cost: /Measured cost: ₹0/.test(t), approval: /Approve/.test(t) }; })()`);
  const listed = await b.eval(`document.querySelector('#list-panel').innerText.slice(0,240)`);
  const ok = facts.awaiting && facts.provider && facts.attributed && facts.cost;
  stage("script-generation", ok ? "VERIFIED" : "FAILED", `${JSON.stringify(facts)} ${listed}`);
  await b.shot(OUT + "/episode-script-1440.png");
  await b.viewport(390, 844);
  const narrow = await b.metrics();
  await b.shot(OUT + "/episode-script-390.png");
  stage("script-layout-390", narrow.overflowX ? "FAILED" : "VERIFIED", `${narrow.sw}/${narrow.cw}`);
  await b.viewport(1440, 900);
  await b.goto(BASE + "/#/production");
  await b.send("Page.reload");
  await sleep(1200);
  const prod = await b.eval(`document.querySelector('#list-panel')?.innerText.slice(0,900) || document.body.innerText.slice(0,900)`);
  stage("scene-audio-render-qc-package", "NOT_RUN", prod);
  await b.shot(OUT + "/episode-production-1440.png");
  await b.send("Page.reload");
  await sleep(600);
  const persisted = await b.eval(`localStorage.getItem('ds_review_job_id')`);
  stage("refresh", persisted === JOB ? "VERIFIED" : "FAILED", persisted);
} catch (error) {
  stage("runner", "FAILED", String(error.stack || error).slice(0, 700));
} finally {
  writeFileSync(OUT + "/episode-confirm.json", JSON.stringify({ jobId: JOB, stages }, null, 2));
  console.log("JOB_STAGES " + stages.map((s) => `${s.name}=${s.status}`).join("; "));
  b.close();
  process.exit(stages.some((s) => s.status === "FAILED") ? 1 : 0);
}
