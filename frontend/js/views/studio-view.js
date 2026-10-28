// Studio tab: one ordered view of the whole production for the active job.
// Read-only over GET /jobs/{id}/studio/{language}, which is derived from saved
// server state, so a refresh, restart or another device resumes at the same
// stage. Actions stay on the tab that owns them; "Open" only navigates there.
// No polling: state is re-read on mount and on Refresh.
import { escapeHtml as esc } from "../components/status-badge.js";

const STATE_LABEL = {
  NOT_STARTED: "Not started", GENERATED: "Generated", AWAITING_REVIEW: "Awaiting review",
};

function label(state) {
  return STATE_LABEL[state] || state.charAt(0) + state.slice(1).toLowerCase().replace(/_/g, " ");
}

function version(stage) {
  const parts = [];
  if (stage.version != null) parts.push(`v${stage.version}`);
  if (stage.approved_version != null && stage.approved_version !== stage.version) {
    parts.push(`approved v${stage.approved_version}`);
  }
  return parts.join(" · ");
}

export function renderStudioRailHtml(studio, selectedKey) {
  if (!studio || !studio.stages.length) return '<p class="empty-state">No production state for this job yet.</p>';
  const rows = studio.stages.map((s, i) => {
    const current = s.key === studio.current_stage;
    return `<li class="studio-stage${current ? " studio-stage--current" : ""}${s.done ? " studio-stage--done" : ""}"
        data-stage="${esc(s.key)}" data-state="${esc(s.state)}">
      <button type="button" data-select="${esc(s.key)}" aria-pressed="${s.key === selectedKey}"
        ${current ? 'aria-current="step"' : ""}>
        <span class="studio-step">${i + 1}</span>
        <span class="studio-title">${esc(s.title)}</span>
        <span class="chip">${esc(label(s.state))}</span>
        ${version(s) ? `<span class="meta">${esc(version(s))}</span>` : ""}
      </button></li>`;
  }).join("");
  const next = studio.stages.find((s) => s.key === studio.current_stage);
  const banner = next
    ? `<p class="meta" data-studio-current>Next: <strong>${esc(next.title)}</strong></p>`
    : '<p class="meta" data-studio-current>Every stage is done. Publishing is a separate, explicitly authorised action.</p>';
  return `${banner}<ol class="studio-rail">${rows}</ol>
    <p class="meta">Derived from saved state; the same after a refresh, a restart or on another device.</p>`;
}

export function renderStudioDetailHtml(studio, key) {
  const s = studio && studio.stages.find((x) => x.key === key);
  if (!s) return "";
  const gate = s.review_gate
    ? "This stage needs an explicit human approval; generating output does not approve it."
    : "This stage has no human review gate yet; generated output counts as done here.";
  return `<h2>${esc(s.title)}</h2>
    <p><span class="chip">${esc(label(s.state))}</span>
      <span class="meta">server status: ${esc(s.raw_status)}${version(s) ? ` · ${esc(version(s))}` : ""}</span></p>
    ${s.stale_reason ? `<p role="alert" class="meta">Stale: ${esc(s.stale_reason)}</p>` : ""}
    ${s.detail ? `<p class="meta">${esc(s.detail)}</p>` : ""}
    <p class="meta">${gate}</p>
    <button type="button" data-open-tab="${esc(s.tab)}">Open ${esc(s.tab)} tab</button>`;
}

export async function mountStudio({ api, jobId, list, detail, filters, isCurrent, onOpenTab }) {
  filters.innerHTML = `<label for="studio-language">Language
      <select id="studio-language"><option value="ta">Tamil</option><option value="en">English</option></select></label>
    <button type="button" data-studio-refresh>Refresh</button>`;
  const languageSelect = filters.querySelector("#studio-language");
  try {  // default to the job's own language
    const job = await api.request("GET", `/jobs/${encodeURIComponent(jobId)}`);
    if (["ta", "en"].includes(job.language)) languageSelect.value = job.language;
  } catch (_) { /* keep the default */ }
  let studio = null, selected = null;

  function showDetail() {
    detail.innerHTML = renderStudioDetailHtml(studio, selected);
    const open = detail.querySelector("[data-open-tab]");
    if (open) open.addEventListener("click", () => onOpenTab(open.dataset.openTab));
  }

  async function load() {
    list.innerHTML = '<p class="loading-state">Loading production state…</p>';
    detail.innerHTML = "";
    try {
      studio = await api.request("GET",
        `/jobs/${encodeURIComponent(jobId)}/studio/${encodeURIComponent(languageSelect.value)}`);
    } catch (error) {
      if (!isCurrent()) return;
      list.innerHTML = `<p role="alert" class="error-state">${esc(error.message || String(error))}</p>`;
      return;
    }
    if (!isCurrent()) return;
    if (!studio.stages.some((s) => s.key === selected)) selected = studio.current_stage || studio.stages[0].key;
    list.innerHTML = renderStudioRailHtml(studio, selected);
    list.querySelectorAll("[data-select]").forEach((btn) => btn.addEventListener("click", () => {
      selected = btn.dataset.select;
      list.querySelectorAll("[data-select]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      showDetail();
    }));
    showDetail();
  }

  languageSelect.addEventListener("change", load);
  filters.querySelector("[data-studio-refresh]").addEventListener("click", load);
  await load();
}
