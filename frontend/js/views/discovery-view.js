// Phase 1A feed discovery view: submit a request, browse ranked candidates
// with provenance/ranking explanations, select some, and confirm the
// selection before it is handed to F3 acquisition. Reuses the existing
// text-field/toast primitives; no new UI primitive is introduced here.

import { textFieldHtml } from "../components/text-field.js";
import { escapeHtml } from "../components/status-badge.js";

export function renderDiscoveryRequestFormHtml(defaultTopic) {
  return `
    <div class="empty-state">
      <p>No discovery has run for this job yet. Submit a topic to start Phase 1A feed discovery.</p>
      <form id="discovery-request-form">
        ${textFieldHtml({ id: "discovery-topic", label: "Topic", value: defaultTopic || "", placeholder: "e.g. Rafale deal" })}
        ${textFieldHtml({ id: "discovery-language", label: "Language", value: "en", placeholder: "en or ta" })}
        ${textFieldHtml({ id: "discovery-terms", label: "Search terms (comma-separated)", value: "", placeholder: "optional" })}
        <button type="submit" class="primary">Submit discovery request</button>
      </form>
      <p class="meta">After submitting, queue the job so the worker can run discovery.</p>
      <button type="button" id="discovery-queue-btn">Queue job</button>
    </div>`;
}

export function renderProviderOutcomesHtml(providers) {
  if (!providers || !providers.length) return "";
  return `<ul class="provider-outcomes">${providers.map((p) => `
    <li class="chip" title="${escapeHtml(p.message)}">${escapeHtml(p.provider)}: ${escapeHtml(p.status)}</li>
  `).join("")}</ul>`;
}

export function renderDiscoveryCandidatesListHtml(candidates, selectedIds) {
  if (!candidates || !candidates.length) {
    return `<div class="empty-state">No candidates matched the current filters.</div>`;
  }
  return `<ul class="candidate-list">${candidates.map((c) => renderCandidateCard(c, selectedIds)).join("")}</ul>`;
}

function renderCandidateCard(candidate, selectedIds) {
  const meta = candidate.declared_metadata || {};
  const ranking = candidate.ranking || {};
  const checked = selectedIds && selectedIds.has(candidate.candidate_id) ? "checked" : "";
  return `
    <li class="candidate-card" data-candidate-id="${escapeHtml(candidate.candidate_id)}">
      <label class="inline">
        <input type="checkbox" data-select-candidate="${escapeHtml(candidate.candidate_id)}" ${checked}>
        <strong>${escapeHtml(meta.title || "(no title collected)")}</strong>
      </label>
      <div class="meta">${escapeHtml(meta.publisher || "unknown publisher")}
        &middot; ${escapeHtml(candidate.provider)}
        &middot; score ${Number(ranking.total_score || 0).toFixed(2)}
        &middot; <a href="${escapeHtml(candidate.canonical_url)}" target="_blank" rel="noopener">source</a></div>
      <div class="meta">${tagsHtml(candidate)}</div>
      ${candidate.snippet ? `<p class="snippet">${escapeHtml(candidate.snippet)}</p>` : ""}
      <div class="meta">Published ${escapeHtml(meta.published_at || "date not declared")}
        &middot; retrieved ${escapeHtml(candidate.retrieved_at || candidate.discovered_at || "unknown")}</div>
      ${(candidate.filter_flags || []).length
        ? `<div class="meta warn">Filter could not be verified: ${candidate.filter_flags.map(flagLabel).map(escapeHtml).join(", ")}</div>` : ""}
      <details>
        <summary>Why this ranking</summary>
        <ul>${(ranking.explanation || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
      </details>
      ${(candidate.merged_from_providers || []).length
        ? `<div class="meta">Also seen from: ${candidate.merged_from_providers.map(escapeHtml).join(", ")}</div>` : ""}
    </li>`;
}

export function renderSelectionSummaryHtml(selection) {
  if (!selection || !selection.selected_candidate_ids || !selection.selected_candidate_ids.length) {
    return `<div class="meta">No candidates confirmed yet.</div>`;
  }
  return `<div class="meta">${selection.selected_candidate_ids.length} candidate(s) confirmed for acquisition.</div>`;
}

const FLAG_LABELS = {
  category_filter_unverifiable: "category unknown",
  region_filter_unverifiable: "region unknown",
  source_class_filter_unverifiable: "source class unknown",
  date_filter_unverifiable: "publication date not declared",
};
function flagLabel(flag) { return FLAG_LABELS[flag] || flag; }

function prettyEnum(value) {
  return String(value || "").toLowerCase().split("_").filter(Boolean).join(" ");
}

function tagsHtml(candidate) {
  const tags = [];
  tags.push(`category: ${prettyEnum(candidate.category || "UNCLASSIFIED")}`);
  (candidate.regions || []).forEach((r) => tags.push(`region: ${prettyEnum(r)}`));
  tags.push(`source: ${prettyEnum(candidate.source_class || "UNKNOWN")}`);
  return tags.map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join(" ");
}

/** Filter bar for the search results. `taxonomy` comes from /api/discovery/taxonomy. */
export function renderDiscoveryFilterFormHtml(taxonomy, filters) {
  const tx = taxonomy || { categories: [], regions: [], source_classes: [] };
  const select = (id, label, values, current) => `
    <label for="${id}">${label}
      <select id="${id}"><option value="">Any</option>${values.map((v) =>
        `<option value="${escapeHtml(v)}"${v === current ? " selected" : ""}>${escapeHtml(prettyEnum(v))}</option>`).join("")}
      </select></label>`;
  return `
    ${select("discovery-filter-category", "Category", tx.categories, filters.category)}
    ${select("discovery-filter-region", "Region", tx.regions, filters.region)}
    ${select("discovery-filter-source-class", "Source class", tx.source_classes, filters.sourceClass)}
    <label for="discovery-filter-date-from">From
      <input type="date" id="discovery-filter-date-from" value="${escapeHtml(filters.dateFrom || "")}"></label>
    <label for="discovery-filter-date-to">To
      <input type="date" id="discovery-filter-date-to" value="${escapeHtml(filters.dateTo || "")}"></label>`;
}

/** Provider coverage / failure strip: what ran, what failed and why. Never hides a failure. */
export function renderCoverageHtml(result) {
  const outcomes = (result && result.providers) || [];
  if (!outcomes.length) return "";
  const failed = outcomes.filter((p) => ["ERROR", "PARTIAL", "UNAVAILABLE_MISSING_CREDENTIALS",
    "UNAVAILABLE_NOT_CONFIGURED", "UNAVAILABLE_OPTIONAL_DEPENDENCY_MISSING"].includes(p.status));
  const allUnavailable = outcomes.every((p) => String(p.status).startsWith("UNAVAILABLE") || p.status === "ERROR");
  const describe = (p) => `${escapeHtml(p.provider)} (${escapeHtml(p.status)}): ${escapeHtml(p.message)}`;
  const banner = allUnavailable
    ? `<div class="banner error">No provider could run for this search. ${failed.map(describe).join(" &middot; ")}</div>`
    : failed.length
      ? `<div class="banner warn">Some providers did not fully run; results are partial. ${failed.map(describe).join(" &middot; ")}</div>`
      : "";
  const rows = outcomes.map((p) => `<li class="chip" title="${escapeHtml(p.message)}">
    ${escapeHtml(p.provider)}: ${escapeHtml(p.status)} &middot; ${Number(p.candidates_returned || 0)} result(s)
    ${p.duration_ms ? ` &middot; ${Number(p.duration_ms)} ms` : ""}${p.rate_limited ? " &middot; rate limited" : ""}</li>`).join("");
  const note = (result && result.coverage_note) ? `<p class="meta">${escapeHtml(result.coverage_note)}</p>` : "";
  return `${banner}<ul class="provider-outcomes">${rows}</ul>${note}`;
}

export function renderEmptyResultsHtml(result) {
  const n = ((result && result.providers) || []).length;
  return `<div class="empty-state">No results from ${n} configured provider${n === 1 ? "" : "s"} for the current filters. `
    + `This does not mean no coverage exists elsewhere.</div>`;
}

export function renderPaginationHtml(result) {
  if (!result) return "";
  const shown = (result.candidates || []).length;
  const total = Number(result.total_count || shown);
  const more = result.next_cursor
    ? `<button type="button" id="discovery-load-more" data-cursor="${escapeHtml(result.next_cursor)}">Load more</button>` : "";
  return `<div class="meta">Showing ${shown} of ${total}.</div>${more}`;
}

/** Past searches (derived from persisted jobs). Reopen shows the stored result; rerun creates a new run. */
export function renderSearchHistoryHtml(searches) {
  if (!searches || !searches.length) return `<div class="meta">No previous searches yet.</div>`;
  return `<ul class="search-history">${searches.map((s) => `
    <li class="search-run" data-job-id="${escapeHtml(s.job_id)}">
      <strong>${escapeHtml(s.topic)}</strong>
      <span class="chip">${escapeHtml(s.status)}</span>
      <span class="meta">${s.candidate_count == null ? "" : `${Number(s.candidate_count)} candidate(s) &middot; `}${escapeHtml(s.created_at || "")}</span>
      <button type="button" data-reopen-search="${escapeHtml(s.job_id)}">Reopen</button>
      <button type="button" data-rerun-search="${escapeHtml(s.job_id)}">Run again</button>
    </li>`).join("")}</ul>`;
}
