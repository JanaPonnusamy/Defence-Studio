import { createApiClient, ApiError, newIdempotencyKey } from "./api.js";
import { escapeHtml } from "./components/status-badge.js";
import { icon } from "./components/icons.js";
import { mountEvidence } from "./views/evidence-view.js";
import { mountScript } from "./views/script-view.js";
import { mountScene } from "./views/scene-view.js";
import { mountProduction } from "./views/production-view.js";
import { loadSession, saveToken, saveJobId } from "./state.js";
import { textFieldHtml } from "./components/text-field.js";
import { showToast } from "./components/toast.js";
import { renderSourcesList, renderSourceDetail } from "./views/sources-view.js";
import { renderClaimsList, renderClaimDetail } from "./views/claims-view.js";
import { renderDecisionPanelHtml, wireDecisionPanel, renderHistoryHtml } from "./views/decision-panel.js";
import { renderDashboardHtml } from "./views/dashboard-view.js";
import { renderLoginHtml } from "./views/login-view.js";
import {
  renderDiscoveryRequestFormHtml, renderDiscoveryCandidatesListHtml, renderSelectionSummaryHtml,
  renderDiscoveryFilterFormHtml, renderCoverageHtml, renderEmptyResultsHtml, renderPaginationHtml,
  renderSearchHistoryHtml,
} from "./views/discovery-view.js";

const session = loadSession();
const state = {
  token: session.token,
  jobId: session.jobId,
  tab: "dashboard", // "dashboard" | "sources" | "claims" | "discovery" | "evidence" | "script" | "scene"
  jobsResult: null,
  sourceStatus: "",
  claimStatus: "",
  claimConflictOnly: false,
  claimSourceFilter: "",
  selectedSourceId: null,
  selectedClaimId: null,
  sourcesResult: null,
  claimsResult: null,
  discoveryResult: null,
  discoverySelection: null,
  discoveryFilters: { language: "", sourceType: "", provider: "", query: "", category: "", region: "",
    sourceClass: "", dateFrom: "", dateTo: "" },
  discoveryTaxonomy: null,
  discoveryHistory: [],
  selectedCandidateIds: new Set(),
};

const api = createApiClient(() => state.token);

const el = {
  shell: document.querySelector(".app-shell"),
  topbar: document.getElementById("topbar"),
  tabs: document.getElementById("tabs"),
  filters: document.getElementById("filters"),
  list: document.getElementById("list-panel"),
  detail: document.getElementById("detail-panel"),
  conn: document.getElementById("conn-indicator"),
  themeToggle: document.getElementById("theme-toggle"),
  brandbarRight: document.querySelector(".brandbar-right"),
};

// ---- Login / session gate ------------------------------------------------
// Whether the server offers a safe loopback dev session. Probed once at boot;
// drives whether the login screen shows the "Enter development mode" button.
let devAvailable = false;
let devOwner = "";
let loginMount = null;

async function probeDevSession() {
  try {
    const info = await api.devSession();
    devAvailable = true;
    devOwner = info && info.owner_id ? info.owner_id : "";
    return info;
  } catch {
    devAvailable = false;
    devOwner = "";
    return null;
  }
}

function showLogin() {
  if (el.shell) el.shell.hidden = true;
  if (!loginMount) {
    loginMount = document.createElement("div");
    loginMount.id = "login-mount";
    document.body.appendChild(loginMount);
  }
  loginMount.hidden = false;
  loginMount.innerHTML = renderLoginHtml({ devAvailable, devOwner });
  const devBtn = loginMount.querySelector("#dev-enter-btn");
  if (devBtn) devBtn.addEventListener("click", enterDevMode);
  const form = loginMount.querySelector("#login-token-form");
  if (form) form.addEventListener("submit", (e) => {
    e.preventDefault();
    const value = loginMount.querySelector("#login-token-field").value.trim();
    if (!value) { showLoginError("Enter a bearer token to sign in."); return; }
    completeLogin(value);
  });
}

function hideLogin() {
  if (loginMount) loginMount.hidden = true;
  if (el.shell) el.shell.hidden = false;
}

function showLoginError(message) {
  const box = loginMount && loginMount.querySelector("#login-error");
  if (box) { box.textContent = message; box.hidden = false; }
}

async function enterDevMode() {
  try {
    const info = await api.devSession();
    completeLogin(info.token);
  } catch (err) {
    showLoginError(err instanceof ApiError
      ? "Development mode is not available in this configuration."
      : "Could not reach the server for a development session.");
  }
}

function completeLogin(token) {
  state.token = token;
  saveToken(token);
  hideLogin();
  renderSessionControl();
  bootWorkspace();
}

function signOut() {
  state.token = "";
  state.jobId = "";
  state.jobsResult = null;
  saveToken("");
  saveJobId("");
  renderSessionControl();
  showLogin();
}

// Session indicator + sign-out in the brand bar (replaces the old static conn
// chip position with a real, token-derived control). The owner id is decoded
// from the token payload purely for display; the server remains authoritative.
function ownerFromToken(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload && typeof payload.sub === "string" ? payload.sub : "";
  } catch { return ""; }
}

function renderSessionControl() {
  let ctrl = document.getElementById("session-control");
  if (!ctrl) {
    ctrl = document.createElement("div");
    ctrl.id = "session-control";
    ctrl.className = "session-control";
    el.brandbarRight.insertBefore(ctrl, el.brandbarRight.firstChild);
  }
  if (!state.token) { ctrl.innerHTML = ""; return; }
  const owner = ownerFromToken(state.token);
  ctrl.innerHTML = `
    <span class="session-owner" title="Signed-in owner">
      <span class="session-dot" aria-hidden="true"></span>${escapeHtml(owner || "session")}
    </span>
    <button type="button" class="icon-btn" id="sign-out-btn" aria-label="Sign out">${icon("exit", { size: 16 })}</button>`;
  const btn = ctrl.querySelector("#sign-out-btn");
  if (btn) btn.addEventListener("click", signOut);
}

// ---- Real connection / job-context indicator ---------------------------
// Driven only by an actual backend response (a getJob probe), never a
// fabricated "all providers online" state. See probeConnection().
function setConn(kind, text) {
  if (!el.conn) return;
  el.conn.className = `conn conn--${kind}`;
  const label = el.conn.querySelector(".conn-text");
  if (label) label.textContent = text;
}

function connErrorText(err) {
  if (err instanceof ApiError) {
    if (err.status === 401 || err.status === 403) return "Authentication failed";
    if (err.status === 404) return "Job not found or not yours";
    if (err.status >= 500) return "Backend error";
    return `Request error (${err.status})`;
  }
  return "Backend unreachable";
}

// Probe the real session against the real job. getJob returns 200 only when
// the token is valid AND the job is owned by the caller, so its outcome is an
// honest connection + ownership signal, and its payload gives real job context.
async function probeConnection() {
  if (!state.jobId || !state.token) { setConn("idle", "Awaiting session"); return; }
  setConn("connecting", "Connecting…");
  try {
    const job = await api.getJob(state.jobId);
    const short = String(state.jobId).slice(-8);
    const status = (job && (job.status || job.state)) || "loaded";
    setConn("online", `Job …${short} · ${status}`);
  } catch (err) {
    setConn("error", connErrorText(err));
  }
}

// ---- Theme toggle (dark is primary; light is a real, persisted option) --
const THEME_KEY = "ds_theme";
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
  if (el.themeToggle) {
    const effective = theme || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    el.themeToggle.innerHTML = icon(effective === "dark" ? "sun" : "moon");
    el.themeToggle.setAttribute("aria-label", effective === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }
}
function initTheme() {
  const saved = localStorage.getItem(THEME_KEY) || "";
  applyTheme(saved);
  if (el.themeToggle) {
    el.themeToggle.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme")
        || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
    });
  }
}

function parseHash() {
  const parts = (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "dashboard") {
    state.tab = "dashboard";
  } else if (parts[0] === "claims") {
    state.tab = "claims";
    state.selectedClaimId = parts[1] ? decodeURIComponent(parts[1]) : null;
  } else if (parts[0] === "evidence") {
    state.tab = "evidence";
  } else if (parts[0] === "discovery") {
    state.tab = "discovery";
  } else if (parts[0] === "script") {
    state.tab = "script";
  } else if (parts[0] === "scene") {
    state.tab = "scene";
  } else if (parts[0] === "production") {
    state.tab = "production";
  } else {
    state.tab = "sources";
    state.selectedSourceId = parts[1] ? decodeURIComponent(parts[1]) : null;
  }
}

function setHash(tab, id) {
  location.hash = id ? `#/${tab}/${encodeURIComponent(id)}` : `#/${tab}`;
}

function renderTopbar() {
  // Job selection is primarily via the Dashboard (real DB-backed job cards);
  // this bar shows the active job and offers a direct load-by-ID for operators
  // who already have a job_id. The token is handled by the login gate, not here.
  el.topbar.innerHTML = `
    <div class="topbar-active" aria-live="polite">
      ${state.jobId
        ? `<span class="topbar-label">Active job</span>
           <span class="mono topbar-jobid">${escapeHtml(state.jobId)}</span>`
        : `<span class="topbar-label">No active job</span>
           <span class="meta">Pick one from the Dashboard, or load by ID.</span>`}
    </div>
    <div class="topbar-loader">
      ${textFieldHtml({ id: "job-id-field", label: "Load job by ID", value: state.jobId, placeholder: "workflow job_id" })}
      <button type="button" id="load-job-btn" class="primary">Load</button>
    </div>`;
  const idField = document.getElementById("job-id-field");
  if (idField) idField.addEventListener("change", (e) => { state.jobId = e.target.value.trim(); });
  const loadBtn = document.getElementById("load-job-btn");
  if (loadBtn) loadBtn.addEventListener("click", () => {
    const value = document.getElementById("job-id-field").value.trim();
    if (!value) return;
    selectJob(value, "sources");
  });
}

// Set the active job (persisted) and navigate to a tab for it.
function selectJob(jobId, tab = "sources") {
  state.jobId = jobId;
  saveJobId(jobId);
  renderTopbar();
  probeConnection();
  setHash(tab, null);
}

function renderTabs() {
  const tab = (id, iconName, label) => `
    <button type="button" role="tab" aria-selected="${state.tab === id}" data-tab="${id}">
      <span class="tab-icon">${icon(iconName, { size: 17 })}</span><span>${label}</span>
    </button>`;
  el.tabs.innerHTML = `
    ${tab("dashboard", "grid", "Dashboard")}
    ${tab("discovery", "radar", "Discovery")}
    ${tab("sources", "layers", "Sources")}
    ${tab("claims", "claims", "Claims")}
    ${tab("evidence", "shield", "Evidence")}
    ${tab("script", "script", "Script")}
    ${tab("scene", "scene", "Scene")}
    ${tab("production", "layers", "Production")}`;
  el.tabs.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setHash(btn.dataset.tab, null);
    });
  });
}

function renderFilters() {
  if (state.tab === "evidence" || state.tab === "script" || state.tab === "scene" || state.tab === "production" || state.tab === "dashboard") { el.filters.innerHTML = ""; return; }
  const statusOptions = ["", "PENDING", "APPROVED_FOR_USE", "REJECTED", "NEEDS_MORE_EVIDENCE", "CONFLICT_UNRESOLVED"];
  const optionHtml = (value) => `<option value="${value}">${value || "All statuses"}</option>`;
  if (state.tab === "discovery") {
    const hasRun = !!(state.discoveryResult && !state.discoveryResult.empty_reason);
    el.filters.innerHTML = hasRun ? `
      ${textFieldHtml({ id: "discovery-filter-language", label: "Filter by language", value: state.discoveryFilters.language, placeholder: "en" })}
      ${textFieldHtml({ id: "discovery-filter-query", label: "Filter by title", value: state.discoveryFilters.query, placeholder: "keyword" })}
      ${renderDiscoveryFilterFormHtml(state.discoveryTaxonomy, state.discoveryFilters)}
      <button type="button" id="discovery-confirm-btn" class="primary" disabled>Confirm selection</button>`
      : `<p class="meta">Submit a topic below and queue the job. Discovered URLs are fetched and extracted automatically. Refresh to see candidates, sources, and claims.</p>`;
    const langInput = el.filters.querySelector("#discovery-filter-language");
    if (langInput) langInput.addEventListener("change", (e) => {
      state.discoveryFilters.language = e.target.value.trim(); loadDiscovery();
    });
    const queryInput = el.filters.querySelector("#discovery-filter-query");
    if (queryInput) queryInput.addEventListener("change", (e) => {
      state.discoveryFilters.query = e.target.value.trim(); loadDiscovery();
    });
    [["#discovery-filter-category", "category"], ["#discovery-filter-region", "region"],
      ["#discovery-filter-source-class", "sourceClass"], ["#discovery-filter-date-from", "dateFrom"],
      ["#discovery-filter-date-to", "dateTo"]].forEach(([selector, key]) => {
      const input = el.filters.querySelector(selector);
      if (input) input.addEventListener("change", (e) => { state.discoveryFilters[key] = e.target.value; loadDiscovery(); });
    });
    const confirmBtn = el.filters.querySelector("#discovery-confirm-btn");
    if (confirmBtn) confirmBtn.addEventListener("click", confirmDiscoverySelection);
    updateConfirmButtonState();
  } else if (state.tab === "sources") {
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
      ${state.claimSourceFilter ? `<span class="chip">Source: ${escapeHtml(state.claimSourceFilter)}
        <button type="button" id="clear-source-filter" class="chip-dismiss" aria-label="Clear source filter">${icon("close", { size: 12 })}</button></span>` : ""}`;
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
    el.list.innerHTML = `<div class="empty-state">No active job. Open one from the <a href="#/dashboard">Dashboard</a>, or load a job by ID above.</div>`;
    el.detail.innerHTML = "";
    el.detail.classList.remove("detail-overlay");
    return false;
  }
  return true;
}

async function loadDashboard() {
  el.detail.innerHTML = "";
  el.detail.classList.remove("detail-overlay");
  el.list.classList.add("list--full");
  el.list.innerHTML = `<div class="loading-state">Loading jobs…</div>`;
  try {
    state.jobsResult = await api.listJobs();
    const jobs = (state.jobsResult && state.jobsResult.jobs) || [];
    el.list.innerHTML = renderDashboardHtml(jobs, state.jobId);
    el.list.querySelectorAll("[data-job-card]").forEach((card) => {
      const open = () => selectJob(card.dataset.jobCard, "sources");
      card.addEventListener("click", open);
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
    });
  } catch (err) {
    el.list.innerHTML = errorHtml(err);
  }
}

async function loadDiscovery() {
  if (!state.token) {
    el.list.innerHTML = `<div class="empty-state">Sign in before starting a topic.</div>`;
    return;
  }
  if (!state.jobId) {
    el.detail.innerHTML = "";
    el.detail.classList.remove("detail-overlay");
    el.list.innerHTML = renderDiscoveryRequestFormHtml("");
    wireDiscoveryRequestForm();
    return;
  }
  el.detail.innerHTML = "";
  el.detail.classList.remove("detail-overlay");
  el.list.innerHTML = `<div class="loading-state">Loading discovery…</div>`;
  try {
    if (!state.discoveryTaxonomy) {
      state.discoveryTaxonomy = await api.getDiscoveryTaxonomy().catch(() => null);
    }
    const result = await api.listDiscoveryCandidates(state.jobId, {
      language: state.discoveryFilters.language || undefined,
      query: state.discoveryFilters.query || undefined,
      category: state.discoveryFilters.category || undefined,
      region: state.discoveryFilters.region || undefined,
      sourceClass: state.discoveryFilters.sourceClass || undefined,
      dateFrom: state.discoveryFilters.dateFrom || undefined,
      dateTo: state.discoveryFilters.dateTo || undefined,
    });
    state.discoveryResult = result;
    state.discoveryHistory = await api.listDiscoverySearches().then((r) => r.searches).catch(() => []);
    if (result.empty_reason && !result.candidates.length) {
      const requestInfo = await api.getDiscoveryRequest(state.jobId).catch(() => null);
      el.list.innerHTML = renderDiscoveryRequestFormHtml(requestInfo && requestInfo.request ? requestInfo.request.topic : "");
      wireDiscoveryRequestForm();
      return;
    }
    state.discoverySelection = await api.getDiscoverySelection(state.jobId).catch(() => null);
    state.selectedCandidateIds = new Set((state.discoverySelection && state.discoverySelection.selected_candidate_ids) || []);
    el.list.innerHTML = `
      ${renderCoverageHtml(result)}
      ${renderSelectionSummaryHtml(state.discoverySelection)}
      ${result.candidates.length
        ? renderDiscoveryCandidatesListHtml(result.candidates, state.selectedCandidateIds)
        : renderEmptyResultsHtml(result)}
      ${renderPaginationHtml(result)}
      <details class="search-history-panel"><summary>Previous searches (${state.discoveryHistory.length})</summary>
        ${renderSearchHistoryHtml(state.discoveryHistory)}</details>`;
    wireDiscoveryExtras();
    el.list.querySelectorAll("[data-select-candidate]").forEach((box) => {
      box.addEventListener("change", (e) => {
        const id = e.target.dataset.selectCandidate;
        if (e.target.checked) state.selectedCandidateIds.add(id);
        else state.selectedCandidateIds.delete(id);
        updateConfirmButtonState();
      });
    });
    renderFilters();
  } catch (err) {
    el.list.innerHTML = errorHtml(err);
  }
}

function wireDiscoveryExtras() {
  const more = el.list.querySelector("#discovery-load-more");
  if (more) {
    more.addEventListener("click", async () => {
      more.disabled = true;
      try {
        const page = await api.listDiscoveryCandidates(state.jobId, {
          language: state.discoveryFilters.language || undefined,
          query: state.discoveryFilters.query || undefined,
          category: state.discoveryFilters.category || undefined,
          region: state.discoveryFilters.region || undefined,
          sourceClass: state.discoveryFilters.sourceClass || undefined,
          dateFrom: state.discoveryFilters.dateFrom || undefined,
          dateTo: state.discoveryFilters.dateTo || undefined,
          cursor: more.dataset.cursor,
        });
        const list = el.list.querySelector(".candidate-list");
        if (list) list.insertAdjacentHTML("beforeend",
          renderDiscoveryCandidatesListHtml(page.candidates, state.selectedCandidateIds)
            .replace(/^<ul class="candidate-list">/, "").replace(/<\/ul>$/, ""));
        state.discoveryResult = { ...page, candidates: [...state.discoveryResult.candidates, ...page.candidates] };
        more.outerHTML = renderPaginationHtml(state.discoveryResult);
        wireDiscoveryExtras();
        el.list.querySelectorAll("[data-select-candidate]").forEach((box) => {
          box.onchange = (e) => {
            const id = e.target.dataset.selectCandidate;
            if (e.target.checked) state.selectedCandidateIds.add(id); else state.selectedCandidateIds.delete(id);
            updateConfirmButtonState();
          };
        });
      } catch (err) {
        more.disabled = false;
        showToast(err instanceof ApiError ? err.detail : "Could not load more results.", { isError: true });
      }
    });
  }
  el.list.querySelectorAll("[data-reopen-search]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.jobId = btn.dataset.reopenSearch;
      saveJobId(state.jobId);
      renderTopbar();
      loadDiscovery();
    });
  });
  el.list.querySelectorAll("[data-rerun-search]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const created = await api.rerunDiscovery(btn.dataset.rerunSearch);
        state.jobId = created.job_id;
        saveJobId(state.jobId);
        renderTopbar();
        showToast("New search created. Queue the job to run it.");
        loadDiscovery();
      } catch (err) {
        showToast(err instanceof ApiError ? err.detail : "Could not rerun the search.", { isError: true });
      }
    });
  });
}

function updateConfirmButtonState() {
  const btn = el.filters.querySelector("#discovery-confirm-btn");
  if (!btn) return;
  const n = state.selectedCandidateIds.size;
  btn.disabled = n === 0;
  btn.textContent = n ? `Confirm selection (${n})` : "Confirm selection";
}

function wireDiscoveryRequestForm() {
  const form = el.list.querySelector("#discovery-request-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const topic = el.list.querySelector("#discovery-topic").value.trim();
      const language = el.list.querySelector("#discovery-language").value.trim() || "en";
      const terms = el.list.querySelector("#discovery-terms").value.trim();
      try {
        if (!topic) throw new ApiError(422, "Enter a topic first.");
        if (!state.jobId) {
          const created = await api.createJob(topic, language, newIdempotencyKey());
          state.jobId = created.job_id;
          saveJobId(state.jobId);
          renderTopbar();
        }
        await api.createDiscoveryRequest(state.jobId, {
          topic, language,
          search_terms: terms ? terms.split(",").map((t) => t.trim()).filter(Boolean) : [],
        });
        showToast("Topic saved. Queue the job so discovery, fetch, and extraction can run.");
      } catch (err) {
        showToast(err instanceof ApiError ? err.detail : "Could not save the discovery request.", { isError: true });
      }
    });
  }
  const queueBtn = el.list.querySelector("#discovery-queue-btn");
  if (queueBtn) {
    queueBtn.addEventListener("click", async () => {
      try {
        await api.queueJob(state.jobId);
        showToast("Job queued. The worker will discover URLs, fetch pages, and extract claims. Evidence stays unapproved.");
      } catch (err) {
        showToast(err instanceof ApiError ? err.detail : "Could not queue the job.", { isError: true });
      }
    });
  }
}

async function confirmDiscoverySelection() {
  if (!state.selectedCandidateIds.size) {
    showToast("Select at least one candidate before confirming.", { isError: true });
    return;
  }
  try {
    await api.selectDiscoveryCandidates(state.jobId, Array.from(state.selectedCandidateIds));
    showToast(`${state.selectedCandidateIds.size} candidate(s) confirmed for F3 acquisition.`);
    await loadDiscovery();
  } catch (err) {
    showToast(err instanceof ApiError ? err.detail : "Could not confirm the selection.", { isError: true });
  }
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
    const claim = await api.getClaim(state.jobId, claimId);
    const history = claim.has_conflict ? await api.claimHistory(state.jobId, claimId) : [];
    const conflictPairs = await resolveConflictPairs(claim);
    el.detail.innerHTML = `<button type="button" class="back-button" id="back-btn">&larr; Back</button>${renderClaimDetail(claim, conflictPairs)}`;
    wireBackButton();
    const viewSourceBtn = el.detail.querySelector('[data-action="view-source"]');
    if (viewSourceBtn) viewSourceBtn.addEventListener("click", () => setHash("sources", viewSourceBtn.dataset.sourceId));
    if (!claim.has_conflict) return;
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
  return `<div class="error-state" role="alert">${escapeHtml(String(message))}</div>`;
}

function dispatchTab() {
  // Dashboard is full-width and job-independent (only needs a token); every
  // other tab uses the list+detail layout and requires an active job.
  if (state.tab !== "dashboard") el.list.classList.remove("list--full");
  if (state.tab === "dashboard") { loadDashboard(); return; }
  if (state.tab === "evidence") {
    if (!requireJob()) return;
    const jobId = state.jobId;
    mountEvidence({api, jobId, list:el.list, detail:el.detail, filters:el.filters,
      isCurrent:() => state.tab === "evidence" && state.jobId === jobId});
  }
  else if (state.tab === "script") {
    if (!requireJob()) return;
    const jobId = state.jobId;
    mountScript({api, jobId, list:el.list, detail:el.detail, filters:el.filters,
      isCurrent:() => state.tab === "script" && state.jobId === jobId});
  }
  else if (state.tab === "scene") {
    if (!requireJob()) return;
    const jobId = state.jobId;
    mountScene({api, jobId, list:el.list, detail:el.detail, filters:el.filters,
      isCurrent:() => state.tab === "scene" && state.jobId === jobId});
  }
  else if (state.tab === "production") {
    if (!requireJob()) return;
    const jobId = state.jobId;
    mountProduction({api, jobId, list:el.list, detail:el.detail, filters:el.filters,
      isCurrent:() => state.tab === "production" && state.jobId === jobId});
  }
  else if (state.tab === "discovery") loadDiscovery();
  else if (state.tab === "sources") loadSources();
  else loadClaims();
}

function refreshActiveTab() {
  renderTabs();
  renderFilters();
  dispatchTab();
}

function route() {
  parseHash();
  renderTabs();
  renderFilters();
  dispatchTab();
}

// ---- Boot ----------------------------------------------------------------
// The workspace shell only runs with an authenticated session. Without a token
// we show the login gate (offering a dev session when the server allows it);
// with one, we render the shell and route to the current (or default) tab.

function bootWorkspace() {
  renderTopbar();
  renderSessionControl();
  probeConnection();
  if (!location.hash || location.hash === "#" || location.hash === "#/") {
    setHash("dashboard", null);
  } else {
    route();
  }
}

async function boot() {
  initTheme();
  // Probe whether a safe dev session is available so the login screen can offer
  // it; this never logs anyone in on its own.
  await probeDevSession();
  if (state.token) {
    hideLogin();
    bootWorkspace();
  } else {
    showLogin();
  }
}

window.addEventListener("hashchange", () => { if (state.token) route(); });
boot();
