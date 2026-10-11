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
