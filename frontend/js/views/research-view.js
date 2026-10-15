// Search & Evidence workspace: live research monitor + evidence explorer.
// Everything shown comes from /api/jobs/{id}/monitor and /evidence/explorer. Nothing is simulated;
// unknown values render as "Unknown" / "Estimating". Fetched text is always escaped.
import { escapeHtml as esc } from "../components/status-badge.js";
import { newIdempotencyKey } from "../api.js";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const STATE_LABEL = {
  DRAFT: "Draft", QUEUED: "Queued", RUNNING: "Running", WAITING_RETRY: "Waiting / retrying",
  PAUSED: "Paused", AWAITING_READINESS: "Awaiting readiness", COMPLETED: "Completed",
  FAILED: "Failed", CANCELLED: "Cancelled",
};
export const FILTERS = [
  ["all", "All"], ["AUTO_ACCEPTED", "Accepted"], ["REJECTED", "Rejected"], ["NEEDS_REVIEW", "Needs review / unresolved"],
  ["SOURCE_BLOCKED", "Source blocked"], ["contradicted", "Contradicted"], ["single", "Single-source"],
  ["quant", "Quantitative / high-impact"],
];

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "Unknown";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function filterClaims(claims, { filter = "all", query = "", host = "" } = {}) {
  const q = query.trim().toLowerCase();
  return (claims || []).filter((c) => {
    if (filter === "contradicted" && !c.contradicted) return false;
    if (filter === "single" && c.single_source !== true) return false;
    if (filter === "quant" && !(c.quantitative || c.high_impact)) return false;
    if (["AUTO_ACCEPTED", "REJECTED", "NEEDS_REVIEW", "SOURCE_BLOCKED"].includes(filter) && c.state !== filter) return false;
    if (host && (c.hostname || "") !== host && (c.source_category || "") !== host) return false;
    if (q && !`${c.text} ${c.hostname} ${(c.reasons || []).join(" ")}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function stageRow(s) {
  const prog = s.progress
    ? `<progress max="${esc(String(s.progress.total))}" value="${esc(String(s.progress.done))}"></progress> ${esc(String(s.progress.done))}/${esc(String(s.progress.total))} ${esc(s.progress.unit)}`
    : (s.status === "RUNNING" ? `<span class="meta">In progress (no measurable denominator)</span>` : "");
  const retry = s.attempt_count ? `attempt ${s.attempt_count}/${s.max_attempts}${s.retry_after ? `, retry after ${esc(s.retry_after)}` : ""}` : "";
  return `<li class="stage stage--${esc(s.status)}" data-stage="${esc(s.stage_key)}">
    <strong>${esc(s.label)}</strong> <span class="badge ${esc(s.status)}">${esc(s.status)}</span>
    <div class="meta">${s.started_at ? `Started ${esc(s.started_at)}` : "Not started"}${s.ended_at ? ` · ended ${esc(s.ended_at)}` : ""}
      · duration ${esc(formatDuration(s.duration_seconds ?? s.elapsed_seconds))}${retry ? ` · ${retry}` : ""}</div>
    ${prog ? `<div>${prog}</div>` : ""}
    ${s.error ? `<p class="error-state" role="alert">${esc(s.error)}</p>` : ""}</li>`;
}

export function renderMonitorHtml(m) {
  const est = m.estimate || {};
  const remaining = TERMINAL.has(m.job_status) ? "—"
    : est.remaining_seconds === null || est.remaining_seconds === undefined ? "Estimating"
      : `${formatDuration(est.remaining_seconds)} (${esc(est.basis)}, ${esc(est.confidence)} confidence)`;
  const c = m.counts || {};
  const counts = [["Approved", c.approved], ["Fetched", c.fetched], ["Failed", c.failed], ["Rejected", c.rejected],
    ["Duplicate", c.duplicate], ["Pending", c.pending]]
    .map(([k, v]) => `<div class="stat"><span class="meta">${k}</span><strong data-count="${k.toLowerCase()}">${esc(String(v ?? 0))}</strong></div>`).join("");
  const rows = (m.sources || []).map((s) => `<tr>
      <td>${esc(s.hostname || "Unknown")}</td><td>${esc(s.title || "Unknown")}</td>
      <td>${esc(s.category || "Unknown")}</td><td>${esc(s.independence_group || "Unknown")}</td>
      <td><span class="badge ${esc(s.acquisition_status || "")}">${esc(s.acquisition_status || "Unknown")}</span>
        ${s.policy_state ? `<span class="chip">${esc(s.policy_state)}</span>` : ""}</td>
      <td>${esc(formatDuration(s.fetch_duration_seconds))}</td>
      <td>${esc(s.error || "")}</td></tr>`).join("");
  return `
    <h2>${esc(m.topic)}</h2>
    <p><span class="badge ${esc(m.display_state)}" data-state="${esc(m.display_state)}">${esc(STATE_LABEL[m.display_state] || m.display_state)}</span>
      <span class="meta">${esc(String(m.stages_done))}/${esc(String(m.stages_total))} stages complete</span></p>
    <progress max="${esc(String(m.stages_total))}" value="${esc(String(m.stages_done))}" aria-label="Overall stage progress"></progress>
    <dl class="timing">
      <dt>Started</dt><dd>${esc(m.started_at || "Not started")}</dd>
      <dt>Last update</dt><dd>${esc(m.updated_at)}</dd>
      <dt>Elapsed</dt><dd>${esc(formatDuration(m.elapsed_seconds))}</dd>
      <dt>Estimated remaining</dt><dd>${remaining}</dd>
    </dl>
    <div class="stats">${counts}</div>
    <h3>Pipeline</h3><ol class="pipeline">${(m.stages || []).map(stageRow).join("")}</ol>
    <h3>Sources</h3>
    ${rows ? `<div class="table-wrap"><table><thead><tr><th>Host</th><th>Title</th><th>Category</th><th>Independence group</th>
      <th>Status</th><th>Fetch time</th><th>Error</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<div class="empty-state">No sources have been acquired yet.</div>`}
    <p class="meta">Pause, resume, cancel and retry are not offered: the backend does not implement them.</p>`;
}

export function renderExplorerHtml(ex, ui) {
  if (!ex.policy_run) {
    return `<div class="empty-state"><p>The automatic evidence policy has not run for this job.</p>
      <button type="button" data-action="run-policy">Run evidence policy</button></div>`;
  }
  const claims = filterClaims(ex.claims, ui);
  const hosts = [...new Set(ex.claims.flatMap((c) => [c.hostname, c.source_category]).filter(Boolean))].sort();
  const ready = ex.readiness;
  const rows = claims.map((c) => `<tr class="clickable${ui.selected === c.claim_id ? " selected" : ""}" tabindex="0"
      data-claim-id="${esc(c.claim_id)}"><td>${esc(c.text.length > 140 ? c.text.slice(0, 140) + "…" : c.text)}</td>
      <td>${esc(c.category || "Unknown")}</td><td>${esc(String(c.score?.total ?? "—"))}</td>
      <td><span class="badge ${esc(c.state)}">${esc(c.state)}</span></td>
      <td>${esc(c.hostname || "Unknown")}</td><td>${esc(c.independence_group || "Unknown")}</td>
      <td>${esc(String(c.passage_ref.start))}–${esc(String(c.passage_ref.end))}</td>
      <td>${c.corroboration?.applicable ? `${esc(String(c.corroboration.independent_groups.length))} group(s)` : "n/a"}</td>
      <td>${c.contradicted ? "Contradicted" : "None found"}</td></tr>`).join("");
  return `
    <div class="readiness ${ready.ready ? "ok" : "blocked"}" role="status">
      <strong>${ready.ready ? "Ready for script generation" : "Not ready for script generation"}</strong>
      ${ready.reasons.length ? `<ul>${ready.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      <p class="meta">Policy ${esc(ex.policy_run.policy_version)} · scoring ${esc(ex.policy_run.scoring_policy_version || "Unknown")}
        · analysis ${esc((ex.policy_run.analysis_sha256 || "").slice(0, 12))} · evidence ${esc(ex.freshness.status)}
        ${(ex.freshness.reasons || []).map((r) => `· ${esc(r)}`).join("")}</p>
    </div>
    <div class="filter-bar">
      <label>Search <input type="search" id="ex-query" value="${esc(ui.query || "")}"></label>
      <label>Filter <select id="ex-filter">${FILTERS.map(([v, l]) => `<option value="${v}"${ui.filter === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <label>Host / category <select id="ex-host"><option value="">All</option>${hosts.map((h) => `<option${ui.host === h ? " selected" : ""}>${esc(h)}</option>`).join("")}</select></label>
      <button type="button" data-action="export" data-format="csv">Export CSV</button>
      <button type="button" data-action="export" data-format="json">Export JSON</button>
    </div>
    <p class="meta">${claims.length} of ${ex.claims.length} claims. Decisions are automatic; rejected or unresolved claims are never usable evidence.</p>
    ${rows ? `<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Category</th><th>Score</th><th>Decision</th>
      <th>Source</th><th>Group</th><th>Passage</th><th>Corroboration</th><th>Contradiction</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<div class="empty-state">No claims match the current filter.</div>`}`;
}

export function renderClaimDetailHtml(c, ex) {
  if (!c) return `<div class="empty-state">Select a claim to inspect it.</div>`;
  const comp = c.score?.components || {};
  const weights = c.score?.weights || {};
  const comps = Object.keys(comp).map((k) =>
    `<tr><td>${esc(k)}</td><td>${esc(String(comp[k]))}</td><td>${esc(String(weights[k] ?? ""))}</td></tr>`).join("");
  const ctx = c.context
    ? `<blockquote class="passage"><span class="meta">${esc(c.context.before)}</span><mark>${esc(c.context.passage)}</mark><span class="meta">${esc(c.context.after)}</span></blockquote>`
    : `<p class="meta">The stored passage could not be located at the recorded offsets.</p>`;
  const src = (ex.sources || []).find((s) => s.source_id === c.source_id) || {};
  const safeUrl = /^https?:\/\//i.test(src.canonical_url || "") ? src.canonical_url : null;
  return `
    <h2>Claim</h2>
    <p>${esc(c.text)}</p>
    <p><span class="badge ${esc(c.state)}">${esc(c.state)}</span> <span class="chip">score ${esc(String(c.score?.total ?? "—"))}/100</span>
      ${c.flags.map((f) => `<span class="chip">${esc(f)}</span>`).join("")}</p>
    <h3>Decision reasons</h3><ul>${c.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    <h3>Supporting passage</h3>${ctx}
    <p class="meta">Offsets ${esc(String(c.passage_ref.start))}–${esc(String(c.passage_ref.end))} in the stored normalized text.</p>
    <h3>Source</h3>
    <p><button type="button" data-action="view-source" data-source-id="${esc(c.source_id)}">${esc(src.title || c.hostname || c.source_id)}</button></p>
    <p class="meta">Publisher: ${esc(src.publisher || "Unknown")} · category ${esc(c.source_category || "Unknown")}
      · group ${esc(c.independence_group || "Unknown")} · published ${esc(src.published_at || "Unknown")}</p>
    ${safeUrl ? `<p><a href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">Open original source</a></p>` : `<p class="meta">Original URL unavailable.</p>`}
    <h3>Score components</h3>
    <table><thead><tr><th>Component</th><th>Value (0–1)</th><th>Weight</th></tr></thead><tbody>${comps}</tbody></table>
    <h3>Corroboration and contradiction</h3>
    <p>${c.corroboration?.applicable
      ? `Figure(s) ${esc(c.corroboration.quantities.join(", "))} appear in ${esc(String(c.corroboration.independent_groups.length))} independent group(s): ${esc(c.corroboration.independent_groups.join(", "))}`
      : "No checkable figure; corroboration is not applicable."}</p>
    <p>${c.contradicted ? `Contradicting claims: ${esc(c.contradicting.map((x) => x.claim_id).join(", "))}` : "No contradicting evidence detected."}</p>
    <p class="meta">Policy ${esc(c.policy_version)} · scoring ${esc(c.scoring_policy_version || "Unknown")}
      · analysis ${esc(c.analysis_sha256 || "Unknown")} · evidence ${esc(ex.freshness.status)}</p>`;
}

export function renderSourceDetailHtml(s) {
  return `<h2>${esc(s.title || s.hostname || s.source_id)}</h2>
    <p class="meta">Acquisition ${esc(s.acquisition_status || "Unknown")} · policy ${esc(s.state)} · category ${esc(s.category || "Unknown")}
      · group ${esc(s.independence_group || "Unknown")} · published ${esc(s.published_at || "Unknown")}</p>
    ${s.error ? `<p class="error-state" role="alert">${esc(s.error)}</p>` : ""}
    <p class="meta">Retrieved ${esc(s.retrieved_at || "Unknown")} · sha256 ${esc(s.sha256 || "Unknown")}</p>
    <h3>Extracted claims (${esc(String((s.claim_details || []).length))})</h3>
    <ul>${(s.claim_details || []).map((c) => `<li><span class="badge ${esc(c.state)}">${esc(c.state)}</span> ${esc(c.text)}</li>`).join("")}</ul>`;
}

export async function downloadReport(api, jobId, format) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/evidence/report.${format}`, { headers: api.authHeader() });
  if (!res.ok) throw new Error(`Export failed (${res.status})`);
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: `evidence-report.${format}` });
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

export function startFormHtml() {
  return `<form id="research-start" class="card">
    <h2>Start research</h2>
    <label>Topic <input id="rs-topic" required maxlength="1000" placeholder="e.g. Rafale deal"></label>
    <label>Language <select id="rs-lang"><option value="en">English</option><option value="ta">Tamil</option></select></label>
    <p id="rs-error" class="error-state" role="alert" hidden></p>
    <button type="submit" class="primary">Start research</button>
    <p class="meta">Sources are fetched only from the controlled source registry.</p></form>`;
}

export async function mountResearch({ api, jobId, list, detail, filters, isCurrent, onStarted, pollMs = 3000 }) {
  filters.innerHTML = "<p>Live research progress and evidence. Decisions are automatic and policy-driven.</p>";
  detail.innerHTML = "";
  if (!jobId) {
    list.innerHTML = startFormHtml();
    list.querySelector("#research-start").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const topic = list.querySelector("#rs-topic").value.trim();
      const err = list.querySelector("#rs-error");
      if (!topic) { err.textContent = "Enter a topic to research."; err.hidden = false; return; }
      try {
        const lang = list.querySelector("#rs-lang").value;
        const job = await api.createJob(topic, lang, newIdempotencyKey());
        await api.createDiscoveryRequest(job.job_id, { topic, language: lang });
        await api.queueJob(job.job_id);
        onStarted?.(job.job_id);
      } catch (e) { err.textContent = e.message; err.hidden = false; }
    });
    return;
  }
  const ui = { filter: "all", query: "", host: "", selected: null, explorer: null, timer: null };
  list.innerHTML = '<div id="rm-monitor"></div><div id="rm-explorer"></div>';
  const monEl = list.querySelector("#rm-monitor");
  const exEl = list.querySelector("#rm-explorer");

  const paintExplorer = () => {
    if (!ui.explorer) return;
    exEl.innerHTML = `<h3>Evidence explorer</h3>${renderExplorerHtml(ui.explorer, ui)}`;
    const q = exEl.querySelector("#ex-query");
    q?.addEventListener("input", () => { ui.query = q.value; paintExplorer(); exEl.querySelector("#ex-query").focus(); });
    exEl.querySelector("#ex-filter")?.addEventListener("change", (e) => { ui.filter = e.target.value; paintExplorer(); });
    exEl.querySelector("#ex-host")?.addEventListener("change", (e) => { ui.host = e.target.value; paintExplorer(); });
  };
  const select = (id) => {
    ui.selected = id;
    detail.innerHTML = renderClaimDetailHtml(ui.explorer.claims.find((c) => c.claim_id === id), ui.explorer);
    paintExplorer();
  };
  list.addEventListener("click", async (e) => {
    const row = e.target.closest("[data-claim-id]");
    if (row) return select(row.dataset.claimId);
    const act = e.target.closest("[data-action]");
    if (!act) return;
    try {
      if (act.dataset.action === "export") await downloadReport(api, jobId, act.dataset.format);
      if (act.dataset.action === "run-policy") {
        await api.request("POST", `/jobs/${encodeURIComponent(jobId)}/evidence/policy/run`);
        await refresh();
      }
    } catch (err) { detail.innerHTML = `<p role="alert" class="error-state">${esc(err.message)}</p>`; }
  });
  detail.addEventListener("click", async (e) => {
    const b = e.target.closest('[data-action="view-source"]');
    if (!b) return;
    try {
      const s = await api.request("GET", `/jobs/${encodeURIComponent(jobId)}/evidence/explorer/sources/${encodeURIComponent(b.dataset.sourceId)}`);
      detail.innerHTML = renderSourceDetailHtml(s);
    } catch (err) { detail.innerHTML = `<p role="alert" class="error-state">${esc(err.message)}</p>`; }
  });
  list.addEventListener("keydown", (e) => {
    const row = e.target.closest?.("[data-claim-id]");
    if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(row.dataset.claimId); }
  });

  async function refresh() {
    if (!isCurrent()) return;
    let m;
    try {
      m = await api.request("GET", `/jobs/${encodeURIComponent(jobId)}/monitor`);
      monEl.innerHTML = renderMonitorHtml(m);
    } catch (err) {
      monEl.innerHTML = `<p role="alert" class="error-state">${esc(err.message)}</p>`;
      return;
    }
    try {
      ui.explorer = await api.request("GET", `/jobs/${encodeURIComponent(jobId)}/evidence/explorer`);
      paintExplorer();
    } catch (err) { exEl.innerHTML = `<p role="alert" class="error-state">${esc(err.message)}</p>`; }
    if (!TERMINAL.has(m.job_status) && isCurrent()) ui.timer = setTimeout(refresh, pollMs);
  }
  await refresh();
}
