import { escapeHtml, statusBadgeHtml } from "../components/status-badge.js";

function excerpt(text, max = 160) {
  const t = String(text || "");
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

export function renderClaimsList(claims, selectedId) {
  if (!claims || claims.length === 0) {
    return `<div class="empty-state">No claims match the current filter.</div>`;
  }
  return claims.map((claim) => {
    const selected = claim.claim_id === selectedId ? " selected" : "";
    return `
      <div class="card clickable${selected}" data-claim-id="${escapeHtml(claim.claim_id)}" role="button" tabindex="0">
        <p>${escapeHtml(excerpt(claim.claim_text))}</p>
        <p class="meta">Source: ${escapeHtml(claim.source_id)}</p>
        <div class="chip-row">
          ${statusBadgeHtml(claim.review_status, { stale: claim.is_stale })}
          ${claim.has_conflict ? `<span class="badge CONFLICT_UNRESOLVED">Conflicting evidence</span>` : ""}
          ${claim.evidence_class ? `<span class="chip">${escapeHtml(claim.evidence_class)}</span>` : ""}
          ${claim.has_negation ? `<span class="chip">Negation</span>` : ""}
        </div>
      </div>`;
  }).join("");
}

export function renderClaimDetail(claim, conflictPairs) {
  if (!claim) return `<div class="empty-state">Select a claim to inspect it.</div>`;
  const entities = claim.entities || {};
  const chip = (label, values) => (values && values.length
    ? `<div class="chip-row"><span class="meta">${escapeHtml(label)}:</span> ${values.map((v) => `<span class="chip">${escapeHtml(v)}</span>`).join("")}</div>`
    : "");

  const conflictBlock = (conflictPairs || []).length
    ? `<h4>Conflicting evidence</h4>
       ${conflictPairs.map(({ conflict, other }) => `
         <p class="meta">Basis: ${escapeHtml(conflict.basis)} -- flagged for review, not auto-resolved.</p>
         <div class="conflict-pair">
           <div class="side"><strong>This claim</strong><p>${escapeHtml(claim.claim_text)}</p></div>
           <div class="side"><strong>Conflicting claim (source ${escapeHtml(other ? other.source_id : conflict.claim_id_b)})</strong>
             <p>${other ? escapeHtml(other.claim_text) : "<span class=\"meta\">Could not be loaded.</span>"}</p></div>
         </div>`).join("")}`
    : "";

  return `
    <h2>Claim</h2>
    <div class="chip-row">${statusBadgeHtml(claim.review_status, { stale: claim.is_stale })}
      ${claim.has_conflict ? `<span class="badge CONFLICT_UNRESOLVED">Conflicting evidence</span>` : ""}
      ${claim.evidence_class ? `<span class="chip">${escapeHtml(claim.evidence_class)}</span>` : ""}</div>
    <div class="passage">${escapeHtml(claim.claim_text)}</div>
    <p class="meta">Exact normalized-text offsets: ${escapeHtml(String(claim.passage?.start_offset ?? "unknown"))}–${escapeHtml(String(claim.passage?.end_offset ?? "unknown"))}<br>
      Supporting artifact: ${escapeHtml(claim.passage?.artifact_uri || "unavailable")}</p>
    <p class="meta">Verbatim span from source <button type="button" data-action="view-source" data-source-id="${escapeHtml(claim.source_id)}" style="display:inline;padding:0.1rem 0.5rem;min-height:auto;">${escapeHtml(claim.source_id)}</button>
      &middot; published ${escapeHtml(claim.source_published_at || "unknown")}
      &middot; acquired ${escapeHtml(claim.acquisition_timestamp || "unknown")}</p>
    ${claim.has_negation ? `<p class="chip">Contains a negation -- preserve polarity when reviewing.</p>` : ""}
    ${chip("Qualifiers / attribution", claim.qualifiers)}
    ${chip("Named", entities.named)}
    ${chip("Quantities", entities.quantities)}
    ${chip("Dates", entities.dates)}
    ${chip("Designations", entities.designations)}
    ${(claim.warnings || []).length ? `<div class="chip-row">${claim.warnings.map((w) => `<span class="chip">${escapeHtml(w)}</span>`).join("")}</div>` : ""}
    ${conflictBlock}
    <h4>Review decision</h4>
    <div id="decision-panel-mount"></div>
    <h4>History</h4>
    <div id="history-mount"></div>`;
}
