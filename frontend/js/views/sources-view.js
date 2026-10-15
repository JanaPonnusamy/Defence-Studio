import { escapeHtml, statusBadgeHtml } from "../components/status-badge.js";

function safeLink(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function renderUrl(url) {
  const href = safeLink(url);
  if (!href) return `<span class="meta">${escapeHtml(url || "(no URL recorded)")}</span>`;
  return `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(href)}</a>`;
}

export function renderSourcesList(sources, selectedId, emptyMessage) {
  if (!sources || sources.length === 0) {
    const message = emptyMessage || "No sources match the current filter.";
    return `<div class="empty-state">${escapeHtml(message)}</div>`;
  }
  return sources.map((source) => {
    const title = source.declared_metadata?.title || source.canonical_url || source.source_id;
    const selected = source.source_id === selectedId ? " selected" : "";
    return `
      <div class="card clickable${selected}" data-source-id="${escapeHtml(source.source_id)}" role="button" tabindex="0">
        <h3>${escapeHtml(title)}</h3>
        <p class="meta">${escapeHtml(source.declared_metadata?.publisher || "Publisher unknown")}
          ${source.status === "ACQUIRED" && !source.normalized ? " · text not extracted" : ""}
          ${source.normalized && source.normalized.status && source.normalized.status !== "NORMALIZED"
            ? ` · ${escapeHtml(source.normalized.status)}` : ""}
          &middot; ${escapeHtml(source.declared_metadata?.language || "language unknown")}</p>
        <div class="chip-row">
          ${statusBadgeHtml(source.status)}
          ${statusBadgeHtml(source.review_status, { stale: source.is_stale })}
        </div>
      </div>`;
  }).join("");
}

export function renderSourceDetail(source) {
  if (!source) return `<div class="empty-state">Select a source to inspect it.</div>`;
  const dm = source.declared_metadata || {};
  const field = (label, value, status) => `
    <p><strong>${escapeHtml(label)}:</strong> ${value ? escapeHtml(value) : `<span class="meta">(${escapeHtml(status)})</span>`}</p>`;

  const normalized = source.normalized;
  const normalizationBlock = normalized
    ? `<h4>Normalization (F4)</h4>
       ${statusBadgeHtml(normalized.status)}
       <p class="meta">Language detected: ${escapeHtml(normalized.language_detected)} &middot;
         ${normalized.normalized_char_count} characters</p>
       ${normalized.warnings && normalized.warnings.length
          ? `<div class="chip-row">${normalized.warnings.map((w) => `<span class="chip">${escapeHtml(w)}</span>`).join("")}</div>` : ""}`
    : `<p class="meta">This source has not been through F4 analysis yet.</p>`;

  const duplicateBlock = (source.duplicate_groups || []).length
    ? `<h4>Duplicate relationships</h4>
       ${source.duplicate_groups.map((g) => `
         <p class="meta">Basis: ${escapeHtml(g.basis)} &middot; ${g.member_source_ids.length} related source(s).
         Textual relationship only -- not independent verification. ${escapeHtml(g.explanation || "")}</p>`).join("")}`
    : "";

  const errorBlock = source.error
    ? `<p class="error-state">Acquisition error: ${escapeHtml(source.error.message)} (${escapeHtml(source.error.code)})</p>`
    : "";
  const warningsBlock = (source.warnings || []).length
    ? `<div class="chip-row">${source.warnings.map((w) => `<span class="chip">${escapeHtml(w)}</span>`).join("")}</div>`
    : "";

  return `
    <h2>${escapeHtml(dm.title || source.canonical_url || source.source_id)}</h2>
    <div class="chip-row">${statusBadgeHtml(source.status)} ${statusBadgeHtml(source.review_status, { stale: source.is_stale })}</div>
    <p>${renderUrl(source.canonical_url)}</p>
    ${source.final_url && source.final_url !== source.canonical_url ? `<p class="meta">Redirected to: ${renderUrl(source.final_url)}</p>` : ""}
    ${field("Publisher", dm.publisher, dm.publisher_status)}
    ${field("Published", dm.published_at, dm.published_at_status)}
    ${field("Language", dm.language, dm.language_status)}
    <p class="meta">Acquired: ${escapeHtml(source.acquisition_timestamp)} via ${escapeHtml(source.method)}</p>
    ${errorBlock}
    ${warningsBlock}
    ${normalizationBlock}
    ${duplicateBlock}
    <div style="margin-top:0.6rem;">
      <button type="button" data-action="view-claims">View claims from this source</button>
    </div>
    <h4>Review decision</h4>
    <div id="decision-panel-mount"></div>
    <h4>History</h4>
    <div id="history-mount"></div>`;
}
