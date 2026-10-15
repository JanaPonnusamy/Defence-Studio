// Continue an existing disposable episode job through UI review and the evidence gate.
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
  console.log(status.padEnd(10), name, "-", String(detail).replace(/\s+/g, " ").slice(0, 280));
};
const b = await launch(Number(process.env.CDP_PORT || 9361));
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 10000, "login");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="sources"]')`, 10000, "tabs");
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(JOB)})`);
  await b.goto(BASE + "/#/sources");
  await b.send("Page.reload");
  await sleep(1200);
  await b.waitFor(`document.querySelectorAll('[data-source-id]').length >= 1`, 15000, "sources");
  const sourceCount = await b.eval(`document.querySelectorAll('[data-source-id]').length`);
  await b.click("[data-source-id]");
  await b.waitFor(`!!document.querySelector('#detail-panel a[href]')`, 8000, "provenance");
  const prov = await b.eval(`({text: document.querySelector('#detail-panel').innerText.slice(0,400), hrefs: [...document.querySelectorAll('#detail-panel a[href]')].map(a=>a.href)})`);
  stage("sources-listed", sourceCount >= 2 ? "VERIFIED" : "FAILED", `cards=${sourceCount}`);
  stage("source-provenance", /acquired/i.test(prov.text) && prov.hrefs.some((h) => /missilethreat\.csis\.org|www\.rusi\.org/.test(h)) ? "VERIFIED" : "FAILED", `${prov.hrefs.join(" ")} ${prov.text.slice(0, 160)}`);
  await b.viewport(390, 844);
  const narrow = await b.metrics();
  await b.shot(OUT + "/episode-source-390.png");
  stage("layout-390", narrow.overflowX ? "FAILED" : "VERIFIED", `${narrow.sw}/${narrow.cw}`);
  await b.viewport(1440, 900);

  let approvedSources = 0;
  for (let n = 0; n < 4; n++) {
    await b.goto(BASE + "/#/sources");
    await b.send("Page.reload");
    await b.waitFor(`document.querySelectorAll('[data-source-id]').length >= 1`, 15000, "sources settled");
    const pendingId = await b.eval(`(()=>{const c=[...document.querySelectorAll('[data-source-id]')].find(el=>/pending review/i.test(el.innerText)); return c?c.dataset.sourceId:''})()`);
    if (!pendingId) break;
    await b.eval(`document.querySelector('[data-source-id="${pendingId}"]').click()`);
    await b.waitFor(`!!document.querySelector('#decision-note')`, 8000, "source note");
    await b.setValue("#decision-note", NOTE);
    await b.click('#detail-panel [data-decision="APPROVED_FOR_USE"]');
    await b.waitFor(`!!document.querySelector('.modal [data-action=confirm]')`, 4000, "confirm");
    await b.eval(`document.querySelectorAll('.toast').forEach((n) => n.remove())`);
    await b.click(".modal [data-action=confirm]");
    await b.waitFor(`/Decision saved/.test(document.querySelector('.toast')?.textContent||'')`, 8000, "saved");
    approvedSources += 1;
  }
  const pendingSources = await b.eval(`[...document.querySelectorAll('[data-source-id]')].filter(el=>/pending review/i.test(el.innerText)).length`);
  stage("source-review", pendingSources === 0 && sourceCount >= 2 ? "VERIFIED" : "FAILED", `recordedThisRun=${approvedSources} pendingLeft=${pendingSources} cards=${sourceCount}`);

  let approvedClaims = 0;
  for (let n = 0; n < 50; n++) {
    if (n % 5 === 0) await b.goto(BASE + "/#/claims");
    await b.send("Page.reload");
    await b.waitFor(`document.querySelector('#list-panel') && !/Loading claims/.test(document.querySelector('#list-panel').innerText) && (document.querySelector('[data-claim-id]') || document.querySelector('#list-panel .empty-state'))`, 15000, "claims loaded");
    const pendingId = await b.eval(`(()=>{const c=[...document.querySelectorAll('[data-claim-id]')].find(el=>/pending review/i.test(el.innerText)); return c?c.dataset.claimId:''})()`);
    if (!pendingId) break;
    await b.eval(`const panel=document.querySelector('#detail-panel'); if(panel) panel.innerHTML='';`);
    await b.eval(`document.querySelector('[data-claim-id="${pendingId}"]').click()`);
    await b.waitFor(`!!document.querySelector('#detail-panel #decision-note')`, 8000, "claim note");
    await b.setValue("#decision-note", NOTE);
    await b.click('#detail-panel [data-decision="APPROVED_FOR_USE"]');
    await b.waitFor(`!!document.querySelector('.modal [data-action=confirm]')`, 4000, "claim confirm");
    await b.eval(`document.querySelectorAll('.toast').forEach((n) => n.remove())`);
    await b.click(".modal [data-action=confirm]");
    await b.waitFor(`/Decision saved/.test(document.querySelector('.toast')?.textContent||'')`, 8000, "claim saved");
    approvedClaims += 1;
    if (approvedClaims % 10 === 0) console.log("claims-recorded", approvedClaims);
  }
  await b.waitFor(`document.querySelector('#list-panel') && !/Loading claims/.test(document.querySelector('#list-panel').innerText) && (document.querySelector('[data-claim-id]') || document.querySelector('#list-panel .empty-state'))`, 15000, "claims settled");
  const pendingLeft = await b.eval(`[...document.querySelectorAll('[data-claim-id]')].filter(el=>/pending review/i.test(el.innerText)).length`);
  const claimCards = await b.eval(`document.querySelectorAll('[data-claim-id]').length`);
  stage("claim-review", pendingLeft === 0 && claimCards >= 43 ? "VERIFIED" : "BLOCKED", `recorded=${approvedClaims} pendingLeft=${pendingLeft} cards=${claimCards}`);

  await b.goto(BASE + "/#/evidence");
  await b.send("Page.reload");
  await sleep(1500);
  await b.waitFor(`/Evidence package/.test(document.querySelector('#list-panel')?.innerText||'') && !/Loading evidence package/.test(document.querySelector('#list-panel')?.innerText||'')`, 20000, "package");
  const evidence = await b.eval(`({text: document.querySelector('#list-panel').innerText.slice(0,700), approveDisabled: document.querySelector('#approve-package').disabled})`);
  const ready = /Ready for human package approval|Approved current revision/.test(evidence.text);
  stage("evidence-package", ready ? "VERIFIED" : "BLOCKED", `approveDisabled=${evidence.approveDisabled} ${evidence.text.replace(/\\s+/g, " ").slice(0, 400)}`);
  await b.shot(OUT + "/episode-evidence-1440.png");

  await b.goto(BASE + "/#/script");
  await b.send("Page.reload");
  await sleep(800);
  await b.waitFor(`!!document.querySelector('#gen-submit')`, 10000, "script");
  b.resetLog();
  const genDisabled = await b.eval(`document.querySelector('#gen-submit').disabled`);
  await b.click("#gen-submit");
  await sleep(500);
  const posts = b.log.http.filter((h) => h.req && h.method === "POST" && /script\/generate/.test(h.url)).length;
  stage("script-generation", genDisabled && posts === 0 ? "BLOCKED" : "FAILED", `disabled=${genDisabled} posts=${posts}`);

  await b.goto(BASE + "/#/production");
  await b.send("Page.reload");
  await sleep(1500);
  const prod = await b.eval(`document.querySelector('#list-panel')?.innerText.slice(0,350) || ''`);
  stage("scene-audio-render-qc-package", "NOT_RUN", prod.replace(/\s+/g, " ").slice(0, 260));
  await b.send("Page.reload");
  await sleep(800);
  const after = await b.eval(`localStorage.getItem('ds_review_job_id')`);
  stage("refresh", after === JOB ? "VERIFIED" : "FAILED", after);
  await b.shot(OUT + "/episode-production-1440.png");
} catch (error) {
  stage("runner", "FAILED", String(error.stack || error).slice(0, 700));
} finally {
  writeFileSync(OUT + "/episode-review.json", JSON.stringify({ jobId: JOB, stages }, null, 2));
  console.log("JOB_STAGES " + stages.map((s) => `${s.name}=${s.status}`).join("; "));
  b.close();
  process.exit(stages.some((s) => s.status === "FAILED") ? 1 : 0);
}
