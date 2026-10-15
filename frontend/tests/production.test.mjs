import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient, ApiError } from "../js/api.js";
import { renderScenesHtml, renderVisualHtml, renderAudioHtml, SECTIONS, runAction } from "../js/views/production-view.js";

const plan = { version: 2, status: "DRAFT", scenes: [
  { scene_id: "sc-1", title: "<b>x</b>", narration: "<script>1</script>", claim_ids: ["c1"], transition_in: "cut",
    planned_timing: { estimated_seconds: 4.2 } },
  { scene_id: "sc-2", title: "B", narration: "n2", claim_ids: [], transition_in: "fade", planned_timing: { estimated_seconds: 3 } },
] };

test("scene editor escapes narration, orders controls and gates approval on status", () => {
  const html = renderScenesHtml([plan]);
  assert.ok(!html.includes("<script>1") && !html.includes("<b>x</b>"));
  assert.ok(html.includes('data-act="approve-plan" data-version="2"') && !/approve-plan[^>]*disabled/.test(html));
  assert.match(html, /data-act="up" data-scene="sc-1" disabled/);
  assert.match(html, /data-act="down" data-scene="sc-2" disabled/);
  const approved = renderScenesHtml([{ ...plan, status: "APPROVED" }]);
  assert.match(approved, /approve-plan[^>]*disabled/);
  assert.match(renderScenesHtml([]), /data-act="generate-plan"[^>]*disabled/);
  assert.ok(!/data-act="generate-plan"[^>]*disabled/.test(renderScenesHtml([], null, { canGenerate: true })));
});

test("scene editor shows the staleness reason and keeps generate/approve disabled when the bound script is stale", () => {
  const noPlans = renderScenesHtml([], null, { canGenerate: false, staleReason: "Upstream evidence changed after this revision was generated" });
  assert.ok(noPlans.includes("Upstream evidence changed after this revision was generated"));
  assert.match(noPlans, /data-act="generate-plan"[^>]*disabled/);
  // An existing plan whose bound script went stale must not be approvable,
  // even though its own status (DRAFT/AWAITING_REVIEW) would otherwise allow it --
  // the P2 defect was exactly this: gating only on the plan's own status.
  const staleDraft = renderScenesHtml([plan], null, { canGenerate: false, staleReason: "Upstream evidence changed after this revision was generated" });
  assert.match(staleDraft, /approve-plan[^>]*disabled/);
  assert.match(staleDraft, /regenerate-plan[^>]*disabled/);
  assert.ok(staleDraft.includes("no longer current") && staleDraft.includes("STALE"));
  // A fresh, currently-approved script keeps both actions enabled, same as before.
  const fresh = renderScenesHtml([plan], null, { canGenerate: true, staleReason: "" });
  assert.ok(!/regenerate-plan[^>]*disabled/.test(fresh) && !fresh.includes("no longer current"));
});

test("audio section shows the staleness reason distinct from the no-approved-plan case", () => {
  const notApproved = renderAudioHtml({ items: [], provider_languages: {} }, null, { canGenerate: false, staleReason: "" });
  assert.ok(notApproved.includes("An approved scene plan is required"));
  assert.ok(!notApproved.includes("no longer current"));
  const stale = renderAudioHtml({ items: [], provider_languages: {} }, null,
    { canGenerate: false, staleReason: "The approved script revision changed since this plan was built; regenerate/re-sync the scene plan before approval" });
  assert.match(stale, /data-act="generate-audio"[^>]*disabled/);
  assert.ok(stale.includes("no longer current") && stale.includes("re-sync"));
});

test("runAction refreshes from the server on a 409 even though the action failed, so stale local readiness cannot linger", async () => {
  const node = { querySelector: () => ({ textContent: "" }) };
  let refreshed = 0;
  const ctx409 = { refresh: () => { refreshed += 1; } };
  await runAction(ctx409, node, "Generating", () => { throw new ApiError(409, "LANGUAGE_STALE: re-sync required"); });
  assert.equal(refreshed, 1, "a 409 must trigger a refresh so the UI re-syncs to server truth");

  let refreshed2 = 0;
  const ctx500 = { refresh: () => { refreshed2 += 1; } };
  await runAction(ctx500, node, "Generating", () => { throw new ApiError(500, "internal"); });
  assert.equal(refreshed2, 0, "a non-409 failure must not be treated as a readiness change");

  let refreshed3 = 0;
  const ctxOk = { refresh: () => { refreshed3 += 1; } };
  await runAction(ctxOk, node, "Generating", async () => {});
  assert.equal(refreshed3, 1, "success still refreshes exactly once, as before");
});

test("scenes section render() derives canGenerate from /script/staleness, not from status alone (upstream evidence change, refresh, recovery)", async () => {
  const sceneSection = SECTIONS.find((s) => s.id === "scenes");
  const makeApi = (staleState) => ({
    request: async (method, path) => {
      if (path.includes("/scene/plans?")) return { plans: [] };
      if (path.includes("/script/revisions?")) return { revisions: [{ language: "ta", version: 3, status: "APPROVED" }] };
      throw new Error(`unexpected request ${path}`);
    },
    getScriptStaleness: async () => ({
      revisions: [{ language: "ta", version: 3, state: staleState, reason: staleState === "STALE" ? "Upstream evidence changed after this revision was generated" : "" }],
    }),
  });
  const ctxCurrent = { jobId: "j1", language: "ta", api: makeApi("CURRENT") };
  const htmlCurrent = await sceneSection.render(ctxCurrent);
  assert.ok(!/data-act="generate-plan"[^>]*disabled/.test(htmlCurrent), "an approved AND current revision must allow generation");

  // Upstream evidence changed after approval: status is still APPROVED, but
  // staleness now reports STALE -- this is the exact scenario the P2 defect
  // missed (status-only check would have left Generate enabled here).
  const ctxStale = { jobId: "j1", language: "ta", api: makeApi("STALE") };
  const htmlStale = await sceneSection.render(ctxStale);
  assert.match(htmlStale, /data-act="generate-plan"[^>]*disabled/, "an approved but now-stale revision must disable generation");
  assert.ok(htmlStale.includes("no longer current"));

  // Recovery: after a valid re-approval/resync, staleness flips back to
  // CURRENT and a fresh render() must re-enable the action again.
  const ctxRecovered = { jobId: "j1", language: "ta", api: makeApi("CURRENT") };
  const htmlRecovered = await sceneSection.render(ctxRecovered);
  assert.ok(!/data-act="generate-plan"[^>]*disabled/.test(htmlRecovered), "re-sync/reapproval must re-enable generation");
});

test("scenes section render() fails closed (disabled) when /script/staleness itself is unreachable", async () => {
  const sceneSection = SECTIONS.find((s) => s.id === "scenes");
  const ctx = {
    jobId: "j1", language: "ta",
    api: {
      request: async (method, path) => {
        if (path.includes("/scene/plans?")) return { plans: [] };
        if (path.includes("/script/revisions?")) return { revisions: [{ language: "ta", version: 3, status: "APPROVED" }] };
        throw new Error("unexpected");
      },
      getScriptStaleness: async () => { throw new Error("network down"); },
    },
  };
  const html = await sceneSection.render(ctx);
  assert.match(html, /data-act="generate-plan"[^>]*disabled/, "readiness must not fall back to the status-only check when staleness is unreachable");
});

test("audio section render() requires the bound script version to be CURRENT, not just the plan's own APPROVED status", async () => {
  const audioSection = SECTIONS.find((s) => s.id === "audio");
  const basePlan = { status: "APPROVED", script_version: 5 };
  const makeCtx = (staleState) => ({
    jobId: "j1", language: "ta", plan: basePlan,
    api: {
      request: async (method, path) => {
        if (path.includes("/audio/")) return { items: [] };
        throw new Error("unexpected");
      },
      getScriptStaleness: async () => ({
        revisions: [{ language: "ta", version: 5, state: staleState, reason: staleState === "STALE" ? "the approved script revision this plan was built from is no longer current" : "" }],
      }),
    },
  });
  const current = await audioSection.render(makeCtx("CURRENT"));
  assert.ok(!/data-act="generate-audio"[^>]*disabled/.test(current));
  const stale = await audioSection.render(makeCtx("STALE"));
  assert.match(stale, /data-act="generate-audio"[^>]*disabled/);
  assert.ok(stale.includes("no longer current"));
});

test("visual section: build disabled until plan approved; shows stale and retry", () => {
  assert.match(renderVisualHtml({ items: [] }, plan), /build-visual"[^>]*disabled/);
  const html = renderVisualHtml({ items: [{ version: 1, status: "FAILED", stale: true, progress: 0.4, detail: "RIGHTS_UNKNOWN" }] },
    { ...plan, status: "APPROVED" });
  assert.ok(html.includes("FAILED · STALE") && html.includes("retry-visual") && html.includes("RIGHTS_UNKNOWN"));
  assert.ok(!/build-visual"[^>]*disabled/.test(html));
});

test("sections registered and generic request sends auth + idempotency header", async () => {
  assert.ok(SECTIONS.map((s) => s.id).includes("scenes") && SECTIONS.map((s) => s.id).includes("visual"));
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, o) => { calls.push({ url, o }); return new Response("{}", { status: 202 }); };
  try {
    await createApiClient(() => "tok").request("POST", "/jobs/j/visual/ta/plan", { body: {}, idempotencyKey: "k1" });
    assert.ok(calls[0].url.endsWith("/api/jobs/j/visual/ta/plan"));
    assert.equal(calls[0].o.headers["Idempotency-Key"], "k1");
    assert.equal(calls[0].o.headers.Authorization, "Bearer tok");
  } finally { globalThis.fetch = original; }
});

test("audio section shows measured vs planned, flags mock, approximate alignment and provider languages", () => {
  const html = renderAudioHtml(
    { items: [{ version: 1, status: "SUCCEEDED", stale: false, progress: 1, input_binding: { provider: "local-pyttsx3" }, detail: "ok" }],
      provider_languages: { "local-pyttsx3": ["en"], "mock-tts": [] } },
    { scenes: [{ scene_id: "sc-1", text: "<i>hi</i>", duration_seconds: 2.5, planned_seconds: 3, is_mock: true,
                 alignment: { quality_label: "approximate_proportional_vad", mean_confidence: 0.4 } }] });
  assert.ok(html.includes("2.50s measured") && html.includes("3.0s planned"));
  assert.ok(html.includes("MOCK - not narration") && html.includes("approximate_proportional_vad"));
  assert.ok(!html.includes("<i>hi</i>") && html.includes("local-pyttsx3: en") && html.includes("no production languages"));
  assert.match(renderAudioHtml({ items: [], provider_languages: {} }, null), /data-act="generate-audio"[^>]*disabled/);
  assert.ok(!/data-act="generate-audio"[^>]*disabled/.test(renderAudioHtml({ items: [], provider_languages: {} }, null, { canGenerate: true })));
});

import { renderRenderHtml } from "../js/views/production-view.js";

test("render section: start disabled until measured visual timeline exists; downloads only for SUCCEEDED", () => {
  const none = renderRenderHtml({ items: [] }, 1, [{ status: "SUCCEEDED", stale: false, input_binding: { timing: "scene_plan_estimate" } }]);
  assert.match(none, /start-render"[^>]*disabled/);
  const ok = renderRenderHtml(
    { items: [{ version: 2, status: "SUCCEEDED", stale: false, progress: 1, detail: "ok" },
              { version: 3, status: "FAILED", stale: false, progress: 0.2, detail: "<b>x</b>" }] },
    1, [{ status: "SUCCEEDED", stale: false, input_binding: { timing: "measured_audio" } }]);
  assert.ok(!/start-render"[^>]*disabled/.test(ok));
  assert.ok(ok.includes('data-which="subtitled"') && ok.includes("retry-render") && !ok.includes("<b>x</b>"));
  assert.equal((ok.match(/data-which="srt"/g) || []).length, 1);
});

import { renderQcHtml } from "../js/views/production-view.js";

test("QC section: approve disabled on failure, warnings require acknowledgement controls, text escaped", () => {
  const report = { version: 3, summary: { passed: false, rerun_stages: ["assets"] }, checks: [
    { id: "VISUAL_COVERAGE", status: "FAIL", severity: "BLOCKER", detail: "<b>gaps</b>", rerun_stage: "assets" },
    { id: "LOUDNESS", status: "WARN", severity: "WARNING", detail: "-30 LUFS", rerun_stage: "audio" }] };
  const html = renderQcHtml({ reports: [{ version: 3, status: "SUCCEEDED", detail: "FAIL" }], approvals: [] }, report,
    [{ status: "SUCCEEDED", stale: false }]);
  assert.match(html, /approve-render"[^>]*disabled/);
  assert.ok(html.includes('data-ack="LOUDNESS"') && !html.includes("<b>gaps</b>") && html.includes("Fix and re-run: assets"));
  const pass = renderQcHtml({ reports: [], approvals: [] }, { ...report, summary: { passed: true, rerun_stages: [] } }, []);
  assert.ok(!/approve-render"[^>]*disabled/.test(pass));
  assert.match(renderQcHtml({ reports: [], approvals: [] }, null, []), /run-qc"[^>]*disabled/);
});

import { renderPackageHtml } from "../js/views/production-view.js";

test("package section: build gated on QC approval, metadata escaped, duplicate warning shown", () => {
  assert.match(renderPackageHtml({ items: [] }, null, false), /build-pkg"[^>]*disabled/);
  const manifest = { source_register_count: 2, duplicate_check: { similar_previous_titles: [{ title: "<i>Old</i>" }] },
    metadata: { title: "T<b>", description: "d <script>", default_language: "ta", category_id: "25", visibility: "private",
                chapters: [{}, {}, {}], chapter_note: "" } };
  const html = renderPackageHtml({ items: [{ version: 1, status: "AWAITING_REVIEW", stale: false, uri: "u" }] }, manifest, true);
  assert.ok(!/build-pkg"[^>]*disabled/.test(html) && html.includes("approve-pkg") && html.includes("visibility private"));
  assert.ok(!html.includes("<script>") && !html.includes("<i>Old</i>") && html.includes("Similar earlier titles"));
  const stale = renderPackageHtml({ items: [{ version: 1, status: "AWAITING_REVIEW", stale: true, uri: "u" }] }, null, true);
  assert.ok(!stale.includes("approve-pkg") && stale.includes("STALE"));
});

import { renderYoutubeConnectionHtml, renderPublishQueueHtml } from "../js/views/production-view.js";

test("YouTube connection: unconfigured server blocks connect, no secrets, confirmation required", () => {
  const unconf = renderYoutubeConnectionHtml({ config: { configured: false, missing: ["DEFENCE_STUDIO_YT_CLIENT_ID"] }, connections: [] });
  assert.ok(unconf.includes("DEFENCE_STUDIO_YT_CLIENT_ID") && /yt-connect"[^>]*disabled/.test(unconf));
  const pending = renderYoutubeConnectionHtml({ config: { configured: true, missing: [] }, connections: [
    { connection_id: "c1", status: "PENDING_CONFIRMATION", channel_title: "<b>X</b>", channel_id: "UC1", granted_scopes: ["a"], channel_mismatch: true }] });
  assert.ok(pending.includes("yt-confirm") && pending.includes("NOT THE EXPECTED CHANNEL") && !pending.includes("<b>X</b>"));
});

test("publish queue: typed confirmation shown for public, upload only after approval, thumbnail retry independent", () => {
  const base = { publish_id: "p1", title: "T", visibility: "public", state: "AWAITING_APPROVAL", progress: 0, attempts: 0,
    max_attempts: 5, thumbnail_state: "NONE", required_confirmation: "PUBLISH PUBLIC", approved: false, is_terminal: false };
  const html = renderPublishQueueHtml({ jobs: [base], daily_quota_limit: 10000, quota_used_today_estimate: 1600 },
    [{ status: "APPROVED", stale: false, version: 2 }], [{ status: "CONNECTED" }]);
  assert.ok(html.includes("PUBLISH PUBLIC") && html.includes("yt-approve") && !html.includes('data-act="yt-queue"'));
  const approved = renderPublishQueueHtml({ jobs: [{ ...base, approved: true }] }, [], []);
  assert.ok(approved.includes('data-act="yt-queue"') && /yt-create"[^>]*disabled/.test(approved));
  const failedThumb = renderPublishQueueHtml({ jobs: [{ ...base, state: "FAILED_RETRYABLE", approved: true,
    youtube_video_id: "VID1", thumbnail_state: "FAILED" }] }, [], []);
  assert.ok(failedThumb.includes('data-part="thumbnail"') && failedThumb.includes("youtube.com/watch?v=VID1"));
});

import { renderOverviewHtml, renderUsageHtml, renderCredentialsHtml } from "../js/views/production-view.js";

test("overview flags stages needing attention and escapes", () => {
  const html = renderOverviewHtml({ stages: [{ stage: "render", status: "FAILED", version: 2, detail: "<b>x</b>" }],
    needs_attention: ["render"] });
  assert.ok(html.includes("Needs attention: render") && !html.includes("<b>x</b>") && html.includes("v2"));
  assert.equal(renderOverviewHtml(null), "");
});

test("usage never shows an unmeasured cost as zero", () => {
  const html = renderUsageHtml({ text_generation: { total_inr: null, target_inr: 10, within_target: null, calls: [], unmeasured_calls: 1, note: "n" },
    tts: { total_inr: null }, image_generation: { enabled: false }, youtube_quota_units_estimate: 0 });
  assert.ok(html.includes("not measured") && !html.includes("₹0.00") && html.includes("not enabled"));
  const over = renderUsageHtml({ text_generation: { total_inr: 12, target_inr: 10, within_target: false, calls: [1], unmeasured_calls: 0, note: "n" },
    tts: { total_inr: 0 }, image_generation: { enabled: false }, youtube_quota_units_estimate: 3 });
  assert.ok(over.includes("OVER target") && over.includes("₹12.00"));
});

test("credentials show only last four and a password field", () => {
  const html = renderCredentialsHtml({ credentials: [{ provider: "openai", configured: true, source: "owner", last4: "9876" }] });
  assert.ok(html.includes("ends 9876") && html.includes('type="password"') && html.includes("cred-del"));
  assert.ok(!html.includes("sk-"));
});
