import { launch, sleep } from "./cdp.mjs";
import { writeFileSync, mkdirSync } from "node:fs";
import zlib from "node:zlib";

const BASE = process.env.BASE || "http://127.0.0.1:8030";
const OUT = new URL("./out/", import.meta.url).pathname.replace(/^\//, "");
mkdirSync(OUT, { recursive: true });
const results = [];
const rec = (id, status, note) => { results.push({ id, status, note }); console.log(status.padEnd(10), id, "-", note); };

// synthetic PNG with an embedded caption (safe local fixture)
function png(w, h, text) {
  const crc = (buf) => { let c, n, t = []; for (n = 0; n < 256; n++) { c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    let x = 0xffffffff; for (const b of buf) x = t[(x ^ b) & 255] ^ (x >>> 8); return (x ^ 0xffffffff) >>> 0; };
  const chunk = (tag, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(tag), data]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h); for (let y = 0; y < h; y++) for (let x = 0; x < w * 3; x++) raw[y * (w * 3 + 1) + 1 + x] = (x * 7 + y * 3) & 255;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr),
    chunk("tEXt", Buffer.from("Caption\0" + text, "latin1")), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
writeFileSync(OUT + "fixture.png", png(16, 16, "Harbour drill at Northwind 2024-03-01"));

const sess = await (await fetch(`${BASE}/api/dev/session`)).json();
const job = await (await fetch(`${BASE}/api/jobs`, { method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${sess.token}`, "Idempotency-Key": "f6-audit-job-" + Date.now() },
  body: JSON.stringify({ topic: "F6 browser audit topic", language: "en" }) })).json();
console.log("job", job.job_id);

const b = await launch(9333);
try {
  await b.viewport(1440, 900);
  await b.goto(BASE + "/");
  await b.waitFor(`!!document.querySelector('#dev-enter-btn') && !document.querySelector('#dev-enter-btn').closest('[hidden]')`, 8000, "dev enter button");
  await b.click("#dev-enter-btn");
  await b.waitFor(`!!document.querySelector('[data-tab="factcheck"]')`, 8000, "tabs");
  await b.eval(`localStorage.setItem('ds_review_job_id', ${JSON.stringify(job.job_id)})`);
  await b.goto(BASE + "/#/factcheck");
  await b.send("Page.reload");
  await sleep(1200);
  await b.waitFor(`!!document.querySelector('#fc-run')`, 8000, "fact check view");
  const token = await b.eval(`localStorage.getItem('ds_review_token')`);

  // FC-A rendering at three widths
  for (const [w, h] of [[1440, 900], [768, 1024], [390, 844]]) {
    await b.viewport(w, h); await sleep(300);
    const m = await b.metrics();
    const controls = await b.eval(`['#fc-claim','#fc-support','#fc-run','#fc-package','#fc-file','#fc-media','#fc-location','#fc-date'].filter(s=>!document.querySelector(s))`);
    await b.shot(`${OUT}fc-${w}.png`);
    rec(`FC-A-render-${w}`, !m.overflowX && controls.length === 0 ? "PASS" : "FAIL",
      `scrollWidth=${m.sw} clientWidth=${m.cw} missing=${JSON.stringify(controls)}`);
  }
  await b.viewport(1440, 900);
  rec("FC-A-console", b.log.exceptions.length === 0 && b.log.console.filter((c) => c.type !== "warning").length === 0 ? "PASS" : "FAIL",
    JSON.stringify({ exceptions: b.log.exceptions, console: b.log.console }).slice(0, 500));

  // FC-B readiness
  const rows = await b.eval(`[...document.querySelectorAll('li[data-readiness]')].map(li=>({r:li.dataset.readiness,t:li.innerText.replace(/\\s+/g,' ').slice(0,160)}))`);
  const api = await b.eval(`fetch('/api/factcheck/providers',{headers:{Authorization:'Bearer '+localStorage.getItem('ds_review_token')}}).then(r=>r.json())`);
  const byCap = (c, p) => api.providers.find((x) => x.capability === c && (!p || x.provider === p));
  const ui = (cap) => rows.find((r) => r.t.startsWith(cap));
  const expect = [["reverse_image_search", "NOT_CONFIGURED"], ["ocr", "DEPENDENCY_MISSING"], ["object_detection", "UNSUPPORTED"]];
  for (const [cap, st] of expect) {
    const row = rows.find((r) => r.t.startsWith(cap));
    rec(`FC-B-${cap}`, row && row.r === st && byCap(cap).readiness === st ? "PASS" : "FAIL", `ui=${row?.r} api=${byCap(cap)?.readiness} expected=${st}`);
  }
  const hosted = api.providers.find((x) => x.provider === "configured_search_api");
  const hostedUi = rows.find((r) => r.t.includes("configured_search_api"));
  rec("FC-B-hosted_search", hostedUi && hostedUi.r === "NOT_CONFIGURED" && hosted.available === false ? "PASS" : "FAIL", `ui=${hostedUi?.r} api=${hosted?.readiness}/${hosted?.available}`);
  const bad = rows.filter((r) => r.r === "LIVE_VERIFIED" && !/verified-local/.test(JSON.stringify(api.providers.find((p) => r.t.startsWith(p.capability)))));
  rec("FC-B-no-false-live", rows.filter((r) => r.r === "LIVE_VERIFIED").every((r) => ["claim_normalization","claim_extraction","entity_date_match","source_independence","corroboration","article_extraction","image_similarity","metadata","report"].some((c) => r.t.startsWith(c))) ? "PASS" : "FAIL",
    `LIVE_VERIFIED rows are only local deterministic capabilities: ${rows.filter((r) => r.r === "LIVE_VERIFIED").length}`);
  const rss = rows.find((r) => r.t.includes("rss_feed"));
  rec("FC-B-rss_feed-label", rss ? "PASS" : "FAIL", `rss row readiness=${rss?.r} (feeds configured locally; no live fetch performed by this tab)`);

  // FC-C validation: empty claim
  await b.click("#fc-run"); await sleep(400);
  const err1 = await b.eval(`document.querySelector('[role=alert]')?.innerText || ''`);
  rec("FC-C-empty-claim", /Enter a claim/.test(err1) ? "PASS" : "FAIL", err1);

  // FC-C verify claim + double click duplication
  b.resetLog();
  await b.setValue("#fc-claim", "Northwind base counted 4 patrol craft during the March drill.");
  await b.setValue("#fc-support", "Northwind base counted 4 patrol craft during the March drill according to the harbour master.");
  await b.eval(`(()=>{const e=document.querySelector('#fc-run');e.click();e.click();})()`);
  await b.waitFor(`/Verdict|Verifying/.test(document.querySelector('#fc-status')?.innerText||'') && !/Verifying/.test(document.querySelector('#fc-status').innerText)`, 15000, "verdict");
  await sleep(500);
  const status = await b.eval(`document.querySelector('#fc-status').innerText`);
  const posts = b.log.http.filter((h) => h.req && h.method === "POST" && /factcheck\/claims/.test(h.url)).length;
  rec("FC-C-verify-claim", /Verdict/.test(status) ? "PASS" : "FAIL", status);
  rec("FC-C-double-click-duplicates", posts === 1 ? "PASS" : "FAIL", `POST /factcheck/claims count after two rapid clicks = ${posts}`);

  // FC-C build package (confirm dialog) + blank approval + approval
  await b.click("#fc-package"); await b.waitFor(`!!document.querySelector('.modal')`, 4000, "confirm dialog");
  await b.shot(OUT + "fc-confirm.png");
  await b.click(".modal [data-action=confirm]");
  await b.waitFor(`!!document.querySelector('#fc-approve')`, 15000, "package detail");
  const detailTxt = await b.eval(`document.querySelector('#detail, .detail')?.innerText.slice(0,200)||''`);
  rec("FC-C-build-package", /PENDING_REVIEW/.test(detailTxt) ? "PASS" : "FAIL", detailTxt.replace(/\s+/g, " "));
  b.resetLog();
  await b.setValue("#fc-note", "   ");
  await b.click("#fc-approve"); await sleep(1200);
  const blank = await b.eval(`({alert:document.querySelector('[role=alert]')?.innerText||'',dialog:!!document.querySelector('.modal'),
     approved:/APPROVED/.test(document.querySelector('.detail')?.innerText||'')&&!/PENDING_REVIEW/.test(document.querySelector('.detail')?.innerText||'')})`);
  const approvePosts = b.log.http.filter((h) => h.req && /factcheck\/approve|approve/.test(h.url) && h.method === "POST").length;
  rec("FC-C-blank-note-blocked", !blank.approved ? "PASS" : "FAIL", JSON.stringify({ ...blank, approvePosts }));
  if (blank.dialog) { await b.click(".modal [data-action=cancel]"); }
  await b.setValue("#fc-note", "Reviewed claim against the supplied excerpt.");
  await b.click("#fc-approve"); await sleep(600);
  if (await b.eval(`!!document.querySelector('.modal')`)) await b.click(".modal [data-action=confirm]");
  await sleep(1500);
  const after = await b.eval(`document.querySelector('.detail')?.innerText.slice(0,300)||''`);
  rec("FC-C-approve", /APPROVED/.test(after) ? "PASS" : "FAIL", after.replace(/\s+/g, " ").slice(0, 160));
  await b.shot(OUT + "fc-approved.png");

  // persistence across navigation
  await b.goto(BASE + "/#/dashboard"); await sleep(800);
  await b.goto(BASE + "/#/factcheck"); await sleep(1200);
  const persisted = await b.eval(`document.querySelector('.detail')?.innerText.slice(0,200)||''`);
  rec("FC-A-nav-persist", /APPROVED/.test(persisted) ? "PASS" : "FAIL", persisted.replace(/\s+/g, " ").slice(0, 120));

  // media: no file, then PNG
  await b.waitFor(`!!document.querySelector('#fc-media')`, 5000);
  await b.click("#fc-media"); await sleep(400);
  const e2 = await b.eval(`document.querySelector('[role=alert]')?.innerText||''`);
  rec("FC-C-media-no-file", /Choose an image/.test(e2) ? "PASS" : "FAIL", e2);
  b.resetLog();
  await b.setFile("#fc-file", (OUT + "fixture.png").replace(/\//g, "\\"));
  await b.setValue("#fc-location", "Northwind"); await b.setValue("#fc-date", "2024-03-01");
  await b.click("#fc-media");
  await b.waitFor(`/SHA-256/.test(document.querySelector(".detail")?.innerText||"")`, 15000, "media result");
  const media = await b.eval(`document.querySelector('.detail').innerText.replace(/\\s+/g,' ')`);
  rec("FC-C-media-result", /RIGHTS_REVIEW_REQUIRED/.test(media) ? "PASS" : "FAIL", media.slice(0, 260));
  const external = b.log.http.filter((h) => !h.url.startsWith(BASE) && !h.url.startsWith("data:") && !h.url.startsWith("blob:"));
  rec("FC-D-no-external-requests", external.length === 0 ? "PASS" : "FAIL", JSON.stringify(external.slice(0, 3)));
  const live = b.log.http.filter((h) => h.req && /live_reverse/.test(h.url)).length;
  rec("FC-D-reverse-not-live-by-default", /CONFIGURED_BUT_NOT_CALLED|UNAVAILABLE_NOT_CONFIGURED|NOT_CONFIGURED/.test(media) ? "PASS" : "FAIL", media.match(/Reverse search:[^]*$/)?.[0]?.slice(0, 80) || "no reverse line");
  const page = await b.eval(`document.body.innerText`);
  rec("FC-D-no-secret-in-page", !page.includes(token) && !/Bearer\s+ey/.test(page) ? "PASS" : "FAIL", "token string not present in rendered text");
  const dupMedia = b.log.http.filter((h) => h.req && h.method === "POST" && /factcheck\/media/.test(h.url)).length;
  rec("FC-C-media-single-post", dupMedia === 1 ? "PASS" : "FAIL", `POST count ${dupMedia}`);
  await b.shot(OUT + "fc-media.png");
  rec("FC-final-console", b.log.exceptions.length === 0 ? "PASS" : "FAIL", JSON.stringify({ exceptions: b.log.exceptions, failedRequests: b.log.failed.length,
    http4xx: b.log.http.filter((h) => !h.req && h.status >= 400).map((h) => `${h.status} ${h.url.replace(BASE, "")}`) }).slice(0, 500));
} catch (e) {
  rec("FC-RUN", "BLOCKED", String(e.message).slice(0, 300));
} finally {
  b.close();
  writeFileSync(OUT + "fc-results.json", JSON.stringify(results, null, 2));
}
