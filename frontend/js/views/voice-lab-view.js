// Voice Lab tab (Studio Phase 2): choose a declared provider/voice, set only the
// controls that provider reports, generate short previews, compare them, and
// save + explicitly approve a voice profile for this job's narration.
// All state is the server's (GET /jobs/{id}/voice-lab/{lang}); approval is
// refused by the server unless a preview of the exact configuration exists.
import { escapeHtml as esc } from "../components/status-badge.js";
import { newIdempotencyKey } from "../api.js";
import { singleFlight } from "../components/single-flight.js";

const STEPS = { rate_wpm: 1, volume: 0.05, pitch_semitones: 0.25 };
const LABELS = { rate_wpm: "Speaking rate (wpm)", volume: "Volume", pitch_semitones: "Pitch (semitones)" };

export function configKey(provider, voiceId, controls) {
  const sorted = Object.keys(controls || {}).sort().reduce((o, k) => ({ ...o, [k]: controls[k] }), {});
  return JSON.stringify([provider, voiceId, sorted]);
}

function bindingKey(b) { return configKey(b.provider, b.voice_id, b.controls); }

// The audio module reports the voice as an id string; adapters may also give {id, languages}.
function voiceName(voice) { return typeof voice === "string" ? voice : (voice && voice.id) || "voice not reported"; }

export function renderVoiceFormHtml(state, sel) {
  if (state.provider_error) return `<p role="alert" class="error-state">${esc(state.provider_error)}</p>`;
  const providers = state.providers.filter((p) => p.available);
  if (!providers.length) {
    return '<p class="empty-state">No TTS provider is declared and available for this language. Declare one in DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES.</p>';
  }
  const provider = providers.find((p) => p.provider === sel.provider) || providers[0];
  const voices = provider.voices.map((v) => `<option value="${esc(v.id)}" ${v.id === sel.voice_id ? "selected" : ""}>${esc(v.id)}</option>`).join("");
  const controls = Object.entries(provider.controls).map(([key, spec]) => {
    const [low, high] = spec.range;
    const on = key in sel.controls;
    const value = on ? sel.controls[key] : (low + high) / 2;
    return `<div class="vl-control">
      <label><input type="checkbox" data-control-on="${esc(key)}" ${on ? "checked" : ""}> ${esc(LABELS[key] || key)}</label>
      <input type="range" data-control="${esc(key)}" min="${low}" max="${high}" step="${STEPS[key] || 0.1}" value="${value}"
        aria-label="${esc(LABELS[key] || key)}" ${on ? "" : "disabled"}>
      <output data-control-value="${esc(key)}">${on ? esc(value) : "provider default"}</output>
      <span class="meta">${esc(spec.kind || "")}</span></div>`;
  }).join("");
  const unsupported = Object.entries(state.unsupported || {})
    .map(([k, why]) => `<li><strong>${esc(k.replace(/_/g, " "))}</strong>: ${esc(why)}</li>`).join("");
  return `<form data-voice-form>
      <label>Provider <select data-field="provider">${providers.map((p) => `<option value="${esc(p.provider)}" ${p.provider === provider.provider ? "selected" : ""}>${esc(p.provider)} (${esc(p.model || "model unknown")})</option>`).join("")}</select></label>
      ${provider.network ? `<p class="meta">${esc(provider.notes || "Online provider: text leaves this machine.")}</p>` : ""}
      <label>Voice <select data-field="voice_id">${voices}</select></label>
      ${controls}
      <label>Preview text <span class="meta">(up to ${state.preview_max_chars} characters)</span>
        <textarea data-field="text" rows="3" maxlength="${state.preview_max_chars}" lang="${esc(state.language)}">${esc(sel.text)}</textarea></label>
      <div class="vl-actions">
        <button type="button" data-act="preview">Generate preview</button>
        <button type="button" data-act="save-profile">Save as voice profile</button>
      </div>
      <div data-action-status role="status"></div>
    </form>
    <details><summary>Not supported</summary><ul class="run-list">${unsupported}</ul></details>`;
}

export function renderPreviewsHtml(previews) {
  if (!previews.length) return '<p class="meta">No previews yet.</p>';
  return `<ol class="run-list">${[...previews].reverse().map((p) => {
    const r = p.result;
    const b = p.input_binding;
    const controls = Object.entries(b.controls).map(([k, v]) => `${k}=${v}`).join(", ") || "provider defaults";
    return `<li data-preview="${p.version}">
      <span class="chip">${esc(p.status)}</span> <strong>v${p.version}</strong>
      <span class="meta">${esc(b.provider)} · ${esc(b.voice_id)} · ${esc(controls)}</span>
      ${r ? `<p class="meta">Used: ${esc(r.provider)} / ${esc(r.model)} / ${esc(voiceName(r.voice))} ·
        ${esc(r.duration_seconds)}s · confirmed: ${esc(Object.keys(r.applied_controls).join(", ") || "none")}
        ${r.unconfirmed_controls.length ? ` · <strong>not confirmed: ${esc(r.unconfirmed_controls.join(", "))}</strong>` : ""}</p>
        <p lang="${esc(r.language || "")}">${esc(r.text)}</p>
        <button type="button" data-act="play" data-version="${p.version}">Play</button>
        <audio controls hidden data-audio-for="${p.version}"></audio>` : ""}
      ${p.status === "FAILED" ? `<p role="alert" class="meta">${esc(p.detail)}</p>` : ""}
      <button type="button" data-act="use" data-version="${p.version}">Use these settings</button></li>`;
  }).join("")}</ol>`;
}

export function renderProfilesHtml(profiles, previews) {
  if (!profiles.length) return '<p class="meta">No voice profile saved. Narration uses the per-language default voice until one is approved.</p>';
  const heard = new Set(previews.filter((p) => p.status === "SUCCEEDED" && p.result && !p.result.is_mock)
    .map((p) => bindingKey(p.input_binding)));
  return `<ol class="run-list">${[...profiles].reverse().map((p) => {
    const b = p.input_binding;
    const controls = Object.entries(b.controls).map(([k, v]) => `${k}=${v}`).join(", ") || "provider defaults";
    const reviewable = p.status === "AWAITING_REVIEW";
    const canApprove = reviewable && heard.has(bindingKey(b));
    return `<li data-profile="${p.version}" data-status="${esc(p.status)}">
      <span class="chip">${esc(p.status)}</span> <strong>Profile v${p.version}</strong>
      <span class="meta">${esc(b.provider)} · ${esc(b.voice_id)} · ${esc(controls)}</span>
      ${p.detail && !reviewable ? `<p class="meta">${esc(p.detail)}</p>` : ""}
      ${reviewable ? `<div class="vl-review">
        <input type="text" data-note="${p.version}" maxlength="500" placeholder="Review note (required)" aria-label="Review note for profile v${p.version}">
        <button type="button" data-act="approve" data-version="${p.version}" ${canApprove ? "" : "disabled"}>Approve</button>
        <button type="button" data-act="reject" data-version="${p.version}">Reject</button>
        ${canApprove ? "" : '<span class="meta">Generate a preview of exactly these settings before approving.</span>'}</div>` : ""}
    </li>`;
  }).join("")}</ol>`;
}

export async function mountVoiceLab({ api, jobId, list, detail, filters, isCurrent }) {
  filters.innerHTML = `<label for="vl-language">Language
      <select id="vl-language"><option value="ta">Tamil</option><option value="en">English</option></select></label>`;
  const languageSelect = filters.querySelector("#vl-language");
  try {
    const job = await api.request("GET", `/jobs/${encodeURIComponent(jobId)}`);
    if (["ta", "en"].includes(job.language)) languageSelect.value = job.language;
  } catch (_) { /* keep the default */ }
  const flight = singleFlight();
  let state = null;
  let sel = { provider: null, voice_id: null, controls: {}, text: "" };
  const base = () => `/jobs/${encodeURIComponent(jobId)}/voice-lab/${encodeURIComponent(languageSelect.value)}`;

  function syncSelection() {
    const providers = state.providers.filter((p) => p.available);
    const provider = providers.find((p) => p.provider === sel.provider) || providers[0];
    if (!provider) return;
    sel.provider = provider.provider;
    if (!provider.voices.some((v) => v.id === sel.voice_id)) sel.voice_id = provider.voices[0] && provider.voices[0].id;
    sel.controls = Object.fromEntries(Object.entries(sel.controls).filter(([k]) => k in provider.controls));
    if (!sel.text) sel.text = state.sample_text || "";
  }

  function readForm() {
    const form = list.querySelector("[data-voice-form]");
    if (!form) return;
    sel.provider = form.querySelector('[data-field="provider"]').value;
    sel.voice_id = form.querySelector('[data-field="voice_id"]').value;
    sel.text = form.querySelector('[data-field="text"]').value;
    sel.controls = {};
    form.querySelectorAll("[data-control]").forEach((input) => {
      if (form.querySelector(`[data-control-on="${input.dataset.control}"]`).checked) {
        sel.controls[input.dataset.control] = Number(input.value);
      }
    });
  }

  async function act(button, label, fn) {
    const out = list.querySelector("[data-action-status]") || detail.querySelector("[data-action-status]");
    await flight(button, async () => {
      if (out) out.textContent = `${label}…`;
      try { await fn(); await load(); } catch (error) { if (out) out.textContent = error.message || String(error); }
    });
  }

  function render() {
    syncSelection();
    list.innerHTML = `<h2>Voice settings</h2>${renderVoiceFormHtml(state, sel)}
      <h2>Voice profiles</h2>${renderProfilesHtml(state.profiles, state.previews)}`;
    detail.innerHTML = `<h2>Previews</h2><p class="meta">Each preview names the provider, model and voice that actually spoke it.</p>
      ${renderPreviewsHtml(state.previews)}<div data-action-status role="status"></div>`;
    const form = list.querySelector("[data-voice-form]");
    if (form) {
      form.querySelector('[data-field="provider"]').addEventListener("change", () => { readForm(); render(); });
      form.querySelectorAll("[data-control-on]").forEach((box) => box.addEventListener("change", () => { readForm(); render(); }));
      form.querySelectorAll("[data-control]").forEach((input) => input.addEventListener("input", () => {
        form.querySelector(`[data-control-value="${input.dataset.control}"]`).textContent = input.value;
      }));
      form.querySelector('[data-act="preview"]').addEventListener("click", (e) => { readForm(); act(e.currentTarget, "Generating preview", () =>
        api.request("POST", `${base()}/previews`, { body: { provider: sel.provider, voice_id: sel.voice_id, controls: sel.controls, text: sel.text }, idempotencyKey: newIdempotencyKey() })); });
      form.querySelector('[data-act="save-profile"]').addEventListener("click", (e) => { readForm(); act(e.currentTarget, "Saving profile", () =>
        api.request("POST", `${base()}/profiles`, { body: { provider: sel.provider, voice_id: sel.voice_id, controls: sel.controls }, idempotencyKey: newIdempotencyKey() })); });
    }
    list.querySelectorAll('[data-act="approve"], [data-act="reject"]').forEach((btn) => btn.addEventListener("click", () => {
      const note = list.querySelector(`[data-note="${btn.dataset.version}"]`).value;
      act(btn, btn.dataset.act === "approve" ? "Approving" : "Rejecting", () => api.request("POST",
        `${base()}/profiles/${btn.dataset.version}/review`,
        { body: { decision: btn.dataset.act === "approve" ? "APPROVED" : "REJECTED", note } }));
    }));
    detail.querySelectorAll('[data-act="use"]').forEach((btn) => btn.addEventListener("click", () => {
      const p = state.previews.find((x) => String(x.version) === btn.dataset.version);
      sel = { ...sel, provider: p.input_binding.provider, voice_id: p.input_binding.voice_id, controls: { ...p.input_binding.controls } };
      render();
    }));
    detail.querySelectorAll('[data-act="play"]').forEach((btn) => btn.addEventListener("click", async () => {
      const out = detail.querySelector("[data-action-status]");
      try {
        const response = await fetch(`/api${base()}/previews/${btn.dataset.version}/file`, { headers: api.authHeader() });
        if (!response.ok) throw new Error(`Audio request failed (${response.status})`);
        const player = detail.querySelector(`[data-audio-for="${btn.dataset.version}"]`);
        player.src = URL.createObjectURL(await response.blob()); player.hidden = false; player.play().catch(() => {});
      } catch (error) { out.textContent = error.message; }
    }));
  }

  async function load() {
    try {
      const next = await api.request("GET", base());
      if (!isCurrent()) return;
      state = next;
      render();
    } catch (error) {
      if (!isCurrent()) return;
      list.innerHTML = `<p role="alert" class="error-state">${esc(error.message || String(error))}</p>`;
    }
  }

  languageSelect.addEventListener("change", () => { sel = { provider: null, voice_id: null, controls: {}, text: "" }; list.innerHTML = '<p class="loading-state">Loading…</p>'; load(); });
  list.innerHTML = '<p class="loading-state">Loading voice lab…</p>';
  detail.innerHTML = "";
  detail.classList.remove("detail-overlay");
  await load();
}
