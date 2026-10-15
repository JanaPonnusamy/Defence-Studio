// Browser acceptance for the Inspector tab (pipeline trace + request/response inspector).
// Run against a DISPOSABLE seeded db (see README). Uses mocked-provider records only; no live OpenAI call.
import { launch, sleep } from "./cdp.mjs";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const BASE = process.env.BASE || "http://127.0.0.1:8030";
const JOB = process.env.JOB;
const SECRET = process.env.SECRET_MARK || "sk-browser-ACCEPT-SECRET-0000";
const OUT = fileURLToPath(new URL("./out/", import.meta.url));
mkdirSync(OUT, { recursive: true });
if (!JOB) throw new Error("JOB env var required");
const results = [];
const rec = (id, ok, note) => { results.push({ id, status: ok ? "PASS" : "FAIL", note }); console.log((ok ? "PASS" : "FAIL").padEnd(5), id, "-", String(note).slice(0, 220)); };

const sess = await (await fetch(`${BASE}/api/dev/session`)).json();
const H = { Authorization: `Bearer ${sess.token}` };
// A second job with no analysis/inspections (missing-state coverage).
const empty = await (await fetch(`${BASE}/api/jobs`, { method: "POST", headers: { ...H, "Content-Type": "application/json",
  "Idempotency-Key": "insp-empty-" + Date.now() }, body: JSON.stringify({ topic: "Empty job for missing states", language: "en" }) })).json();

const b = await launch(9344);
const text = () => b.eval("document.body.innerText");
async function openInspector(jobId) {
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(jobId)})`);
  await b.goto(`${BASE}/#inspector`);
  await b.eval("location.reload()"); await sleep(1200);
  await b.waitFor(`!!document.querySelector('[data-tab="inspector"]')`, 8000, "inspector tab");
  await b.click('[data-tab="inspector"]');
  await sleep(1200);
}
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn')`, 8000, "dev enter");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="inspector"]')`, 8000, "tabs");
  await openInspector(JOB);
  await b.waitFor(`document.body.innerText.includes('Research pipeline trace')`, 8000, "trace");

  // --- pipeline trace ---
  const t = await text();
  rec("trace-raw-and-clean", /2\. Raw extracted content/.test(t) && /3\. Clean normalized content/.test(t), "raw+clean sections present");
  rec("trace-structured", /4\. Structured representation/.test(t) && /canonical_url/.test(t), "structured block");
  rec("trace-claims-passages", /5\. Claims and supporting passages/.test(t) && /matches normalized text: true/.test(t), "claims with passage check");
  rec("trace-hashes", /analysis sha256/.test(t) && /raw hash verified\s*true/.test(t.replace(/\n/g, " ")), "hashes shown + verified");
  const api = await (await fetch(`${BASE}/api/jobs/${JOB}/research/pipeline-trace`, { headers: H })).json();
  rec("trace-matches-api", api.sources.length > 0 && t.includes(api.sources[0].clean.sha256), `sources=${api.sources.length}`);
  await b.shot(OUT + "inspector-trace-1440.png");

  // --- inspector: each recorded outcome ---
  const recs = (await (await fetch(`${BASE}/api/jobs/${JOB}/script/inspections/ta`, { headers: H })).json()).records;
  const byOutcome = Object.fromEntries(recs.map((r) => [r.outcome, r.record_id]));
  rec("records-listed", recs.length === 4 && ["AWAITING_REVIEW", "VALIDATION_FAILED", "MALFORMED_JSON", "BLOCKED_BUDGET"].every((o) => byOutcome[o]),
    JSON.stringify(recs.map((r) => r.outcome)));
  const open = async (outcome) => {
    await b.click(`[data-record="${byOutcome[outcome]}"]`);
    await b.waitFor(`document.body.innerText.includes('Generation attempt ${byOutcome[outcome]}')`, 8000, outcome);
    await sleep(300);
    return text();
  };

  let d = await open("AWAITING_REVIEW");
  rec("valid-request-shown", /Exact serialized request body/i.test(d) && /chat\/completions/i.test(d) && /"messages"/i.test(d) && /Wire messages match client input: true/i.test(d), "wire body + endpoint");
  rec("valid-instructions-vs-material", /Instructions - system/i.test(d) && /Normalized source material/i.test(d) && /Evidence references/i.test(d), "sections labelled");
  rec("valid-raw-response", /A\. Raw response/i.test(d) && /req_MOCK/i.test(d) && /input tokens\s*410/i.test(d.replace(/\n/g, " ")), "raw+usage");
  rec("valid-parsed-clean-validation", /B\. Parsed response/i.test(d) && /C\. Clean output - ACCEPTED_FOR_REVIEW/i.test(d) && /D\. Validation report/i.test(d) && /passed\s*true/i.test(d.replace(/\n/g, " ")), "views");
  rec("valid-accepted-label", /Accepted for human review/i.test(d), "label");
  rec("secret-not-in-page", !d.includes(SECRET) && !/Bearer\s+[A-Za-z0-9]/i.test(d) && !/[A-Z]:\\Users/i.test(d), "no key / bearer / local path");
  await b.shot(OUT + "inspector-valid-1440.png");

  // copy-request behaviour (stub clipboard, capture what is written)
  await b.eval(`window.__copied=null; Object.defineProperty(navigator,'clipboard',{value:{writeText:(v)=>{window.__copied=v;return Promise.resolve()}},configurable:true})`);
  await b.click('[data-copy="request"]'); await sleep(200);
  const copied = await b.eval("window.__copied");
  rec("copy-request", !!copied && copied.includes('"messages"') && !copied.includes(SECRET), `copied ${copied ? copied.length : 0} chars, no key`);

  d = await open("VALIDATION_FAILED");
  rec("invalid-never-success", /Not an accepted script/i.test(d) && /REJECTED_NOT_A_SCRIPT/i.test(d) && !/Accepted for human review/i.test(d), "rejected label");
  rec("invalid-ref-flagged", /UNRESOLVED/i.test(d) && /NOT in eligible set/i.test(d) && /Validation report|D\. Validation report/i.test(d), "unresolved evidence ref");
  await b.shot(OUT + "inspector-invalid-1440.png");

  d = await open("MALFORMED_JSON");
  rec("malformed-state", /Not an accepted script/i.test(d) && /this is not json/i.test(d) && /No parseable output/i.test(d), "raw kept, no clean output");

  d = await open("BLOCKED_BUDGET");
  rec("budget-zero-calls", /provider calls\s*0/i.test(d.replace(/\n/g, " ")) && /provider was not called/i.test(d), "zero calls, no response");

  // --- missing / empty states ---
  await openInspector(empty.job_id);
  const e = await text();
  rec("missing-trace-state", /No analysis_result artifact|No pipeline trace/.test(e), "honest unavailable message");
  rec("missing-inspection-state", /No generation attempts recorded/.test(e), "empty attempts list");

  // --- owner-only access / auth ---
  const other = await fetch(`${BASE}/api/jobs/${JOB}/script/inspections/ta`, { headers: { Authorization: "Bearer bogus" } });
  rec("auth-required", other.status === 401, `status ${other.status}`);
  const rid = byOutcome["AWAITING_REVIEW"];
  const raw = await (await fetch(`${BASE}/api/jobs/${JOB}/script/inspections/ta/${rid}`, { headers: H })).text();
  rec("api-no-secret", !raw.includes(SECRET) && !/Bearer\s+\S{8,}/.test(raw) && !/"Authorization"/i.test(raw), "stored record clean");

  // --- viewports, overflow, console ---
  await openInspector(JOB);
  await b.waitFor(`document.body.innerText.includes('Research pipeline trace')`, 8000, "trace again");
  for (const [w, h] of [[1440, 900], [768, 1024], [390, 844]]) {
    await b.viewport(w, h); await sleep(300);
    await b.click(`[data-record="${byOutcome["AWAITING_REVIEW"]}"]`); await sleep(500);
    const m = await b.metrics();
    const wide = await b.eval(`[...document.querySelectorAll('.inspect-pre,.inspect-dl')].some(e=>e.scrollWidth>e.clientWidth+1 && getComputedStyle(e).overflowX==='visible')`);
    await b.shot(`${OUT}inspector-${w}.png`);
    rec(`viewport-${w}`, !m.overflowX && !wide, `scrollWidth=${m.sw} clientWidth=${m.cw}`);
  }
  const bad = b.log.console.filter((c) => c.type !== "warning" && !/\b(401|404|409)\b/.test(c.text));
  rec("console-clean", b.log.exceptions.length === 0 && bad.length === 0, JSON.stringify({ ex: b.log.exceptions, c: bad }).slice(0, 300));
} catch (err) {
  rec("harness", false, err.message);
} finally {
  b.close();
  writeFileSync(OUT + "inspector-results.json", JSON.stringify(results, null, 2));
  const f = results.filter((r) => r.status === "FAIL").length;
  console.log(`\n${results.length - f}/${results.length} passed`);
  process.exit(f ? 1 : 0);
}
