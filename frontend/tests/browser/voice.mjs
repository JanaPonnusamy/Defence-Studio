import { launch, sleep } from "./cdp.mjs";
import { writeFileSync, mkdirSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";

const BASE = process.env.BASE || "http://127.0.0.1:8030";
const OUT = new URL("./out/", import.meta.url).pathname.replace(/^\//, "");
mkdirSync(OUT, { recursive: true });
const results = [];
const rec = (id, status, note) => { results.push({ id, status, note }); console.log(status.padEnd(10), id, "-", note); };

// Owned local fixture: speech synthesized locally by an OS voice (no third-party audio).
const ref = (OUT + "voice-ref.wav").replace(/\//g, "\\");
if (!existsSync(ref)) {
  execFileSync("powershell", ["-NoProfile", "-Command",
    `Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;$s.SelectVoice('Microsoft Hazel Desktop');` +
    `$s.SetOutputToWaveFile('${ref}');$s.Speak('Breaking news tonight. The ministry confirmed a new test. Officials said more details will follow soon.');$s.Dispose()`]);
}

const b = await launch(9334);
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 8000, "dev enter");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="voice"]')`, 8000, "voice tab button");
  b.resetLog();
  // reach the tab by clicking the real navigation control
  await b.click('[data-tab="voice"]');
  await b.waitFor(`!!document.querySelector('#va-form')`, 8000, "analyzer panel");
  rec("V-A-nav-click", "PASS", "Voice tab opened from the tab bar; analyzer form rendered");

  for (const [w, h] of [[1440, 900], [768, 1024], [390, 844]]) {
    await b.viewport(w, h); await sleep(300);
    const m = await b.metrics();
    const missing = await b.eval(`['#va-form input[type=file]','#va-form [name=profile_name]','#va-form [name=authorization_ref]','#va-form button[type=submit]'].filter(s=>!document.querySelector(s))`);
    await b.shot(`${OUT}voice-${w}.png`);
    rec(`V-A-render-${w}`, !m.overflowX && !missing.length ? "PASS" : "FAIL", `scrollWidth=${m.sw} clientWidth=${m.cw} missing=${JSON.stringify(missing)}`);
  }
  await b.viewport(1440, 900);
  rec("V-A-console", !b.log.exceptions.length && !b.log.console.filter((c) => c.type !== "warning").length ? "PASS" : "FAIL",
    JSON.stringify({ e: b.log.exceptions, c: b.log.console }).slice(0, 400));

  // V-B models panel
  await b.click('[data-panel="models"]');
  await b.waitFor(`!!document.querySelector('.voice-model')`, 8000, "models");
  const models = await b.eval(`[...document.querySelectorAll('.voice-model')].map(e=>e.innerText.replace(/\\s+/g,' '))`);
  const page = await b.eval(`document.body.innerText.replace(/\\s+/g,' ')`);
  const narrator = models.find((m) => m.includes("DEFENCE_ERA_FEMALE_001")) || "";
  rec("V-B-narrator-not-provisioned", /NOT_PROVISIONED/.test(narrator) && /not available/.test(narrator) ? "PASS" : "FAIL", narrator.slice(0, 160));
  const os = models.filter((m) => /OS_VOICE/.test(m));
  rec("V-B-os-voices-english-only", os.length > 0 && os.every((m) => /Tamil verified: no/.test(m) && !/ta-IN/.test(m)) ? "PASS" : "FAIL", `${os.length} OS voice(s); ` + (os[0] || "").slice(0, 150));
  rec("V-B-tone-hidden-by-default", !models.some((m) => /TEST_TONE/.test(m)) ? "PASS" : "FAIL", "synthetic tone voice not listed (env flag unset)");
  rec("V-B-aligner-honest", /Forced aligner:[^.]*not available/.test(page) && /proportional estimates/.test(page) ? "PASS" : "FAIL",
    (page.match(/Forced aligner:[^.]*\./) || [""])[0]);
  rec("V-B-license-visible", /commercial not allowed/.test(os[0] || "") ? "PASS" : "FAIL", "commercial/training/YouTube terms displayed as not allowed/unverified");
  await b.shot(OUT + "voice-models.png");

  // V-C generation panel
  await b.click('[data-panel="generation"]');
  await b.waitFor(`!!document.querySelector('#vg-form')`, 8000, "generation form");
  const gen = await b.eval(`(()=>{const f=document.querySelector('#vg-form').elements;
     return {voices:[...f.voice_id.options].map(o=>o.value+(o.disabled?':disabled':'')),langs:[...f.language.options].map(o=>o.value),
       note:document.querySelector('#vg-note').textContent,controls:document.querySelector('#vg-controls').textContent,goDisabled:document.querySelector('#vg-go').disabled}})()`);
  rec("V-C-tamil-not-offered", !gen.langs.some((l) => /^ta/.test(l)) ? "PASS" : "FAIL", JSON.stringify(gen.langs));
  rec("V-C-narrator-option-disabled", gen.voices.some((v) => v.startsWith("DEFENCE_ERA_FEMALE_001") && v.endsWith(":disabled")) ? "PASS" : "FAIL", JSON.stringify(gen.voices));
  rec("V-C-ignored-controls-disclosed", /Ignored:[^.]*pitch_variation/.test(gen.controls) ? "PASS" : "FAIL", gen.controls);

  b.resetLog();
  await b.setValue('#vg-form [name=script]', "Breaking news tonight. The ministry confirmed a new test. More details will follow.");
  await b.eval(`(()=>{const f=document.querySelector('#vg-form');f.requestSubmit();f.requestSubmit();})()`);   // rapid double submit
  await b.waitFor(`document.querySelector('#vg-status audio')`, 90000, "generated audio");
  await sleep(500);
  const g = await b.eval(`({audio:!!document.querySelector('#vg-status audio'),src:document.querySelector('#vg-status audio')?.src?.slice(0,5),
      text:document.querySelector('#vg-status').innerText.replace(/\\s+/g,' ').slice(0,420),
      dur:(()=>{const a=document.querySelector('#vg-status audio');return a?a.duration:null})()})`);
  const genPosts = b.log.http.filter((h) => h.req && h.method === "POST" && /\/api\/voice\/generations$/.test(h.url)).length;
  rec("V-C-generate-playable", g.audio && g.src === "blob:" ? "PASS" : "FAIL", g.text.slice(0, 240));
  rec("V-C-alignment-labelled", /estimated \(proportional\)/.test(g.text) || /Alignment/.test(g.text) ? "PASS" : "FAIL", g.text.match(/Alignment[^]*/)?.[0]?.slice(0, 160) || g.text.slice(-160));
  rec("V-C-double-submit-duplicates", genPosts === 1 ? "PASS" : "FAIL", `POST /voice/generations count after two rapid submits = ${genPosts}`);
  await b.shot(OUT + "voice-generated.png");
  const external = b.log.http.filter((h) => !h.url.startsWith(BASE) && !/^(blob|data):/.test(h.url));
  rec("V-D-no-external-requests", external.length === 0 ? "PASS" : "FAIL", JSON.stringify(external.slice(0, 3)));

  // V-C analyzer: validation then real upload
  await b.click('[data-panel="analyzer"]');
  await b.waitFor(`!!document.querySelector('#va-form')`, 5000);
  const invalid = await b.eval(`(()=>{const f=document.querySelector('#va-form');return f.checkValidity()})()`);
  rec("V-C-analyzer-required-fields", invalid === false ? "PASS" : "FAIL", "empty form is invalid (file/profile name/authorization required)");
  b.resetLog();
  await b.setFile('#va-form input[type=file]', ref);
  await b.setValue('#va-form [name=profile_name]', "F11 own-voice reference");
  await b.setValue('#va-form [name=authorization_ref]', "locally synthesized by OS voice; owned");
  await b.eval(`document.querySelector('#va-form [name=language]').value='en-US'`);
  await b.eval(`(()=>{const f=document.querySelector('#va-form');f.requestSubmit();f.requestSubmit();})()`);
  await b.waitFor(`/COMPLETED|FAILED/.test(document.querySelector('#va-status')?.innerText||'')`, 90000, "analysis job");
  await sleep(1500);
  const an = await b.eval(`document.querySelector('#va-status').innerText.replace(/\\s+/g,' ')`);
  const anPosts = b.log.http.filter((h) => h.req && h.method === "POST" && /\/api\/voice\/analysis$/.test(h.url)).length;
  rec("V-C-analysis-completes", /COMPLETED/.test(an) && /read-only/.test(an) ? "PASS" : "FAIL", an.slice(0, 260));
  rec("V-C-analysis-inference-labelled", /heuristic inference/i.test(an) ? "PASS" : "FAIL", "emotion shown as heuristic inference");
  rec("V-C-analysis-double-submit-duplicates", anPosts === 1 ? "PASS" : "FAIL", `POST /voice/analysis count after two rapid submits = ${anPosts}`);
  await b.shot(OUT + "voice-analysis.png");

  // refresh persistence: history reads persisted server state
  await b.send("Page.reload"); await sleep(1500);
  await b.waitFor(`!!document.querySelector('[data-panel="history"]')`, 8000, "voice panel after reload");
  await b.click('[data-panel="history"]');
  await b.waitFor(`document.querySelectorAll('.voice-job').length>0`, 8000, "history rows");
  const hist = await b.eval(`[...document.querySelectorAll('.voice-job')].map(e=>e.innerText.replace(/\\s+/g,' ').slice(0,140))`);
  rec("V-C-history-persisted-after-refresh", hist.some((h) => /COMPLETED/.test(h)) ? "PASS" : "FAIL", `${hist.length} job(s): ` + hist.slice(0, 2).join(" | "));
  await b.click('.voice-job button[data-act="open"]');
  await sleep(2500);
  const opened = await b.eval(`document.querySelector('.detail')?.innerText.replace(/\\s+/g,' ').slice(0,160)||''`);
  rec("V-C-open-result-from-history", opened.length > 20 ? "PASS" : "FAIL", opened);
  rec("V-final-console", !b.log.exceptions.length ? "PASS" : "FAIL", JSON.stringify({ e: b.log.exceptions, c: b.log.console.slice(0, 3),
    http4xx: b.log.http.filter((h) => !h.req && h.status >= 400).map((h) => `${h.status} ${h.url.replace(BASE, "")}`) }).slice(0, 400));
} catch (e) {
  rec("V-RUN", "BLOCKED", String(e.message).slice(0, 300));
} finally {
  b.close();
  writeFileSync(OUT + "voice-results.json", JSON.stringify(results, null, 2));
}
