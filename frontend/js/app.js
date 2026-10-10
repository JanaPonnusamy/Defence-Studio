import { createApiClient, ApiError } from "./api.js";
import { loadSession, saveToken, saveJobId } from "./state.js";
import { textFieldHtml } from "./components/text-field.js";
import { showToast } from "./components/toast.js";
import { renderSourcesList, renderSourceDetail } from "./views/sources-view.js";
import { renderClaimsList, renderClaimDetail } from "./views/claims-view.js";
import { renderDecisionPanelHtml, wireDecisionPanel, renderHistoryHtml } from "./views/decision-panel.js";

const session = loadSession();
const state = {
  token: session.token,
  jobId: session.jobId,
  tab: "sources", // "sources" | "claims"
  sourceStatus: "",
  claimStatus: "",
  claimConflictOnly: false,
  claimSourceFilter: "",
  selectedSourceId: null,
  selectedClaimId: null,
  sourcesResult: null,
  claimsResult: null,
};

const api = createApiClient(() => state.token);

const el = {
  topbar: document.getElementById("topbar"),
  tabs: document.getElementById("tabs"),
  filters: document.getElementById("filters"),
  list: document.getElementById("list-panel"),
  detail: document.getElementById("detail-panel"),
};

function parseHash() {
  const parts = (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "claims") {
    state.tab = "claims";
    state.selectedClaimId = parts[1] ? decodeURIComponent(parts[1]) : null;
  } else {
    state.tab = "sources";
    state.selectedSourceId = parts[1] ? decodeURIComponent(parts[1]) : null;
  }
}

function setHash(tab, id) {
  location.hash = id ? `#/${tab}/${encodeURIComponent(id)}` : `#/${tab}`;
}

function renderTopbar() {
  el.topbar.innerHTML = `
    ${textFieldHtml({ id: "job-id-field", label: "Job ID", value: state.jobId, placeholder: "paste a workflow job_id" })}
    ${textFieldHtml({ id: "token-field", label: "Bearer token", value: state.token, type: "password", placeholder: "signed HS256 token" })}
    <button type="button" id="load-job-btn" class="primary">Load</button>
    <span id="job-status" class="meta" aria-live="polite"></span>`;
  document.getElementById("job-id-field").addEventListener("change", (e) => { state.jobId = e.target.value.trim(); });
  document.getElementById("token-field").addEventListener("change", (e) => { state.token = e.target.value.trim(); });
  document.getElementById("load-job-btn").addEventListener("click", () => {
    saveToken(state.token);
    saveJobId(state.jobId);
    refreshActiveTab();
  });
}

function renderTabs() {
  el.tabs.innerHTML = `
    <button type="button" role="tab" aria-selected="${state.tab === "sources"}" data-tab="sources">Sources</button>
    <button type="button" role="tab" aria-selected="${state.tab === "claims"}" data-tab="claims">Claims</button>`;
  el.tabs.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setHash(btn.dataset.tab, null);
    });
  });
}

function renderFilters() {
  const statusOptions = ["", "PENDING", "APPROVED_FOR_USE", "REJECTED", "NEEDS_MORE_EVIDENCE", "CONFLICT_UNRESOLVED"];
  const optionHtml = (value) => `<option value="${value}">${value || "All statuses"}</option>`;
  if (state.tab === "sources") {
    el.filters.innerHTML = `
      <label for="source-status-filter">Status
        <select id="source-status-filter">${statusOptions.map(optionHtml).join("")}</select>
      </label>`;
    const sel = el.filters.querySelector("#source-status-filter");
    sel.value = state.sourceStatus;
    sel.addEventListener("change", (e) => { state.sourceStatus = e.target.value; loadSources(); });
  } else {
    el.filters.innerHTML = `
      <label for="claim-status-filter">Status
        <select id="claim-status-filter">${statusOptions.map(optionHtml).join("")}</select>
      </label>
      <label class="inline"><input type="checkbox" id="claim-conflict-filter"> Conflicts only</label>
      ${state.claimSourceFilter ? `<span class="chip">Source: ${state.claimSourceFilter}
        <button type="button" id="clear-source-filter" style="min-height:auto;padding:0 0.3rem;">x</button></span>` : ""}`;
    const sel = el.filters.querySelector("#claim-status-filter");
    sel.value = state.claimStatus;
    sel.addEventListener("change", (e) => { state.claimStatus = e.target.value; loadClaims(); });
    const chk = el.filters.querySelector("#claim-conflict-filter");
    chk.checked = state.claimConflictOnly;
    chk.addEventListener("change", (e) => { state.claimConflictOnly = e.target.checked; loadClaims(); });
    const clearBtn = el.filters.querySelector("#clear-source-filter");
    if (clearBtn) clearBtn.addEventListener("click", () => { state.claimSourceFilter = ""; loadClaims(); });
  }
}

function requireJob() {
  if (!state.jobId || !state.token) {
    el.list.innerHTML = `<div class="empty-state">Enter a Job ID and bearer token above, then press Load.</div>`;
    el.detail.innerHTML = "";
    el.detail.classList.remove("detail-overlay");
    return false;
  }
  return true;
}

async function loadSources() {
  if (!requireJob()) return;
  el.list.innerHTML = `<div class="loading-state">Loading sources…</div>`;
  try {
    state.sourcesResult = await api.listSources(state.jobId, state.sourceStatus || undefined);
    el.list.innerHTML = renderSourcesList(state.sourcesResult.sources, state.selectedSourceId);
    wireSourceCards();
    if (state.selectedSourceId) await loadSourceDetail(state.selectedSourceId);
    else clearDetail();
  } catch (err) {
    el.list.innerHTML = errorHtml(err);
  }
}

function wireSourceCards() {
  el.list.querySelectorAll("[data-source-id]").forEach((card) => {
    const activate = () => setHash("sources", card.dataset.sourceId);
    card.addEventListener("click", activate);
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } });
  });
}

async function loadSourceDetail(sourceId) {
  el.detail.classList.add("detail-overlay");
  el.detail.innerHTML = `<button type="button" class="back-button" id="back-btn">&larr; Back</button><div class="loading-state">Loading…</div>`;
  wireBackButton();
  try {
    const [source, history] = await Promise.all([
      api.getSource(state.jobId, sourceId),
      api.sourceHistory(state.jobId, sourceId),
    ]);
    el.detail.innerHTML = `<button type="button" class="back-button" id="back-btn">&larr; Back</button>${renderSourceDetail(source)}`;
    wireBackButton();
    const viewClaimsBtn = el.detail.querySelector('[data-action="view-claims"]');
    if (viewClaimsBtn) viewClaimsBtn.addEventListener("click", () => {
      state.claimSourceFilter = sourceId;
      setHash("claims", null);
    });
    mountDecisionPanel({
      kind: "source", subjectId: sourceId,
      review: source.review, isStale: source.is_stale,
      reviewedArtifactSha256: state.sourcesResult.acquisition_artifact_sha256,
      history,
      onDecide: async (decision, note, expectedRevision, sha, idemKey) => {
        await api.decideSource(state.jobId, sourceId, {
          decision, note, expected_revision: expectedRevision, reviewed_artifact_sha256: sha,
        }, idemKey);
        await loadSources();
      },
    });
  } catch (err) {
    el.detail.innerHTML = `<button type="button" class="back-button" id="back-btn">&larr; Back</button>${errorHtml(err)}`;
    wireBackButton();
  }
}

async function loadClaims() {
  if (!requireJob()) return;
  el.list.innerHTML = `<div class="loading-state">Loading claims…</div>`;
  try {
    state.claimsResult = await api.listClaims(state.jobId, {
      status: state.claimStatus || undefined,
      conflict: state.claimConflictOnly || undefined,
      sourceId: state.claimSourceFilter || undefined,
    });
    el.list.innerHTML = renderClaimsList(state.claimsResult.claims, state.selectedClaimId);
    wireClaimCards();
    if (state.selectedClaimId) await loadClaimDetail(state.selectedClaimId);
    else clearDetail();
  } catch (err) {
    el.list.innerHTML = errorHtml(err);
  }
}

function wireClaimCards() {
  el.list.querySelectorAll("[data-claim-id]").forEach((card) => {
    const activate = () => setHash("claims", card.dataset.claimId);
    card.addEventListener("click", activate);
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } });
  });
}

async function resolveConflictPairs(claim) {
  if (!claim.has_conflict || !state.claimsResult) return [];
  const conflicts = (state.claimsResult.conflicts || []).filter(
    (c) => c.claim_id_a === claim.claim_id || c.claim_id_b === claim.claim_id,
  );
  const pairs = [];
  for (const conflict of conflicts) {
    const otherId = conflict.claim_id_a === claim.claim_id ? conflict.claim_id_b : conflict.claim_id_a;
    let other = (state.claimsResult.claims || []).find((c) => c.claim_id === otherId) || null;
    if (!other) {
      try { other = await api.getClaim(state.jobId, otherId); } catch { other = null; }
    }
    pairs.push({ conflict, other });
  }
  return pairs;
}

async function loadClaimDetail(claimId) {
  el.detail.classList.add("detail-overlay");
  el.detail.innerHTML = `<button type="button" class="back-button" id="back-btn">&larr; Back</button><div class="loading-state">Loading…</div>`;
  wireBackButton();
  try {
    const [claim, history] = await Promise.all([
      api.getClaim(state.jobId, claimId),
      api.claimHistory(state.jobId, claimId),
    ]);
    const conflictPairs = await resolveConflictPairs(claim);
    el.detail.innerHTML = `<button type="button" class="back-button" id="back-btn">&larr; Back</button>${renderClaimDetail(claim, conflictPairs)}`;
    wireBackButton();
    const viewSourceBtn = el.detail.querySelector('[data-action="view-source"]');
    if (viewSourceBtn) viewSourceBtn.addEventListener("click", () => setHash("sources", viewSourceBtn.dataset.sourceId));
    mountDecisionPanel({
      kind: "claim", subjectId: claimId,
      review: claim.review, isStale: claim.is_stale,
      reviewedArtifactSha256: state.claimsResult.analysis_artifact_sha256,
      history,
      onDecide: async (decision, note, expectedRevision, sha, idemKey) => {
        await api.decideClaim(state.jobId, claimId, {
          decision, note, expected_revision: expectedRevision, reviewed_artifact_sha256: sha,
        }, idemKey);
        await loadClaims();
      },
    });
  } catch (err) {
    el.detail.innerHTML = `<button type="button" class="back-button" id="back-btn">&larr; Back</button>${errorHtml(err)}`;
    wireBackButton();
  }
}

function mountDecisionPanel({ review, isStale, reviewedArtifactSha256, history, onDecide }) {
  const mount = el.detail.querySelector("#decision-panel-mount");
  const historyMount = el.detail.querySelector("#history-mount");
  if (!mount) return;
  mount.innerHTML = renderDecisionPanelHtml(review, isStale);
  historyMount.innerHTML = renderHistoryHtml(history);
  const expectedRevision = review ? review.revision : 0;
  wireDecisionPanel(mount, {
    expectedRevision,
    reviewedArtifactSha256,
    disabled: !reviewedArtifactSha256,
    onDecide: async (decision, note, rev, sha, idemKey) => {
      try {
        await onDecide(decision, note, rev, sha, idemKey);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // Reload the current item so the reviewer sees the latest revision
          // before deciding again, instead of silently retrying blind.
          if (state.tab === "sources") await loadSourceDetail(state.selectedSourceId);
          else await loadClaimDetail(state.selectedClaimId);
        }
        throw err;
      }
    },
  });
}

function wireBackButton() {
  const btn = el.detail.querySelector("#back-btn");
  if (btn) btn.addEventListener("click", () => setHash(state.tab, null));
}

function clearDetail() {
  el.detail.classList.remove("detail-overlay");
  el.detail.innerHTML = state.tab === "sources"
    ? `<div class="empty-state">Select a source to inspect it.</div>`
    : `<div class="empty-state">Select a claim to inspect it.</div>`;
}

function errorHtml(err) {
  const message = err instanceof ApiError ? err.detail : (err && err.message) || "Unexpected error.";
  return `<div class="error-state">${message}</div>`;
}

function refreshActiveTab() {
  renderTabs();
  renderFilters();
  if (state.tab === "sources") loadSources();
  else loadClaims();
}

function route() {
  parseHash();
  renderTabs();
  renderFilters();
  if (state.tab === "sources") loadSources();
  else loadClaims();
}

window.addEventListener("hashchange", route);
renderTopbar();
if (!location.hash) setHash("sources", null);
else route();
