import test from "node:test";
import assert from "node:assert/strict";
import { renderVoiceFormHtml, renderPreviewsHtml, renderProfilesHtml, configKey } from "../js/views/voice-lab-view.js";

const state = {
  language: "ta", preview_max_chars: 400, provider_error: null,
  unsupported: { voice_cloning: "No configured provider implements voice cloning; none is offered." },
  providers: [
    { provider: "edge-neural", model: "edge-read-aloud-neural", available: true, network: true, notes: "Online <b>",
      controls: { rate_wpm: { range: [80, 260], kind: "native" }, pitch_semitones: { range: [-3, 3], kind: "approx" } },
      voices: [{ id: "ta-IN-ValluvarNeural" }, { id: "ta-IN-PallaviNeural" }] },
    { provider: "offline", available: false, controls: {}, voices: [] },
  ],
};
const binding = { provider: "edge-neural", voice_id: "ta-IN-ValluvarNeural", controls: { rate_wpm: 150 } };
const ok = { version: 2, status: "SUCCEEDED", input_binding: binding, detail: "1.2s",
  result: { provider: "edge-neural", model: "edge-read-aloud-neural", voice: { id: "ta-IN-ValluvarNeural" },
    duration_seconds: 1.2, applied_controls: { rate_wpm: 150 }, unconfirmed_controls: [], is_mock: false,
    text: "<i>வணக்கம்</i>", language: "ta" } };
const failed = { version: 3, status: "FAILED", input_binding: { ...binding, voice_id: "ta-IN-PallaviNeural" },
  detail: "service_unavailable: edge down", result: null };

test("form offers only available providers and only the controls they report, unset by default", () => {
  const html = renderVoiceFormHtml(state, { provider: null, voice_id: "ta-IN-PallaviNeural", controls: {}, text: "" });
  assert.ok(html.includes("edge-neural (edge-read-aloud-neural)") && !html.includes('value="offline"'));
  assert.match(html, /value="ta-IN-PallaviNeural" selected/);
  assert.match(html, /data-control="rate_wpm" min="80" max="260"/);
  assert.match(html, /data-control="pitch_semitones"/);
  assert.ok(!html.includes('data-control="volume"'));
  assert.equal((html.match(/provider default/g) || []).length, 2);
  assert.ok(html.includes("Online &lt;b&gt;") && html.includes("voice cloning"));
});

test("set controls show their value and no provider is an explicit empty state", () => {
  const html = renderVoiceFormHtml(state, { provider: "edge-neural", voice_id: null, controls: { rate_wpm: 150 }, text: "x" });
  assert.match(html, /data-control-on="rate_wpm" checked/);
  assert.match(html, /<output data-control-value="rate_wpm">150<\/output>/);
  assert.match(renderVoiceFormHtml({ ...state, providers: [state.providers[1]] }, { controls: {} }), /No TTS provider is declared/);
  assert.match(renderVoiceFormHtml({ ...state, provider_error: "Audio module unavailable" }, { controls: {} }), /role="alert"/);
});

test("previews show what actually spoke, newest first, escaped, with failures visible", () => {
  const html = renderPreviewsHtml([ok, failed]);
  assert.ok(html.indexOf('data-preview="3"') < html.indexOf('data-preview="2"'));
  assert.match(html, /Used: edge-neural \/ edge-read-aloud-neural \/ ta-IN-ValluvarNeural/);
  assert.ok(!html.includes("<i>வணக்கம்") && html.includes("&lt;i&gt;வணக்கம்"));
  assert.match(html, /edge down/);
  // the real module reports the voice as a plain id string
  const real = renderPreviewsHtml([{ ...ok, result: { ...ok.result, voice: "ta-IN-PallaviNeural" } }]);
  assert.match(real, /\/ ta-IN-PallaviNeural ·/);
  assert.ok(!/data-act="play" data-version="3"/.test(html));
});

test("approve is enabled only after a successful preview of the exact same settings", () => {
  const awaiting = { version: 1, status: "AWAITING_REVIEW", input_binding: binding, detail: "" };
  assert.ok(!/data-act="approve"[^>]*disabled/.test(renderProfilesHtml([awaiting], [ok])));
  const other = { ...awaiting, input_binding: { ...binding, controls: { rate_wpm: 151 } } };
  const html = renderProfilesHtml([other], [ok, failed]);
  assert.match(html, /data-act="approve"[^>]*disabled/);
  assert.match(html, /preview of exactly these settings/);
  assert.match(renderProfilesHtml([], []), /per-language default voice/);
  assert.equal(configKey("p", "v", { b: 1, a: 2 }), configKey("p", "v", { a: 2, b: 1 }));
});
