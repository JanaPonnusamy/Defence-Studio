// Production tab: post-script stages (scenes, visual plan, audio, render, QC,
// package, publish). Each stage is a section module registered in SECTIONS;
// every control calls the real backend. State shown is persisted server state,
// re-read on every render (refresh / another device shows the same thing).
import { escapeHtml as esc } from "../components/status-badge.js";
import { newIdempotencyKey } from "../api.js";

export const SECTIONS = [];

export function registerSection(section) { SECTIONS.push(section); }

export async function mountProduction({ api, jobId, list, detail, filters, isCurrent }) {
  detail.innerHTML = "";
  detail.classList.remove("detail-overlay");
  filters.innerHTML = `<label for="prod-language">Language
      <select id="prod-language"><option value="ta">Tamil</option><option value="en">English</option></select></label>`;
  const languageSelect = filters.querySelector("#prod-language");
  const ctx = {
    api, jobId, isCurrent, language: languageSelect.value,
    refresh: () => render(),
  };
  languageSelect.addEventListener("change", () => { ctx.language = languageSelect.value; render(); });

  async function render() {
    list.innerHTML = '<p class="loading-state">Loading production state…</p>';
    const parts = [];
    for (const section of SECTIONS) {
      try {
        parts.push(`<section class="prod-section" data-section="${esc(section.id)}"><h2>${esc(section.title)}</h2>
          ${await section.render(ctx)}</section>`);
      } catch (error) {
        parts.push(`<section class="prod-section"><h2>${esc(section.title)}</h2>
          <p role="alert" class="error-state">${esc(error.message || String(error))}</p></section>`);
      }
    }
    if (!isCurrent()) return;
    list.innerHTML = parts.join("");
    for (const section of SECTIONS) {
      const node = list.querySelector(`[data-section="${section.id}"]`);
      if (node && section.wire) section.wire(ctx, node);
    }
  }
  await render();
}

// -- shared small helpers (exported for tests) --------------------------------

export function statusChip(status, stale) {
  const label = stale ? `${status} · STALE` : status;
  return `<span class="chip">${esc(label)}</span>`;
}

export async function runAction(ctx, node, label, fn) {
  const out = node.querySelector("[data-action-status]");
  if (out) out.textContent = `${label}…`;
  try {
    await fn();
    ctx.refresh();
  } catch (error) {
    if (out) out.textContent = error.message || String(error);
  }
}

// -- Scenes + visual plan section ----------------------------------------------

export function renderScenesHtml(plans, selected) {
  if (!plans.length) {
    return `<p class="meta">No scene plan yet. An approved script is required.</p>
      <button type="button" data-act="generate-plan">Generate scene plan from approved script</button>`;
  }
  const latest = selected || plans[plans.length - 1];
  const rows = latest.scenes.map((s, i) => `
    <li class="scene-row" data-scene-id="${esc(s.scene_id)}">
      <div><strong>${i + 1}. ${esc(s.title || s.source_section_id || s.scene_id)}</strong>
        <span class="chip">${Number(s.planned_timing.estimated_seconds).toFixed(1)}s est.</span>
        <span class="chip">${esc(s.transition_in)}</span></div>
      <p>${esc(s.narration)}</p>
      <div class="meta">Claims: ${(s.claim_ids || []).map(esc).join(", ") || "none"}</div>
      <div class="scene-actions">
        <button type="button" data-act="up" data-scene="${esc(s.scene_id)}" ${i === 0 ? "disabled" : ""}>Up</button>
        <button type="button" data-act="down" data-scene="${esc(s.scene_id)}" ${i === latest.scenes.length - 1 ? "disabled" : ""}>Down</button>
        <button type="button" data-act="remove" data-scene="${esc(s.scene_id)}" ${latest.scenes.length === 1 ? "disabled" : ""}>Remove</button>
        <button type="button" data-act="transition" data-scene="${esc(s.scene_id)}">Next transition</button>
      </div>
    </li>`).join("");
  const canApprove = latest.status === "DRAFT" || latest.status === "AWAITING_REVIEW";
  return `<p class="meta">Version ${latest.version} · ${statusChip(latest.status)} · ${latest.scenes.length} scene(s)</p>
    <ol class="scene-list">${rows}</ol>
    <label>Approval note <input type="text" id="plan-note" maxlength="500"></label>
    <button type="button" data-act="approve-plan" data-version="${latest.version}" ${canApprove ? "" : "disabled"}>Approve this plan</button>
    <button type="button" data-act="regenerate-plan">Regenerate from approved script</button>`;
}

export function renderVisualHtml(status, plan) {
  const approved = plan && plan.status === "APPROVED";
  const items = (status && status.items) || [];
  const list = items.length ? `<ul class="run-list">${items.map((it) => `
    <li>v${it.version} ${statusChip(it.status, it.stale)} · ${Math.round((it.progress || 0) * 100)}%
      ${it.detail ? `<span class="meta">${esc(it.detail)}</span>` : ""}
      ${["FAILED", "INTERRUPTED", "CANCELLED"].includes(it.status)
        ? `<button type="button" data-act="retry-visual" data-version="${it.version}">Retry</button>` : ""}
    </li>`).join("")}</ul>` : '<p class="meta">No visual plan has been built yet.</p>';
  return `${list}
    <p class="meta">Timing sent to the Visual Module is the scene plan's estimate; it is rebuilt from measured
    audio alignment later. Shots with no rights-cleared asset stay as gaps and are never filled automatically.</p>
    <button type="button" data-act="build-visual" ${approved ? "" : "disabled"}>Build visual timeline</button>
    ${approved ? "" : '<p class="meta">Approve the scene plan first.</p>'}`;
}

registerSection({
  id: "scenes", title: "Scene plan",
  async render(ctx) {
    const result = await ctx.api.request("GET", `/jobs/${encodeURIComponent(ctx.jobId)}/scene/plans?language=${ctx.language}`);
    const rows = result.plans || [];
    const plans = [];
    for (const row of rows.slice(-1)) {
      plans.push({ ...(await ctx.api.request("GET",
        `/jobs/${encodeURIComponent(ctx.jobId)}/scene/plans/${ctx.language}/${row.version}`)), plan_sha256: row.plan_sha256 });
    }
    ctx.plan = plans[0] || null;
    return renderScenesHtml(plans) + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    const job = encodeURIComponent(ctx.jobId);
    const edit = (edits) => runAction(ctx, node, "Saving edit", () => ctx.api.request(
      "POST", `/jobs/${job}/scene/plans/${ctx.language}/revise`,
      { body: { base_version: ctx.plan.version, edits }, idempotencyKey: newIdempotencyKey() }));
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", () => {
      const act = btn.dataset.act, sid = btn.dataset.scene;
      const ids = ctx.plan ? ctx.plan.scenes.map((s) => s.scene_id) : [];
      if (act === "generate-plan" || act === "regenerate-plan") {
        runAction(ctx, node, "Generating plan", () => ctx.api.request("POST", `/jobs/${job}/scene/generate`, {
          body: { language: ctx.language, target_total_seconds: 600, words_per_minute: 150, default_transition: "cut" },
          idempotencyKey: newIdempotencyKey() }));
      } else if (act === "up" || act === "down") {
        const i = ids.indexOf(sid), j = act === "up" ? i - 1 : i + 1;
        [ids[i], ids[j]] = [ids[j], ids[i]];
        edit({ reorder: ids });
      } else if (act === "remove") {
        edit({ remove: [sid] });
      } else if (act === "transition") {
        const order = ["cut", "fade", "dissolve", "slide", "none"];
        const scene = ctx.plan.scenes.find((s) => s.scene_id === sid);
        edit({ update: { [sid]: { transition_in: order[(order.indexOf(scene.transition_in) + 1) % order.length] } } });
      } else if (act === "approve-plan") {
        const note = node.querySelector("#plan-note").value.trim();
        if (!note) { node.querySelector("[data-action-status]").textContent = "Enter an approval note."; return; }
        runAction(ctx, node, "Approving", () => ctx.api.request(
          "POST", `/jobs/${job}/scene/plans/${ctx.language}/${ctx.plan.version}/approve`,
          { body: { plan_sha256: ctx.plan.plan_sha256, note }, idempotencyKey: newIdempotencyKey() }));
      }
    }));
  },
});

registerSection({
  id: "visual", title: "Visual timeline",
  async render(ctx) {
    const status = await ctx.api.request("GET", `/jobs/${encodeURIComponent(ctx.jobId)}/visual/${ctx.language}`);
    ctx.visual = status;
    return renderVisualHtml(status, ctx.plan) + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    const job = encodeURIComponent(ctx.jobId);
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", () => {
      if (btn.dataset.act === "build-visual") {
        runAction(ctx, node, "Starting", () => ctx.api.request("POST", `/jobs/${job}/visual/${ctx.language}/plan`,
          { body: {}, idempotencyKey: newIdempotencyKey() }));
      } else if (btn.dataset.act === "retry-visual") {
        runAction(ctx, node, "Retrying", () => ctx.api.request(
          "POST", `/jobs/${job}/visual/${ctx.language}/plan/${btn.dataset.version}/retry`,
          { body: {}, idempotencyKey: newIdempotencyKey() }));
      }
    }));
    // A queued/running build is polled so progress shown is the server's.
    const active = (ctx.visual.items || []).some((i) => ["QUEUED", "RUNNING"].includes(i.status));
    if (active) setTimeout(() => ctx.isCurrent() && ctx.refresh(), 2000);
  },
});

// -- Audio section (RM-4) --------------------------------------------------------

export function renderAudioHtml(status, setBody) {
  const items = (status && status.items) || [];
  const providers = Object.entries((status && status.provider_languages) || {});
  const runs = items.length ? `<ul class="run-list">${items.map((it) => `
    <li>v${it.version} ${statusChip(it.status, it.stale)} · ${Math.round((it.progress || 0) * 100)}%
      <span class="meta">${esc(it.input_binding.provider)} · ${esc(it.detail || "")}</span>
      ${["FAILED", "INTERRUPTED", "CANCELLED"].includes(it.status)
        ? `<button type="button" data-act="retry-audio" data-version="${it.version}">Retry</button>` : ""}
    </li>`).join("")}</ul>` : '<p class="meta">No narration audio has been generated yet.</p>';
  const providerNote = providers.map(([name, langs]) =>
    `${esc(name)}: ${langs.length ? langs.map(esc).join(", ") : "no production languages"}`).join(" · ");
  const scenes = setBody ? `<ol class="scene-list">${setBody.scenes.map((s) => `
    <li class="scene-row" data-scene-id="${esc(s.scene_id)}">
      <div><strong>${esc(s.scene_id)}</strong> <span class="chip">${Number(s.duration_seconds).toFixed(2)}s measured</span>
        <span class="chip">${Number(s.planned_seconds).toFixed(1)}s planned</span>
        ${s.is_mock ? '<span class="chip">MOCK - not narration</span>' : ""}
        ${s.alignment ? `<span class="chip">alignment: ${esc(s.alignment.quality_label)} (${Number(s.alignment.mean_confidence).toFixed(2)})</span>` : ""}
      </div>
      <p>${esc(s.text)}</p>
      <button type="button" data-act="load-audio" data-scene="${esc(s.scene_id)}">Load audio preview</button>
      <audio controls hidden data-audio-for="${esc(s.scene_id)}"></audio>
    </li>`).join("")}</ol>` : "";
  return `${runs}${scenes}
    <p class="meta">Voices available by provider: ${providerNote || "none reported"}. A language a provider is not
    declared to support is refused rather than spoken with the wrong voice. Word timing is approximate; scene durations
    are measured from the audio.</p>
    <label>Provider <input type="text" id="audio-provider" value="local-pyttsx3"></label>
    <button type="button" data-act="generate-audio">Generate narration audio</button>`;
}

registerSection({
  id: "audio", title: "Narration audio",
  async render(ctx) {
    const job = encodeURIComponent(ctx.jobId);
    const status = await ctx.api.request("GET", `/jobs/${job}/audio/${ctx.language}`);
    ctx.audio = status;
    const done = [...status.items].reverse().find((i) => i.status === "SUCCEEDED" && !i.stale);
    ctx.audioSet = done ? await ctx.api.request("GET", `/jobs/${job}/audio/${ctx.language}/${done.version}`) : null;
    ctx.audioVersion = done ? done.version : null;
    return renderAudioHtml(status, ctx.audioSet) + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    const job = encodeURIComponent(ctx.jobId);
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      if (act === "generate-audio") {
        const provider = node.querySelector("#audio-provider").value.trim() || "local-pyttsx3";
        runAction(ctx, node, "Starting", () => ctx.api.request("POST", `/jobs/${job}/audio/${ctx.language}/generate`,
          { body: { provider }, idempotencyKey: newIdempotencyKey() }));
      } else if (act === "retry-audio") {
        runAction(ctx, node, "Retrying", () => ctx.api.request(
          "POST", `/jobs/${job}/audio/${ctx.language}/${btn.dataset.version}/retry`,
          { body: {}, idempotencyKey: newIdempotencyKey() }));
      } else if (act === "load-audio") {
        const out = node.querySelector("[data-action-status]");
        try {
          const response = await fetch(
            `/api/jobs/${job}/audio/${ctx.language}/${ctx.audioVersion}/scene/${encodeURIComponent(btn.dataset.scene)}/file`,
            { headers: ctx.api.authHeader() });
          if (!response.ok) throw new Error(`Audio request failed (${response.status})`);
          const url = URL.createObjectURL(await response.blob());
          const player = node.querySelector(`[data-audio-for="${CSS.escape(btn.dataset.scene)}"]`);
          player.src = url; player.hidden = false; player.play().catch(() => {});
        } catch (error) { out.textContent = error.message; }
      }
    }));
    const active = (ctx.audio.items || []).some((i) => ["QUEUED", "RUNNING"].includes(i.status));
    if (active) setTimeout(() => ctx.isCurrent() && ctx.refresh(), 2500);
  },
});

// -- Render section (RM-5) -----------------------------------------------------

export function renderRenderHtml(status, audioVersion, visualItems) {
  const items = (status && status.items) || [];
  const measured = (visualItems || []).filter((v) => v.status === "SUCCEEDED" && !v.stale
    && v.input_binding && v.input_binding.timing === "measured_audio");
  const runs = items.length ? `<ul class="run-list">${items.map((it) => `
    <li data-render-version="${it.version}">v${it.version} ${statusChip(it.status, it.stale)} ·
      ${Math.round((it.progress || 0) * 100)}% <span class="meta">${esc(it.detail || "")}</span>
      ${it.status === "SUCCEEDED" ? `
        <button type="button" data-act="get-file" data-version="${it.version}" data-which="subtitled">Download MP4 (subtitled)</button>
        <button type="button" data-act="get-file" data-version="${it.version}" data-which="plain">Download MP4 (no subtitles)</button>
        <button type="button" data-act="get-file" data-version="${it.version}" data-which="srt">SRT</button>
        <button type="button" data-act="get-file" data-version="${it.version}" data-which="vtt">VTT</button>` : ""}
      ${["QUEUED", "RUNNING"].includes(it.status) ? `<button type="button" data-act="cancel-render" data-version="${it.version}">Cancel</button>` : ""}
      ${["FAILED", "INTERRUPTED", "CANCELLED"].includes(it.status) ? `<button type="button" data-act="retry-render" data-version="${it.version}">Retry</button>` : ""}
    </li>`).join("")}</ul>` : '<p class="meta">No render has been started yet.</p>';
  const ready = audioVersion != null && measured.length > 0;
  return `${runs}
    <p class="meta">A render needs current narration audio and a visual timeline built from that audio's measured timing.
    Gaps in the timeline are rendered as visible “VISUAL GAP” cards, never filled automatically.</p>
    <button type="button" data-act="build-measured" ${audioVersion != null ? "" : "disabled"}>Rebuild visual timeline from audio timing</button>
    <button type="button" data-act="start-render" ${ready ? "" : "disabled"}>Render video with subtitles</button>
    ${ready ? "" : '<p class="meta">Generate audio, then rebuild the visual timeline from it.</p>'}`;
}

registerSection({
  id: "render", title: "Render and subtitles",
  async render(ctx) {
    const job = encodeURIComponent(ctx.jobId);
    const status = await ctx.api.request("GET", `/jobs/${job}/render/${ctx.language}`);
    ctx.renderStatus = status;
    return renderRenderHtml(status, ctx.audioVersion, (ctx.visual && ctx.visual.items) || [])
      + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    const job = encodeURIComponent(ctx.jobId);
    const latestMeasured = () => [...((ctx.visual && ctx.visual.items) || [])].reverse().find((v) =>
      v.status === "SUCCEEDED" && !v.stale && v.input_binding.timing === "measured_audio");
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      if (act === "build-measured") {
        runAction(ctx, node, "Starting", () => ctx.api.request("POST", `/jobs/${job}/visual/${ctx.language}/plan-measured`,
          { body: { audio_version: ctx.audioVersion }, idempotencyKey: newIdempotencyKey() }));
      } else if (act === "start-render") {
        const v = latestMeasured();
        runAction(ctx, node, "Starting render", () => ctx.api.request("POST", `/jobs/${job}/render/${ctx.language}/start`,
          { body: { audio_version: ctx.audioVersion, visual_version: v.version, burn_subtitles: true },
            idempotencyKey: newIdempotencyKey() }));
      } else if (act === "cancel-render") {
        runAction(ctx, node, "Cancelling", () => ctx.api.request(
          "POST", `/jobs/${job}/render/${ctx.language}/${btn.dataset.version}/cancel`));
      } else if (act === "retry-render") {
        runAction(ctx, node, "Retrying", () => ctx.api.request(
          "POST", `/jobs/${job}/render/${ctx.language}/${btn.dataset.version}/retry`,
          { body: {}, idempotencyKey: newIdempotencyKey() }));
      } else if (act === "get-file") {
        const out = node.querySelector("[data-action-status]");
        try {
          const response = await fetch(
            `/api/jobs/${job}/render/${ctx.language}/${btn.dataset.version}/file/${btn.dataset.which}`,
            { headers: ctx.api.authHeader() });
          if (!response.ok) throw new Error(`Download failed (${response.status})`);
          const url = URL.createObjectURL(await response.blob());
          const a = document.createElement("a");
          a.href = url; a.download = `render-v${btn.dataset.version}-${btn.dataset.which}`; a.click();
          setTimeout(() => URL.revokeObjectURL(url), 5000);
        } catch (error) { out.textContent = error.message; }
      }
    }));
    const active = (ctx.renderStatus.items || []).some((i) => ["QUEUED", "RUNNING"].includes(i.status));
    if (active) setTimeout(() => ctx.isCurrent() && ctx.refresh(), 2500);
  },
});

// -- QC and final approval (RM-6) ---------------------------------------------------

export function renderQcHtml(status, report, renders) {
  const reports = (status && status.reports) || [];
  const succeeded = (renders || []).filter((r) => r.status === "SUCCEEDED" && !r.stale);
  const runs = reports.length ? `<ul class="run-list">${reports.map((r) => `
    <li>QC v${r.version} ${statusChip(r.status)} <span class="meta">${esc(r.detail || "")}</span></li>`).join("")}</ul>`
    : '<p class="meta">No QC has been run yet.</p>';
  let detail = "";
  if (report) {
    const rows = report.checks.map((c) => `
      <tr class="qc-${esc(c.status.toLowerCase())}"><td>${esc(c.id)}</td><td>${esc(c.status)}</td>
      <td>${esc(c.severity)}</td><td>${esc(c.detail)}</td><td>${esc(c.rerun_stage)}</td></tr>`).join("");
    const warns = report.checks.filter((c) => c.status === "WARN");
    detail = `<table class="qc-table"><thead><tr><th>Check</th><th>Status</th><th>Severity</th><th>Detail</th><th>Stage</th></tr></thead>
      <tbody>${rows}</tbody></table>
      ${report.summary.rerun_stages.length ? `<p class="meta">Fix and re-run: ${report.summary.rerun_stages.map(esc).join(", ")}</p>` : ""}
      ${warns.length ? `<fieldset><legend>Acknowledge warnings</legend>${warns.map((w) => `
        <label class="inline"><input type="checkbox" data-ack="${esc(w.id)}"> ${esc(w.id)}</label>`).join("")}</fieldset>` : ""}
      <label>Decision note <input type="text" id="qc-note" maxlength="500"></label>
      <button type="button" data-act="approve-render" data-version="${report.version}"
        ${report.summary.passed ? "" : "disabled"}>Approve this render</button>
      <button type="button" data-act="reject-render" data-version="${report.version}">Reject</button>`;
  }
  const approvals = ((status && status.approvals) || []).map((a) =>
    `<li>${statusChip(a.status)} for render v${a.input_binding.render_version} / QC v${a.input_binding.qc_version}</li>`).join("");
  return `${runs}${detail}${approvals ? `<h3>Decisions</h3><ul>${approvals}</ul>` : ""}
    <button type="button" data-act="run-qc" ${succeeded.length ? "" : "disabled"}>Run QC on latest render</button>`;
}

registerSection({
  id: "qc", title: "Quality control and approval",
  async render(ctx) {
    const job = encodeURIComponent(ctx.jobId);
    const status = await ctx.api.request("GET", `/jobs/${job}/qc/${ctx.language}`);
    ctx.qcStatus = status;
    const done = [...status.reports].reverse().find((r) => r.status === "SUCCEEDED");
    ctx.qcReport = done ? { ...(await ctx.api.request("GET", `/jobs/${job}/qc/${ctx.language}/${done.version}`)), version: done.version } : null;
    return renderQcHtml(status, ctx.qcReport, (ctx.renderStatus && ctx.renderStatus.items) || [])
      + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    const job = encodeURIComponent(ctx.jobId);
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", () => {
      const act = btn.dataset.act;
      if (act === "run-qc") {
        const latest = [...ctx.renderStatus.items].reverse().find((r) => r.status === "SUCCEEDED" && !r.stale);
        runAction(ctx, node, "Starting QC", () => ctx.api.request("POST", `/jobs/${job}/qc/${ctx.language}/run`,
          { body: { render_version: latest.version }, idempotencyKey: newIdempotencyKey() }));
      } else if (act === "approve-render" || act === "reject-render") {
        const note = node.querySelector("#qc-note").value.trim();
        const acked = [...node.querySelectorAll("[data-ack]:checked")].map((b) => b.dataset.ack);
        if (!note) { node.querySelector("[data-action-status]").textContent = "Enter a decision note."; return; }
        const path = act === "approve-render" ? "approve" : "reject";
        runAction(ctx, node, "Saving decision", () => ctx.api.request("POST", `/jobs/${job}/qc/${ctx.language}/${path}`,
          { body: { qc_version: Number(btn.dataset.version), note, acknowledged_warnings: acked },
            idempotencyKey: newIdempotencyKey() }));
      }
    }));
    const active = (ctx.qcStatus.reports || []).some((r) => ["QUEUED", "RUNNING"].includes(r.status));
    if (active) setTimeout(() => ctx.isCurrent() && ctx.refresh(), 2500);
  },
});

// -- Branding and publishing package (RM-7) ------------------------------------------

export function renderPackageHtml(status, manifest, gateOpen) {
  const items = (status && status.items) || [];
  const runs = items.length ? `<ul class="run-list">${items.map((it) => `
    <li>v${it.version} ${statusChip(it.status, it.stale)} <span class="meta">${esc(it.detail || "")}</span>
      ${["AWAITING_REVIEW", "APPROVED"].includes(it.status) ? `
        <button type="button" data-act="get-pkg" data-version="${it.version}" data-name="video_branded">Video</button>
        <button type="button" data-act="get-pkg" data-version="${it.version}" data-name="thumbnail">Thumbnail</button>
        <button type="button" data-act="get-pkg" data-version="${it.version}" data-name="description">Description</button>
        <button type="button" data-act="get-pkg" data-version="${it.version}" data-name="source_register">Source register</button>` : ""}
      ${it.status === "AWAITING_REVIEW" && !it.stale ? `
        <button type="button" data-act="approve-pkg" data-version="${it.version}">Approve package</button>
        <button type="button" data-act="reject-pkg" data-version="${it.version}">Reject</button>` : ""}
    </li>`).join("")}</ul>` : '<p class="meta">No package has been built yet.</p>';
  let detail = "";
  if (manifest) {
    const m = manifest.metadata;
    const dup = manifest.duplicate_check && manifest.duplicate_check.similar_previous_titles || [];
    detail = `<h3>${esc(m.title)}</h3>
      <p class="meta">${esc(m.default_language)} · category ${esc(m.category_id)} · visibility ${esc(m.visibility)} ·
        ${manifest.source_register_count} source(s) · ${(m.chapters || []).length} chapter(s)</p>
      ${m.chapter_note ? `<p class="meta">${esc(m.chapter_note)}</p>` : ""}
      ${dup.length ? `<div class="banner warn">Similar earlier titles: ${dup.map((d) => esc(d.title)).join("; ")}</div>` : ""}
      <pre class="package-description">${esc(m.description)}</pre>`;
  }
  return `${runs}${detail}
    <label>Review note <input type="text" id="pkg-note" maxlength="500"></label>
    <button type="button" data-act="build-pkg" ${gateOpen ? "" : "disabled"}>Build publishing package</button>
    ${gateOpen ? "" : '<p class="meta">A final QC approval of the current render is required first.</p>'}`;
}

registerSection({
  id: "package", title: "Branding and publishing package",
  async render(ctx) {
    const job = encodeURIComponent(ctx.jobId);
    const status = await ctx.api.request("GET", `/jobs/${job}/package/${ctx.language}`);
    ctx.pkgStatus = status;
    const shown = [...status.items].reverse().find((i) => i.uri);
    ctx.pkgManifest = shown ? await ctx.api.request("GET", `/jobs/${job}/package/${ctx.language}/${shown.version}`) : null;
    const approvals = (ctx.qcStatus && ctx.qcStatus.approvals) || [];
    const gateOpen = approvals.length > 0 && approvals[approvals.length - 1].status === "APPROVED";
    return renderPackageHtml(status, ctx.pkgManifest, gateOpen) + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    const job = encodeURIComponent(ctx.jobId);
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      const out = node.querySelector("[data-action-status]");
      if (act === "build-pkg") {
        runAction(ctx, node, "Building package", () => ctx.api.request("POST", `/jobs/${job}/package/${ctx.language}/build`,
          { body: {}, idempotencyKey: newIdempotencyKey() }));
      } else if (act === "approve-pkg" || act === "reject-pkg") {
        const note = node.querySelector("#pkg-note").value.trim();
        if (!note) { out.textContent = "Enter a review note."; return; }
        runAction(ctx, node, "Saving review", () => ctx.api.request(
          "POST", `/jobs/${job}/package/${ctx.language}/${btn.dataset.version}/review`,
          { body: { decision: act === "approve-pkg" ? "APPROVED" : "REJECTED", note } }));
      } else if (act === "get-pkg") {
        try {
          const response = await fetch(
            `/api/jobs/${job}/package/${ctx.language}/${btn.dataset.version}/file/${btn.dataset.name}`,
            { headers: ctx.api.authHeader() });
          if (!response.ok) throw new Error(`Download failed (${response.status})`);
          const url = URL.createObjectURL(await response.blob());
          const a = document.createElement("a"); a.href = url; a.download = `package-v${btn.dataset.version}-${btn.dataset.name}`; a.click();
          setTimeout(() => URL.revokeObjectURL(url), 5000);
        } catch (error) { out.textContent = error.message; }
      }
    }));
    const active = (ctx.pkgStatus.items || []).some((i) => ["QUEUED", "RUNNING"].includes(i.status));
    if (active) setTimeout(() => ctx.isCurrent() && ctx.refresh(), 2500);
  },
});
