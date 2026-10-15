// Controlled English episode on a disposable database.
// Allowlisted public pages only. No source-policy change. No paid provider.
// OUT receives screenshots and episode-stages.json.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { launch, sleep } from "./cdp.mjs";

const BASE = process.env.BASE;
const OUT = process.env.OUT;
const BACKEND = process.env.BACKEND;
mkdirSync(OUT, { recursive: true });
const NOTE = "Disposable browser acceptance 2026-10-15. UI decision path only; not an editorial approval for publication.";
const URLS = [
  "https://missilethreat.csis.org/defsys/s-400-triumf/",
  "https://www.rusi.org/explore-our-research/publications/rusi-defence-systems/russias-air-defence-challenge-syria",
];
const stages = [];
const stage = (name, status, detail) => {
  stages.push({ name, status, detail });
  console.log(status.padEnd(10), name, "-", String(detail).replace(/\s+/g, " ").slice(0, 240));
};

const b = await launch(Number(process.env.CDP_PORT || 9360));
let worker = null;
let workerLog = "";
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 10000, "login");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="discovery"]')`, 10000, "tabs");
  await b.eval(`localStorage.removeItem('ds_review_job_id')`);
  await b.goto(BASE + "/#/discovery");
  await b.send("Page.reload");
  await sleep(800);
  await b.waitFor(`!!document.querySelector('#discovery-save-urls')`, 10000, "url form");
  await b.setValue("#discovery-topic", "S-400 Triumf English acceptance");
  await b.setValue("#discovery-language", "en");
  await b.setValue("#discovery-seed-urls", URLS.join("\n"));
  await b.click("#discovery-save-urls");
  await b.waitFor(`/Saved|Could not|already|not permitted/.test(document.querySelector('#discovery-url-status')?.textContent||'')`, 10000, "saved");
  const saveMsg = await b.eval(`document.querySelector('#discovery-url-status')?.textContent || ''`);
  const jobId = await b.eval(`localStorage.getItem('ds_review_job_id')`);
  stage("discover-save", /Saved 2 source URL/.test(saveMsg) ? "VERIFIED" : "FAILED", `${saveMsg} job=${jobId}`);
  if (!jobId) throw new Error("no job id");

  await b.click("#discovery-queue-btn");
  await b.waitFor(`/queued/i.test(document.querySelector('.toast')?.textContent||'')`, 8000, "queued");
  const workerEnv = { ...process.env, PYTHONPATH: BACKEND };
  delete workerEnv.DEFENCE_STUDIO_OPENAI_API_KEY;
  worker = spawn(process.env.PYTHON || "python", ["-m", "defence_studio.research_worker"], {
    cwd: BACKEND, env: workerEnv, stdio: ["ignore", "pipe", "pipe"],
  });
  worker.stdout.on("data", (c) => { workerLog += c; });
  worker.stderr.on("data", (c) => { workerLog += c; });

  let discoveryText = "";
  for (let i = 0; i < 30; i++) {
    await sleep(2000);
    await b.goto(BASE + "/#/discovery");
    await b.send("Page.reload");
    await sleep(800);
    discoveryText = await b.eval(`document.querySelector('#list-panel')?.innerText || ''`);
    if (/manual_url|candidate|not permitted|ERROR|No results/i.test(discoveryText) && !/Saving|Loading discovery/.test(discoveryText)) break;
  }
  const candidateCount = await b.eval(`document.querySelectorAll('.candidate-card, [data-candidate-id]').length`);
  const fetchFailed = /SOURCE_NOT_ALLOWLISTED|not permitted|manual_url \(ERROR\)/i.test(discoveryText);
  stage("discover-fetch", candidateCount > 0 && !fetchFailed ? "VERIFIED" : fetchFailed ? "FAILED" : "BLOCKED",
    `candidates=${candidateCount} ${discoveryText.slice(0, 280)}`);
  await b.shot(OUT + "/episode-discovery-1440.png");

  let sourceCount = 0;
  for (let i = 0; i < 30; i++) {
    await b.goto(BASE + "/#/sources");
    await b.send("Page.reload");
    await sleep(1500);
    sourceCount = await b.eval(`document.querySelectorAll('[data-source-id]').length`);
    if (sourceCount >= 1) break;
  }
  stage("sources-listed", sourceCount >= 1 ? "VERIFIED" : "BLOCKED", `cards=${sourceCount}`);
  if (sourceCount >= 1) {
    await b.click("[data-source-id]");
    await b.waitFor(`!!document.querySelector('#detail-panel a[href]')`, 8000, "provenance");
    const prov = await b.eval(`document.querySelector('#detail-panel')?.innerText.slice(0,500) || ''`);
    const hrefs = await b.eval(`[...document.querySelectorAll('#detail-panel a[href]')].map(a=>a.href)`);
    stage("source-provenance", /Acquired:/.test(prov) && hrefs.some((h) => /missilethreat|rusi/.test(h)) ? "VERIFIED" : "FAILED", `${hrefs.join(" ")} ${prov.slice(0, 180)}`);
    await b.viewport(390, 844);
    await sleep(300);
    const narrow = await b.metrics();
    await b.shot(OUT + "/episode-source-390.png");
    stage("layout-390", narrow.overflowX ? "FAILED" : "VERIFIED", `${narrow.sw}/${narrow.cw}`);
    await b.viewport(1440, 900);
  }

  let approvedSources = 0;
  for (let n = 0; n < 4; n++) {
    await b.goto(BASE + "/#/sources");
    await b.send("Page.reload");
    await sleep(900);
    const pendingId = await b.eval(`(()=>{const c=[...document.querySelectorAll('[data-source-id]')].find(el=>/Pending review/.test(el.innerText)); return c?c.dataset.sourceId:''})()`);
    if (!pendingId) break;
    await b.eval(`document.querySelector('[data-source-id="${pendingId}"]').click()`);
    await b.waitFor(`!!document.querySelector('#decision-note')`, 8000, "source note");
    await b.setValue("#decision-note", NOTE);
    await b.click('#detail-panel [data-decision="APPROVED_FOR_USE"]');
    await b.waitFor(`!!document.querySelector('.modal [data-action=confirm]')`, 4000, "source confirm");
    await b.click(".modal [data-action=confirm]");
    await b.waitFor(`/Decision saved/.test(document.querySelector('.toast')?.textContent||'')`, 8000, "source saved");
    approvedSources += 1;
  }
  stage("source-review", approvedSources >= 1 ? "VERIFIED" : "BLOCKED", `ui approvals recorded=${approvedSources}; note marks disposable acceptance, not publication approval`);

  let approvedClaims = 0;
  for (let n = 0; n < 12; n++) {
    await b.goto(BASE + "/#/claims");
    await b.send("Page.reload");
    await sleep(900);
    const pendingId = await b.eval(`(()=>{const c=[...document.querySelectorAll('[data-claim-id]')].find(el=>/Pending review/.test(el.innerText)); return c?c.dataset.claimId:''})()`);
    if (!pendingId) break;
    await b.eval(`document.querySelector('[data-claim-id="${pendingId}"]').click()`);
    await b.waitFor(`!!document.querySelector('#detail-panel #decision-note')`, 8000, "claim note");
    await b.setValue("#decision-note", NOTE);
    await b.click('#detail-panel [data-decision="APPROVED_FOR_USE"]');
    await b.waitFor(`!!document.querySelector('.modal [data-action=confirm]')`, 4000, "claim confirm");
    await b.click(".modal [data-action=confirm]");
    await b.waitFor(`/Decision saved/.test(document.querySelector('.toast')?.textContent||'')`, 8000, "claim saved");
    approvedClaims += 1;
  }
  const claimsLeft = await b.eval(`document.querySelectorAll('[data-claim-id]').length`);
  stage("claim-review", approvedClaims >= 1 ? "VERIFIED" : "BLOCKED", `ui approvals recorded=${approvedClaims}; cards still listed=${claimsLeft}`);

  await b.goto(BASE + "/#/evidence");
  await b.send("Page.reload");
  await sleep(1500);
  await b.waitFor(`/Evidence package|Loading evidence package/.test(document.querySelector('#list-panel')?.innerText||'') && !/Loading evidence package/.test(document.querySelector('#list-panel')?.innerText||'')`, 15000, "package");
  const evidence = await b.eval(`({text: document.querySelector('#list-panel').innerText.slice(0,600), approveDisabled: document.querySelector('#approve-package')?.disabled})`);
  const ready = /Ready for human package approval|Approved current revision/.test(evidence.text);
  stage("evidence-package", ready ? "VERIFIED" : "BLOCKED", `approveDisabled=${evidence.approveDisabled} ${evidence.text.replace(/\s+/g, " ").slice(0, 320)}`);
  await b.shot(OUT + "/episode-evidence-1440.png");

  await b.goto(BASE + "/#/script");
  await b.send("Page.reload");
  await sleep(1000);
  await b.waitFor(`!!document.querySelector('#gen-submit')`, 10000, "script");
  b.resetLog();
  const genDisabled = await b.eval(`document.querySelector('#gen-submit').disabled`);
  if (genDisabled) {
    await b.click("#gen-submit");
    await sleep(400);
    const posts = b.log.http.filter((h) => h.req && h.method === "POST" && /script\/generate/.test(h.url)).length;
    stage("script-generation", "BLOCKED", `button disabled; generate posts=${posts}; offline provider was configured and was not called`);
  } else if (process.env.DEFENCE_STUDIO_SCRIPT_PROVIDER === "offline") {
    await b.click("#gen-submit");
    await b.waitFor(`!/Generating|Loading/.test(document.querySelector('#script-gen-status')?.innerText||'x') || document.querySelectorAll('[data-open-revision]').length>0`, 20000, "generate");
    await sleep(800);
    const genText = await b.eval(`(document.querySelector('#script-gen-status')?.innerText||'') + ' ' + (document.querySelector('#list-panel')?.innerText||'')`);
    const posts = b.log.http.filter((h) => h.req && h.method === "POST" && /script\/generate/.test(h.url)).length;
    stage("script-generation", /revision|draft|English/i.test(genText) && posts === 1 ? "VERIFIED" : "FAILED", `posts=${posts} ${genText.replace(/\s+/g, " ").slice(0, 240)}`);
  } else {
    stage("script-generation", "NOT_RUN", "button enabled but no non-paid provider was configured");
  }

  for (const name of ["scene", "production"]) {
    await b.goto(BASE + `/#/${name}`);
    await b.send("Page.reload");
    await sleep(1200);
  }
  const prod = await b.eval(`document.querySelector('#list-panel')?.innerText.slice(0,400) || ''`);
  stage("scene-audio-render-qc-package", /No approved|disabled|NONE|No scene|No narration|No render|No package/i.test(prod) ? "NOT_RUN" : "BLOCKED", prod.replace(/\s+/g, " ").slice(0, 280));

  await b.send("Page.reload");
  await sleep(1000);
  const after = await b.eval(`({job: localStorage.getItem('ds_review_job_id'), hash: location.hash, conn: document.querySelector('#conn-indicator')?.innerText || ''})`);
  stage("refresh", after.job === jobId ? "VERIFIED" : "FAILED", JSON.stringify(after));
  writeFileSync(OUT + "/worker.log", workerLog.slice(-6000));
  writeFileSync(OUT + "/episode-stages.json", JSON.stringify({ jobId, urls: URLS, base: BASE, stages }, null, 2));
} catch (error) {
  stage("runner", "FAILED", String(error.stack || error).slice(0, 800));
  writeFileSync(OUT + "/episode-stages.json", JSON.stringify({ stages, error: String(error) }, null, 2));
} finally {
  if (worker) worker.kill();
  const failed = stages.some((s) => s.status === "FAILED");
  console.log("JOB_STAGES " + stages.map((s) => `${s.name}=${s.status}`).join("; "));
  b.close();
  process.exit(failed ? 1 : 0);
}
