import test from "node:test";
import assert from "node:assert/strict";
import { renderMonitorHtml, renderExplorerHtml, renderClaimDetailHtml, filterClaims, formatDuration, mountResearch }
  from "../js/views/research-view.js";

const claim = (o = {}) => ({
  claim_id: "c1", text: "The <script>x</script> S-400 has 400 km range.", category: "weapon", state: "AUTO_ACCEPTED",
  reasons: ["ok"], flags: ["strict_class"], score: { total: 90, components: { relevance: 1 }, weights: { relevance: 15 } },
  source_id: "a", hostname: "csis.example", source_category: "RESEARCH_INSTITUTION", independence_group: "csis",
  passage_ref: { start: 1, end: 5 }, context: { before: "<b>b", passage: "p", after: "a" },
  corroboration: { applicable: true, quantities: ["400"], independent_groups: ["csis", "rusi"] },
  contradicting: [], contradicted: false, single_source: false, quantitative: true, high_impact: true,
  analysis_sha256: "abc", policy_version: "pv", scoring_policy_version: "sv", ...o,
});
const monitor = (o = {}) => ({
  topic: "<img onerror=1>", job_status: "RUNNING", display_state: "WAITING_RETRY", stages_done: 2, stages_total: 13,
  updated_at: "t", started_at: null, elapsed_seconds: 65, estimate: { remaining_seconds: null, basis: "default", confidence: "low" },
  counts: { approved: 1, fetched: 2, failed: 1, rejected: 0, duplicate: 0, pending: 3 },
  sources: [{ hostname: "h.example", title: null, category: null, independence_group: null, acquisition_status: "FAILED",
    fetch_duration_seconds: null, error: "boom" }],
  stages: [{ stage_key: "k", label: "Acquisition", status: "RUNNING", started_at: "s", ended_at: null,
    duration_seconds: null, elapsed_seconds: 5, attempt_count: 1, max_attempts: 3, retry_after: "r", error: null, progress: null }], ...o });

test("monitor escapes content, shows distinct state, Estimating and unknowns", () => {
  const h = renderMonitorHtml(monitor());
  assert.ok(!h.includes("<img onerror")); assert.ok(h.includes("Waiting / retrying"));
  assert.ok(h.includes("Estimating")); assert.ok(h.includes("Unknown"));
  assert.ok(h.includes("In progress (no measurable denominator)"));
  assert.ok(h.includes('data-count="pending">3'));
});
test("unimplemented controls are not rendered", () => {
  const h = renderMonitorHtml(monitor());
  assert.ok(!/<button[^>]*>\s*(Pause|Resume|Cancel|Retry)/i.test(h));
});
test("filters", () => {
  const cs = [claim(), claim({ claim_id: "c2", state: "NEEDS_REVIEW", single_source: true, quantitative: false, high_impact: false, hostname: "x" })];
  assert.equal(filterClaims(cs, { filter: "AUTO_ACCEPTED" }).length, 1);
  assert.equal(filterClaims(cs, { filter: "single" })[0].claim_id, "c2");
  assert.equal(filterClaims(cs, { filter: "quant" })[0].claim_id, "c1");
  assert.equal(filterClaims(cs, { host: "x" }).length, 1);
  assert.equal(filterClaims(cs, { query: "400 km" }).length, 2);
});
test("explorer shows readiness failure reasons and escapes claim text", () => {
  const ex = { policy_run: { policy_version: "pv", scoring_policy_version: "sv", analysis_sha256: "abcdef0123456789" },
    claims: [claim()], sources: [], freshness: { status: "STALE", reasons: ["analysis changed"] },
    readiness: { ready: false, reasons: ["min_accepted_claims: required 8, actual 1"] } };
  const h = renderExplorerHtml(ex, { filter: "all" });
  assert.ok(h.includes("Not ready for script generation") && h.includes("required 8, actual 1"));
  assert.ok(h.includes("STALE") && !h.includes("<script>x"));
});
test("claim detail renders passage context, components, unknown date; unsafe URL is not linked", () => {
  const ex = { sources: [{ source_id: "a", canonical_url: "javascript:alert(1)", published_at: "Unknown" }], freshness: { status: "CURRENT" } };
  const h = renderClaimDetailHtml(claim(), ex);
  assert.ok(h.includes("<mark>p</mark>") && h.includes("&lt;b&gt;b") && h.includes("Unknown"));
  assert.ok(!h.includes("javascript:") && h.includes("Original URL unavailable"));
});
test("duration formatting is Unknown for missing values", () => {
  assert.equal(formatDuration(null), "Unknown"); assert.equal(formatDuration(75), "1m 15s");
});
test("start form validates the topic and never calls the API with an empty topic", async () => {
  const handlers = {}; const err = { hidden: true, textContent: "" };
  const topic = { value: "  " };
  const form = { addEventListener: (_e, fn) => { handlers.submit = fn; } };
  const list = { set innerHTML(_v) {}, querySelector: (s) => s === "#research-start" ? form : s === "#rs-topic" ? topic : s === "#rs-error" ? err : { value: "en" } };
  let called = 0;
  await mountResearch({ api: { createJob: async () => { called++; } }, jobId: null, list,
    detail: { innerHTML: "" }, filters: { innerHTML: "" }, isCurrent: () => true });
  await handlers.submit({ preventDefault() {} });
  assert.equal(called, 0); assert.equal(err.hidden, false);
});
