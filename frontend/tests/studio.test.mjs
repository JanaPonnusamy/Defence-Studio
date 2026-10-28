import test from "node:test";
import assert from "node:assert/strict";
import { renderStudioRailHtml, renderStudioDetailHtml } from "../js/views/studio-view.js";

const studio = {
  current_stage: "audio",
  stages: [
    { key: "script", title: "Script", tab: "script", state: "DRAFT", raw_status: "DRAFT", version: 3,
      approved_version: 2, review_gate: true, done: true, stale_reason: "", detail: "" },
    { key: "audio", title: "Voice & audio", tab: "production", state: "STALE", raw_status: "SUCCEEDED",
      version: 1, approved_version: null, review_gate: false, done: false,
      stale_reason: "<b>plan changed</b>", detail: "" },
    { key: "final_review", title: "Final review", tab: "production", state: "AWAITING_REVIEW",
      raw_status: "AWAITING_REVIEW", version: 1, approved_version: null, review_gate: true, done: false,
      stale_reason: "", detail: "" },
  ],
};

test("rail marks the current stage, shows versions and escapes server text", () => {
  const html = renderStudioRailHtml(studio, "audio");
  assert.match(html, /data-stage="audio" data-state="STALE"/);
  assert.match(html, /aria-current="step"/);
  assert.equal((html.match(/aria-current/g) || []).length, 1);
  assert.match(html, /Next: <strong>Voice &amp; audio<\/strong>/);
  assert.match(html, /v3 · approved v2/);
  assert.match(html, /Awaiting review/);
});

test("detail separates generation from approval and links to the owning tab", () => {
  const audio = renderStudioDetailHtml(studio, "audio");
  assert.ok(!audio.includes("<b>plan changed</b>") && audio.includes("&lt;b&gt;plan changed"));
  assert.match(audio, /server status: SUCCEEDED/);
  assert.match(audio, /no human review gate yet/);
  assert.match(audio, /data-open-tab="production"/);
  assert.match(renderStudioDetailHtml(studio, "final_review"), /needs an explicit human approval/);
  assert.equal(renderStudioDetailHtml(studio, "missing"), "");
});

test("empty and all-done states are explicit", () => {
  assert.match(renderStudioRailHtml({ current_stage: null, stages: [] }), /No production state/);
  const done = renderStudioRailHtml({ current_stage: null, stages: [{ ...studio.stages[0] }] }, "script");
  assert.match(done, /Publishing is a separate, explicitly authorised action/);
});
