// Phase 4 M1: scene planning view. Generates a deterministic scene plan from
// the currently-approved script revision, lists/edits draft plan versions,
// and reviews/approves a specific plan hash. All authority lives on the
// server; this view only disables actions the server would reject rather
// than merely warning after the fact, and surfaces the same stale/blocked
// states the server enforces (unapproved script, re-sync required after the
// bound script revision changed, superseded plan).
import { escapeHtml as esc } from "../components/status-badge.js";
import { textFieldHtml } from "../components/text-field.js";
import { confirmDialog } from "../components/confirm-dialog.js";
import { newIdempotencyKey } from "../api.js";

const DEFAULT_CONFIG = {
  words_per_minute: 150, default_transition: "cut", target_total_seconds: 300,
};

export async function mountScene({ api, jobId, list, detail, filters, isCurrent }) {
  list.innerHTML = '<p class="loading-state">Loading scene plans…</p>';
  detail.innerHTML = "";
  detail.classList.remove("detail-overlay");
  filters.innerHTML = "<p>Approving one language never approves the other. Generation requires a currently approved script revision.</p>";
  let state = { language: "ta" };
  const showError = (error) => { if (isCurrent()) list.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`; };

  // Script readiness is checked proactively (not only discovered after a
  // failed generate attempt), driven by the authoritative /script/staleness
  // result -- the same freshness check the server re-runs (and fails closed
  // with 409 on) before every scene-plan generate/approve call. A revision
  // that is APPROVED but STALE (the evidence package changed after it was
  // approved) must not be treated as ready: an APPROVED status row alone
  // does not mean the server will currently accept it.
  const isReady = (staleByKey, scriptRevisions, language) => {
    const approved = (scriptRevisions || []).find(r => r.language === language && r.status === "APPROVED");
    if (!approved) return { ready: false, reason: "" };
    const current = staleByKey.get(`${language}:${approved.version}`);
    if (current && current.state === "STALE") return { ready: false, reason: current.reason };
    return { ready: true, reason: "" };
  };

  const readinessHtml = (staleByKey, scriptRevisions, language) => {
    const { ready, reason } = isReady(staleByKey, scriptRevisions, language);
    if (ready) {
      return `<p class="inline-ok" role="status">An approved ${language === "ta" ? "Tamil" : "English"} script revision is current -- scene generation is available.</p>`;
    }
    if (reason) {
      return `<div class="inline-blocked" role="alert">
        <strong>The approved ${language === "ta" ? "Tamil" : "English"} script revision is no longer current.</strong>
        <p>${esc(reason)} Re-approve the script on the Script tab before generating a scene plan.</p>
      </div>`;
    }
    return `<div class="inline-blocked" role="alert">
      <strong>No approved ${language === "ta" ? "Tamil" : "English"} script revision.</strong>
      <p>A script revision must be human-approved on the Script tab before a scene plan can be generated for this language.</p>
    </div>`;
  };

  const render = async () => {
    try {
      const [result, scriptResult, staleness] = await Promise.all([
        api.listScenePlans(jobId),
        api.listScriptRevisions(jobId).catch(() => ({ revisions: [] })),
        api.getScriptStaleness(jobId).catch(() => null),
      ]);
      if (!isCurrent()) return;
      const plans = result.plans || [];
      const scriptRevisions = scriptResult.revisions || [];
      const staleByKey = new Map(((staleness && staleness.revisions) || [])
        .map(r => [`${r.language}:${r.version}`, r]));
      const byLanguage = { ta: plans.filter(p => p.language === "ta"), en: plans.filter(p => p.language === "en") };
      list.innerHTML = `<h2>Scene planning</h2>
        <form id="scene-generate-form">
          <fieldset><legend>Generate a scene plan</legend>
            <label>Language
              <select id="scene-gen-language">
                <option value="ta" ${state.language === "ta" ? "selected" : ""}>Tamil (primary narration)</option>
                <option value="en" ${state.language === "en" ? "selected" : ""}>English</option>
              </select>
            </label>
            <div id="scene-gen-readiness">${readinessHtml(staleByKey, scriptRevisions, state.language)}</div>
            ${textFieldHtml({ id: "scene-gen-wpm", label: "Words per minute", value: String(DEFAULT_CONFIG.words_per_minute) })}
            ${textFieldHtml({ id: "scene-gen-transition", label: "Default transition", value: DEFAULT_CONFIG.default_transition })}
            ${textFieldHtml({ id: "scene-gen-duration", label: "Target total duration (seconds)", value: String(DEFAULT_CONFIG.target_total_seconds) })}
            <button type="submit" id="scene-gen-submit" ${isReady(staleByKey, scriptRevisions, state.language).ready ? "" : "disabled"}>Generate plan</button>
          </fieldset>
        </form>
        <div id="scene-gen-status" role="status"></div>
        <h3>Tamil plans (${byLanguage.ta.length})</h3>
        ${renderPlanList(byLanguage.ta, state.selected)}
        <h3>English plans (${byLanguage.en.length})</h3>
        ${renderPlanList(byLanguage.en, state.selected)}`;
      list.querySelectorAll("[data-open-plan]").forEach(btn => {
        btn.addEventListener("click", () => {
          const [language, version] = btn.dataset.openPlan.split(":");
          state.selected = { language, version: Number(version) };
          openPlan(state.selected);
        });
      });
      const languageSelect = list.querySelector("#scene-gen-language");
      languageSelect.addEventListener("change", () => {
        state.language = languageSelect.value;
        list.querySelector("#scene-gen-readiness").innerHTML = readinessHtml(staleByKey, scriptRevisions, state.language);
        list.querySelector("#scene-gen-submit").disabled = !isReady(staleByKey, scriptRevisions, state.language).ready;
      });
      const form = list.querySelector("#scene-generate-form");
      const statusEl = list.querySelector("#scene-gen-status");
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitBtn = list.querySelector("#scene-gen-submit");
        submitBtn.disabled = true;
        statusEl.textContent = "Generating scene plan from the approved script…";
        try {
          const config = {
            language: languageSelect.value,
            words_per_minute: parseInt(list.querySelector("#scene-gen-wpm").value, 10),
            default_transition: list.querySelector("#scene-gen-transition").value.trim(),
            target_total_seconds: parseInt(list.querySelector("#scene-gen-duration").value, 10),
          };
          const plan = await api.generateScenePlan(jobId, config, newIdempotencyKey());
          if (!isCurrent()) return;
          statusEl.textContent = `Scene plan generated: version ${plan.version}, status ${plan.status}.`;
          await render();
        } catch (error) {
          if (!isCurrent()) return;
          statusEl.textContent = error.message;
          // A 409 means the server's own freshness check just rejected this
          // attempt (e.g. the script was re-approved or went stale between
          // page load and submit) -- the cached scriptRevisions/staleByKey
          // snapshot used to enable this button is now known wrong. Re-sync
          // from the server rather than re-enabling from stale local state.
          if (error && error.status === 409) await render();
          else submitBtn.disabled = !isReady(staleByKey, scriptRevisions, languageSelect.value).ready;
        }
      });
      if (state.selected) await openPlan(state.selected);
      else clearDetail();
    } catch (error) { if (isCurrent()) { list.innerHTML = ""; showError(error); } }
  };

  function clearDetail() {
    detail.classList.remove("detail-overlay");
    detail.innerHTML = '<div class="empty-state">Select a scene plan to inspect its scenes, timing, and review history.</div>';
  }

  async function openPlan({ language, version }) {
    detail.classList.add("detail-overlay");
    detail.innerHTML = '<div class="loading-state">Loading scene plan…</div>';
    try {
      const [plan, history] = await Promise.all([
        api.getScenePlan(jobId, language, version),
        api.getScenePlanHistory(jobId, language, version),
      ]);
      if (!isCurrent()) return;
      detail.innerHTML = renderPlanDetail(plan, history);
      wirePlanActions(plan);
    } catch (error) {
      if (isCurrent()) detail.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`;
    }
  }

  function wirePlanActions(plan) {
    const statusEl = detail.querySelector("#plan-action-status");
    const changesBtn = detail.querySelector("#scene-request-changes-btn");
    if (changesBtn) changesBtn.addEventListener("click", async () => {
      const note = detail.querySelector("#scene-review-note").value.trim();
      if (!note) { statusEl.textContent = "Enter a note describing the requested changes."; return; }
      try {
        await api.requestScenePlanChanges(jobId, plan.language, plan.version, note, newIdempotencyKey());
        await openPlan({ language: plan.language, version: plan.version });
      } catch (error) { if (isCurrent()) statusEl.textContent = error.message; }
    });
    const approveBtn = detail.querySelector("#scene-approve-btn");
    if (approveBtn) approveBtn.addEventListener("click", async () => {
      const note = detail.querySelector("#scene-review-note").value.trim();
      if (!note) { statusEl.textContent = "Enter an approval explanation."; return; }
      if (!await confirmDialog({
        title: "Approve scene plan",
        message: `Approve this exact ${plan.language === "ta" ? "Tamil" : "English"} scene plan (version ${plan.version})? This does not approve the other language.`,
      })) return;
      try {
        const planSha = (await api.listScenePlans(jobId, plan.language)).plans
          .find(p => p.version === plan.version).plan_sha256;
        await api.approveScenePlan(jobId, plan.language, plan.version, planSha, note, newIdempotencyKey());
        await openPlan({ language: plan.language, version: plan.version });
        await render();
      } catch (error) { if (isCurrent()) statusEl.textContent = error.message; }
    });
  }

  await render();
}

function statusClass(status) {
  if (status === "APPROVED") return "inline-ok";
  if (status === "SUPERSEDED" || status === "CHANGES_REQUESTED") return "inline-blocked";
  return "meta";
}

function renderPlanList(plans, selected) {
  if (!plans.length) return '<p class="meta">No scene plans generated yet.</p>';
  return `<ul class="revision-list">${plans.map(p => `
    <li>
      <button type="button" data-open-plan="${esc(p.language)}:${p.version}"
        class="${selected && selected.language === p.language && selected.version === p.version ? "selected" : ""}">
        v${p.version} · ${esc(p.status)} · ${esc(p.created_at)}
      </button>
    </li>`).join("")}</ul>`;
}

function renderPlanDetail(plan, history) {
  const canReview = plan.status === "AWAITING_REVIEW" || plan.status === "DRAFT";
  const canApprove = plan.status !== "APPROVED" && plan.status !== "SUPERSEDED";
  const stateLabel = {
    DRAFT: "Draft — not yet submitted for review.",
    AWAITING_REVIEW: "Awaiting review.",
    CHANGES_REQUESTED: "Changes requested — revise and resubmit.",
    APPROVED: "Approved and current.",
    SUPERSEDED: "Superseded by a later approved plan — read-only.",
  }[plan.status] || plan.status;
  return `<h2>Scene plan</h2>
    <p class="meta">${esc(plan.language)} · version ${plan.version} · <span class="${statusClass(plan.status)}">${esc(stateLabel)}</span></p>
    <p class="meta">Built from script revision ${esc((plan.script_revision_sha256 || "").slice(0, 12))}… (script v${plan.script_version})</p>
    <h3>Scenes (${plan.scenes.length})</h3>
    ${plan.scenes.map(s => `
      <article class="script-section">
        <h4>Scene ${esc(s.scene_id)} — ${esc(s.purpose || s.source_section_id)}</h4>
        <p>${esc(s.narration)}</p>
        <p class="meta">Estimated duration: ${Math.round((s.planned_timing?.duration_seconds || 0) * 10) / 10}s (estimate, not measured) · Transition in: ${esc(s.transition_in || "—")}</p>
        <details><summary>Evidence</summary>
          <p>Claims: ${s.claim_ids.length ? s.claim_ids.map(esc).join(", ") : "None"}</p>
        </details>
      </article>`).join("")}
    <h3>Review</h3>
    ${textFieldHtml({ id: "scene-review-note", label: "Review note / approval explanation", multiline: true, maxLength: 4000 })}
    <button type="button" id="scene-request-changes-btn" ${canReview ? "" : "disabled"}>Request changes</button>
    <button type="button" id="scene-approve-btn" ${canApprove ? "" : "disabled"}>Approve this plan</button>
    <div id="plan-action-status" role="status"></div>
    <details><summary>Review history (${history.length})</summary>
      ${history.map(h => `<p>${esc(h.action)} · ${esc(h.occurred_at)} · ${esc(h.reviewer_id)}<br>${esc(h.note)}</p>`).join("") || "<p>No review actions yet.</p>"}
    </details>`;
}
