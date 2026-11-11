// Production tab: post-script stages (scenes, visual plan, audio, render, QC,
// package, publish). Each stage is a section module registered in SECTIONS;
// every control calls the real backend. State shown is persisted server state,
// re-read on every render (refresh / another device shows the same thing).
import { escapeHtml as esc } from "../components/status-badge.js";
import { newIdempotencyKey } from "../api.js";

export const SECTIONS = [];

export function registerSection(section) {
  if (section.first) SECTIONS.unshift(section); else SECTIONS.push(section);
}

export async function mountProduction({ api, jobId, list, detail, filters, isCurrent }) {
  detail.innerHTML = "";
  detail.classList.remove("detail-overlay");
  filters.innerHTML = `<label for="prod-language">Language
      <select id="prod-language"><option value="ta">Tamil</option><option value="en">English</option></select></label>`;
  const languageSelect = filters.querySelector("#prod-language");
  try {  // default to the job's own language so a populated job is not shown as empty
    const jobInfo = await api.request("GET", `/jobs/${encodeURIComponent(jobId)}`);
    if (["ta", "en"].includes(jobInfo.language)) languageSelect.value = jobInfo.language;
  } catch (_) { /* keep the default */ }
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
    // A 409 means the server's own freshness/prerequisite check just fired --
    // the UI's cached readiness (computed before this click) is now known
    // wrong. Re-render from the server rather than leaving stale-looking
    // buttons enabled until the next unrelated refresh.
    if (error && error.status === 409) ctx.refresh();
  }
}

// -- Scenes + visual plan section ----------------------------------------------

export function renderScenesHtml(plans, selected, { canGenerate = false, staleReason = "" } = {}) {
  const blockedNote = staleReason
    ? `<p class="meta" role="alert">The approved script is no longer current (${esc(staleReason)}). Re-approve the script, then regenerate the scene plan.</p>`
    : '<p class="meta">Approve the script on the Script tab before generating a plan.</p>';
  if (!plans.length) {
    return `<p class="meta">No scene plan yet. An approved script is required.</p>
      <button type="button" data-act="generate-plan" ${canGenerate ? "" : "disabled"}>Generate scene plan from approved script</button>
      ${canGenerate ? "" : blockedNote}`;
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
  // Approval also requires the bound script revision still be current -- a
  // plan built on a now-superseded script cannot be approved server-side
  // (SceneStaleRevision/409) even while its own status is still DRAFT/
  // AWAITING_REVIEW, so staleReason gates this the same as generation.
  const canApprove = (latest.status === "DRAFT" || latest.status === "AWAITING_REVIEW") && !staleReason;
  return `<p class="meta">Version ${latest.version} · ${statusChip(latest.status, !!staleReason)} · ${latest.scenes.length} scene(s)</p>
    ${staleReason ? `<p class="meta" role="alert">This plan's script revision is no longer current (${esc(staleReason)}); it cannot be approved. Regenerate after re-approving the script.</p>` : ""}
    <ol class="scene-list">${rows}</ol>
    <label>Approval note <input type="text" id="plan-note" maxlength="500"></label>
    <button type="button" data-act="approve-plan" data-version="${latest.version}" ${canApprove ? "" : "disabled"}>Approve this plan</button>
    <button type="button" data-act="regenerate-plan" ${canGenerate ? "" : "disabled"}>Regenerate from approved script</button>
    ${canGenerate ? "" : blockedNote}`;
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
    // Readiness is the authoritative server freshness result, not a bare
    // "an APPROVED row exists" check: an approved revision that the evidence
    // package has since outgrown is reported APPROVED but STALE, and the
    // server refuses to generate/approve a plan against it (409).
    let canGenerate = false, staleReason = "";
    try {
      const [scripts, staleness] = await Promise.all([
        ctx.api.request("GET", `/jobs/${encodeURIComponent(ctx.jobId)}/script/revisions?language=${encodeURIComponent(ctx.language)}`),
        ctx.api.getScriptStaleness(ctx.jobId, ctx.language).catch(() => null),
      ]);
      const staleByKey = new Map(((staleness && staleness.revisions) || [])
        .map((r) => [`${r.language}:${r.version}`, r]));
      const approved = (scripts.revisions || []).find((row) => row.status === "APPROVED");
      const current = approved && staleByKey.get(`${approved.language}:${approved.version}`);
      canGenerate = !!(approved && current && current.state === "CURRENT");
      if (approved && current && current.state === "STALE") staleReason = current.reason;
    } catch (_) { canGenerate = false; }
    return renderScenesHtml(plans, null, { canGenerate, staleReason }) + '<div data-action-status role="status"></div>';
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

// Provider + voice come from what the server/audio module actually declare; nothing is hard-coded here.
export function renderVoicePickerHtml(status) {
  const declared = Object.keys((status && status.provider_languages) || {});
  const def = (status && status.default_provider) || declared[0] || "";
  const names = declared.includes(def) || !def ? declared : [def, ...declared];
  const voices = (status && status.voices) || [];
  const suggested = status && status.suggested_voice;
  const terms = (status && status.provider_terms) || {};
  const options = (provider) => {
    const own = voices.filter((v) => v.provider === provider);
    return `<option value="">Provider default voice</option>${own.map((v) => `<option value="${esc(v.voice_id)}"
      ${v.voice_id === suggested ? "selected" : ""} ${v.available ? "" : "disabled"}>${esc(v.voice_id)} (${esc(v.locale)})${
      v.available ? "" : " — unavailable"}</option>`).join("")}`;
  };
  return `<div class="voice-picker">
    <label>Provider <select id="audio-provider">${names.map((n) =>
      `<option value="${esc(n)}" ${n === def ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
    <label>Voice <select id="audio-voice">${options(def)}</select></label>
    <p class="meta" id="audio-selected" aria-live="polite"></p>
    ${status && status.voices_error ? `<p class="inline-warn">Voice list unavailable: ${esc(status.voices_error)}</p>` : ""}
    ${Object.entries(terms).map(([n, t]) => `<p class="inline-warn" data-terms-for="${esc(n)}">${esc(n)}: ${esc(t)}</p>`).join("")}
  </div>`;
}

export function voiceOptionsFor(status, provider) {
  return ((status && status.voices) || []).filter((v) => v.provider === provider);
}

export function renderAudioHtml(status, setBody, { canGenerate = false, staleReason = "" } = {}) {
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
    ${renderVoicePickerHtml(status)}
    <button type="button" data-act="generate-audio" ${canGenerate ? "" : "disabled"}>Generate narration audio</button>
    ${canGenerate ? "" : `<p class="meta">${staleReason
        ? `The approved scene plan is no longer current (${esc(staleReason)}). Re-sync/regenerate it before generating audio.`
        : "An approved scene plan is required before narration audio can be generated."}</p>`}`;
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
    // An APPROVED scene plan is not sufficient on its own: the server
    // (`_approved_plan`) additionally requires the script revision the plan
    // was built from (`script_version`) to still be CURRENT per
    // /script/staleness, and fails closed (409 IntegrationStale) otherwise.
    // Re-derive that same freshness check here so the button does not show
    // enabled for a plan the server would reject.
    let canGenerate = false, staleReason = "";
    if (ctx.plan && ctx.plan.status === "APPROVED") {
      try {
        const staleness = await ctx.api.getScriptStaleness(ctx.jobId, ctx.language);
        const bound = (staleness.revisions || []).find((r) =>
          r.language === ctx.language && r.version === ctx.plan.script_version);
        canGenerate = !!(bound && bound.state === "CURRENT");
        if (!canGenerate) staleReason = (bound && bound.reason) || "the approved script revision this plan was built from is no longer current";
      } catch (_) { canGenerate = false; }
    }
    return renderAudioHtml(status, ctx.audioSet, { canGenerate, staleReason }) + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    const job = encodeURIComponent(ctx.jobId);
    const providerSel = node.querySelector("#audio-provider"), voiceSel = node.querySelector("#audio-voice");
    const showSelected = () => {
      const label = node.querySelector("#audio-selected");
      if (label) label.textContent = `Selected: provider ${providerSel.value || "none"} · voice ${voiceSel.value || "provider default"}`;
    };
    if (providerSel && voiceSel) {
      providerSel.addEventListener("change", () => {
        voiceSel.innerHTML = '<option value="">Provider default voice</option>' + voiceOptionsFor(ctx.audio, providerSel.value)
          .map((v) => `<option value="${esc(v.voice_id)}" ${v.available ? "" : "disabled"}>${esc(v.voice_id)} (${esc(v.locale)})</option>`).join("");
        showSelected();
      });
      voiceSel.addEventListener("change", showSelected);
      showSelected();
    }
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      if (act === "generate-audio") {
        const provider = node.querySelector("#audio-provider").value;
        const voice_id = node.querySelector("#audio-voice").value || null;
        runAction(ctx, node, "Starting", () => ctx.api.request("POST", `/jobs/${job}/audio/${ctx.language}/generate`,
          { body: { provider, voice_id }, idempotencyKey: newIdempotencyKey() }));
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

// -- YouTube connection and publishing queue (RM-8) ----------------------------------

export function renderYoutubeConnectionHtml(data) {
  const cfg = (data && data.config) || {};
  const conns = (data && data.connections) || [];
  const warn = cfg.configured ? "" : `<div class="banner warn">YouTube is not configured on the server. Missing:
    ${(cfg.missing || []).map(esc).join(", ")}. Nothing can be connected or uploaded until these are set.</div>`;
  const rows = conns.map((c) => `
    <li>${statusChip(c.status)} <strong>${esc(c.channel_title)}</strong>
      <span class="meta">${esc(c.channel_id)} ${c.channel_handle ? "· " + esc(c.channel_handle) : ""}
      · scopes: ${(c.granted_scopes || []).length}</span>
      ${c.channel_mismatch ? '<span class="chip">NOT THE EXPECTED CHANNEL</span>' : ""}
      ${c.status === "PENDING_CONFIRMATION" ? `<button type="button" data-act="yt-confirm" data-id="${esc(c.connection_id)}"
          data-channel="${esc(c.channel_id)}">This is my channel — confirm</button>` : ""}
      ${["CONNECTED", "REAUTH_REQUIRED", "PENDING_CONFIRMATION"].includes(c.status)
        ? `<button type="button" data-act="yt-disconnect" data-id="${esc(c.connection_id)}">Disconnect</button>` : ""}
    </li>`).join("");
  return `${warn}${rows ? `<ul class="run-list">${rows}</ul>` : '<p class="meta">No YouTube channel connected.</p>'}
    <p class="meta">Tokens are stored encrypted on the server and never shown here. Real upload behaviour is unverified
    until a private test upload has succeeded on a connected channel.</p>
    <button type="button" data-act="yt-connect" ${cfg.configured ? "" : "disabled"}>Connect YouTube</button>`;
}

export function renderPublishQueueHtml(queue, packages, connections) {
  const jobs = (queue && queue.jobs) || [];
  const rows = jobs.map((j) => {
    const phrase = j.required_confirmation;
    const awaiting = j.state === "AWAITING_APPROVAL";
    return `<li class="scene-row" data-publish="${esc(j.publish_id)}">
      <div><strong>${esc(j.title)}</strong> ${statusChip(j.state)}
        <span class="chip">${esc(j.visibility)}${j.scheduled_utc ? " · scheduled " + esc(j.scheduled_utc) : ""}</span>
        ${j.youtube_video_id ? `<a href="https://www.youtube.com/watch?v=${encodeURIComponent(j.youtube_video_id)}" target="_blank" rel="noopener">open on YouTube</a>` : ""}
      </div>
      <div class="meta">Progress ${Math.round((j.progress || 0) * 100)}% · ${Number(j.bytes_uploaded || 0)}/${Number(j.bytes_total || 0)} bytes ·
        thumbnail ${esc(j.thumbnail_state)} · attempts ${j.attempts}/${j.max_attempts}
        ${j.error_message ? "· " + esc(j.error_message) : ""}</div>
      ${awaiting && !j.approved ? `
        <label>Approval note <input type="text" data-note maxlength="500"></label>
        ${phrase ? `<label>Type <code>${esc(phrase)}</code> to confirm <input type="text" data-confirm></label>` : ""}
        <button type="button" data-act="yt-approve" data-id="${esc(j.publish_id)}">Approve exactly this metadata</button>` : ""}
      ${awaiting && j.approved ? `<button type="button" data-act="yt-queue" data-id="${esc(j.publish_id)}">Upload now</button>` : ""}
      ${["FAILED_RETRYABLE", "REAUTH_REQUIRED"].includes(j.state) && j.approved
        ? `<button type="button" data-act="yt-queue" data-id="${esc(j.publish_id)}">Resume</button>` : ""}
      ${j.youtube_video_id && j.thumbnail_state === "FAILED"
        ? `<button type="button" data-act="yt-retry" data-part="thumbnail" data-id="${esc(j.publish_id)}">Retry thumbnail</button>` : ""}
      ${!j.is_terminal ? `<button type="button" data-act="yt-cancel" data-id="${esc(j.publish_id)}">Cancel</button>` : ""}
      <button type="button" data-act="yt-detail" data-id="${esc(j.publish_id)}">Timeline</button>
      <ol class="yt-events" data-events-for="${esc(j.publish_id)}" hidden></ol>
    </li>`;
  }).join("");
  const eligible = (packages || []).filter((p) => p.status === "APPROVED" && !p.stale);
  const connected = (connections || []).filter((c) => c.status === "CONNECTED");
  const ready = eligible.length > 0 && connected.length > 0;
  return `${rows ? `<ul class="scene-list">${rows}</ul>` : '<p class="meta">No publishing jobs.</p>'}
    <p class="meta">Quota used today (estimate): ${Number((queue && queue.quota_used_today_estimate) || 0)} /
    ${Number((queue && queue.daily_quota_limit) || 0)}. Uploads are always created private; any other outcome needs a typed confirmation.</p>
    <button type="button" data-act="yt-create" ${ready ? "" : "disabled"}>Create publishing job from latest approved package (private)</button>
    ${ready ? "" : '<p class="meta">Needs an approved, current package and a confirmed channel connection.</p>'}`;
}

registerSection({
  id: "yt-connection", title: "YouTube channel",
  async render(ctx) {
    const data = await ctx.api.request("GET", "/youtube/connections");
    ctx.ytConnections = data;
    return renderYoutubeConnectionHtml(data) + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      if (act === "yt-connect") {
        try {
          const r = await ctx.api.request("POST", "/youtube/connect/start");
          window.location.assign(r.authorization_url);
        } catch (e) { node.querySelector("[data-action-status]").textContent = e.message; }
      } else if (act === "yt-confirm") {
        runAction(ctx, node, "Confirming", () => ctx.api.request("POST", `/youtube/connections/${btn.dataset.id}/confirm`,
          { body: { channel_id: btn.dataset.channel } }));
      } else if (act === "yt-disconnect") {
        runAction(ctx, node, "Disconnecting", () => ctx.api.request("POST", `/youtube/connections/${btn.dataset.id}/disconnect`));
      }
    }));
  },
});

registerSection({
  id: "yt-queue", title: "Publishing queue",
  async render(ctx) {
    const queue = await ctx.api.request("GET", "/youtube/jobs");
    ctx.ytQueue = queue;
    return renderPublishQueueHtml(queue, (ctx.pkgStatus && ctx.pkgStatus.items) || [],
      (ctx.ytConnections && ctx.ytConnections.connections) || []) + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
      const act = btn.dataset.act, id = btn.dataset.id;
      const row = btn.closest("[data-publish]");
      if (act === "yt-create") {
        const pkg = [...ctx.pkgStatus.items].reverse().find((p) => p.status === "APPROVED" && !p.stale);
        const conn = ctx.ytConnections.connections.find((c) => c.status === "CONNECTED");
        runAction(ctx, node, "Creating job", () => ctx.api.request("POST", "/youtube/jobs", {
          body: { job_id: ctx.jobId, language: ctx.language, package_version: pkg.version, connection_id: conn.connection_id },
          idempotencyKey: newIdempotencyKey() }));
      } else if (act === "yt-approve") {
        const note = row.querySelector("[data-note]").value.trim();
        const confirmEl = row.querySelector("[data-confirm]");
        const confirmation = confirmEl ? confirmEl.value.trim() : null;
        if (!note) { node.querySelector("[data-action-status]").textContent = "Enter an approval note."; return; }
        runAction(ctx, node, "Approving", async () => {
          const job = await ctx.api.request("GET", `/youtube/jobs/${id}`);
          await ctx.api.request("POST", `/youtube/jobs/${id}/approve`,
            { body: { approval_digest: job.approval_digest_current, note, confirmation } });
        });
      } else if (act === "yt-queue") {
        runAction(ctx, node, "Queueing", () => ctx.api.request("POST", `/youtube/jobs/${id}/queue`));
      } else if (act === "yt-cancel") {
        runAction(ctx, node, "Cancelling", () => ctx.api.request("POST", `/youtube/jobs/${id}/cancel`));
      } else if (act === "yt-retry") {
        runAction(ctx, node, "Retrying", () => ctx.api.request("POST", `/youtube/jobs/${id}/retry-part`,
          { body: { part: btn.dataset.part } }));
      } else if (act === "yt-detail") {
        const job = await ctx.api.request("GET", `/youtube/jobs/${id}`);
        const list = row.querySelector("[data-events-for]");
        list.innerHTML = job.events.map((e) => `<li>${esc(e.occurred_at)} — ${esc(e.event)} ${esc(e.new_state || "")}
          <span class="meta">${esc(e.detail)}</span></li>`).join("");
        list.hidden = false;
      }
    }));
    const live = ["QUEUED", "VALIDATING", "UPLOADING", "UPLOAD_RETRY_WAIT", "THUMBNAIL_UPLOADING",
      "CAPTIONS_UPLOADING", "PUBLISHING", "VERIFYING"];
    const active = ((ctx.ytQueue && ctx.ytQueue.jobs) || []).some((j) => live.includes(j.state));
    if (active) setTimeout(() => ctx.isCurrent() && ctx.refresh(), 3000);
  },
});

// -- Pipeline overview, usage and credentials (RM-9) -----------------------------------

export function renderOverviewHtml(overview) {
  if (!overview) return "";
  const rows = overview.stages.map((s) => `
    <li data-stage="${esc(s.stage)}">${statusChip(s.status)} <strong>${esc(s.stage.replace(/_/g, " "))}</strong>
      ${s.version != null ? `<span class="meta">v${s.version}</span>` : ""}
      ${s.detail ? `<span class="meta">${esc(s.detail)}</span>` : ""}</li>`).join("");
  const attention = overview.needs_attention.length
    ? `<div class="banner warn">Needs attention: ${overview.needs_attention.map((x) => esc(x)).join(", ")}</div>` : "";
  const workflow = overview.workflow;
  const queue = workflow ? `<div data-workflow-status="${esc(workflow.status)}">
    <strong>Workflow queue</strong> ${statusChip(workflow.status)}
    <span class="meta">${esc(workflow.stages_completed)} / ${esc(workflow.stages_total)} stages complete.
    ${workflow.next_stage ? `Next stage: ${esc(workflow.next_stage.replace(/_/g, " "))}.` : ""}
    Media artifact states below are separate from queue completion.</span></div>` : "";
  return `${attention}${queue}<ol class="run-list">${rows}</ol>
    <p class="meta">Derived from saved state; the same on every device and after a restart.</p>`;
}

export function renderUsageHtml(usage) {
  if (!usage) return "";
  const t = usage.text_generation;
  const money = (v) => (v == null ? "not measured" : `₹${Number(v).toFixed(2)}`);
  return `<ul class="run-list">
    <li>Text generation: ${money(t.total_inr)} of a ₹${t.target_inr} target
      ${t.within_target === true ? "· within target" : t.within_target === false ? "· OVER target" : ""}
      <span class="meta">(${t.calls.length} call(s); ${t.unmeasured_calls} unmeasured)</span></li>
    <li>Voice (TTS): ${money(usage.tts.total_inr)}</li>
    <li>Image generation: ${usage.image_generation.enabled ? money(usage.image_generation.total_inr) : "not enabled"}</li>
    <li>YouTube quota (estimate): ${Number(usage.youtube_quota_units_estimate)} units</li></ul>
    <p class="meta">${esc(t.note)}</p>`;
}

export function renderCredentialsHtml(status) {
  const rows = ((status && status.credentials) || []).map((c) => `
    <li><strong>${esc(c.provider)}</strong> ${statusChip(c.configured ? "CONFIGURED" : "NOT SET")}
      ${c.source ? `<span class="meta">source: ${esc(c.source)}${c.last4 ? ` · ends ${esc(c.last4)}` : ""}</span>` : ""}
      <input type="password" autocomplete="off" data-cred-input="${esc(c.provider)}" placeholder="paste new key">
      <button type="button" data-act="cred-set" data-provider="${esc(c.provider)}">Save</button>
      ${c.source === "owner" ? `<button type="button" data-act="cred-del" data-provider="${esc(c.provider)}">Remove</button>` : ""}
    </li>`).join("");
  return `<ul class="run-list">${rows}</ul>
    <p class="meta">Keys are encrypted on the server and never displayed again; only the last four characters are shown.</p>`;
}

registerSection({
  id: "overview", title: "Pipeline status", first: true,
  async render(ctx) {
    const job = encodeURIComponent(ctx.jobId);
    return renderOverviewHtml(await ctx.api.request("GET", `/jobs/${job}/pipeline/${ctx.language}`));
  },
});

registerSection({
  id: "usage", title: "Usage and cost",
  async render(ctx) {
    return renderUsageHtml(await ctx.api.request("GET", `/jobs/${encodeURIComponent(ctx.jobId)}/usage`));
  },
});

registerSection({
  id: "credentials", title: "Provider credentials",
  async render(ctx) {
    return renderCredentialsHtml(await ctx.api.request("GET", "/settings/credentials"))
      + '<div data-action-status role="status"></div>';
  },
  wire(ctx, node) {
    node.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", () => {
      const provider = btn.dataset.provider;
      if (btn.dataset.act === "cred-set") {
        const input = node.querySelector(`[data-cred-input="${provider}"]`);
        const value = input.value.trim();
        input.value = "";  // never keep the secret in the page
        runAction(ctx, node, "Saving", () => ctx.api.request("PUT", `/settings/credentials/${provider}`, { body: { value } }));
      } else {
        runAction(ctx, node, "Removing", () => ctx.api.request("DELETE", `/settings/credentials/${provider}`));
      }
    }));
  },
});
