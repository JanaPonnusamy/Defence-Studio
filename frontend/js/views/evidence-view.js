// Evidence timeline and immutable package approval. All authority lives on the server.
import { escapeHtml as esc } from "../components/status-badge.js";
import { textFieldHtml } from "../components/text-field.js";
import { confirmDialog } from "../components/confirm-dialog.js";
import { newIdempotencyKey } from "../api.js";

export async function mountEvidence({ api, jobId, list, detail, filters, isCurrent }) {
  list.innerHTML = '<p class="loading-state">Loading evidence package…</p>';
  detail.innerHTML = "";
  detail.classList.remove("detail-overlay");
  filters.innerHTML = '<p>Review approval permits use; it does not establish truth.</p>';
  const showError = (error) => {
    if (isCurrent()) detail.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`;
  };
  try {
    const result = await api.evidencePackage(jobId);
    if (!isCurrent()) return;
    const pkg = result.package;
    list.innerHTML = `<h2>Evidence package</h2>
      <p><strong>${result.current_approval ? "Approved current revision" : pkg.ready ? "Ready for human package approval" : "Draft — not approved"}</strong></p>
      <p class="meta">Revision ${esc(result.revision)}</p>
      <p>${pkg.sources.length} sources · ${pkg.claims.length} claims · ${pkg.eligible_claim_ids.length} eligible claims</p>
      <h3>Required review</h3>${pkg.blockers.length ? `<ul>${pkg.blockers.map(b => `<li>${esc(b)}</li>`).join("")}</ul>` : '<p>No blocking issues in current revision.</p>'}
      <details><summary>Limitations and missing evidence</summary><ul>${pkg.limitations.map(b => `<li>${esc(b)}</li>`).join("")}</ul></details>
      <details><summary>Conflicts (${pkg.conflicts.length})</summary>${pkg.conflicts.map(c => `<p>${esc(c.explanation)}</p><div class="conflict-pair">${[c.claim_id_a,c.claim_id_b].map(id => {
        const claim = pkg.claims.find(x => x.claim_id === id);
        return `<div><a href="#/claims/${encodeURIComponent(id)}">${esc(id)}</a><p>${esc(claim?.claim_text || "Missing claim")}</p><p>${esc(claim?.source_id || "")} · ${esc(claim?.review?.decision || "PENDING")}</p></div>`;
      }).join("")}</div>`).join("")}</details>
      ${textFieldHtml({id:"package-note",label:"Package approval explanation",multiline:true,maxLength:4000})}
      <button type="button" id="approve-package" ${!pkg.ready || result.current_approval ? "disabled" : ""}>Approve this revision</button>
      <button type="button" id="export-approved" ${!result.current_approval ? "disabled" : ""}>Export approved JSON</button>
      <button type="button" id="export-draft">Download unapproved draft</button>
      <div id="package-action-status" role="status"></div>
      <details><summary>Package approval history (${result.approvals.length})</summary>${result.approvals.map(a => `<p>Version ${a.version} · ${esc(a.created_at)} · ${esc(a.reviewer_id)}<br>${esc(a.note)}<br>${a.revision === result.revision ? "Current" : "Stale for current evidence"} · ${esc(a.sha256)}</p>`).join("")}</details>`;
    detail.innerHTML = `<h2>Evidence timeline</h2><p>Publication, acquisition, analysis and review times remain distinct. Unknown dates are listed separately.</p>
      <form id="timeline-filters">${[
        ["start","From (ISO time with timezone)"],["end","Through (ISO time with timezone)"],
        ["source_id","Source ID"],["claim_id","Claim ID"],["language","Language (ta/en)"]
      ].map(([id,label]) => textFieldHtml({id:`timeline-${id}`,label})).join("")}
      <button type="submit">Filter timeline</button></form><div id="timeline-items"></div>`;
    const timelineMount = detail.querySelector("#timeline-items");
    let timelineSequence = 0;
    const renderTimeline = async (criteria = {}) => {
      const sequence = ++timelineSequence;
      timelineMount.innerHTML = '<p class="loading-state">Loading timeline…</p>';
      try {
        const timeline = await api.evidenceTimeline(jobId, criteria);
        if (!isCurrent() || sequence !== timelineSequence) return;
        timelineMount.innerHTML = `<p class="meta">${esc(timeline.note)}</p>${timeline.items.length ? timeline.items.map(item => `<details class="timeline-event"><summary>${esc(item.kind)} · ${esc(item.date.original || "Date unavailable")} · ${esc(item.date.precision)}</summary>
          <p>UTC: ${esc(item.date.utc || "Unknown")} · Language: ${esc(item.language || "Unknown")}</p>
          ${item.source_id ? `<a href="#/sources/${encodeURIComponent(item.source_id)}">Inspect source ${esc(item.source_id)}</a>` : ""}
          ${item.claim_id ? `<a href="#/claims/${encodeURIComponent(item.claim_id)}">Inspect claim and review</a>` : ""}
          <p>${esc(item.original_url || "Original URL unavailable")}</p>
          ${item.passage ? `<blockquote>${esc(item.passage.text)}</blockquote><p>Offsets ${item.passage.start_offset}–${item.passage.end_offset}</p>` : ""}
          ${item.review ? `<p>${esc(item.review.decision)} · ${esc(item.review.note)}</p>` : ""}</details>`).join("") : '<p>No matching timeline records.</p>'}`;
      } catch (error) { if (isCurrent()) timelineMount.innerHTML = `<p role="alert">${esc(error.message)}</p>`; }
    };
    detail.querySelector("form").addEventListener("submit", event => {
      event.preventDefault();
      renderTimeline(Object.fromEntries(["start","end","source_id","claim_id","language"].map(k => [k,detail.querySelector(`#timeline-${k}`).value.trim()])));
    });
    const actionStatus = list.querySelector("#package-action-status");
    list.querySelector("#approve-package").addEventListener("click", async event => {
      const note = list.querySelector("#package-note").value.trim();
      if (!note) { actionStatus.textContent = "Enter an approval explanation."; return; }
      if (!await confirmDialog({title:"Approve evidence package",message:"Approve this exact revision, including its disclosed limitations and excluded evidence?"})) return;
      if (!isCurrent()) return;
      event.target.disabled = true;
      try {
        await api.approveEvidence(jobId, result.revision, note, newIdempotencyKey());
        if (isCurrent()) await mountEvidence({api,jobId,list,detail,filters,isCurrent});
      } catch (error) { if (isCurrent()) { actionStatus.textContent = error.message + " Reload the Evidence tab before retrying."; event.target.disabled = false; } }
    });
    for (const [id, draft] of [["export-approved",false],["export-draft",true]]) {
      list.querySelector(`#${id}`).addEventListener("click", async () => {
        try {
          const body = await api.exportEvidence(jobId, draft);
          if (!isCurrent()) return;
          const url = URL.createObjectURL(new Blob([JSON.stringify(body,null,2)],{type:"application/json"}));
          const link = document.createElement("a"); link.href = url; link.download = `evidence-${draft ? "UNAPPROVED" : "approved"}.json`; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          actionStatus.textContent = draft ? "Unapproved draft downloaded." : "Approved package downloaded.";
        } catch (error) { if (isCurrent()) actionStatus.textContent = error.message; }
      });
    }
    await renderTimeline();
  } catch (error) { if (isCurrent()) list.innerHTML = ""; showError(error); }
}
