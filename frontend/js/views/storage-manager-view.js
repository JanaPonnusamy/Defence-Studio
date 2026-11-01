// Storage Manager. Every number rendered here is taken from the API payload.
// Missing measurements stay "not reported". This view does not delete files
// unless the operator types the confirmation phrase and the API accepts it.

import { escapeHtml } from "../components/status-badge.js";

export function cleanupConfirmPhrase(fileCount) {
  return `DELETE ${fileCount} FILES`;
}

export function formatBytes(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "not reported";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const shown = unit === 0 ? String(size) : size.toFixed(size >= 10 ? 0 : 1);
  return `${shown} ${units[unit]}`;
}

export function renderStorageSummaryHtml(summary) {
  if (!summary) return `<p role="alert">Storage summary was not returned.</p>`;
  const moduleRows = moduleRowsOf(summary);
  const roots = Array.isArray(summary.roots) ? summary.roots : [];
  return `<section class="storage-summary">
    <h2>Storage</h2>
    <p class="meta">Measured ${escapeHtml(summary.measured_at || "time not reported")}.</p>
    <dl class="inspect-dl">
      ${metric("Managed storage", formatBytes(summary.total_bytes))}
      ${metric("Permanent", formatBytes(summary.permanent_bytes))}
      ${metric("Temporary", formatBytes(summary.temporary_bytes))}
      ${metric("Orphaned", formatBytes(summary.orphan_bytes))}
      ${metric("Unclassified", formatBytes(summary.unclassified_bytes))}
      ${metric("Reclaimed by recorded cleanups", formatBytes(summary.reclaimed_bytes_total ?? summary.last_cleanup?.reclaimed_bytes))}
    </dl>
    ${roots.length ? `<h3>Roots</h3><ul>${roots.map((root) => `<li>${escapeHtml(root.root || "root not reported")} — ${escapeHtml(formatBytes(root.bytes))} · free ${escapeHtml(formatBytes(root.disk_free_bytes))}</li>`).join("")}</ul>` : ""}
    <h3>By module</h3>
    ${moduleRows.length ? `<ul>${moduleRows.map((row) => `<li>${escapeHtml(row.module)} — ${escapeHtml(formatBytes(row.bytes))} (${escapeHtml(row.count ?? "n/a")} files)</li>`).join("")}</ul>` : `<p>No module breakdown was returned.</p>`}
    <h3>Largest files</h3>
    ${renderFileTable(summary.largest)}
    <h3>Last cleanup</h3>
    ${summary.last_cleanup ? `<p>${escapeHtml(summary.last_cleanup.finished_at || "time not reported")} · reclaimed ${escapeHtml(formatBytes(summary.last_cleanup.reclaimed_bytes))} · dry-run ${escapeHtml(String(summary.last_cleanup.dry_run))}</p>` : `<p>No cleanup has been recorded.</p>`}
  </section>`;
}

export function renderRenderControlsHtml(controls) {
  if (!controls) return `<p role="alert">Render controls were not returned.</p>`;
  const fields = [
    ["max_simultaneous_renders", "Simultaneous renders"],
    ["disk_floor_bytes", "Disk floor"],
    ["stream_inputs", "Stream large inputs"],
    ["thumbnail_cache", "Thumbnail cache"],
    ["reuse_assets", "Reuse unchanged assets"],
    ["reuse_tts", "Avoid repeat TTS"],
    ["reuse_ffmpeg_preprocess", "Avoid repeat FFmpeg preprocess"],
  ];
  return `<section>
    <h3>Render controls</h3>
    <p class="meta">${escapeHtml(controls.note || "Values appear only when the API reports them.")}</p>
    <dl class="inspect-dl">${fields.map(([key, label]) => metric(label, controlValue(controls[key]))).join("")}</dl>
  </section>`;
}

export function renderRetentionHtml(retention) {
  const categories = retention?.categories || [];
  if (!categories.length) return `<p>No retention configuration was returned.</p>`;
  return `<form id="storage-retention-form"><h3>Retention</h3><ul>${categories.map((category) => `
    <li><label>${escapeHtml(category.label || category.name)}
      <input data-retention="${escapeHtml(category.name)}" value="${escapeHtml(category.preserve ? "preserve" : category.days ?? "")}">
    </label> <span class="meta">${category.preserve ? "preserve" : "days"}</span></li>`).join("")}</ul>
    <button type="submit">Save retention</button></form>`;
}

export function renderCleanupResultHtml(result) {
  if (!result) return `<p role="alert">Cleanup returned no result.</p>`;
  const rows = result.would_delete || result.deleted || result.candidates || [];
  return `<section>
    <h3>${result.dry_run ? "Dry run" : "Cleanup result"}</h3>
    <p>Reclaimed ${escapeHtml(formatBytes(result.reclaimed_bytes ?? result.would_reclaim_bytes))} · files ${escapeHtml(rows.length)}</p>
    ${result.errors?.length ? `<ul class="error-state">${result.errors.map((error) => `<li>${escapeHtml(error)}</li>`).join("")}</ul>` : `<p>No cleanup errors were returned.</p>`}
    ${renderFileTable(rows)}
  </section>`;
}

export function renderHistoryHtml(history) {
  const rows = Array.isArray(history) ? history : (history?.cleanups || []);
  if (!rows.length) return `<p>No cleanup history was returned.</p>`;
  return `<ul>${rows.map((row) => `<li>${escapeHtml(row.finished_at || "time not reported")} · ${row.dry_run ? "dry run" : "cleanup"} · ${escapeHtml(formatBytes(row.reclaimed_bytes))}</li>`).join("")}</ul>`;
}

export function cleanupConfirmed(value, fileCount) {
  return value === cleanupConfirmPhrase(fileCount);
}

export function mountStorage({ api, list, detail, isCurrent }) {
  const state = { summary: null, controls: null, retention: null, history: null, errors: [], dryRun: null };

  function paint() {
    if (!isCurrent()) return;
    const fileCount = (state.dryRun?.candidates || state.dryRun?.would_delete || []).length;
    list.innerHTML = `${state.errors.map((error) => `<p role="alert" class="error-state">${escapeHtml(error)}</p>`).join("")}
      ${state.summary ? renderStorageSummaryHtml(state.summary) : `<p>Storage measurements are hidden until the storage API returns them.</p>`}
      ${state.controls ? renderRenderControlsHtml(state.controls) : `<p class="meta">Render controls were not returned.</p>`}`;
    detail.innerHTML = `
      ${state.retention ? renderRetentionHtml(state.retention) : `<p>Retention configuration was not returned.</p>`}
      <section>
        <h3>Cleanup</h3>
        <p class="meta">Dry-run does not delete. After a dry-run, cleanup requires the exact phrase ${escapeHtml(state.dryRun ? cleanupConfirmPhrase(fileCount) : "DELETE <count> FILES")}.</p>
        <button type="button" id="storage-dry-run">Dry run</button>
        <label>Confirmation <input id="storage-confirm" autocomplete="off"></label>
        <button type="button" id="storage-cleanup" disabled>Run cleanup</button>
        <div id="storage-cleanup-result"></div>
        <h3>History</h3>
        ${renderHistoryHtml(state.history)}
      </section>`;
    const confirm = document.getElementById("storage-confirm");
    const cleanup = document.getElementById("storage-cleanup");
    if (confirm && cleanup) {
      confirm.addEventListener("input", () => {
        cleanup.disabled = !state.dryRun || !cleanupConfirmed(confirm.value, fileCount);
      });
    }
    document.getElementById("storage-dry-run")?.addEventListener("click", () => postCleanup(true));
    cleanup?.addEventListener("click", () => postCleanup(false));
    document.getElementById("storage-retention-form")?.addEventListener("submit", saveRetention);
  }

  async function loadOne(path, assign) {
    try {
      assign(await api.request("GET", path));
    } catch (error) {
      state.errors.push(`${path}: ${error.message || "unavailable"}`);
    }
  }

  async function load() {
    state.errors = [];
    await loadOne("/storage/summary", (value) => { state.summary = value; });
    await loadOne("/storage/render-controls", (value) => { state.controls = value; });
    await loadOne("/storage/retention", (value) => { state.retention = value; });
    await loadOne("/storage/cleanup/history", (value) => { state.history = value; });
    paint();
  }

  async function postCleanup(dryRun) {
    const fileCount = (state.dryRun?.candidates || state.dryRun?.would_delete || []).length;
    const body = dryRun ? {} : { dry_run_id: state.dryRun?.run_id, confirm: cleanupConfirmPhrase(fileCount) };
    const result = await api.request("POST", dryRun ? "/storage/cleanup/dry-run" : "/storage/cleanup", { body });
    if (dryRun) state.dryRun = result;
    const slot = document.getElementById("storage-cleanup-result");
    if (slot) slot.innerHTML = renderCleanupResultHtml(result);
    if (dryRun) paint();
    else await load();
  }

  async function saveRetention(event) {
    event.preventDefault();
    const categories = {};
    document.querySelectorAll("[data-retention]").forEach((input) => {
      categories[input.dataset.retention] = input.value.trim().toLowerCase() === "preserve" ? null : Number(input.value);
    });
    state.retention = await api.request("PUT", "/storage/retention", { body: { categories } });
    paint();
  }

  load();
}

function moduleRowsOf(summary) {
  const rows = summary.by_module;
  if (Array.isArray(rows)) return rows;
  if (rows && typeof rows === "object") {
    return Object.entries(rows).map(([module, value]) => ({
      module,
      bytes: typeof value === "number" ? value : value?.bytes,
      count: value?.count,
    }));
  }
  return [];
}

function metric(label, value) {
  return `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`;
}

function controlValue(field) {
  if (!field || field.status === "NOT_CONFIGURED" || field.status === "NOT_IMPLEMENTED" || field.value == null && !field.status) {
    return field?.status ? `${field.status}${field.detail ? ` — ${field.detail}` : ""}` : "not reported";
  }
  if (typeof field.value === "number") return formatBytes(field.value).endsWith(" B") && field.unit !== "bytes"
    ? String(field.value)
    : (field.unit === "bytes" ? formatBytes(field.value) : String(field.value));
  return String(field.value);
}

function renderFileTable(rows) {
  if (!rows?.length) return `<p>No files were returned.</p>`;
  return `<div class="table-wrap"><table><thead><tr><th>Path</th><th>Module</th><th>Size</th><th>State</th></tr></thead><tbody>
    ${rows.map((row) => `<tr><td>${escapeHtml(row.path_relative || row.path || "path not reported")}</td><td>${escapeHtml(row.module || "")}</td><td>${escapeHtml(formatBytes(row.bytes))}</td><td>${escapeHtml(row.lifecycle_state || row.reason || "")}</td></tr>`).join("")}
  </tbody></table></div>`;
}
