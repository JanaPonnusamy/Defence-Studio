import test from "node:test";
import assert from "node:assert/strict";
import { renderModelsHtml, renderProfileHtml, renderAlignmentHtml, renderJobRowHtml, renderCompareHtml, renderStatus }
  from "../js/views/voice-view.js";

test("models view reports unavailable narrator and unverified Tamil honestly", () => {
  const html = renderModelsHtml({
    voices: [
      { voice_id: "DEFENCE_ERA_FEMALE_001", display_name: "Narrator", kind: "NOT_PROVISIONED", ready: false,
        languages: [], natural_speech: false, tamil_verified: false, license: null, reason: "Not provisioned" },
      { voice_id: "OS_SAPI_ZIRA", display_name: "Zira", kind: "OS_VOICE", ready: true, languages: ["en-US"],
        natural_speech: true, tamil_verified: false, supported_controls: ["tempo"], unsupported_controls: ["pitch_variation"],
        license: { speaker_license_ref: "OS", training_allowed: false, commercial_use_allowed: false, youtube_use_allowed: false } },
    ],
    aligner: { name: "defence-align", available: false, reason: "no torch", license_note: "CC-BY-NC" },
    limitations: ["Tamil not verified"],
  });
  assert.match(html, /not available/);
  assert.match(html, /Tamil verified: no/);
  assert.match(html, /Not applied by this model: pitch_variation/);
  assert.match(html, /commercial not allowed/);
  assert.match(html, /not available\. no torch/);
});

test("alignment labels estimated vs measured and never says forced alignment ran", () => {
  const html = renderAlignmentHtml({
    alignment_version: "1.0", note: "n", aligner: { forced_alignment_run: false },
    segments: [{ text: "a", start_ms: 0, end_ms: 10, accuracy: "MEASURED_RENDER_BOUNDARY" }],
    words: [{ text: "a", start_ms: 0, end_ms: 10, accuracy: "ESTIMATED_PROPORTIONAL" }],
  });
  assert.match(html, /measured \(render boundary\)/);
  assert.match(html, /estimated \(proportional\)/);
  assert.match(html, /Forced aligner run: no/);
});

test("profile view marks read-only, shows unavailable metrics and inference label, escapes html", () => {
  const html = renderProfileHtml({
    voice_style_id: "VSP-1", profile_name: "<b>x</b>", version: 2, language: "ta-IN", analysis_version: "1.0",
    analysis_hash: "a".repeat(64), provenance: { state: "AUTHORIZED", authorization_ref: "ref", raw_audio_retained: false },
    confidence: { overall: 0.7, warnings: [] }, pitch: { median_hz: null }, speaking_rate: { words_per_minute: null, words_per_minute_kind: "UNAVAILABLE" },
    energy_profile: {}, pause_profile: {}, rhythm_profile: {}, emotion_profile: { label_share: { CALM: 1 } },
    limitations: ["Emotion is heuristic"], versions: [{ version: 1, analysis_hash: "b".repeat(64) }, { version: 2, analysis_hash: "a".repeat(64) }],
  });
  assert.match(html, /read-only/);
  assert.match(html, /unavailable/);
  assert.match(html, /heuristic inference/);
  assert.doesNotMatch(html, /<b>x<\/b>/);
});

test("job rows expose only actions the backend allows", () => {
  const base = { job_id: "j", kind: "GENERATION", status: "FAILED", created_at: null, topic: "t", error: "boom",
                 stage: { attempt: 1, max_attempts: 3 }, progress_note: "" };
  const failed = renderJobRowHtml({ ...base, can_retry: true, can_cancel: false });
  assert.match(failed, /data-act="retry"/); assert.doesNotMatch(failed, /data-act="cancel"/); assert.match(failed, /boom/);
  const running = renderJobRowHtml({ ...base, status: "RUNNING", error: null, can_cancel: true, can_retry: false });
  assert.match(running, /data-act="cancel"/); assert.doesNotMatch(running, /data-act="retry"/);
  assert.doesNotMatch(renderStatus(running ? { ...base, status: "RUNNING" } : base), /%/);   // no fabricated percentage
});

test("compare view states that versions are unmodified", () => {
  const html = renderCompareHtml({ a: 1, b: 2, identical: false, metrics: [{ metric: "pitch.median_hz", a: 200, b: 210, delta: 10 }] });
  assert.match(html, /neither version was modified/); assert.match(html, /pitch\.median_hz/);
});

import { singleFlight } from "../js/components/single-flight.js";

test("voice submit guard: a second submit while one is in flight is ignored", async () => {
  const once = singleFlight();
  const button = { disabled: false, isConnected: true };
  let posts = 0, release;
  const gate = new Promise((r) => { release = r; });
  const a = once(button, async () => { posts++; await gate; });
  const b = await once(button, async () => { posts++; });
  assert.equal(b, false);
  release(); await a;
  assert.equal(posts, 1);
  assert.equal(button.disabled, false);
});
