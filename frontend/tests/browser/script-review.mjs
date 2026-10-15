// Read a copied disposable episode and record one explicit "request changes" decision.
// Does not approve the script, generate a scene plan, or call a provider.
import { mkdirSync, writeFileSync } from "node:fs";
import { launch, sleep } from "./cdp.mjs";

const BASE = process.env.BASE;
const OUT = process.env.OUT;
const JOB = process.env.JOB;
mkdirSync(OUT, { recursive: true });
const NOTE = "Disposable script-review check 2026-10-15. Requests changes only. Not an approval and not for publication.";
const stages = [];
const stage = (name, status, detail) => {
  stages.push({ name, status, detail });
  console.log(status.padEnd(10), name, "-", String(detail).replace(/\s+/g, " ").slice(0, 300));
};
const posts = (pattern) => b.log.http.filter((h) => h.req && h.method === "POST" && pattern.test(h.url)).length;
const b = await launch(Number(process.env.CDP_PORT || 9375));
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 10000, "login");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="script"]')`, 10000, "tabs");
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(JOB)})`);
  await b.goto(BASE + "/#/script");
  await b.send("Page.reload");
  await b.waitFor(`!!document.querySelector('[data-open-revision="en:1"]')`, 20000, "revision");
  await b.click('[data-open-revision="en:1"]');
  await b.waitFor(`/claim-05608baa58e4c016/.test(document.querySelector('#detail-panel')?.textContent||'')`, 15000, "warning");
  const detail = await b.eval(`document.querySelector('#detail-panel').textContent`);
  const shown = {
    warning: detail.includes("claim-05608baa58e4c016"),
    source: detail.includes("148827db7320d204c6c3f4e53296339538cf636f"),
    offline: detail.includes("Offline extractive draft"),
    awaiting: /status AWAITING_REVIEW/.test(detail),
    approveEnabled: await b.eval(`!document.querySelector('#approve-revision-btn').disabled`),
    changesEnabled: await b.eval(`!document.querySelector('#request-changes-btn').disabled`),
  };
  stage("script-inspect", shown.warning && shown.source && shown.offline && shown.awaiting && shown.approveEnabled && shown.changesEnabled ? "VERIFIED" : "FAILED", JSON.stringify(shown));
  await b.shot(OUT + "/script-review-1440.png");

  b.resetLog();
  await b.click("#approve-revision-btn");
  const emptyApprove = await b.eval(`document.querySelector('#revision-action-status')?.textContent || ''`);
  await b.setValue("#review-note", NOTE);
  await b.click("#approve-revision-btn");
  await b.waitFor(`!!document.querySelector('.modal [data-action=cancel]')`, 4000, "approve confirm");
  await b.click(".modal [data-action=cancel]");
  await sleep(400);
  const still = await b.eval(`document.querySelector('#detail-panel').textContent.includes('AWAITING_REVIEW')`);
  stage("approve-requires-confirm", emptyApprove.includes("Enter an approval explanation") && posts(/\/approve$/) === 0 && still ? "VERIFIED" : "FAILED", `empty=${emptyApprove} approvePosts=${posts(/\/approve$/)} awaiting=${still}`);

  b.resetLog();
  await b.setValue("#review-note", "");
  await b.click("#request-changes-btn");
  const emptyChanges = await b.eval(`document.querySelector('#revision-action-status')?.textContent || ''`);
  await b.setValue("#review-note", NOTE);
  await b.click("#request-changes-btn");
  await b.waitFor(`/CHANGES_REQUESTED/.test(document.querySelector('#detail-panel')?.textContent||'')`, 15000, "changes saved");
  stage("request-changes", emptyChanges.includes("Enter a note") && posts(/request-changes/) === 1 ? "VERIFIED" : "FAILED", `empty=${emptyChanges} posts=${posts(/request-changes/)}`);

  await b.send("Page.reload");
  await b.waitFor(`!!document.querySelector('[data-open-revision="en:1"]')`, 15000, "revision after refresh");
  await b.click('[data-open-revision="en:1"]');
  await b.waitFor(`/CHANGES_REQUESTED/.test(document.querySelector('#detail-panel')?.textContent||'')`, 15000, "persisted");
  const persisted = await b.eval(`({status: /status CHANGES_REQUESTED/.test(document.querySelector('#detail-panel').textContent), note: document.querySelector('#detail-panel').textContent.includes(${JSON.stringify(NOTE)}), approveDisabled: document.querySelector('#approve-revision-btn').disabled, job: localStorage.getItem('ds_review_job_id')})`);
  stage("review-persisted", persisted.status && persisted.note && persisted.approveDisabled && persisted.job === JOB ? "VERIFIED" : "FAILED", JSON.stringify(persisted));

  await b.viewport(390, 844);
  const narrow = await b.metrics();
  await b.shot(OUT + "/script-review-390.png");
  stage("script-layout-390", narrow.overflowX ? "FAILED" : "VERIFIED", `${narrow.sw}/${narrow.cw}`);
  await b.viewport(1440, 900);

  await b.goto(BASE + "/#/scene");
  await b.send("Page.reload");
  await b.waitFor(`!!document.querySelector('#scene-gen-language')`, 15000, "scene");
  await b.eval(`document.querySelector('#scene-gen-language').value='en'; document.querySelector('#scene-gen-language').dispatchEvent(new Event('change'))`);
  await sleep(300);
  const scene = await b.eval(`({disabled: document.querySelector('#scene-gen-submit').disabled, text: document.querySelector('#list-panel').innerText})`);
  stage("scene-plan", scene.disabled && /No approved English script revision/.test(scene.text) ? "BLOCKED" : "FAILED", scene.text.replace(/\s+/g, " ").slice(0, 220));

  await b.goto(BASE + "/#/production");
  await b.send("Page.reload");
  await b.waitFor(`/Pipeline status/.test(document.querySelector('#list-panel')?.innerText||'')`, 20000, "production");
  const gates = await b.eval(`({
    text: document.querySelector('#list-panel').innerText,
    plan: document.querySelector('[data-act="generate-plan"]')?.disabled,
    audio: document.querySelector('[data-act="generate-audio"]')?.disabled,
    visual: document.querySelector('[data-act="build-visual"]')?.disabled,
    render: document.querySelector('[data-act="start-render"]')?.disabled,
    qc: document.querySelector('[data-act="run-qc"]')?.disabled,
    pkg: document.querySelector('[data-act="build-pkg"]')?.disabled
  })`);
  const absent = /NONE scene plan/.test(gates.text) && /NONE assets/.test(gates.text) && /NONE audio/.test(gates.text) && /NONE render/.test(gates.text) && /NONE qc/.test(gates.text) && /NONE publish package/.test(gates.text);
  const disabled = gates.plan && gates.audio && gates.visual && gates.render && gates.qc && gates.pkg;
  stage("downstream-gates", absent && disabled ? "VERIFIED" : "FAILED", `plan=${gates.plan} audio=${gates.audio} visual=${gates.visual} render=${gates.render} qc=${gates.qc} pkg=${gates.pkg} ${gates.text.replace(/\s+/g, " ").slice(0, 240)}`);
  await b.shot(OUT + "/production-gates-1440.png");
  await b.viewport(390, 844);
  const prodNarrow = await b.metrics();
  await b.shot(OUT + "/production-gates-390.png");
  stage("production-layout-390", prodNarrow.overflowX ? "FAILED" : "VERIFIED", `${prodNarrow.sw}/${prodNarrow.cw}`);
} catch (error) {
  stage("runner", "FAILED", String(error.stack || error).slice(0, 700));
} finally {
  writeFileSync(OUT + "/script-review.json", JSON.stringify({ jobId: JOB, database: "copy", stages }, null, 2));
  console.log("JOB_STAGES " + stages.map((s) => `${s.name}=${s.status}`).join("; "));
  b.close();
  process.exit(stages.some((s) => s.status === "FAILED") ? 1 : 0);
}
