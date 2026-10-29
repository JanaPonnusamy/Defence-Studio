import test from "node:test";
import assert from "node:assert/strict";
import { renderVoicePickerHtml, renderAudioHtml, voiceOptionsFor } from "../js/views/production-view.js";

const STATUS = {
  items: [], provider_languages: { "edge-neural": ["ta"], "local-pyttsx3": ["en"] }, default_provider: "edge-neural",
  suggested_voice: "ta-IN-PallaviNeural", voices_error: null,
  voices: [
    { provider: "edge-neural", voice_id: "ta-IN-PallaviNeural", locale: "ta-in", available: true },
    { provider: "edge-neural", voice_id: "ta-IN-ValluvarNeural", locale: "ta-in", available: true },
    { provider: "edge-neural", voice_id: "ta-LK-SaranyaNeural", locale: "ta-lk", available: true },
  ],
  provider_terms: { "edge-neural": "EVALUATION_ONLY: commercial terms unresolved" },
};

test("picker lists declared providers with the server default selected (no hard-coded provider)", () => {
  const html = renderVoicePickerHtml(STATUS);
  assert.match(html, /<option value="edge-neural" selected>/);
  assert.match(html, /<option value="local-pyttsx3" >/);
  assert.doesNotMatch(renderAudioHtml(STATUS, null), /type="text" id="audio-provider"/);
});

test("Tamil voices come from the status and the suggested evaluation voice is preselected", () => {
  const html = renderVoicePickerHtml(STATUS);
  assert.match(html, /value="ta-IN-PallaviNeural"\s+selected/);
  assert.match(html, /ta-IN-ValluvarNeural \(ta-in\)/);
  assert.match(html, /ta-LK-SaranyaNeural \(ta-lk\)/);
  assert.match(html, /Provider default voice/);
  assert.equal(voiceOptionsFor(STATUS, "local-pyttsx3").length, 0);
});

test("evaluation-only terms and an unavailable voice list are shown", () => {
  assert.match(renderVoicePickerHtml(STATUS), /edge-neural: EVALUATION_ONLY/);
  const down = renderVoicePickerHtml({ ...STATUS, voices: [], voices_error: "ConnectError: refused" });
  assert.match(down, /Voice list unavailable: ConnectError: refused/);
});

test("an unavailable voice is disabled", () => {
  const html = renderVoicePickerHtml({ ...STATUS, voices: [{ ...STATUS.voices[0], available: false }] });
  assert.match(html, /disabled>ta-IN-PallaviNeural \(ta-in\) — unavailable/);
});
