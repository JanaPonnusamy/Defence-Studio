/* Visual Composition Studio UI -- plain JS, talks only to this module's own API. */
"use strict";
const state = { project: null, plans: [], plan: null, shots: [], providers: [], blobUrls: [] };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtT = (s) => (s == null ? "" : Number(s).toFixed(2));
const ls = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
             set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } } };

function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 6000);
}
function headers(extra = {}) { const h = { ...extra }; const k = ls.get("vvm_api_key"); if (k) h["X-API-Key"] = k; return h; }
async function api(path, opts = {}) {
  const o = { ...opts, headers: headers(opts.headers || {}) };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; delete o.json; }
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) { const m = body && body.error_code ? `${body.error_code}: ${body.error_message}` : `HTTP ${r.status}`; const e = new Error(m); e.body = body; throw e; }
  return body;
}
async function blobUrl(path) {
  const r = await fetch(path, { headers: headers() });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const u = URL.createObjectURL(await r.blob()); state.blobUrls.push(u);
  if (state.blobUrls.length > 300) URL.revokeObjectURL(state.blobUrls.shift());
  return u;
}
function setImg(img, path) { img.removeAttribute("src"); if (!path) return; blobUrl(path).then((u) => { img.src = u; }).catch(() => { img.alt = "preview unavailable"; }); }
function needProject() { if (!state.project) { toast("Select or create a project first.", true); return false; } return true; }
const badge = (v, cls) => `<span class="badge ${esc(cls || v)}">${esc(v)}</span>`;
async function guarded(fn) { try { await fn(); } catch (e) { toast(e.message, true); } }
const loadingRow = (cols) => `<tr><td colspan="${cols}" class="muted">Loading…</td></tr>`;
const money = (v) => (v == null ? "—" : "$" + v);
/* Phase 2: honest classification badges. Never show a placeholder or fixture as AI. */
function kindBadges(a) {
  const out = [];
  const pc = a.provenance_class || "";
  if (pc === "PLACEHOLDER") out.push(badge("PLACEHOLDER · not AI", "PLACEHOLDER"));
  else if (pc === "TEST_FIXTURE" || a.is_mock) out.push(badge("MOCK / fixture", "MOCK"));
  else if (pc === "AI_GENERATED") out.push(badge("REAL AI · not evidence", "AI_GENERATED"));
  if (a.is_fallback) out.push(badge(`FALLBACK for ${a.fallback_from || "?"}`, "FALLBACK"));
  if (a.duplicate_of) out.push(badge("near-duplicate", "UNKNOWN"));
  return out.join(" ");
}
const previewable = (a) => a.file_url && /^(image\/|video\/|application\/pdf)/.test(a.mime_type || "");
const previewPath = (a) => ((a.mime_type || "").startsWith("image/") ? a.file_url : `/assets/${a.asset_id}/preview?max_side=480`);

/* ---------------- tabs ---------------- */
$$(".tab").forEach((b) => b.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + b.dataset.tab));
  ls.set("vvm_tab", b.dataset.tab);
  onTab(b.dataset.tab);
}));
function onTab(t) {
  if (t === "dashboard") { refreshJobs(); refreshProjectSummary(); }
  if (t === "assets") refreshAssets();
  if (t === "planner") { refreshScenes(); refreshPlans(); }
  if (t === "preview") { if (!state.shots.length && state.project) refreshPlans(); else fillPreviewShots(); }
  if (t === "costs") refreshCosts();
  if (t === "diagnostics") { refreshDiag(); refreshLogs(); }
}

/* ---------------- health / projects ---------------- */
async function checkHealth() {
  const el = $("#apiStatus");
  try { const h = await api("/health"); el.textContent = `API ${h.status} · v${h.version}`; el.className = "api-status ok"; $("#healthBox").textContent = JSON.stringify(h, null, 2); }
  catch (e) { el.textContent = "API unreachable"; el.className = "api-status bad"; $("#healthBox").textContent = e.message; }
}
async function loadProjects(selectId) {
  const list = await api("/projects");
  const sel = $("#projectSelect");
  sel.innerHTML = `<option value="">— select —</option>` + list.map((p) => `<option value="${esc(p.project_id)}">${esc(p.name)}</option>`).join("");
  const want = selectId || ls.get("vvm_project");
  if (want && list.some((p) => p.project_id === want)) { sel.value = want; await selectProject(want); }
}
async function selectProject(id) {
  state.project = id || null; ls.set("vvm_project", id || "");
  state.plan = null; state.shots = [];
  refreshProjectSummary();
  const active = $(".tab.active"); if (active) onTab(active.dataset.tab);
}
$("#projectSelect").addEventListener("change", (e) => guarded(() => selectProject(e.target.value)));
$("#newProjectForm").addEventListener("submit", (e) => { e.preventDefault(); guarded(async () => {
  const f = new FormData(e.target); const body = { name: f.get("name") };
  if (f.get("ai_image_budget_usd")) body.ai_image_budget_usd = Number(f.get("ai_image_budget_usd"));
  const p = await api("/projects", { method: "POST", json: body }); e.target.reset();
  toast(`Project created: ${p.name}`); await loadProjects(p.project_id);
}); });
async function refreshProjectSummary() {
  const el = $("#projectSummary");
  if (!state.project) { el.textContent = "Select or create a project."; return; }
  await guarded(async () => {
    const p = await api(`/projects/${state.project}`);
    el.innerHTML = `<b>${esc(p.name)}</b><br>id <code>${esc(p.project_id)}</code><br>AI budget: $${esc(p.ai_image_budget_usd)}<br>` +
      `assets ${p.counts.assets} · scenes ${p.counts.scenes} · plans ${p.counts.visual_plans} · jobs ${p.counts.jobs}`;
  });
}

/* ---------------- jobs ---------------- */
async function refreshJobs() {
  const all = $("#jobsAllProjects").checked;
  if (!all && !state.project) { $("#jobsTable tbody").innerHTML = `<tr><td colspan="9" class="muted">Select a project (or tick "all projects").</td></tr>`; return; }
  if (!$("#jobsTable tbody").children.length) $("#jobsTable tbody").innerHTML = loadingRow(9);
  await guarded(async () => {
    const jobs = await api(all ? "/jobs?limit=100" : `/jobs?project_id=${state.project}&limit=100`);
    $("#jobsTable tbody").innerHTML = jobs.map((j) => `<tr>
      <td><code>${esc(j.job_id.slice(0, 8))}</code></td><td>${esc(j.job_type)}</td><td>${badge(j.status)}</td>
      <td>${Math.round(j.progress * 100)}%</td><td>${esc(j.stage)}</td><td>${esc((j.created_at || "").replace("T", " ").slice(0, 19))}</td>
      <td>${j.error_code ? esc(j.error_code + ": " + (j.error_message || "")) : ""}</td><td><code>${esc((j.result_id || "").slice(0, 8))}</code></td>
      <td>${["QUEUED", "RUNNING"].includes(j.status) ? `<button class="small danger" data-cancel="${esc(j.job_id)}">Cancel</button>` : ""}
          ${["FAILED", "CANCELLED"].includes(j.status) ? `<button class="small" data-retry="${esc(j.job_id)}">Retry</button>` : ""}</td></tr>`).join("") ||
      `<tr><td colspan="9" class="muted">No jobs yet.</td></tr>`;
  });
}
$("#jobsTable").addEventListener("click", (e) => {
  const c = e.target.dataset.cancel, r = e.target.dataset.retry;
  if (c) guarded(async () => { await api(`/jobs/${c}/cancel`, { method: "POST" }); toast("Cancel requested"); refreshJobs(); });
  if (r) guarded(async () => { const j = await api(`/jobs/${r}/retry`, { method: "POST" }); toast(`Retry queued: ${j.job_id.slice(0, 8)}`); refreshJobs(); });
});
$("#refreshJobs").addEventListener("click", refreshJobs);
$("#jobsAllProjects").addEventListener("change", refreshJobs);
setInterval(() => { if ($("#jobsAutoRefresh").checked && $("#panel-dashboard").classList.contains("active")) refreshJobs(); }, 4000);

async function pollJob(jobId, onUpdate) {
  for (let i = 0; i < 600; i++) {
    const j = await api(`/jobs/${jobId}`); onUpdate && onUpdate(j);
    if (["SUCCEEDED", "FAILED", "CANCELLED"].includes(j.status)) return j;
    await new Promise((r) => setTimeout(r, 700));
  }
  throw new Error("Timed out waiting for job");
}

/* ---------------- assets ---------------- */
["IMAGE", "VIDEO_CLIP", "MAP", "DIAGRAM", "CHART", "STAT_CARD", "DOCUMENT", "SATELLITE", "SOURCE_PHOTO", "AI_IMAGE", "PLACEHOLDER_IMAGE", "TITLE_CARD", "TRANSITION"]
  .forEach((t) => $("#fltType").insertAdjacentHTML("beforeend", `<option>${t}</option>`));
async function refreshAssets() {
  const tb = $("#assetsTable tbody");
  if (!state.project) { tb.innerHTML = `<tr><td colspan="9" class="muted">Select a project.</td></tr>`; return; }
  tb.innerHTML = loadingRow(9);
  await guarded(async () => {
    const q = new URLSearchParams({ project_id: state.project });
    if ($("#fltType").value) q.set("asset_type", $("#fltType").value);
    if ($("#fltSource").value) q.set("source_type", $("#fltSource").value);
    if ($("#fltRights").value) q.set("rights_status", $("#fltRights").value);
    if ($("#fltKind").value) q.set("generation_kind", $("#fltKind").value);
    const rows = await api(`/assets?${q}`);
    state.assets = rows;
    tb.innerHTML = rows.map((a, i) => `<tr class="clickable" data-i="${i}">
      <td>${previewable(a) ? `<img class="thumb" data-src="${esc(previewPath(a))}" alt="preview">` : `<span class="muted small">${esc(a.mime_type || "no file")}</span>`}</td>
      <td>${esc(a.title)}</td><td>${esc(a.asset_type)}</td><td>${esc(a.source_type)}</td>
      <td>${esc(a.provenance_class || "")} ${kindBadges(a)}</td>
      <td>${badge(a.editorial_category)}</td><td title="${esc(a.licence || "")}">${esc(a.rights_label)}</td>
      <td>${a.width ? `${a.width}×${a.height}` : ""}</td><td>${a.reuse_count}</td></tr>`).join("") || `<tr><td colspan="9" class="muted">No assets in this project yet. Import one above.</td></tr>`;
    $$("img[data-src]", tb).forEach((img) => setImg(img, img.dataset.src));
  });
}
["#fltType", "#fltSource", "#fltRights", "#fltKind"].forEach((s) => $(s).addEventListener("change", refreshAssets));
$("#refreshAssets").addEventListener("click", refreshAssets);
$("#assetsTable").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-i]"); if (tr) showAsset(state.assets[Number(tr.dataset.i)]); });
function showAsset(a) {
  const el = $("#assetDetail");
  el.innerHTML = `${previewable(a) ? `<img class="detail-img" id="detailImg" alt="preview">` : ""}<div data-note class="small muted"></div>
    <b>${esc(a.title)}</b> ${kindBadges(a)} ${badge(a.editorial_category)}<br>
    <span class="small">class <b>${esc(a.provenance_class)}</b> · generation ${esc(a.generation_kind)} · evidence-eligible <b>${a.evidence_eligible ? "yes" : "no"}</b> · rights usable ${a.rights_usable ? "yes" : "no"}</span><br>
    <span class="small">type ${esc(a.asset_type)} · source ${esc(a.source_type)} · ${esc(a.mime_type || "")} ${a.width ? `${a.width}×${a.height}` : ""}${a.duration ? ` · ${fmtT(a.duration)}s` : ""}</span><br>
    <span class="small">rights: <b>${esc(a.rights_label)}</b> · licence: ${esc(a.licence || "—")}${a.licence_url ? ` (${esc(a.licence_url)})` : ""} · usage: ${esc(a.usage_rights || "—")}</span><br>
    <span class="small">attribution${a.attribution_required ? " (REQUIRED)" : ""}: ${esc(a.attribution || "—")} · creator: ${esc(a.creator || "—")}</span><br>
    <span class="small">source: ${esc(a.source_identity || "—")} · URL: ${esc(a.source_url || "—")} · retrieved ${esc(a.source_retrieved_at || "—")} · acquired ${esc(a.acquired_at || "—")}</span><br>
    <span class="small">provider/model: ${esc(a.provider || "—")} / ${esc(a.model || "—")} · provider request: ${esc(a.provider_request_id || "—")} · cost: ${money(a.generation_cost)} (${esc(a.cost_status || "n/a")})</span><br>
    ${a.is_fallback ? `<span class="small">fallback reason: ${esc(a.fallback_reason || "")}</span><br>` : ""}
    <span class="small">sha256: <code>${esc(a.checksum_sha256 || "—")}</code>${a.duplicate_of ? ` · near-duplicate of <code>${esc(a.duplicate_of.slice(0, 8))}</code>` : ""}</span>
    <details><summary class="small">metadata</summary><pre class="pre-small">${esc(JSON.stringify(a.extra_metadata, null, 2))}</pre></details>
    ${(a.mime_type || "").startsWith("image/") ? `<form id="variantForm" class="form"><label class="wide">Create crop variant (normalized x,y,w,h)
      <input name="crop" value='{"x":0.2,"y":0.2,"w":0.6,"h":0.6}'></label><button type="submit">Create variant</button></form>` : ""}`;
  const img = $("#detailImg");
  if (img) {
    img.removeAttribute("src");
    blobUrl(previewPath(a)).then((u) => { img.src = u; })
      .catch(() => { el.querySelector("[data-note]").textContent = "Preview unavailable for this file (decoder missing or file undecodable; see Diagnostics → previews)."; });
  }
  const vf = $("#variantForm");
  if (vf) vf.addEventListener("submit", (e) => { e.preventDefault(); guarded(async () => {
    const v = await api(`/assets/${a.asset_id}/variants`, { method: "POST", json: { crop: JSON.parse(new FormData(vf).get("crop")) } });
    toast(`Variant created: ${v.asset_id.slice(0, 8)}`); refreshAssets(); showAsset(v);
  }); });
}
$("#importForm").addEventListener("submit", (e) => { e.preventDefault(); if (!needProject()) return; guarded(async () => {
  const f = new FormData(e.target); const fd = new FormData();
  fd.set("project_id", state.project);
  for (const k of ["asset_type", "source_type", "rights_status", "title", "licence", "attribution", "creator", "source_url", "duration",
    "source_identity", "licence_url", "usage_rights"]) { const v = f.get(k); if (v) fd.set(k, v); }
  if (f.get("attribution_required")) fd.set("attribution_required", "true");
  if (f.get("source_retrieved_at")) fd.set("source_retrieved_at", new Date(f.get("source_retrieved_at")).toISOString());
  let meta = {}; const raw = (f.get("extra_metadata") || "").trim();
  if (raw) { try { meta = JSON.parse(raw); } catch (err) { throw new Error("Extra metadata is not valid JSON"); } }
  const tags = (f.get("tags") || "").split(",").map((t) => t.trim()).filter(Boolean); if (tags.length) meta.tags = tags;
  if (Object.keys(meta).length) fd.set("extra_metadata", JSON.stringify(meta));
  const file = f.get("file"); if (file && file.name) fd.set("file", file);
  const r = await fetch("/assets/import", { method: "POST", body: fd, headers: headers() });
  const body = await r.json();
  if (!r.ok) throw new Error(`${body.error_code}: ${body.error_message}`);
  toast(body.deduplicated ? "Identical file already imported (deduplicated by checksum)" : `Imported ${body.asset_type}: ${body.title || body.asset_id}`);
  refreshAssets(); showAsset(body);
}); });

/* ---------------- maps & stats ---------------- */
const parseJSON = (s, what, dflt) => { s = (s || "").trim(); if (!s) return dflt; try { return JSON.parse(s); } catch (e) { throw new Error(`${what} is not valid JSON`); } };
const tagList = (s) => (s || "").split(",").map((t) => t.trim()).filter(Boolean);
function showGraphic(a) {
  const bm = (a.extra_metadata || {}).basemap;
  $("#graphicResult").innerHTML = `<img class="detail-img" id="gImg" alt=""><b>${esc(a.title)}</b> ${badge(a.editorial_category)}<br><span class="small">${esc(a.attribution)}</span>` +
    (bm && bm.requested ? `<br><span class="small">basemap: ${bm.used ? "used" : `not used (${esc(bm.reason)}) - schematic fallback`}</span>` : "");
  setImg($("#gImg"), a.file_url);
}
$("#statForm").addEventListener("submit", (e) => { e.preventDefault(); if (!needProject()) return; guarded(async () => {
  const f = new FormData(e.target);
  const a = await api("/assets/stats", { method: "POST", json: { project_id: state.project, tags: tagList(f.get("tags")),
    spec: { graphic_type: f.get("graphic_type"), title: f.get("title"), data: parseJSON(f.get("data"), "Data points", []) } } });
  toast("Statistic graphic rendered"); showGraphic(a);
}); });
$("#mapForm").addEventListener("submit", (e) => { e.preventDefault(); if (!needProject()) return; guarded(async () => {
  const f = new FormData(e.target);
  const spec = { title: f.get("title"), data_source: f.get("data_source"), markers: parseJSON(f.get("markers"), "Markers", []),
    route: parseJSON(f.get("route"), "Route", []), basemap: !!f.get("basemap") };
  const region = parseJSON(f.get("region"), "Region", null); if (region) spec.region = region;
  const a = await api("/assets/maps", { method: "POST", json: { project_id: state.project, spec, tags: tagList(f.get("tags")) } });
  toast("Map rendered"); showGraphic(a);
}); });

/* ---------------- scenes & plans ---------------- */
$("#scenesForm").addEventListener("submit", (e) => { e.preventDefault(); if (!needProject()) return; guarded(async () => {
  const f = new FormData(e.target);
  const body = { project_id: state.project, replace_existing: !!f.get("replace"), words_per_minute: Number(f.get("wpm")) || 150 };
  const segs = (f.get("segments") || "").trim();
  if (segs) body.segments = parseJSON(segs, "Segments", []); else body.narration = f.get("narration");
  const r = await api("/scenes", { method: "POST", json: body });
  toast(`${r.scene_count} scene(s) created`); refreshScenes();
}); });
async function refreshScenes() {
  if (!state.project) { $("#scenesTable tbody").innerHTML = ""; return; }
  await guarded(async () => {
    const rows = await api(`/scenes?project_id=${state.project}`);
    $("#scenesTable tbody").innerHTML = rows.map((s) => `<tr><td>${s.order_index + 1}</td><td>${esc(s.title)} ${s.importance === "HIGH" ? badge("HIGH", "QUEUED") : ""}</td>
      <td>${fmtT(s.start_time)}–${fmtT(s.end_time)}s</td><td>${esc(s.timing_source)}</td>
      <td>${s.visual_intents.map((i) => esc(i.intent)).join(", ")}</td>
      <td class="small">${s.narration_segments.map((g) => `<div>[${fmtT(g.start)}] ${esc(g.text)}</div>`).join("")}</td></tr>`).join("") ||
      `<tr><td colspan="6" class="muted">No scenes yet.</td></tr>`;
  });
}
$("#refreshScenes").addEventListener("click", refreshScenes);
$("#buildForm").addEventListener("submit", (e) => { e.preventDefault(); if (!needProject()) return; guarded(async () => {
  const f = new FormData(e.target);
  const options = { generate_ai: !!f.get("generate_ai"), ai_provider: f.get("ai_provider") || null, max_ai_images: Number(f.get("max_ai_images")),
    allow_unknown_rights: !!f.get("allow_unknown_rights"), allow_placeholder_fallback: !!f.get("allow_placeholder_fallback") };
  if (f.get("max_shot_seconds")) options.max_shot_seconds = Number(f.get("max_shot_seconds"));
  const out = (f.get("output") || "").trim();
  if (out) { const m = out.match(/^(\d+)x(\d+)@(\d+(?:\.\d+)?)$/); if (!m) throw new Error("Output must look like 1920x1080@30");
    options.output_width = Number(m[1]); options.output_height = Number(m[2]); options.frame_rate = Number(m[3]); }
  const st = $("#buildStatus");
  st.innerHTML = `<span class="muted">Submitting…</span>`;
  let job;
  try { job = await api("/visual-plans/build", { method: "POST", json: { project_id: state.project, options } }); }
  catch (err) { st.innerHTML = `${badge("FAILED")} <span class="small">${esc(err.message)}</span>`; throw err; }
  const done = await pollJob(job.job_id, (j) => { st.innerHTML = `job ${esc(j.job_id.slice(0, 8))}: ${badge(j.status)} ${Math.round(j.progress * 100)}% ${esc(j.stage || "")}`; });
  if (done.status === "SUCCEEDED") { toast("Visual plan built"); await refreshPlans(done.result_id); }
  else st.innerHTML += `<br><span class="small">${esc(done.error_code)}: ${esc(done.error_message)}</span>`;
}); });
async function refreshPlans(selectId) {
  if (!state.project) return;
  await guarded(async () => {
    state.plans = await api(`/visual-plans?project_id=${state.project}`);
    $("#planSelect").innerHTML = state.plans.map((p) => `<option value="${esc(p.visual_plan_id)}">${esc((p.created_at || "").slice(0, 19).replace("T", " "))} · ${esc(p.status)} · ${p.stats.shot_count ?? 0} shots</option>`).join("");
    const id = selectId || (state.plan && state.plan.visual_plan_id) || (state.plans[0] && state.plans[0].visual_plan_id);
    if (id) { $("#planSelect").value = id; await loadPlan(id); } else { $("#planSummary").textContent = "No plans yet."; $("#shotsTable tbody").innerHTML = ""; $("#timelineBar").innerHTML = ""; }
  });
}
async function loadPlan(id) {
  const p = await api(`/visual-plans/${id}?include_timeline=false`); state.plan = p;
  $("#timelineLink").href = `/visual-plans/${id}/timeline`;
  const s = p.stats || {};
  $("#planSummary").innerHTML = `${badge(p.status)} ${p.error_code ? esc(p.error_code + ": " + p.error_message) : ""}
    shots <b>${s.shot_count ?? "-"}</b> · visual changes ${s.visual_change_count ?? "-"} · distinct assets ${s.distinct_asset_count ?? "-"} ·
    real AI images ${s.real_ai_asset_count ?? 0} · placeholders ${s.placeholder_asset_count ?? 0}${s.fallback_placeholders_in_plan ? ` (${s.fallback_placeholders_in_plan} fallback)` : ""} ·
    mock ${s.mock_assets_used ?? 0} · gaps ${s.gap_count ?? 0} · duration ${fmtT(s.total_duration_s)}s · timing ${esc((s.timing_sources || []).join("+") || "—")}
    ${(p.warnings || []).map((w) => `<div class="small">⚠ ${esc(w)}</div>`).join("")}`;
  if (p.status !== "BUILT") { state.shots = []; $("#shotsTable tbody").innerHTML = ""; $("#timelineBar").innerHTML = ""; return; }
  const r = await api(`/shots/${id}`); state.shots = r.shots;
  const total = state.shots.length ? state.shots[state.shots.length - 1].end - state.shots[0].start : 1;
  $("#timelineBar").innerHTML = state.shots.map((sh, i) => `<div class="seg ${sh.status === "GAP" ? "GAP" : esc(sh.editorial_label)}" data-i="${i}" style="flex:${sh.duration / total}" title="#${sh.order} ${fmtT(sh.start)}-${fmtT(sh.end)}s ${esc(sh.visual_type)} ${esc(sh.framing || sh.gap_code || "")}"></div>`).join("");
  $("#shotsTable tbody").innerHTML = state.shots.map((sh, i) => `<tr class="clickable" data-i="${i}"><td>${sh.order}</td><td>${fmtT(sh.start)}–${fmtT(sh.end)}</td>
    <td>${esc(sh.visual_type)} ${badge(sh.editorial_label)}</td><td>${badge(sh.status)} ${esc(sh.gap_code || "")}</td><td>${esc(sh.framing || "")}</td>
    <td>${esc(sh.intent)}</td><td class="small">${esc(sh.change_reason)}</td><td class="small">${esc((sh.narration_anchor.text || "").slice(0, 120))}</td></tr>`).join("");
  fillPreviewShots();
}
$("#planSelect").addEventListener("change", (e) => guarded(() => loadPlan(e.target.value)));
$("#refreshPlans").addEventListener("click", () => refreshPlans());
const gotoPreview = (i) => { $("#previewShot").value = String(i); $$(".tab").find((t) => t.dataset.tab === "preview").click(); showShotPreview(i); };
$("#timelineBar").addEventListener("click", (e) => { const d = e.target.closest("[data-i]"); if (d) gotoPreview(Number(d.dataset.i)); });
$("#shotsTable").addEventListener("click", (e) => { const d = e.target.closest("[data-i]"); if (d) gotoPreview(Number(d.dataset.i)); });

/* ---------------- preview ---------------- */
function fillPreviewShots() {
  const sel = $("#previewShot"); const cur = sel.value;
  sel.innerHTML = state.shots.length ? state.shots.map((s, i) => `<option value="${i}">#${s.order} ${fmtT(s.start)}–${fmtT(s.end)}s ${esc(s.visual_type)} ${esc(s.framing || s.gap_code || "")}</option>`).join("")
    : `<option value="">(build or select a plan in the Planner tab)</option>`;
  if (cur && state.shots[cur]) sel.value = cur;
  if (state.shots.length && $("#panel-preview").classList.contains("active")) showShotPreview(Number(sel.value || 0));
}
function showShotPreview(i) {
  const s = state.shots[i]; if (!s || !state.plan) return;
  setImg($("#pvStart"), `/shots/${state.plan.visual_plan_id}/${s.shot_id}/preview?t=0`);
  setImg($("#pvEnd"), `/shots/${state.plan.visual_plan_id}/${s.shot_id}/preview?t=1`);
  $("#pvMeta").innerHTML = `<b>#${s.order}</b> ${fmtT(s.start)}–${fmtT(s.end)}s (duration ${fmtT(s.duration)}s) ${badge(s.status)} ${badge(s.editorial_label)}<br>
    transition: <b>${esc(s.transition)}</b> · framing: ${esc(s.framing || "—")}<br>
    change reason: ${esc(s.change_reason)}<br>editorial reason: ${esc(s.editorial_reason)}<br>attribution: ${esc(s.attribution || "—")}<br>
    narration: <i>${esc(s.narration_anchor.text)}</i>
    <pre>motion: ${esc(JSON.stringify(s.motion, null, 1))}\ncrop: ${esc(JSON.stringify(s.crop, null, 1))}\noverlays: ${esc(JSON.stringify(s.overlays))}</pre>`;
}
$("#previewShot").addEventListener("change", (e) => showShotPreview(Number(e.target.value)));
$("#loadSheet").addEventListener("click", () => { if (!state.plan) { toast("Select a built plan first.", true); return; } setImg($("#contactSheet"), `/visual-plans/${state.plan.visual_plan_id}/contact-sheet`); });

/* ---------------- generation ---------------- */
const kindLabel = (p) => (p.generation_kind === "PLACEHOLDER" ? " (PLACEHOLDER - not AI)" : p.is_mock ? " (MOCK - test only)" : " (real AI)") +
  (p.state === "NOT_CONFIGURED" ? " - NOT CONFIGURED" : "");
async function loadProviders() {
  const r = await api("/providers"); state.providers = r.providers;
  $$(".providerSelect").forEach((sel) => { sel.innerHTML = r.providers.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}${esc(kindLabel(p))}</option>`).join(""); sel.value = r.default; });
  showProviderInfo();
}
function showProviderInfo() {
  const p = state.providers.find((x) => x.name === $("#genForm [name=provider]").value);
  $("#providerInfo").innerHTML = p ? `model <b>${esc(p.model)}</b> · ${badge(p.label, p.generation_kind)} · state ${badge(p.state, p.state === "NOT_CONFIGURED" ? "FAILED" : "SUCCEEDED")}
    ${p.billable ? badge("billable", "RUNNING") : ""}<br>${esc(p.description)}` : "";
}
$("#genForm [name=provider]").addEventListener("change", showProviderInfo);
$("#checkProviderBtn").addEventListener("click", () => guarded(async () => {
  const name = $("#genForm [name=provider]").value;
  const s = await api(`/providers/${encodeURIComponent(name)}/status`);  // configuration only: never generates, no network
  $("#estimateBox").innerHTML = `provider <b>${esc(s.name)}</b>: ${badge(s.state, s.state === "NOT_CONFIGURED" ? "FAILED" : "SUCCEEDED")} · ${esc(s.probe.detail)}`;
}));
const genBody = () => { const f = new FormData($("#genForm")); return { project_id: state.project, provider: f.get("provider"), prompt: f.get("prompt"), purpose: f.get("purpose"), quality: f.get("quality"), resolution: f.get("resolution"), allow_placeholder_fallback: !!f.get("allow_placeholder_fallback") }; };
$("#estimateBtn").addEventListener("click", () => { if (!needProject()) return; guarded(async () => {
  const e = await api("/assets/generate/estimate", { method: "POST", json: genBody() });
  const cost = e.estimate_status === "NOT_APPLICABLE" ? "cost <b>not applicable</b> (no billable provider)"
    : e.estimated_cost == null ? "cost <b>UNKNOWN</b> (no documented price)" : `estimated cost <b>$${e.estimated_cost}</b> ${esc(e.currency)}`;
  const budget = e.within_budget == null ? "budget cannot be checked" : e.within_budget ? "within budget" : "<b>would exceed budget</b>";
  $("#estimateBox").innerHTML = `${cost} · ${esc(e.provider)}/${esc(e.model)} ${badge(e.generation_kind)} · state ${esc(e.provider_state)}<br>
    <span class="small">${esc(e.estimate_basis)}</span><br>known spend ${money(e.spent_usd)} · committed ${money(e.committed_usd)} of $${esc(e.budget_usd)} · ${budget}`;
}); });
$("#genForm").addEventListener("submit", (e) => { e.preventDefault(); if (!needProject()) return; guarded(async () => {
  const el = $("#genStatus");
  el.innerHTML = `<span class="muted">Submitting…</span>`;
  let job;
  try { job = await api("/assets/generate", { method: "POST", json: genBody() }); }
  catch (err) { el.innerHTML = `${badge("FAILED")} ${esc(err.message)}`; throw err; }
  const done = await pollJob(job.job_id, (j) => { el.innerHTML = `job ${esc(j.job_id.slice(0, 8))}: ${badge(j.status)} ${Math.round(j.progress * 100)}% ${esc(j.stage || "")}`; });
  if (done.status === "SUCCEEDED") {
    const a = await api(`/assets/${done.result_id}`);
    el.innerHTML = `${badge("SUCCEEDED")} asset <code>${esc(a.asset_id.slice(0, 8))}</code> · ${esc(a.provider)}/${esc(a.model)} ${kindBadges(a)}<br>
      class <b>${esc(a.provenance_class)}</b> · editorial ${badge(a.editorial_category)} · cost ${money(a.generation_cost)} (${esc(a.cost_status)})
      ${a.is_fallback ? `<br><span class="small">fallback because: ${esc(a.fallback_reason)}</span>` : ""}<img class="detail-img" id="genImg" alt="generated image">`;
    setImg($("#genImg"), a.file_url);
  } else el.innerHTML += `<br>${badge("FAILED")} ${esc(done.error_code)}: ${esc(done.error_message)}`;
}); });

/* ---------------- costs ---------------- */
async function refreshCosts() {
  if (!state.project) { $("#costSummary").innerHTML = `<span class="muted">Select a project.</span>`; return; }
  $("#costSummary").innerHTML = `<span class="muted">Loading…</span>`;
  await guarded(async () => {
    const c = await api(`/costs/${state.project}`);
    const k = (v, l) => `<div class="kpi"><b>${esc(v ?? "—")}</b><span>${esc(l)}</span></div>`;
    const lb = c.totals_complete ? "" : " (lower bound: some costs UNKNOWN)";
    $("#costNote").textContent = c.totals_complete ? "All recorded costs are known (or not applicable)." :
      `${c.unknown_cost_count} real-AI generation(s) have UNKNOWN cost; totals below are lower bounds and are NOT counted as zero.`;
    $("#costNote").className = c.totals_complete ? "small muted" : "small warn";
    $("#costSummary").innerHTML = k("$" + c.ai_generation_cost, "known AI generation cost" + lb) + k(c.ai_image_count, "REAL AI images") +
      k(c.unknown_cost_count, "generations with UNKNOWN cost") + k(c.placeholder_image_count, "PLACEHOLDER images (not AI, no cost)") +
      k(c.mock_ai_image_count, "MOCK / fixture images") + k(c.source_asset_count, "source/system assets") + k(c.total_visual_asset_count, "total visual assets") +
      k(money(c.cost_per_generated_image), "cost / real AI image (known)") +
      k(money(c.cost_per_finished_minute), "cost / finished minute" + lb) +
      k(c.finished_minutes, "finished minutes") + k("$" + c.budget_remaining_usd, `budget remaining of $${c.budget_usd}${c.totals_complete ? "" : " (upper bound)"}`) +
      k(c.failed_generation_count, "failed generations");
    $("#costProviders tbody").innerHTML = c.by_provider.map((p) => `<tr><td>${esc(p.provider)}</td><td>${badge(p.generation_kind || "?", p.generation_kind)}</td><td>${p.count}</td>
      <td>$${p.known_cost}</td><td>${p.unknown_cost_count}</td><td>${p.not_applicable_count}</td><td>${esc(p.models.join(", "))}</td></tr>`).join("") ||
      `<tr><td colspan="7" class="muted">No generations in this project.</td></tr>`;
    $("#costRecords tbody").innerHTML = c.records.map((r) => `<tr><td>${esc((r.created_at || "").slice(0, 19).replace("T", " "))}</td><td>${esc(r.provider)} ${r.is_fallback ? badge("FALLBACK") : ""}</td>
      <td>${badge(r.generation_kind, r.generation_kind)}</td><td>${esc(r.model)}</td><td>${esc(r.quality)}</td><td>${esc(r.resolution)}</td>
      <td>${badge(r.cost_status, r.cost_status === "UNKNOWN" ? "FAILED" : r.cost_status === "KNOWN" ? "SUCCEEDED" : "UNKNOWN")} ${money(r.estimated_cost)}</td>
      <td><code>${esc((r.asset_id || "").slice(0, 8))}</code></td></tr>`).join("") || `<tr><td colspan="8" class="muted">No cost records.</td></tr>`;
  });
}
$("#refreshCosts").addEventListener("click", refreshCosts);

/* ---------------- diagnostics ---------------- */
async function refreshDiag() { await guarded(async () => { $("#diagBox").textContent = JSON.stringify(await api("/diagnostics"), null, 2); }); }
async function refreshLogs() { await guarded(async () => { $("#logBox").textContent = (await api("/diagnostics/logs?lines=200")).lines.join("\n"); }); }
$("#refreshDiag").addEventListener("click", refreshDiag);
$("#refreshLogs").addEventListener("click", refreshLogs);
$("#apiKey").value = ls.get("vvm_api_key") || "";
$("#apiKey").addEventListener("change", (e) => { ls.set("vvm_api_key", e.target.value); toast("API key saved in this browser"); });

/* ---------------- boot ---------------- */
(async function boot() {
  await checkHealth(); setInterval(checkHealth, 15000);
  await guarded(loadProviders);
  const qp = new URLSearchParams(location.search);  // deep links: /ui/?project=<id>&tab=planner
  await guarded(() => loadProjects(qp.get("project") || undefined));
  const tab = qp.get("tab") || ls.get("vvm_tab"); const btn = tab && $$(".tab").find((t) => t.dataset.tab === tab);
  if (btn) btn.click(); else onTab("dashboard");
})();
