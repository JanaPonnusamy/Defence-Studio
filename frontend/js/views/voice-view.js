// Voice tab: Voice Analyzer, Style Profiles, Voice Generation, History, Model status.
// Every control calls the real /api/voice/* backend; job state shown here is the
// server's persisted state (re-read on refresh / from another device). There is
// no client-side audio processing and no simulated progress: only the status the
// backend returns is displayed.
import { escapeHtml as esc } from "../components/status-badge.js";
import { newIdempotencyKey } from "../api.js";
import { singleFlight } from "../components/single-flight.js";

const PANELS = [
  ["analyzer", "Analyzer"], ["profiles", "Style profiles"], ["generation", "Generation"],
  ["history", "History"], ["models", "Voice models"],
];
const ACTIVE = new Set(["QUEUED", "RUNNING"]);

// ---- pure renderers (exported for tests) -----------------------------------

export function renderStatus(job) {
  const stage = job.stage ? ` · attempt ${job.stage.attempt}/${job.stage.max_attempts}` : "";
  const note = job.progress_note ? ` · ${esc(job.progress_note)}` : "";
  return `<span class="chip">${esc(job.status)}</span><span class="meta">${esc(job.kind)}${stage}${note}</span>`;
}

export function renderJobRowHtml(job) {
  const when = job.created_at ? new Date(job.created_at).toLocaleString() : "";
  return `<li class="voice-job" data-job="${esc(job.job_id)}">
    <div>${renderStatus(job)} <span class="meta">${esc(when)}</span></div>
    <div class="meta">${esc(job.job_id)} · ${esc(job.topic)}</div>
    ${job.error ? `<p class="error-state" role="alert">${esc(job.error)}</p>` : ""}
    <div class="voice-actions">
      ${job.result_ref ? `<button type="button" data-act="open" data-kind="${esc(job.kind)}" data-ref="${esc(job.result_ref)}">Open result</button>` : ""}
      ${job.can_cancel ? `<button type="button" data-act="cancel" data-job="${esc(job.job_id)}">Cancel</button>` : ""}
      ${job.can_retry ? `<button type="button" data-act="retry" data-job="${esc(job.job_id)}">Retry</button>` : ""}
      ${job.reference_temp_retained && !job.can_cancel ? `<button type="button" data-act="discard" data-job="${esc(job.job_id)}">Delete retained reference file</button>` : ""}
    </div></li>`;
}

function metric(label, value, unit = "") {
  const shown = value === null || value === undefined ? "unavailable" : `${value}${unit}`;
  return `<tr><th scope="row">${esc(label)}</th><td>${esc(shown)}</td></tr>`;
}

export function renderProfileHtml(p) {
  const pt = p.pitch || {};
  const sr = p.speaking_rate || {};
  const en = p.energy_profile || {};
  const pa = p.pause_profile || {};
  const em = (p.emotion_profile && p.emotion_profile.label_share) || {};
  const warn = (p.confidence && p.confidence.warnings) || [];
  return `<article class="card voice-profile" data-profile="${esc(p.voice_style_id)}">
    <h3>${esc(p.profile_name)} <span class="chip">v${p.version}</span> <span class="chip">read-only</span></h3>
    <p class="meta">${esc(p.voice_style_id)} · ${esc(p.language)} · analysis ${esc(p.analysis_version)} · hash ${esc((p.analysis_hash || "").slice(0, 12))}…</p>
    <p class="meta">Provenance: ${esc(p.provenance?.state)} · authorization ref “${esc(p.provenance?.authorization_ref)}” ·
      raw reference audio retained: ${p.provenance?.raw_audio_retained ? "yes" : "no"}</p>
    <p class="meta">Overall confidence: ${esc(p.confidence?.overall)}${warn.length ? ` · warnings: ${esc(warn.join(", "))}` : ""}</p>
    <table class="voice-table"><tbody>
      ${metric("Pitch median", pt.median_hz, " Hz")}${metric("Pitch range", pt.range_hz, " Hz")}
      ${metric("Pitch variation (semitone σ)", pt.variation_semitones_std)}${metric("Voiced ratio", pt.voiced_ratio)}
      ${metric("Syllable rate (estimated)", sr.syllables_per_sec, " /s")}
      ${metric("Words per minute (" + (sr.words_per_minute_kind || "n/a") + ")", sr.words_per_minute)}
      ${metric("Pauses", pa.count)}${metric("Longest pause", pa.max_ms, " ms")}
      ${metric("Mean loudness", en.mean_dbfs, " dBFS")}${metric("Dynamic range", en.dynamic_range_db, " dB")}
      ${metric("Speech density", p.rhythm_profile?.speech_density)}
    </tbody></table>
    <p class="meta">Delivery estimates (heuristic inference, not a validated emotion classifier):
      ${Object.keys(em).length ? Object.entries(em).map(([k, v]) => `${esc(k)} ${Math.round(v * 100)}%`).join(" · ") : "none"}</p>
    <ul class="meta">${(p.limitations || []).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>
    <p class="meta">Versions: ${(p.versions || []).map((v) => `v${v.version} (${esc(String(v.analysis_hash).slice(0, 8))})`).join(", ")}</p>
  </article>`;
}

export function renderModelsHtml(prov) {
  const rows = prov.voices.map((v) => `<li class="card voice-model" data-voice="${esc(v.voice_id)}">
      <h3>${esc(v.display_name)} <span class="chip">${esc(v.kind)}</span>
        <span class="chip">${v.ready ? "ready" : "not available"}</span></h3>
      <p class="meta">${esc(v.voice_id)} · languages: ${esc((v.languages || []).join(", ") || "none")} ·
        natural speech: ${v.natural_speech ? "yes" : "no"} · Tamil verified: ${v.tamil_verified ? "yes" : "no"}</p>
      ${v.license ? `<p class="meta">License: ${esc(v.license.speaker_license_ref)} · training ${v.license.training_allowed ? "allowed" : "not allowed"}
        · commercial ${v.license.commercial_use_allowed ? "allowed" : "not allowed"} · YouTube ${v.license.youtube_use_allowed ? "allowed" : "not allowed"}</p>` : ""}
      ${(v.supported_controls || []).length ? `<p class="meta">Controls applied: ${esc(v.supported_controls.join(", "))}</p>` : ""}
      ${(v.unsupported_controls || []).length ? `<p class="meta">Not applied by this model: ${esc(v.unsupported_controls.join(", "))}</p>` : ""}
      ${v.reason ? `<p class="inline-warn">${esc(v.reason)}</p>` : ""}</li>`).join("");
  const al = prov.aligner || {};
  return `<ul class="candidate-list">${rows}</ul>
    <article class="card"><h3>Alignment</h3>
      <p class="meta">Forced aligner: ${esc(al.name)} — ${al.available ? "available" : "not available"}. ${esc(al.reason)}</p>
      <p class="meta">${esc(al.license_note)}</p>
      <p class="meta">Without a forced aligner, sentence times are measured from the rendered audio and word times are proportional estimates.</p></article>
    <ul class="meta">${(prov.limitations || []).map((l) => `<li>${esc(l)}</li>`).join("")}</ul>`;
}

export function renderAlignmentHtml(al) {
  const label = { MEASURED_RENDER_BOUNDARY: "measured (render boundary)", ESTIMATED_PROPORTIONAL: "estimated (proportional)",
                  MODEL_DERIVED: "model-derived" };
  const rows = (items) => items.map((s) => `<tr><td>${esc(s.text)}</td><td>${s.start_ms}</td><td>${s.end_ms}</td>
      <td>${esc(label[s.accuracy] || s.accuracy || "unknown")}</td></tr>`).join("");
  return `<h4>Alignment v${esc(al.alignment_version)}</h4>
    <p class="meta">${esc(al.note || "")} Forced aligner run: ${al.aligner?.forced_alignment_run ? "yes" : "no"}.</p>
    <table class="voice-table"><thead><tr><th>Sentence</th><th>start ms</th><th>end ms</th><th>accuracy</th></tr></thead>
      <tbody>${rows(al.segments || [])}</tbody></table>
    <details><summary>Word timings (${(al.words || []).length})</summary>
      <table class="voice-table"><tbody>${rows(al.words || [])}</tbody></table></details>`;
}

export function renderCompareHtml(c) {
  const rows = c.metrics.map((m) => `<tr><th scope="row">${esc(m.metric)}</th><td>${esc(m.a ?? "–")}</td>
    <td>${esc(m.b ?? "–")}</td><td>${esc(m.delta ?? "–")}</td></tr>`).join("");
  return `<p class="meta">v${c.a} vs v${c.b} · ${c.identical ? "identical analysis hash" : "different analysis"} · neither version was modified.</p>
    <table class="voice-table"><thead><tr><th>Metric</th><th>v${c.a}</th><th>v${c.b}</th><th>Δ</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ---- mount -----------------------------------------------------------------

export async function mountVoice({ api, list, detail, filters, isCurrent }) {
  detail.innerHTML = "";
  detail.classList.remove("detail-overlay");
  let panel = "analyzer";
  let timer = null;
  const objectUrls = [];
  const once = singleFlight();

  const cleanup = () => { clearTimeout(timer); objectUrls.splice(0).forEach((u) => URL.revokeObjectURL(u)); };
  const setFilters = () => {
    filters.innerHTML = PANELS.map(([k, label]) =>
      `<button type="button" data-panel="${k}" aria-pressed="${k === panel}">${label}</button>`).join("");
    filters.querySelectorAll("[data-panel]").forEach((b) => b.addEventListener("click", () => { panel = b.dataset.panel; show(); }));
  };
  const err = (e) => `<p role="alert" class="error-state">${esc(e.message || String(e))}</p>`;
  const jobUrl = (id) => `/voice/jobs/${encodeURIComponent(id)}`;

  async function show() {
    cleanup();
    setFilters();
    list.innerHTML = '<p class="loading-state">Loading…</p>';
    try {
      if (panel === "analyzer") await analyzer();
      else if (panel === "profiles") await profiles();
      else if (panel === "generation") await generation();
      else if (panel === "history") await history();
      else await models();
    } catch (e) { if (isCurrent()) list.innerHTML = err(e); }
  }

  function poll(jobId, onUpdate) {
    const tick = async () => {
      if (!isCurrent()) return;
      try {
        const job = await api.request("GET", jobUrl(jobId));
        onUpdate(job);
        if (ACTIVE.has(job.status)) timer = setTimeout(tick, 2000);
      } catch (e) { onUpdate(null, e); }
    };
    tick();
  }

  // -- analyzer
  async function analyzer() {
    list.innerHTML = `<section class="card"><h3>Analyze reference audio / video</h3>
      <p class="meta">Delivery-style analysis only. Upload a file you are authorized to analyze; the raw audio is deleted after analysis unless retention is configured. URL acquisition is not available.</p>
      <form id="va-form">
        <label>File (wav, mp3, m4a, mp4, webm) <input type="file" name="file" required accept=".wav,.mp3,.m4a,.mp4,.webm,.ogg,.flac,.aac"></label>
        <label>Profile name <input name="profile_name" required maxlength="200"></label>
        <label>Language <select name="language"><option value="ta-IN">Tamil (ta-IN)</option><option value="en-US">English (en-US)</option></select></label>
        <label>Authorization / permission reference <input name="authorization_ref" required maxlength="500" placeholder="e.g. owned recording, written permission #"></label>
        <label>Provenance <select name="provenance_state"><option>AUTHORIZED</option><option>OWNED</option><option>LICENSED</option></select></label>
        <label>Transcript (optional — enables measured words/minute) <textarea name="transcript" rows="3" maxlength="20000"></textarea></label>
        <button type="submit">Start analysis</button>
      </form>
      <div id="va-status" aria-live="polite"></div></section>`;
    const form = list.querySelector("#va-form");
    const out = list.querySelector("#va-status");
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      return once(form.querySelector("button[type=submit]"), async () => {
      const fd = new FormData(form);
      for (const [k, v] of [...fd.entries()]) if (v === "") fd.delete(k);
      const headers = { ...api.authHeader() };
      delete headers["Content-Type"];
      headers["Idempotency-Key"] = newIdempotencyKey();
      out.innerHTML = '<p class="loading-state">Uploading…</p>';
      try {
        const res = await fetch("/api/voice/analysis", { method: "POST", headers, body: fd });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || res.statusText));
        watchAnalysis(body.job_id, out);
      } catch (e) { out.innerHTML = err(e); }
      });
    });
  }

  function watchAnalysis(jobId, out) {
    poll(jobId, async (job, e) => {
      if (!isCurrent()) return;
      if (e) { out.innerHTML = err(e); return; }
      out.innerHTML = `<div class="card">${renderStatus(job)}<p class="meta">Job ${esc(job.job_id)} — persisted on the server; safe to refresh.</p>
        ${job.error ? `<p class="error-state">${esc(job.error)}</p>` : ""}</div>`;
      if (job.status === "COMPLETED" && job.result_ref) {
        const p = await api.request("GET", `/voice/profiles/${encodeURIComponent(job.result_ref)}`);
        out.innerHTML += renderProfileHtml(p);
      }
    });
  }

  // -- profiles
  async function profiles() {
    const { profiles: rows } = await api.request("GET", "/voice/profiles");
    if (!isCurrent()) return;
    list.innerHTML = rows.length ? rows.map((p) => `<article class="card clickable" data-id="${esc(p.voice_style_id)}">
        <h3>${esc(p.profile_name)} <span class="chip">v${p.latest_version}</span></h3>
        <p class="meta">${esc(p.voice_style_id)} · ${esc(p.language)} · ${esc(p.provenance_state)}</p></article>`).join("")
      : '<p class="empty-state">No style profiles yet. Run an analysis first.</p>';
    list.querySelectorAll("[data-id]").forEach((c) => c.addEventListener("click", () => openProfile(c.dataset.id)));
  }

  async function openProfile(id, version) {
    const p = await api.request("GET", `/voice/profiles/${encodeURIComponent(id)}${version ? `?version=${version}` : ""}`);
    if (!isCurrent()) return;
    const opts = p.versions.map((v) => `<option value="${v.version}">v${v.version}</option>`).join("");
    detail.innerHTML = `${renderProfileHtml(p)}
      <div class="voice-actions">
        <label>View <select id="vp-ver">${opts}</select></label>
        ${p.versions.length > 1 ? `<label>Compare <select id="vp-a">${opts}</select> with <select id="vp-b">${opts}</select></label>
          <button type="button" id="vp-cmp">Compare</button>` : ""}
      </div><div id="vp-out"></div>`;
    detail.querySelector("#vp-ver").value = String(p.version);
    detail.querySelector("#vp-ver").addEventListener("change", (e) => openProfile(id, e.target.value));
    const cmp = detail.querySelector("#vp-cmp");
    if (cmp) {
      detail.querySelector("#vp-b").value = String(p.versions[p.versions.length - 1].version);
      cmp.addEventListener("click", async () => {
        const out = detail.querySelector("#vp-out");
        try {
          const a = detail.querySelector("#vp-a").value, b = detail.querySelector("#vp-b").value;
          out.innerHTML = renderCompareHtml(await api.request("GET", `/voice/profiles/${encodeURIComponent(id)}/compare?a=${a}&b=${b}`));
        } catch (e) { out.innerHTML = err(e); }
      });
    }
  }

  // -- generation
  async function generation() {
    const [prov, { profiles: styles }] = await Promise.all([api.request("GET", "/voice/providers"),
                                                            api.request("GET", "/voice/profiles")]);
    if (!isCurrent()) return;
    const usable = prov.voices.filter((v) => v.ready);
    const voiceOptions = prov.voices.map((v) =>
      `<option value="${esc(v.voice_id)}" ${v.ready ? "" : "disabled"}>${esc(v.display_name)}${v.ready ? "" : " — unavailable"}</option>`).join("");
    list.innerHTML = `<section class="card"><h3>Generate narration</h3>
      ${usable.length ? "" : '<p class="inline-blocked">No voice is ready. Tamil narration requires a provisioned, verified voice model.</p>'}
      <form id="vg-form">
        <label>Approved narration text <textarea name="script" rows="6" required maxlength="20000"></textarea></label>
        <label>Voice <select name="voice_id">${voiceOptions}</select></label>
        <label>Language <select name="language"></select></label>
        <p class="inline-warn" id="vg-note"></p>
        <label>Style profile (optional) <select name="style_profile_id"><option value="">None</option>
          ${styles.map((s) => `<option value="${esc(s.voice_style_id)}">${esc(s.profile_name)} v${s.latest_version}</option>`).join("")}</select></label>
        <label>Emotion <select name="emotion"><option>NEUTRAL</option><option>CALM</option><option selected>SERIOUS</option>
          <option>URGENT</option><option>DRAMATIC</option><option>CONFIDENT</option><option>CONCERNED</option></select></label>
        <label>Emotion intensity (0–1) <input type="number" name="intensity" min="0" max="1" step="0.05" value="0.7"></label>
        <label>Tempo (0.5–2.0) <input type="number" name="tempo" min="0.5" max="2" step="0.05" value="1"></label>
        <label>Pitch variation (0–1) <input type="number" name="pitch_variation" min="0" max="1" step="0.05" value="0.5"></label>
        <label>Energy (0–1) <input type="number" name="energy" min="0" max="1" step="0.05" value="0.7"></label>
        <label>Emphasis phrase (optional) <input name="emph_text" maxlength="200"></label>
        <label>Emphasis strength (0–1) <input type="number" name="emph_strength" min="0" max="1" step="0.05" value="0.8"></label>
        <label>Pause after each sentence, ms (0–5000) <input type="number" name="pause_ms" min="0" max="5000" step="50" value="300"></label>
        <p class="meta" id="vg-controls"></p>
        <button type="submit" id="vg-go">Generate</button>
      </form><div id="vg-status" aria-live="polite"></div></section>`;
    const form = list.querySelector("#vg-form");
    const voiceSel = form.elements.voice_id, langSel = form.elements.language;
    const refresh = () => {
      const v = prov.voices.find((x) => x.voice_id === voiceSel.value) || { languages: [] };
      langSel.innerHTML = (v.languages.length ? v.languages : ["ta-IN"]).map((l) => `<option>${esc(l)}</option>`).join("");
      list.querySelector("#vg-note").textContent = v.reason || (v.languages.includes("ta-IN") ? "" :
        "This voice does not declare Tamil. Tamil narration cannot be generated with it.");
      list.querySelector("#vg-controls").textContent =
        `Controls this model applies: ${(v.supported_controls || []).join(", ") || "none"}. ` +
        `Ignored: ${(v.unsupported_controls || []).join(", ") || "none"}.`;
      list.querySelector("#vg-go").disabled = !v.ready;
    };
    voiceSel.addEventListener("change", refresh);
    const firstReady = usable[0];
    if (firstReady) voiceSel.value = firstReady.voice_id;
    refresh();
    const out = list.querySelector("#vg-status");
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      return once(form.querySelector("button[type=submit]"), async () => {
      const f = form.elements;
      const direction = {
        voice_id: f.voice_id.value, language: f.language.value, style_profile_id: f.style_profile_id.value || null,
        emotion: { label: f.emotion.value, intensity: Number(f.intensity.value) }, tempo: Number(f.tempo.value),
        pitch_variation: Number(f.pitch_variation.value), energy: Number(f.energy.value),
        emphasis: f.emph_text.value.trim() ? [{ text: f.emph_text.value.trim(), strength: Number(f.emph_strength.value) }] : [],
        pauses: [{ position: "after_sentence", duration_ms: Number(f.pause_ms.value) }],
      };
      out.innerHTML = '<p class="loading-state">Submitting…</p>';
      try {
        const job = await api.request("POST", "/voice/generations", {
          body: { script: f.script.value, direction }, idempotencyKey: newIdempotencyKey() });
        watchGeneration(job.job_id, out);
      } catch (e) { out.innerHTML = err(e); }
      });
    });
  }

  function watchGeneration(jobId, out) {
    poll(jobId, async (job, e) => {
      if (!isCurrent()) return;
      if (e) { out.innerHTML = err(e); return; }
      out.innerHTML = `<div class="card">${renderStatus(job)}<p class="meta">Job ${esc(job.job_id)} — persisted on the server.</p>
        ${job.error ? `<p class="error-state">${esc(job.error)}</p>` : ""}</div>`;
      if (job.status === "COMPLETED" && job.result_ref) await showGeneration(job.result_ref, out);
    });
  }

  // What the style profile really changed: only `applied` controls had an effect.
  function renderProfileApplicationHtml(pa) {
    if (!pa) return "";
    const applied = (pa.applied || []).map((a) =>
      `<li>${esc(a.control)} = ${esc(a.value_ms ?? a.value)}${a.value_ms != null ? " ms" : ""} (${esc(a.basis)})</li>`).join("");
    const ignored = (pa.not_applied || []).map((n) => `<li>${esc(n.control)} — ${esc(n.reason)}</li>`).join("");
    return `<div class="profile-application">
      <p class="meta">Style profile ${esc(pa.style_profile_id)} v${esc(pa.profile_version)} — effect: <strong>${esc(pa.effect)}</strong>.
        Only the controls listed as applied changed the audio.</p>
      ${applied ? `<p class="meta">Applied</p><ul>${applied}</ul>` : ""}
      ${ignored ? `<p class="meta">Not applied (recorded only)</p><ul>${ignored}</ul>` : ""}</div>`;
  }

  async function showGeneration(gid, out) {
    const g = await api.request("GET", `/voice/generations/${encodeURIComponent(gid)}`);
    const al = await api.request("GET", `/voice/generations/${encodeURIComponent(gid)}/alignment`);
    // audio needs the bearer token, so fetch the bytes and play a local object URL
    const res = await fetch(g.audio_url, { headers: api.authHeader() });
    if (!res.ok) throw new Error(`Audio unavailable (${res.status})`);
    const url = URL.createObjectURL(await res.blob());
    objectUrls.push(url);
    const synthetic = g.voice && g.voice.natural_speech === false;
    out.innerHTML += `<article class="card"><h3>Generated audio</h3>
      ${synthetic ? '<p class="inline-warn">This is a synthetic test tone, not speech.</p>' : ""}
      <audio controls src="${url}"></audio>
      <p class="meta">${esc(g.generation_id)} · ${esc(g.voice_id)} · ${esc(g.language)} · ${g.duration_ms} ms (measured) ·
        ${g.sample_rate} Hz ${esc(g.audio_format)} · hash ${esc(String(g.generation_hash).slice(0, 12))}…</p>
      <p class="meta">Effective direction: tempo ${g.effective_direction.tempo}, pitch variation ${g.effective_direction.pitch_variation},
        energy ${g.effective_direction.energy}, emotion ${esc(g.effective_direction.emotion.label)}.</p>
      ${renderProfileApplicationHtml(g.profile_application)}
      <a href="${url}" download="${esc(g.generation_id)}.wav">Download WAV</a>
      ${renderAlignmentHtml(al)}</article>`;
  }

  // -- history
  async function history() {
    const { jobs } = await api.request("GET", "/voice/jobs");
    if (!isCurrent()) return;
    list.innerHTML = jobs.length ? `<ul class="history-list voice-jobs">${jobs.map(renderJobRowHtml).join("")}</ul>`
      : '<p class="empty-state">No voice jobs yet.</p>';
    list.addEventListener("click", async (ev) => {
      const b = ev.target.closest("button[data-act]");
      if (!b) return;
      try {
        if (b.dataset.act === "cancel") await api.request("POST", `${jobUrl(b.dataset.job)}/cancel`);
        else if (b.dataset.act === "retry") await api.request("POST", `${jobUrl(b.dataset.job)}/retry`, { idempotencyKey: newIdempotencyKey() });
        else if (b.dataset.act === "discard") await api.request("DELETE", `${jobUrl(b.dataset.job)}/reference`);
        else if (b.dataset.act === "open") {
          detail.innerHTML = "";
          if (b.dataset.kind === "ANALYSIS") await openProfile(b.dataset.ref);
          else { const box = document.createElement("div"); detail.appendChild(box); await showGeneration(b.dataset.ref, box); }
          return;
        }
        history();
      } catch (e) { detail.innerHTML = err(e); }
    }, { once: true });
  }

  // -- models
  async function models() {
    const prov = await api.request("GET", "/voice/providers");
    if (isCurrent()) list.innerHTML = renderModelsHtml(prov);
  }

  await show();
  return cleanup;
}
