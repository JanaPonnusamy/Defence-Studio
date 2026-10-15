// Fact-check workspace. Every action calls the backend; nothing here invents a verdict.
import { escapeHtml as esc } from "../components/status-badge.js";
import { textFieldHtml } from "../components/text-field.js";
import { confirmDialog } from "../components/confirm-dialog.js";
import { newIdempotencyKey } from "../api.js";
import { singleFlight } from "../components/single-flight.js";

// Readiness vocabulary shown to the operator; falls back to availability only when the backend sent none.
export function providerLineHtml(row) {
  const readiness = row.readiness || (row.available ? "READY" : "NOT_CONFIGURED");
  return `<li data-readiness="${esc(readiness)}"><strong>${esc(row.capability)}</strong> — ${esc(row.provider)} · <span class="chip">${esc(readiness)}</span> · ${esc(row.execution)} · ${esc(row.cost)}<br>${esc(row.dependency)}</li>`;
}

export { singleFlight };

export async function mountFactcheck({ api, jobId, list, detail, filters, isCurrent }) {
  list.innerHTML = '<p class="loading-state">Loading fact-check package…</p>';
  detail.innerHTML = "";
  filters.innerHTML = "<p>Factual support, source trust, media context, and reuse rights stay separate.</p>";
  const once = singleFlight();
  // Errors go to a dedicated alert region so the package detail (note field, approve button) is never wiped.
  const showError = (error) => {
    if (!isCurrent()) return;
    const box = list.querySelector("#fc-alert");
    if (box) { box.textContent = error.message; box.hidden = false; return; }
    detail.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`;
  };
  const clearError = () => {
    const box = list.querySelector("#fc-alert");
    if (box) { box.textContent = ""; box.hidden = true; }
  };
  let providers = [];
  let current = null;
  try {
    providers = (await api.factcheckProviders()).providers || [];
    current = await api.factcheckPackage(jobId);
  } catch (error) {
    if (isCurrent()) list.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`;
    return;
  }
  if (!isCurrent()) return;
  render(current);

  function render(body) {
    current = body;
    const pkg = body.package;
    const claims = pkg ? (pkg.machine.claims || []) : [];
    list.innerHTML = `<h2>Fact check</h2>
      <p>${pkg ? `<strong>${esc(pkg.status)}</strong> · version ${pkg.version}` : "No package yet. Run claim verification, then build a package."}</p>
      <h3>Claims</h3>
      ${claims.length ? `<ul>${claims.map((claim) => `<li><button type="button" data-claim="${esc(claim.claim_id)}">${esc(claim.claim_id)}</button> ${esc(claim.verdict)} — ${esc(claim.normalized_text)}</li>`).join("")}</ul>` : "<p>No claims in the latest package.</p>"}
      <h3>Run</h3>
      ${textFieldHtml({ id: "fc-claim", label: "Claim text", multiline: true, maxLength: 2000 })}
      ${textFieldHtml({ id: "fc-support", label: "Supporting excerpt", multiline: true, maxLength: 2000 })}
      <button type="button" id="fc-run">Verify claim</button>
      <button type="button" id="fc-package" ${claims.length || pkg ? "" : ""}>Build evidence package</button>
      <h3>Media</h3>
      <input id="fc-file" type="file" accept="image/*,video/*">
      ${textFieldHtml({ id: "fc-location", label: "Claimed location", maxLength: 200 })}
      ${textFieldHtml({ id: "fc-date", label: "Claimed event date", maxLength: 40 })}
      <button type="button" id="fc-media">Verify media</button>
      <div id="fc-alert" class="error-state" role="alert" hidden></div>
      <div id="fc-status" role="status"></div>`;
    detail.innerHTML = packageDetail(pkg, providers);
    list.querySelectorAll("[data-claim]").forEach((button) => {
      button.addEventListener("click", () => showClaim(claims.find((row) => row.claim_id === button.dataset.claim), pkg));
    });
    const guarded = (fn) => (event) => { clearError(); return once(event.currentTarget, fn); };
    list.querySelector("#fc-run").addEventListener("click", guarded(runClaims));
    list.querySelector("#fc-package").addEventListener("click", guarded(runPackage));
    list.querySelector("#fc-media").addEventListener("click", guarded(runMedia));
    const approve = detail.querySelector("#fc-approve");
    if (approve) approve.addEventListener("click", guarded(approvePackage));
    const exportJson = detail.querySelector("#fc-export");
    if (exportJson) exportJson.addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `factcheck-${pkg.package_id}.json`;
      link.click();
    });
  }

  function packageDetail(pkg, rows) {
    if (!pkg) {
      return `<h2>Providers</h2><ul>${rows.map(providerLine).join("")}</ul>`;
    }
    return `<h2>Evidence package v${pkg.version}</h2>
      <p>${esc(pkg.status)} · <code>${esc(pkg.content_sha256)}</code></p>
      <p>Usable after approval: ${esc((pkg.approved && pkg.approved.usable_claim_ids || []).join(", ") || "none yet")}</p>
      ${textFieldHtml({ id: "fc-note", label: "Approval note", multiline: true, maxLength: 4000 })}
      <button type="button" id="fc-approve" ${pkg.status === "PENDING_REVIEW" ? "" : "disabled"}>Approve this package</button>
      <button type="button" id="fc-export">Export JSON</button>
      <h3>Report</h3><pre>${esc(pkg.report_markdown)}</pre>
      <h3>Review history</h3><div id="fc-history">Loading history…</div>
      <h3>Providers</h3><ul>${rows.map(providerLine).join("")}</ul>`;
    api.factcheckHistory(jobId).then((hist) => {
      if (!isCurrent()) return;
      const box = detail.querySelector("#fc-history");
      if (!box) return;
      const items = hist.history || [];
      box.innerHTML = items.length
        ? items.map((row) => `<p>${esc(row.occurred_at)} · ${esc(row.action)} · ${esc(row.reviewer_id)}<br>${esc(row.note)}</p>`).join("")
        : "<p>No human decisions yet.</p>";
    }).catch((error) => showError(error));
  }

  function providerLine(row) {
    return providerLineHtml(row);
  }

  function showClaim(claim, pkg) {
    if (!claim) return;
    const factors = claim.confidence ? Object.entries(claim.confidence.factors || {}).map(([name, value]) => `<li>${esc(name)}: ${esc(String(value))}</li>`).join("") : "";
    detail.innerHTML = `<h2>${esc(claim.claim_id)}</h2>
      <p><strong>${esc(claim.verdict)}</strong> · provisional ${esc(claim.provisional_verdict)} · falsity ${esc(claim.falsity_assessment)}</p>
      <p>${esc(claim.normalized_text)}</p>
      <p>Event date ${esc(claim.event_date || "unknown")} · published ${esc(claim.published_at || "unknown")}</p>
      <h3>Confidence factors</h3><ul>${factors}</ul>
      <p>Score ${esc(String(claim.confidence && claim.confidence.score))} is not a calibrated probability.</p>
      <h3>Supporting</h3>${evidenceList(claim.supporting)}
      <h3>Contradicting</h3>${evidenceList(claim.contradicting)}
      <h3>Missing</h3>${(claim.missing || []).map((item) => `<p>${esc(item)}</p>`).join("") || "<p>None recorded.</p>"}
      ${textFieldHtml({ id: "fc-override-note", label: "Correction note", multiline: true, maxLength: 4000 })}
      <button type="button" id="fc-override">Record human correction</button>`;
    detail.querySelector("#fc-override").addEventListener("click", (event) => { clearError(); return once(event.currentTarget, async () => {
      const note = detail.querySelector("#fc-override-note").value.trim();
      if (!note || !pkg) { showError(new Error("A package and a note are required.")); return; }
      const ok = await confirmDialog({ title: "Correct verdict", message: "This keeps the machine verdict and records your correction.", confirmLabel: "Save correction" });
      if (!ok) return;
      try {
        const updated = await api.factcheckOverride(jobId, {
          package_id: pkg.package_id, claim_id: claim.claim_id, verdict: "NEEDS_REVIEW", note, accepted_for_script: false,
        });
        if (isCurrent()) render(updated);
      } catch (error) { showError(error); }
    }); });
  }

  function evidenceList(rows) {
    if (!rows || !rows.length) return "<p>None.</p>";
    return rows.map((row) => `<p>${esc(row.relationship)} · ${esc(row.publisher || row.source_id)} · group ${esc(row.independence_group || "")}<br>${esc(row.url || "no URL")} · published ${esc(row.published_at || "unknown")} · retrieved ${esc(row.retrieved_at || row.retrieved_at_status || "NOT_COLLECTED")}<br>${esc(row.excerpt || "")}</p>`).join("");
  }

  async function runClaims() {
    const text = list.querySelector("#fc-claim").value.trim();
    const support = list.querySelector("#fc-support").value.trim();
    if (!text) { showError(new Error("Enter a claim.")); return; }
    list.querySelector("#fc-status").textContent = "Verifying…";
    try {
      const result = await api.factcheckClaims(jobId, {
        claims: [{ claim_id: "ui-claim", text, source_id: "origin", category: "equipment", entities: [], extraction_confidence: 0.9 }],
        sources: [
          { source_id: "origin", publisher: "Submitted text", excerpt: text, category: "unknown", primary: false },
          ...(support ? [{ source_id: "support-1", publisher: "Submitted excerpt", excerpt: support, category: "news", primary: true, url: "https://example.test/support", named_attribution: true, published_at: new Date().toISOString() }] : []),
        ],
        collection: { attempted: true, errors: [] },
      }, newIdempotencyKey());
      list.querySelector("#fc-status").textContent = result.result ? `Verdict ${result.result.verdicts[0].verdict}` : result.status;
    } catch (error) { showError(error); }
  }

  async function runPackage() {
    const ok = await confirmDialog({ title: "Build package", message: "This versions a new package from the latest stored checks.", confirmLabel: "Build" });
    if (!ok) return;
    try {
      await api.factcheckBuildPackage(jobId, "Research topic", newIdempotencyKey());
      const refreshed = await api.factcheckPackage(jobId);
      if (isCurrent()) render(refreshed);
    } catch (error) { showError(error); }
  }

  async function runMedia() {
    const file = list.querySelector("#fc-file").files[0];
    if (!file) { showError(new Error("Choose an image or video.")); return; }
    const form = new FormData();
    form.append("file", file);
    form.append("claim_json", JSON.stringify({
      location: list.querySelector("#fc-location").value.trim(),
      event_date: list.querySelector("#fc-date").value.trim(),
      subject: file.name,
    }));
    list.querySelector("#fc-status").textContent = "Checking media…";
    try {
      const result = await api.factcheckMedia(jobId, form, newIdempotencyKey());
      const media = result.result;
      detail.innerHTML = `<h2>Media</h2>
        <p><strong>${esc(media.context_status)}</strong> · authenticity ${esc(media.authenticity_status)} · rights ${esc(media.rights.status)}</p>
        <p>SHA-256 ${esc(media.sha256)}</p>
        <p>EXIF ${esc(media.exif_datetime || "absent")} (${esc(media.exif_trust)})</p>
        <ul>${(media.reasons || []).map((reason) => `<li>${esc(reason)}</li>`).join("")}</ul>
        <p>Reverse search: ${esc(media.reverse_search.reason)}</p>`;
      list.querySelector("#fc-status").textContent = media.context_status;
    } catch (error) { showError(error); }
  }

  async function approvePackage() {
    const note = detail.querySelector("#fc-note").value.trim();
    if (!note || !current.package) { showError(new Error("An approval note is required.")); return; }
    const ok = await confirmDialog({ title: "Approve evidence", message: "This records your decision. It does not rerun the checks.", confirmLabel: "Approve" });
    if (!ok) return;
    try {
      await api.factcheckApprove(jobId, current.package.package_id, note);
      current = await api.factcheckPackage(jobId);
      if (isCurrent()) render(current);
    } catch (error) { showError(error); }
  }
}
