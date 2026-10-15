// Manual source-URL entry. Real Chrome, disposable API. One public fetch of
// https://example.com/ is started only after the URL is saved and the job is queued.
// No paid provider is called. OUT receives JSON and screenshots.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { launch, sleep } from "./cdp.mjs";

const BASE = process.env.BASE || "http://127.0.0.1:8032";
const OUT = process.env.OUT;
const GATE = process.env.GATE_JOB;
const BACKEND = process.env.BACKEND;
mkdirSync(OUT, { recursive: true });
const results = [];
const rec = (id, ok, note) => { results.push({ id, ok, note }); console.log((ok ? "PASS" : "FAIL").padEnd(5), id, "-", note); };
const TARGET = "https://example.com/";

const b = await launch(Number(process.env.CDP_PORT || 9346));
let worker = null;
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 10000, "dev enter");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="discovery"]')`, 10000, "tabs");
  await b.eval(`localStorage.removeItem('ds_review_job_id')`);
  await b.goto(BASE + "/#/discovery");
  await b.send("Page.reload");
  await sleep(800);
  await b.waitFor(`!!document.querySelector('#discovery-save-urls')`, 10000, "url control");
  await b.setValue("#discovery-topic", "Manual URL acceptance");

  b.resetLog();
  await b.click("#discovery-save-urls");
  await sleep(400);
  const emptyPosts = b.log.http.filter((h) => h.req && h.method === "POST" && /discovery\/requests/.test(h.url)).length;
  const emptyMsg = await b.eval(`document.querySelector('#discovery-url-status')?.textContent || ''`);
  rec("empty-url", emptyPosts === 0 && /at least one/i.test(emptyMsg), emptyMsg);

  await b.setValue("#discovery-seed-urls", "not a url");
  b.resetLog();
  await b.click("#discovery-save-urls");
  await sleep(300);
  const badPosts = b.log.http.filter((h) => h.req && h.method === "POST" && /discovery\/requests/.test(h.url)).length;
  const badMsg = await b.eval(`document.querySelector('#discovery-url-status')?.textContent || ''`);
  const kept = await b.eval(`document.querySelector('#discovery-seed-urls').value`);
  rec("invalid-url", badPosts === 0 && /http or https/i.test(badMsg) && kept === "not a url", badMsg);

  await b.setValue("#discovery-seed-urls", "https://user:secret@example.com/a");
  b.resetLog();
  await b.click("#discovery-save-urls");
  await sleep(300);
  const credPosts = b.log.http.filter((h) => h.req && h.method === "POST" && /discovery\/requests/.test(h.url)).length;
  const credMsg = await b.eval(`document.querySelector('#discovery-url-status')?.textContent || ''`);
  rec("credential-url-blocked", credPosts === 0 && /password/i.test(credMsg) && !/secret/.test(JSON.stringify(b.log.http)), credMsg);

  await b.setValue("#discovery-seed-urls", TARGET);
  b.resetLog();
  await b.eval(`(()=>{const e=document.querySelector('#discovery-save-urls'); e.click(); e.click();})()`);
  await b.waitFor(`/Saved|Could not|already/.test(document.querySelector('#discovery-url-status')?.textContent||'')`, 10000, "save status");
  await sleep(400);
  const savePosts = b.log.http.filter((h) => h.req && h.method === "POST" && /discovery\/requests/.test(h.url)).length;
  const saveMsg = await b.eval(`document.querySelector('#discovery-url-status')?.textContent || ''`);
  rec("valid-url-once", savePosts === 1 && /Saved 1 source URL/.test(saveMsg) && /Nothing is approved/i.test(saveMsg), `posts=${savePosts} ${saveMsg}`);
  rec("duplicate-submit", savePosts === 1, `discovery request posts=${savePosts}`);

  await b.viewport(390, 844);
  await sleep(300);
  const narrow = await b.metrics();
  await b.shot(OUT + "/manual-url-390.png");
  rec("layout-390", !narrow.overflowX && !!await b.eval(`document.querySelector('#discovery-seed-urls')`), `${narrow.sw}/${narrow.cw}`);
  await b.viewport(1440, 900);

  await b.click("#discovery-queue-btn");
  await b.waitFor(`/queued/i.test(document.querySelector('.toast')?.textContent||'')`, 8000, "queued");
  b.resetLog();
  await b.click("#discovery-save-urls");
  await sleep(800);
  const failMsg = await b.eval(`document.querySelector('#discovery-url-status')?.textContent || ''`);
  const still = await b.eval(`document.querySelector('#discovery-seed-urls').value`);
  rec("api-failure-keeps-input", /already started|queued|no longer/i.test(failMsg) && still === TARGET && !/traceback|site-packages|sqlite/i.test(failMsg), failMsg);

  const workerEnv = {
    ...process.env,
    PYTHONPATH: BACKEND,
    DEFENCE_STUDIO_DB_PATH: process.env.DEFENCE_STUDIO_DB_PATH,
    DEFENCE_STUDIO_STORAGE_PATH: process.env.DEFENCE_STUDIO_STORAGE_PATH,
    DEFENCE_STUDIO_AUTH_SECRET: process.env.DEFENCE_STUDIO_AUTH_SECRET,
  };
  delete workerEnv.DEFENCE_STUDIO_OPENAI_API_KEY;
  delete workerEnv.DEFENCE_STUDIO_SCRIPT_PROVIDER;
  worker = spawn(process.env.PYTHON || "python", ["-m", "defence_studio.research_worker"], {
    cwd: BACKEND,
    env: workerEnv,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let workerLog = "";
  worker.stdout.on("data", (c) => { workerLog += c.toString(); });
  worker.stderr.on("data", (c) => { workerLog += c.toString(); });
  delete process.env.DEFENCE_STUDIO_OPENAI_API_KEY;

  await b.click('[data-tab="discovery"]');
  let discoveryText = "";
  for (let i = 0; i < 20; i++) {
    await b.send("Page.reload");
    await sleep(1500);
    discoveryText = await b.eval(`document.querySelector('#list-panel')?.innerText || ''`);
    if (/manual_url|not permitted|SOURCE_NOT_ALLOWLISTED/i.test(discoveryText)) break;
  }
  const failureShown = /not permitted|SOURCE_NOT_ALLOWLISTED|manual_url/i.test(discoveryText);
  const leak = /traceback|site-packages|[A-Za-z]:\\/i.test(discoveryText);
  rec("fetch-failure-visible", failureShown && !leak, discoveryText.replace(/\s+/g, " ").slice(0, 280));
  await b.shot(OUT + "/manual-url-failure.png");
  await b.click('[data-tab="sources"]');
  await b.waitFor(`!/Loading sources/.test(document.querySelector('#list-panel')?.innerText||'')`, 8000, "sources settled");
  const manualSources = await b.eval(`document.querySelectorAll('[data-source-id]').length`);
  const manualText = await b.eval(`document.querySelector('#list-panel')?.innerText || ''`);
  rec("failed-fetch-creates-no-source", manualSources === 0 && /No sources were acquired/i.test(manualText), manualText.replace(/\s+/g, " ").slice(0, 180));
  await b.send("Page.reload");
  await sleep(1200);
  const afterFail = await b.eval(`document.querySelectorAll('[data-source-id]').length`);
  rec("refresh-keeps-failed-fetch", afterFail === 0, `source cards after refresh=${afterFail}`);
  if (GATE) {
    await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(GATE)})`);
    await b.goto(BASE + "/#/sources");
    await b.send("Page.reload");
    await sleep(1200);
    await b.waitFor(`document.querySelectorAll('[data-source-id]').length >= 1`, 10000, "seeded sources");
    const cards = await b.eval(`document.querySelectorAll('[data-source-id]').length`);
    const pending = await b.eval(`[...document.querySelectorAll('[data-source-id] .badge')].some(b=>/Pending review/.test(b.textContent))`);
    rec("source-list-shows-acquired", cards >= 2 && pending, `cards=${cards}`);
    await b.shot(OUT + "/manual-url-sources.png");
  }

  await b.click('[data-tab="script"]');
  await b.waitFor(`!!document.querySelector('#gen-submit')`, 10000, "script");
  b.resetLog();
  await b.click("#gen-submit");
  await sleep(400);
  const genPosts = b.log.http.filter((h) => h.req && h.method === "POST" && /script\/generate/.test(h.url)).length;
  const genDisabled = await b.eval(`document.querySelector('#gen-submit').disabled`);
  rec("pending-reviews-block-generation", genDisabled === true && genPosts === 0, `disabled=${genDisabled} generatePosts=${genPosts}`);

  if (GATE) {
    await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(GATE)})`);
    await b.goto(BASE + "/#/evidence");
    await b.send("Page.reload");
    await sleep(1200);
    await b.waitFor(`!!document.querySelector('#approve-package')`, 10000, "package approve");
    const gate = await b.eval(`({disabled: document.querySelector('#approve-package').disabled, text: document.querySelector('#list-panel').innerText.slice(0,400)})`);
    await b.click('[data-tab="script"]');
    await b.waitFor(`!!document.querySelector('#gen-submit')`, 10000, "gate script");
    const gateGen = await b.eval(`document.querySelector('#gen-submit').disabled`);
    rec("generate-stays-disabled-until-package-approved", gate.disabled === true && gateGen === true, gate.text.replace(/\s+/g, " ").slice(0, 240));
  }
  writeFileSync(OUT + "/worker.log", workerLog.slice(-8000));
} catch (error) {
  rec("runner", false, String(error.stack || error).slice(0, 800));
} finally {
  if (worker) worker.kill();
  writeFileSync(OUT + "/manual-url.json", JSON.stringify({ target: TARGET, results }, null, 2));
  const failed = results.filter((r) => !r.ok).length;
  console.log(`RESULT ${results.length - failed}/${results.length} passed, ${failed} failed`);
  b.close();
  process.exit(failed ? 1 : 0);
}
