// Voice Lab tab (Studio Phase 2): set a control, generate a real preview, play it,
// save + approve a profile, and check layout at 3 widths.
// Needs the backend wired to a running audio module with a declared provider:
// BASE=<url> JOB=<job id> OUT=<dir> node tests/browser/voicelab.mjs
import { launch, sleep } from "./cdp.mjs";
import { mkdirSync, writeFileSync } from "node:fs";

const BASE = process.env.BASE;
const JOB = process.env.JOB;
const OUT = process.env.OUT;
mkdirSync(OUT, { recursive: true });
const results = [];
const rec = (id, ok, note) => { results.push({ id, ok, note }); console.log((ok ? "PASS" : "FAIL").padEnd(5), id, "-", note); };
// A new rate each run: an identical configuration replays its stored preview/profile instead of creating one.
const RATE = 100 + (Date.now() % 120);
const formReady =`!!(document.querySelector('[data-voice-form]') || document.querySelector('#list-panel .error-state'))`;
const b = await launch(Number(process.env.CDP_PORT || 9345));
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 8000, "login");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="voicelab"]')`, 8000, "tabs");
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(JOB)})`);
  await b.goto(BASE + "/#/voicelab");
  await b.send("Page.reload");
  await b.waitFor(formReady, 20000, "voice form");
  // The seeded job is English; the Tamil voices are on the Tamil side of the lab.
  await b.eval(`(() => { const s = document.querySelector('#vl-language'); s.value = 'ta'; s.dispatchEvent(new Event('change')); })()`);
  await b.waitFor(`!!document.querySelector('[data-field="voice_id"] option[value="ta-IN-ValluvarNeural"]')`, 20000, "tamil voices");
  const err =await b.eval(`document.querySelector('#list-panel .error-state')?.textContent || ''`);
  rec("form-renders", !err, err || "form present");
  const providers = await b.eval(`[...document.querySelectorAll('[data-field="provider"] option')].map(o => o.value).join(',')`);
  rec("declared-available-providers-only", providers === "edge-neural", providers);
  const controls = await b.eval(`[...document.querySelectorAll('[data-control]')].map(i => i.dataset.control + ':' + i.min + '-' + i.max).join(' ')`);
  rec("controls-from-provider", /rate_wpm:80-260/.test(controls), controls);

  const before = await b.eval(`document.querySelectorAll('[data-preview]').length`);
  await b.eval(`(() => { const f = document.querySelector('[data-voice-form]');
    f.querySelector('[data-field="voice_id"]').value = 'ta-IN-ValluvarNeural';
    const on = f.querySelector('[data-control-on="rate_wpm"]'); on.checked = true; on.dispatchEvent(new Event('change')); })()`);
  await sleep(200);
  await b.eval(`(() => { const f = document.querySelector('[data-voice-form]');
    f.querySelector('[data-field="voice_id"]').value = 'ta-IN-ValluvarNeural';
    const r = f.querySelector('[data-control="rate_wpm"]'); r.value = '${RATE}'; r.dispatchEvent(new Event('input'));
    f.querySelector('[data-field="text"]').value = 'பாதுகாப்பு செய்திகளுக்கு வரவேற்கிறோம்.'; })()`);
  rec("control-value-shown", (await b.eval(`document.querySelector('[data-control-value="rate_wpm"]').textContent`)) === String(RATE), String(RATE));
  await b.click('[data-act="preview"]');
  await b.waitFor(`document.querySelectorAll('[data-preview]').length > ${before}`, 60000, "new preview");
  const newest = await b.eval(`document.querySelector('[data-preview]').innerText.replace(/\\s+/g,' ').slice(0,220)`);
  rec("real-preview-succeeded", /SUCCEEDED/.test(newest) && /edge-neural \/ edge-read-aloud-neural \/ ta-IN-ValluvarNeural/.test(newest)
    && newest.includes(`rate_wpm=${RATE}`), newest);
  await b.click('[data-preview] [data-act="play"]');
  await b.waitFor(`(() => { const a = document.querySelector('[data-preview] audio'); return a && !a.hidden && a.src.startsWith('blob:'); })()`, 15000, "audio loaded");
  rec("preview-audio-plays-from-authenticated-blob", true, await b.eval(`document.querySelector('[data-preview] audio').src.slice(0, 20)`));
  await b.shot(`${OUT}/voicelab-1440.png`);

  await b.click('[data-act="save-profile"]');
  await b.waitFor(`!!document.querySelector('[data-profile][data-status="AWAITING_REVIEW"]')`, 15000, "profile saved");
  const approveEnabled = await b.eval(`!document.querySelector('[data-profile][data-status="AWAITING_REVIEW"] [data-act="approve"]').disabled`);
  rec("approve-enabled-after-matching-preview", approveEnabled, String(approveEnabled));
  await b.eval(`document.querySelector('[data-profile][data-status="AWAITING_REVIEW"] [data-note]').value = 'Listened in browser test'`);
  await b.click('[data-profile][data-status="AWAITING_REVIEW"] [data-act="approve"]');
  await b.waitFor(`!document.querySelector('[data-profile][data-status="AWAITING_REVIEW"]')`, 15000, "approved");
  const statuses = await b.eval(`[...document.querySelectorAll('[data-profile]')].map(n => 'v' + n.dataset.profile + ':' + n.dataset.status).join(' ')`);
  rec("one-approved-older-superseded", (statuses.match(/APPROVED/g) || []).length === 1 && /SUPERSEDED/.test(statuses), statuses);

  await b.send("Page.reload");
  await b.waitFor(formReady, 20000, "after reload");
  await b.eval(`(() => { const s = document.querySelector('#vl-language'); s.value = 'ta'; s.dispatchEvent(new Event('change')); })()`);
  await b.waitFor(`!!document.querySelector('[data-profile]')`, 20000, "tamil profiles after reload");
  const persisted = await b.eval(`[...document.querySelectorAll('[data-profile]')].map(n => 'v' + n.dataset.profile + ':' + n.dataset.status).join(' ')`);
  rec("state-survives-reload", persisted === statuses, persisted);

  for (const [w, h] of [[768, 1024], [390, 844]]) {
    await b.viewport(w, h);
    await sleep(400);
    const m = await b.metrics();
    await b.shot(`${OUT}/voicelab-${w}.png`);
    rec(`no-horizontal-overflow-${w}`, !m.overflowX, `${m.sw}/${m.cw}`);
  }
  rec("no-js-exceptions", b.log.exceptions.length === 0, b.log.exceptions.slice(0, 2).join(" | ") || "none");
} catch (error) {
  rec("runner", false, String(error.stack || error).slice(0, 600));
} finally {
  writeFileSync(OUT + "/voicelab.json", JSON.stringify(results, null, 2));
  b.close();
  process.exit(results.some((r) => !r.ok) ? 1 : 0);
}
