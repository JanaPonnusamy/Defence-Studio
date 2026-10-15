// Approve the ready evidence package through the UI, then generate an English draft
// only if that approval enables the existing control. The disposable server must
// already be on DEFENCE_STUDIO_SCRIPT_PROVIDER=offline.
import { mkdirSync, writeFileSync } from "node:fs";
import { launch, sleep } from "./cdp.mjs";

const BASE = process.env.BASE;
const OUT = process.env.OUT;
const JOB = process.env.JOB;
mkdirSync(OUT, { recursive: true });
const NOTE = "Disposable browser acceptance 2026-10-15. UI decision path only; not an editorial approval for publication.";
const stages = [];
const stage = (name, status, detail) => {
  stages.push({ name, status, detail });
  console.log(status.padEnd(10), name, "-", String(detail).replace(/\s+/g, " ").slice(0, 320));
};
const b = await launch(Number(process.env.CDP_PORT || 9366));
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 10000, "login");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="evidence"]')`, 10000, "tabs");
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(JOB)})`);
  await b.goto(BASE + "/#/evidence");
  await b.send("Page.reload");
  await b.waitFor(`!!document.querySelector('#approve-package') && !/Loading evidence package/.test(document.querySelector('#list-panel')?.innerText||'')`, 20000, "package");
  const before = await b.eval(`({disabled: document.querySelector('#approve-package').disabled, text: document.querySelector('#list-panel').innerText.slice(0,500)})`);
  if (before.disabled || !/Ready for human package approval/.test(before.text)) {
    stage("package-approval", "BLOCKED", before.text);
  } else {
    await b.setValue("#package-note", NOTE);
    await b.click("#approve-package");
    await b.waitFor(`!!document.querySelector('.modal [data-action=confirm]')`, 4000, "confirm");
    await b.click(".modal [data-action=confirm]");
    await b.waitFor(`/Approved current revision/.test(document.querySelector('#list-panel')?.innerText||'')`, 15000, "approved");
    const after = await b.eval(`document.querySelector('#list-panel').innerText.slice(0,400)`);
    stage("package-approval", /Approved current revision/.test(after) ? "VERIFIED" : "FAILED", after);
    await b.shot(OUT + "/episode-package-approved-1440.png");
  }

  await b.goto(BASE + "/#/script");
  await b.send("Page.reload");
  await b.waitFor(`!!document.querySelector('#gen-submit')`, 20000, "script form");
  await b.eval(`document.querySelector('#gen-language').value = 'en'`);
  const language = await b.eval(`document.querySelector('#gen-language').value`);
  const enabled = await b.eval(`!document.querySelector('#gen-submit').disabled`);
  b.resetLog();
  if (language !== "en" || !enabled) {
    stage("script-generation", "BLOCKED", `language=${language} enabled=${enabled}`);
  } else {
    await b.click("#gen-submit");
    await b.waitFor(`/Draft generated|Evidence not ready|role="alert"/.test(document.querySelector('#list-panel')?.innerText||'') || /Draft generated|not ready|error/i.test(document.querySelector('#script-gen-status')?.textContent||'')`, 60000, "generate");
    await sleep(500);
    const status = await b.eval(`document.querySelector('#script-gen-status')?.textContent || ''`);
    const body = await b.eval(`document.querySelector('#list-panel')?.innerText.slice(0,500) || ''`);
    const external = b.log.http.filter((h) => /openai|api\\.anthropic|generativelanguage/i.test(h.url));
    const ok = /Draft generated/.test(status) && external.length === 0;
    stage("script-generation", ok ? "VERIFIED" : "FAILED", `language=${language} ${status} external=${external.length} ${body}`);
    await b.shot(OUT + "/episode-script-1440.png");
  }

  await b.viewport(390, 844);
  const narrow = await b.metrics();
  await b.shot(OUT + "/episode-script-390.png");
  stage("script-layout-390", narrow.overflowX ? "FAILED" : "VERIFIED", `${narrow.sw}/${narrow.cw}`);
  await b.viewport(1440, 900);
  await b.send("Page.reload");
  await sleep(800);
  const persisted = await b.eval(`localStorage.getItem('ds_review_job_id')`);
  stage("refresh", persisted === JOB ? "VERIFIED" : "FAILED", persisted);
} catch (error) {
  stage("runner", "FAILED", String(error.stack || error).slice(0, 700));
} finally {
  writeFileSync(OUT + "/episode-script.json", JSON.stringify({ jobId: JOB, stages }, null, 2));
  console.log("JOB_STAGES " + stages.map((s) => `${s.name}=${s.status}`).join("; "));
  b.close();
  process.exit(stages.some((s) => s.status === "FAILED") ? 1 : 0);
}
