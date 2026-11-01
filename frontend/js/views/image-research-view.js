// Independent Image Research workspace. It does not render invented counts:
// the tally string and shortfall come from the job payload.

import { escapeHtml } from "../components/status-badge.js";
import { textFieldHtml } from "../components/text-field.js";
import { newIdempotencyKey } from "../api.js";
import { singleFlight } from "../components/single-flight.js";

export function renderProviderStatusHtml(payload) {
  const providers = payload?.providers || [];
  if (!providers.length) return `<p role="status">No image providers were reported.</p>`;
  return `<ul class="provider-list">${providers.map((provider) => `
    <li>
      <strong>${escapeHtml(provider.label || provider.name)}</strong>
      <span class="badge">${escapeHtml(provider.status || "UNKNOWN")}</span>
      <span>${escapeHtml(provider.detail || "")}</span>
    </li>`).join("")}</ul>
    ${payload?.live_search ? "" : `<p class="meta">Provider list is configuration status, not a completed search.</p>`}`;
}

export function renderImageTallyHtml(summary) {
  if (!summary) return `<p role="status">No image-research tally yet.</p>`;
  const shortfall = Number(summary.shortfall) > 0
    ? `<p role="alert">Shortfall: ${escapeHtml(summary.shortfall)} eligible image${summary.shortfall === 1 ? "" : "s"} below the requested target. This is not complete.</p>`
    : `<p role="status">Eligible candidate target met.</p>`;
  return `<p class="tally">${escapeHtml(summary.tally || "")}</p>${shortfall}
    <p class="meta">Accepted ${escapeHtml(summary.accepted ?? 0)} · Rejected ${escapeHtml(summary.rejected ?? 0)} · Pending ${escapeHtml(summary.pending_review ?? 0)} · Needs verification ${escapeHtml(summary.needs_verification ?? 0)}</p>`;
}

export function renderImageJobListHtml(payload) {
  const jobs = payload?.jobs || [];
  if (!jobs.length) return `<p class="meta">No image-research jobs yet.</p>`;
  return `<ul class="job-list">${jobs.map((job) => `
    <li>
      <button type="button" data-image-job="${escapeHtml(job.job_id)}">${escapeHtml(job.topic)}</button>
      <span class="badge">${escapeHtml(job.status)}</span>
      ${job.error ? `<span class="error-state">${escapeHtml(job.error)}</span>` : ""}
      ${job.summary?.tally ? `<span class="meta">${escapeHtml(job.summary.tally)}</span>` : ""}
    </li>`).join("")}</ul>`;
}

export function renderCandidateCardHtml(candidate) {
  const resolution = candidate.width && candidate.height ? `${candidate.width}×${candidate.height}` : "resolution not reported";
  const preview = candidate.thumbnail_url
    ? `<img alt="" src="${escapeHtml(candidate.thumbnail_url)}" data-preview="${escapeHtml(candidate.image_id)}">`
    : `<div class="image-fallback" data-preview="${escapeHtml(candidate.image_id)}">No thumbnail URL</div>`;
  return `<article class="image-card" data-image-id="${escapeHtml(candidate.image_id)}">
    ${preview}
    <div class="image-card-body">
      <label><input type="checkbox" data-select="${escapeHtml(candidate.image_id)}"> Select</label>
      <h3>${escapeHtml(candidate.title || "Untitled")}</h3>
      <p class="mono">${escapeHtml(candidate.image_id)}</p>
      <dl class="inspect-dl">
        <dt>Source</dt><dd>${escapeHtml(candidate.source_website || "not reported")}</dd>
        <dt>Creator</dt><dd>${escapeHtml(candidate.creator || "not reported")}</dd>
        <dt>License</dt><dd>${escapeHtml(candidate.license_name || "not stated")}</dd>
        <dt>Resolution</dt><dd>${escapeHtml(resolution)} ${escapeHtml(candidate.file_format || "")}</dd>
        <dt>Relevance</dt><dd>${escapeHtml(candidate.relevance_status)} (${escapeHtml(candidate.relevance_score ?? "n/a")})</dd>
        <dt>Duplicate</dt><dd>${escapeHtml(candidate.duplicate_role || "not reported")}${candidate.duplicate_reason ? ` — ${escapeHtml(candidate.duplicate_reason)}` : ""}</dd>
        <dt>Rights</dt><dd>${escapeHtml(candidate.rights_status)} — ${escapeHtml(candidate.rights_notes || "")}</dd>
        <dt>Acquisition</dt><dd>${escapeHtml(candidate.download_status)}${candidate.download_error ? ` — ${escapeHtml(candidate.download_error)}` : ""}</dd>
        <dt>Review</dt><dd>${escapeHtml(candidate.review_status)}</dd>
      </dl>
      <p>
        ${candidate.page_url ? `<a href="${escapeHtml(candidate.page_url)}" target="_blank" rel="noopener noreferrer">Original source</a>` : `<span>Original page not reported</span>`}
        ${candidate.license_url ? ` · <a href="${escapeHtml(candidate.license_url)}" target="_blank" rel="noopener noreferrer">License</a>` : " · License URL not stated"}
      </p>
      <p class="card-actions">
        <button type="button" data-act="accept" data-image="${escapeHtml(candidate.image_id)}">Accept</button>
        <button type="button" data-act="reject" data-image="${escapeHtml(candidate.image_id)}">Reject</button>
        <button type="button" data-act="verify" data-image="${escapeHtml(candidate.image_id)}">Needs verification</button>
      </p>
    </div>
  </article>`;
}

export function renderGalleryHtml(candidates) {
  if (!candidates?.length) return `<p role="status">No candidates in this view.</p>`;
  return `<div class="image-grid">${candidates.map(renderCandidateCardHtml).join("")}</div>`;
}

export function mountImageResearch({ api, list, detail, isCurrent }) {
  const flight = singleFlight();
  let activeJob = null;
  const filters = { source: "", review_status: "", rights_status: "", duplicate_role: "" };

  async function refreshProviders() {
    const payload = await api.request("GET", "/image-research/providers");
    if (isCurrent()) list.innerHTML = `${renderImageFormHtml()}${renderProviderStatusHtml(payload)}<div id="image-job-list"></div>`;
    wireForm();
    const jobs = await api.request("GET", "/image-research/jobs");
    const slot = document.getElementById("image-job-list");
    if (slot && isCurrent()) slot.innerHTML = renderImageJobListHtml(jobs);
    slot?.querySelectorAll("[data-image-job]").forEach((button) => {
      button.addEventListener("click", () => showJob(button.dataset.imageJob));
    });
  }

  function wireForm() {
    const form = document.getElementById("image-research-form");
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type=submit]");
      flight(button, async () => {
        const body = readForm(form);
        const job = await api.request("POST", "/image-research/jobs", { body, idempotencyKey: newIdempotencyKey() });
        const ran = await api.request("POST", `/image-research/jobs/${encodeURIComponent(job.job_id)}/run`);
        await showJob(ran.job_id);
      });
    });
  }

  async function showJob(jobId) {
    activeJob = await api.request("GET", `/image-research/jobs/${encodeURIComponent(jobId)}`);
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value));
    const page = await api.request("GET", `/image-research/jobs/${encodeURIComponent(jobId)}/candidates?${query}`);
    if (!isCurrent()) return;
    detail.innerHTML = `${renderImageTallyHtml(activeJob.summary)}
      ${activeJob.error ? `<p role="alert" class="error-state">${escapeHtml(activeJob.error)}</p>` : ""}
      <div class="filter-bar">
        ${selectHtml("image-filter-source", "Source", filters.source, ["", "wikimedia_commons", "openverse"])}
        ${selectHtml("image-filter-review", "Review", filters.review_status, ["", "PENDING_REVIEW", "ACCEPTED", "REJECTED", "NEEDS_VERIFICATION"])}
        ${selectHtml("image-filter-rights", "Rights", filters.rights_status, ["", "VERIFIED", "UNCLEAR", "RESTRICTED"])}
        ${selectHtml("image-filter-duplicate", "Duplicate", filters.duplicate_role, ["", "UNIQUE", "PREFERRED", "DUPLICATE"])}
      </div>
      <p class="card-actions">
        <button type="button" id="image-bulk-accept">Bulk accept selected eligible</button>
        <button type="button" id="image-bulk-reject">Bulk reject selected</button>
        <button type="button" id="image-export">Export accepted manifest</button>
      </p>
      <div id="image-gallery">${renderGalleryHtml(page.candidates)}</div>
      <pre id="image-export-manifest" class="inspect-pre" hidden></pre>`;
    detail.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", () => {
        filters.source = detail.querySelector("#image-filter-source").value;
        filters.review_status = detail.querySelector("#image-filter-review").value;
        filters.rights_status = detail.querySelector("#image-filter-rights").value;
        filters.duplicate_role = detail.querySelector("#image-filter-duplicate").value;
        showJob(jobId);
      });
    });
    detail.querySelector("#image-bulk-accept")?.addEventListener("click", () => bulk("ACCEPT"));
    detail.querySelector("#image-bulk-reject")?.addEventListener("click", () => bulk("REJECT"));
    detail.querySelector("#image-export")?.addEventListener("click", async () => {
      const manifest = await api.request("GET", `/image-research/jobs/${encodeURIComponent(jobId)}/export`);
      const slot = document.getElementById("image-export-manifest");
      if (slot) {
        slot.hidden = false;
        slot.textContent = JSON.stringify(manifest, null, 2);
      }
    });
    detail.querySelectorAll("[data-act]").forEach((button) => {
      button.addEventListener("click", () => reviewOne(button.dataset.act, button.dataset.image));
    });
  }

  async function reviewOne(act, imageId) {
    const action = act === "accept" ? "ACCEPT" : act === "reject" ? "REJECT" : "NEEDS_VERIFICATION";
    const reason = action === "REJECT" ? window.prompt("Rejection reason") || "" : "";
    if (action === "REJECT" && !reason.trim()) return;
    await api.request("POST", `/image-research/candidates/${encodeURIComponent(imageId)}/review`, {
      body: { action, reason }, idempotencyKey: newIdempotencyKey(),
    });
    if (activeJob) await showJob(activeJob.job_id);
  }

  async function bulk(action) {
    const image_ids = [...detail.querySelectorAll("[data-select]:checked")].map((box) => box.dataset.select);
    if (!image_ids.length || !activeJob) return;
    const reason = action === "REJECT" ? window.prompt("Rejection reason for the selected images") || "" : "";
    if (action === "REJECT" && !reason.trim()) return;
    const result = await api.request("POST", `/image-research/jobs/${encodeURIComponent(activeJob.job_id)}/reviews/bulk`, {
      body: { action, image_ids, reason }, idempotencyKey: newIdempotencyKey(),
    });
    if (result.skipped?.length) window.alert(result.skipped.map((item) => `${item.image_id}: ${item.reason}`).join("\n"));
    await showJob(activeJob.job_id);
  }

  refreshProviders().catch((error) => {
    if (isCurrent()) detail.innerHTML = `<p role="alert" class="error-state">${escapeHtml(error.message || "Image research is unavailable")}</p>`;
  });
}

function renderImageFormHtml() {
  return `<form id="image-research-form">
    <h2>Image Research</h2>
    ${textFieldHtml({ id: "image-topic", label: "Topic", placeholder: "Rafale fighter aircraft" })}
    ${textFieldHtml({ id: "image-keywords", label: "Keywords", placeholder: "aircraft, cockpit" })}
    ${textFieldHtml({ id: "image-language", label: "Search language", value: "en" })}
    ${textFieldHtml({ id: "image-minimum", label: "Minimum unique eligible images", value: "30", type: "number" })}
    <fieldset>
      <legend>Sources</legend>
      <label><input type="checkbox" name="source" value="wikimedia_commons" checked> Wikimedia Commons</label>
      <label><input type="checkbox" name="source" value="openverse" checked> Openverse</label>
      <label><input type="checkbox" value="social_media" disabled> Social media — unsupported without an official API</label>
    </fieldset>
    <button type="submit" class="primary">Search images</button>
  </form>`;
}

function readForm(form) {
  const sources = [...form.querySelectorAll('input[name="source"]:checked')].map((input) => input.value);
  return {
    topic: form.querySelector("#image-topic").value.trim(),
    keywords: form.querySelector("#image-keywords").value.split(",").map((item) => item.trim()).filter(Boolean),
    language: form.querySelector("#image-language").value.trim() || "en",
    minimum_unique: Number(form.querySelector("#image-minimum").value) || 30,
    sources,
  };
}

function selectHtml(id, label, value, options) {
  return `<label>${escapeHtml(label)} <select id="${id}">${options.map((option) =>
    `<option value="${escapeHtml(option)}"${option === value ? " selected" : ""}>${escapeHtml(option || "Any")}</option>`).join("")}</select></label>`;
}
