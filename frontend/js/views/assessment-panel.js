import { escapeHtml as esc } from "../components/status-badge.js";

export function renderAssessmentHtml(value) {
  if (!value) return '<p class="meta">Automated assessment pending. Manual decisions are optional exceptions.</p>';
  return `<section aria-label="Automated evidence assessment"><strong>${esc(value.outcome || value.assessment_outcome || value.state || "ASSESSMENT_FAILED")}</strong>
    <p>${esc(value.evidence?.assessment_basis || value.assessment_basis || "Policy assessment; not certification of factual truth.")}</p>
    <ul>${(value.reasons || []).map(r => `<li>${esc(r)}</li>`).join("")}</ul>
    <p class="meta">${esc(value.assessor_version || "")}  |  ${esc(value.created_at || "")}  |  Analysis ${esc(value.analysis_sha256 || "")}</p></section>`;
}
