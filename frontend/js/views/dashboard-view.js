// Dashboard view — the real operational overview of the owner's workflow jobs.
// Backed entirely by GET /api/jobs (persisted state); it never fabricates jobs,
// statuses, or completion percentages. Every number shown is derived from the
// server's own stage roll-up (stages_completed / stages_total) for that job.

import { escapeHtml } from "../components/status-badge.js";
import { icon } from "../components/icons.js";

// Job-level lifecycle labels. Kept distinct from the per-stage review statuses
// so a job "AWAITING_REVIEW" reads as a workflow state, not a review decision.
const JOB_STATUS = {
  DRAFT: { label: "Draft", cls: "PENDING" },
  QUEUED: { label: "Queued", cls: "PENDING" },
  RUNNING: { label: "Running", cls: "NEEDS_MORE_EVIDENCE" },
  AWAITING_REVIEW: { label: "Awaiting review", cls: "NEEDS_MORE_EVIDENCE" },
  COMPLETED: { label: "Completed", cls: "APPROVED_FOR_USE" },
  FAILED: { label: "Failed", cls: "REJECTED" },
};

// Turn a stage_key (snake_case) into a readable phase name for the roll-up line.
function humanStage(key) {
  if (!key) return "";
  return String(key).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function jobStatusBadge(status) {
  const s = JOB_STATUS[status] || { label: String(status || "Unknown"), cls: "PENDING" };
  return `<span class="badge ${s.cls}">${escapeHtml(s.label)}</span>`;
}

function jobCardHtml(job, activeJobId) {
  const total = Number(job.stages_total) || 0;
  const done = Number(job.stages_completed) || 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const isActive = job.job_id === activeJobId;
  const current = job.current_stage ? humanStage(job.current_stage) : null;
  const shortId = String(job.job_id).slice(0, 8);
  return `
    <article class="job-card${isActive ? " job-card--active" : ""}"
             tabindex="0" role="button"
             data-job-card="${escapeHtml(job.job_id)}"
             aria-label="Open job ${escapeHtml(shortId)}">
      <header class="job-card-head">
        <div class="job-card-title">
          <h3>${escapeHtml(job.topic || "(untitled topic)")}</h3>
          <span class="job-card-meta">
            <span class="mono">${escapeHtml(shortId)}</span>
            <span class="dot-sep" aria-hidden="true">·</span>
            <span>${escapeHtml((job.language || "").toUpperCase())}</span>
          </span>
        </div>
        ${jobStatusBadge(job.status)}
      </header>
      <div class="job-card-progress" role="img"
           aria-label="${done} of ${total} stages complete">
        <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
        <span class="job-card-progress-label">${done}/${total} stages · ${pct}%</span>
      </div>
      <footer class="job-card-foot">
        ${current
          ? `<span class="job-card-stage">${icon("radar", { size: 13 })} ${escapeHtml(current)}</span>`
          : `<span class="job-card-stage muted">Pipeline idle</span>`}
        <span class="job-card-go">Open ${icon("script", { size: 13 })}</span>
      </footer>
    </article>`;
}

export function renderDashboardHtml(jobs, activeJobId) {
  if (!Array.isArray(jobs) || jobs.length === 0) {
    return `
      <div class="dashboard">
        <div class="empty-state dashboard-empty">
          <span class="empty-icon" aria-hidden="true">${icon("layers", { size: 26 })}</span>
          <p>No workflow jobs yet for this session.</p>
          <p class="meta">Start one from the Discovery tab, or load a specific job by ID above.</p>
        </div>
      </div>`;
  }
  const awaiting = jobs.filter((j) => j.status === "AWAITING_REVIEW").length;
  const completed = jobs.filter((j) => j.status === "COMPLETED").length;
  const running = jobs.filter((j) => j.status === "RUNNING" || j.status === "QUEUED").length;
  return `
    <div class="dashboard">
      <div class="dashboard-stats" aria-label="Workflow summary">
        ${statTile(jobs.length, "Total jobs")}
        ${statTile(awaiting, "Awaiting review", "NEEDS_MORE_EVIDENCE")}
        ${statTile(running, "In progress")}
        ${statTile(completed, "Completed", "APPROVED_FOR_USE")}
      </div>
      <div class="job-grid">
        ${jobs.map((j) => jobCardHtml(j, activeJobId)).join("")}
      </div>
    </div>`;
}

function statTile(value, label, accentCls = "") {
  return `
    <div class="stat-tile${accentCls ? " stat-tile--" + accentCls : ""}">
      <span class="stat-value">${Number(value) || 0}</span>
      <span class="stat-label">${escapeHtml(label)}</span>
    </div>`;
}
