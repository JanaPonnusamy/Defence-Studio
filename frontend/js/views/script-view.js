// Phase 3: source-grounded script generation, evidence/validation view,
// revision history, review feedback, and approval. All authority lives on
// the server; this view only disables actions the server would reject rather
// than merely warning after the fact.
import { escapeHtml as esc } from "../components/status-badge.js";
import { textFieldHtml } from "../components/text-field.js";
import { newIdempotencyKey } from "../api.js";
import { singleFlight } from "../components/single-flight.js";

const DEFAULT_CONFIG = {
  audience: "general public", tone: "neutral, measured", script_format: "narration",
  target_duration_seconds: 300, target_words: 1100, min_words: 900, max_words: 1300,
  model: "gpt-4o-mini",
  prompt_version: "defence-studio.source-grounded-script.v1", max_text_cost_inr: 10,
};

export async function mountScript({ api, jobId, list, detail, filters, isCurrent }) {
  list.innerHTML = '<p class="loading-state">Loading script revisions…</p>';
  detail.innerHTML = "";
  detail.classList.remove("detail-overlay");
  filters.innerHTML = "<p>Valid drafts are approved automatically after evidence and validation checks. Tamil and English are checked independently; no manual approval is required.</p>";
  let state = { language: "ta" };
  const generateOnce = singleFlight();
  let generationAttempt;
  const showError = (error) => { if (isCurrent()) list.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`; };

  // Evidence readiness is checked proactively (not only discovered after a
  // failed generate attempt) so the operator sees an honest "not ready yet"
  // state -- driven entirely by the real persisted package, same source of
  // truth the server itself re-checks before every generation call.
  const readinessHtml = (evidence) => {
    if (!evidence) return "";
    const approved = !!evidence.current_approval;
    if (approved) {
      return `<p class="inline-ok" role="status">Evidence package is approved and current -- generation is available.</p>`;
    }
    const blockers = (evidence.package && evidence.package.blockers) || [];
    return `<div class="inline-blocked" role="alert">
      <strong>Evidence not ready for script generation.</strong>
      <p>Evidence is approved automatically when its policy checks pass. Resolve the evidence requirements below, then reassess on the Evidence tab.</p>
      ${blockers.length ? `<ul>${blockers.slice(0, 5).map(b => `<li>${esc(typeof b === "string" ? b : JSON.stringify(b))}</li>`).join("")}</ul>` : ""}
    </div>`;
  };

  const render = async () => {
    try {
      const [result, evidence] = await Promise.all([
        api.listScriptRevisions(jobId),
        api.evidencePackage(jobId).catch(() => null),
      ]);
      if (!isCurrent()) return;
      const revisions = result.revisions || [];
      const evidenceReady = !!(evidence && evidence.current_approval);
      const staleness = await api.getScriptStaleness(jobId).catch(() => null);
      state.stale = new Map(((staleness && staleness.revisions) || [])
        .filter(r => r.state === "STALE").map(r => [`${r.language}:${r.version}`, r.reason]));
      const byLanguage = { ta: revisions.filter(r => r.language === "ta"), en: revisions.filter(r => r.language === "en") };
      list.innerHTML = `${renderStalenessBannerHtml(staleness)}<h2>Script generation</h2>
        ${readinessHtml(evidence)}
        <form id="script-generate-form">
          <fieldset><legend>Generate a draft</legend>
            <label>Language
              <select id="gen-language"><option value="ta">Tamil (primary narration)</option><option value="en">English</option></select>
            </label>
            ${textFieldHtml({ id: "gen-audience", label: "Audience", value: DEFAULT_CONFIG.audience })}
            ${textFieldHtml({ id: "gen-tone", label: "Tone", value: DEFAULT_CONFIG.tone })}
            ${textFieldHtml({ id: "gen-format", label: "Format", value: DEFAULT_CONFIG.script_format })}
            ${textFieldHtml({ id: "gen-words", label: "Target words", value: String(DEFAULT_CONFIG.target_words) })}
            ${textFieldHtml({ id: "gen-duration", label: "Target duration (seconds)", value: String(DEFAULT_CONFIG.target_duration_seconds) })}
            ${textFieldHtml({ id: "gen-model", label: "Model", value: DEFAULT_CONFIG.model })}
            ${textFieldHtml({ id: "gen-budget", label: "Max text-generation cost (INR)", value: String(DEFAULT_CONFIG.max_text_cost_inr) })}
            ${textFieldHtml({ id: "gen-terminology", label: "Preserve terms (comma-separated)", placeholder: "Rafale, AESA, DRDO" })}
            <button type="submit" id="gen-submit" ${evidenceReady ? "" : "disabled"}>Generate draft</button>
            ${evidenceReady ? "" : '<p class="meta">Generation becomes available when the evidence checks pass.</p>'}
          </fieldset>
        </form>
        <div id="script-gen-status" role="status"></div>
        <h3>Tamil revisions (${byLanguage.ta.length})</h3>
        ${renderRevisionList(byLanguage.ta, state.selected, state.stale)}
        <h3>English revisions (${byLanguage.en.length})</h3>
        ${renderRevisionList(byLanguage.en, state.selected, state.stale)}`;
      list.querySelectorAll("[data-open-revision]").forEach(btn => {
        btn.addEventListener("click", () => {
          const [language, version] = btn.dataset.openRevision.split(":");
          state.selected = { language, version: Number(version) };
          openRevision(state.selected);
        });
      });
      const form = list.querySelector("#script-generate-form");
      const statusEl = list.querySelector("#script-gen-status");
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitBtn = list.querySelector("#gen-submit");
        if (!evidenceReady) return;
        await generateOnce(submitBtn, async () => {
        statusEl.textContent = "Generating… this calls the configured text-generation provider and may take a moment.";
        try {
          const terms = list.querySelector("#gen-terminology").value.trim();
          const terminology = {};
          if (terms) terms.split(",").map(t => t.trim()).filter(Boolean).forEach(t => { terminology[t] = t; });
          const config = {
            language: list.querySelector("#gen-language").value,
            audience: list.querySelector("#gen-audience").value.trim(),
            tone: list.querySelector("#gen-tone").value.trim(),
            script_format: list.querySelector("#gen-format").value.trim(),
            target_words: parseInt(list.querySelector("#gen-words").value, 10),
            min_words: DEFAULT_CONFIG.min_words,
            max_words: DEFAULT_CONFIG.max_words,
            target_duration_seconds: parseInt(list.querySelector("#gen-duration").value, 10),
            model: list.querySelector("#gen-model").value.trim(),
            prompt_version: DEFAULT_CONFIG.prompt_version,
            max_text_cost_inr: parseFloat(list.querySelector("#gen-budget").value),
            terminology,
          };
          const signature = JSON.stringify(config);
          if (!generationAttempt || generationAttempt.signature !== signature) {
            generationAttempt = { signature, key: newIdempotencyKey() };
          }
          const revision = await api.generateScript(jobId, config, generationAttempt.key);
          generationAttempt = null; // Response acknowledged; next explicit attempt is new.
          if (!isCurrent()) return;
          state.selected = { language: revision.language, version: revision.version };
          state.generationMessage = generationOutcome(revision);
          await render();
        } catch (error) {
          if (isCurrent()) statusEl.textContent = error.message;
        }
        });
      });
      if (state.generationMessage) statusEl.textContent = state.generationMessage;
      if (state.selected) await openRevision(state.selected);
      else clearDetail();
    } catch (error) { if (isCurrent()) { list.innerHTML = ""; showError(error); } }
  };

  function clearDetail() {
    detail.classList.remove("detail-overlay");
    detail.innerHTML = '<div class="empty-state">Select a revision to inspect its narration, evidence mapping, and validation.</div>';
  }

  async function openRevision({ language, version }) {
    detail.classList.add("detail-overlay");
    detail.innerHTML = '<div class="loading-state">Loading revision…</div>';
    try {
      const [revision, history] = await Promise.all([
        api.getScriptRevision(jobId, language, version),
        api.getScriptRevisionHistory(jobId, language, version),
      ]);
      if (!isCurrent()) return;
      detail.innerHTML = renderRevisionDetail(revision, history);
      wireRevisionActions(revision);
      loadAnalysisPanels(revision);
    } catch (error) {
      if (isCurrent()) detail.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`;
    }
  }

  async function loadAnalysisPanels(revision) {
    const slot = detail.querySelector("#script-analysis-panels");
    if (!slot) return;
    try {
      const repetition = await api.getScriptRepetition(jobId, revision.language, revision.version);
      const older = (await api.listScriptRevisions(jobId, revision.language)).revisions
        .filter(r => r.version < revision.version).map(r => r.version);
      const diff = older.length
        ? await api.compareScriptRevisions(jobId, revision.language, Math.max(...older), revision.version) : null;
      if (!isCurrent()) return;
      slot.innerHTML = renderRepetitionHtml(repetition) + renderComparisonHtml(diff);
    } catch (error) {
      if (isCurrent()) slot.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`;
    }
  }

  function wireRevisionActions(revision) {
    const statusEl = detail.querySelector("#revision-action-status");
    const changesBtn = detail.querySelector("#request-changes-btn");
    if (changesBtn) changesBtn.addEventListener("click", async () => {
      const note = detail.querySelector("#review-note").value.trim();
      if (!note) { statusEl.textContent = "Enter a note describing the requested changes."; return; }
      try {
        await api.requestScriptChanges(jobId, revision.language, revision.version, note, newIdempotencyKey());
        await openRevision({ language: revision.language, version: revision.version });
      } catch (error) { if (isCurrent()) statusEl.textContent = error.message; }
    });
    const exportBtn = detail.querySelector("#export-script-btn");
    if (exportBtn) exportBtn.addEventListener("click", async () => {
      try {
        const body = await api.exportApprovedScript(jobId, revision.language);
        const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], { type: "application/json" }));
        const link = document.createElement("a"); link.href = url; link.download = `script-${revision.language}-approved.json`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { if (isCurrent()) statusEl.textContent = error.message; }
    });
  }

  await render();
}

export function generationOutcome(revision) {
  if (revision.status === "APPROVED" && revision.validation?.passed && revision.approval) {
    return revision.approval.approval_kind === "AUTOMATED_VALIDATION"
      ? `Draft saved and automatically approved: version ${revision.version}.`
      : `Approved script saved: version ${revision.version}.`;
  }
  return revision.status === "AWAITING_REVIEW" && revision.validation?.passed
    ? `Draft saved: version ${revision.version}. Generate a new revision to apply automatic approval.`
    : `Generation failed validation (version ${revision.version}, ${revision.status}). Saved for inspection; this is not an accepted draft.`;
}

export function renderStalenessBannerHtml(staleness) {
  if (!staleness) return "";
  const stale = (staleness.revisions || []).filter(r => r.state === "STALE");
  if (!stale.length) return "";
  return `<div class="banner warn" role="status">${stale.length} script revision(s) no longer match the current approved
    evidence (${esc(stale[0].reason)}). Reassess the evidence and generate a new revision before export.</div>`;
}

export function renderRepetitionHtml(report) {
  if (!report) return "";
  if (!report.matches.length) {
    return `<p class="meta">No repeated wording found against ${Number(report.compared_against)} other script(s).</p>`;
  }
  return `<div class="banner warn"><strong>Repeated wording</strong><ul>${report.matches.map(m =>
    `<li>${esc(m.topic || m.job_id)} v${m.version}: ${Math.round(m.overlap_ratio * 100)}% shared phrases</li>`).join("")}</ul></div>`;
}

export function renderComparisonHtml(diff) {
  if (!diff) return '<p class="meta">No earlier revision to compare with.</p>';
  return `<details><summary>Changes since v${diff.old_version}</summary>
    <ul><li>${Math.round(diff.overlap_ratio * 100)}% phrase overlap, ${diff.word_delta >= 0 ? "+" : ""}${diff.word_delta} words</li>
    <li>Sections added: ${diff.sections_added.map(esc).join(", ") || "none"}; removed: ${diff.sections_removed.map(esc).join(", ") || "none"};
      changed: ${diff.sections_changed.map(esc).join(", ") || "none"}</li>
    <li>Claims added: ${diff.claims_added.map(esc).join(", ") || "none"}; removed: ${diff.claims_removed.map(esc).join(", ") || "none"}</li>
    ${diff.package_changed ? "<li>Built on different evidence than the earlier revision.</li>" : ""}</ul></details>`;
}

const KIND_LABELS = {
  VERIFIED_FACT: "Verified fact (corroborated)", ATTRIBUTED_CLAIM: "Attributed claim",
  ANALYSIS: "Analysis", UNRESOLVED: "Unresolved",
};

function renderRevisionList(revisions, selected, stale = new Map()) {
  if (!revisions.length) return '<p class="meta">No revisions generated yet.</p>';
  return `<ul class="revision-list">${revisions.map(r => `
    <li>
      <button type="button" data-open-revision="${esc(r.language)}:${r.version}"
        class="${selected && selected.language === r.language && selected.version === r.version ? "selected" : ""}">
        v${r.version} · ${esc(r.status)}${stale.has(`${r.language}:${r.version}`) ? " · STALE" : ""} · ${esc(r.created_at)}
      </button>
    </li>`).join("")}</ul>`;
}

export function renderRevisionDetail(revision, history) {
  const canReview = revision.status === "AWAITING_REVIEW";
  return `<h2>${esc(revision.title)}</h2>
    <p class="meta">${esc(revision.language)} · version ${revision.version} · status ${esc(revision.status)}</p>
    <p>${revision.word_count.words} words (estimated narration ${Math.round(revision.word_count.estimated_duration_seconds / 60 * 10) / 10} min at ${revision.word_count.words_per_minute} wpm -- an estimate, not an exact recording time)</p>
    <h3>Validation</h3>
    <p>${revision.validation.passed ? "Passed" : "Failed"} (${revision.validation.validator_version})</p>
    ${revision.validation.issues.length ? `<ul>${revision.validation.issues.map(i =>
      `<li>${esc(i.severity)}: ${esc(i.detail)}${i.section_id ? ` (section ${esc(i.section_id)})` : ""}</li>`).join("")}</ul>` : "<p>No issues.</p>"}
    <h3>Usage and cost</h3>
    <p>Provider: ${esc(revision.usage.provider)} · Model: ${esc(revision.usage.model)} · Retries: ${revision.usage.retries}</p>
    ${revision.usage.provider === "offline-extractive" ? `<p class="meta">Offline extractive draft. Each narration section repeats approved claim text. Recorded token usage: none.</p>` : ""}
    <p>${revision.usage.cost_is_measured
      ? `Measured cost: ₹${revision.usage.measured_text_cost_inr} (${esc(revision.usage.pricing_source)})`
      : "Measured cost unavailable for this call."}</p>
    ${revision.opening_hook ? `<h3>Opening hook</h3><p>${esc(revision.opening_hook)}</p>` : ""}
    <div id="script-analysis-panels"><p class="loading-state">Checking repeated wording…</p></div>
    <h3>Narration sections</h3>
    ${revision.sections.map(s => `
      <article class="script-section">
        <h4>${esc(s.heading || s.section_id)}</h4>
        <p class="meta"><span class="chip">${esc(KIND_LABELS[s.statement_kind] || "Epistemic status not declared")}</span></p>
        <p>${esc(s.narration)}</p>
        ${s.presentation_note ? `<p class="meta">Presentation note: ${esc(s.presentation_note)}</p>` : ""}
        <details><summary>Evidence mapping</summary>
          <p>Claims: ${(s.evidence.claim_ids || []).length ? s.evidence.claim_ids.map((id) => `<a href="#/claims/${encodeURIComponent(id)}">${esc(id)}</a>`).join(", ") : "None"}</p>
          <p>Sources: ${(s.evidence.source_ids || []).length ? s.evidence.source_ids.map((id) => `<a href="#/sources/${encodeURIComponent(id)}">${esc(id)}</a>`).join(", ") : "None recorded"}</p>
          <p>Qualification: ${esc(s.evidence.qualification || "None recorded")}</p>
        </details>
      </article>`).join("")}
    <h3>Closing</h3><p>${esc(revision.closing)}</p>
    <h3>Approval</h3>
    <p>Draft approval is automatic when validation and current evidence checks pass.</p>
    ${canReview ? `<p>This older draft has not been automatically approved. Generate a new revision to apply the current checks.</p>
      ${textFieldHtml({ id: "review-note", label: "Optional feedback", multiline: true, maxLength: 4000 })}
      <button type="button" id="request-changes-btn">Request changes</button>` : ""}
    <button type="button" id="export-script-btn" ${revision.status === "APPROVED" ? "" : "disabled"}>Export approved script</button>
    <div id="revision-action-status" role="status"></div>
    <details><summary>Review history (${history.length})</summary>
      ${history.map(h => `<p>${esc(h.action)} · ${esc(h.occurred_at)} · ${esc(h.reviewer_id)}<br>${esc(h.note)}</p>`).join("") || "<p>No review actions yet.</p>"}
    </details>
    ${revision.approval ? `<p class="meta">${revision.approval.approval_kind === "AUTOMATED_VALIDATION" ? "Automatically approved after validation" : `Approved by ${esc(revision.approval.reviewer_id)}`} at ${esc(revision.approval.approved_at)}</p>` : ""}`;
}
