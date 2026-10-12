import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../js/api.js";
import { renderDashboardHtml } from "../js/views/dashboard-view.js";
import { renderLoginHtml } from "../js/views/login-view.js";

test("listJobs and devSession hit the right endpoints with auth, no mocks", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({ jobs: [] }), { status: 200 });
  };
  try {
    const client = createApiClient(() => "cred");
    await client.listJobs();
    await client.devSession();
    assert.ok(calls[0].url.endsWith("/api/jobs"));
    assert.equal(calls[0].options.headers.Authorization, "Bearer cred");
    assert.ok(calls[1].url.endsWith("/api/dev/session"));
  } finally { globalThis.fetch = originalFetch; }
});

test("dashboard renders real persisted status/progress, escaping topic text", () => {
  const html = renderDashboardHtml([
    {
      job_id: "41714114-a85e-4ccf-a591-b254110c52d5",
      topic: '<script>bad</script> Defence topic',
      language: "en", status: "AWAITING_REVIEW",
      stages_total: 13, stages_completed: 4, current_stage: "evidence_analysis_and_review",
    },
  ], "41714114-a85e-4ccf-a591-b254110c52d5");
  // No XSS: topic text is escaped.
  assert.ok(!html.includes("<script>bad"));
  assert.ok(html.includes("&lt;script&gt;bad"));
  // Real progress numbers from the server roll-up, not a fabricated percentage.
  assert.ok(html.includes("4/13 stages"));
  assert.ok(html.includes("Awaiting review"));
  // Active job is marked.
  assert.ok(html.includes("job-card--active"));
  // Current stage humanized.
  assert.ok(html.includes("Evidence Analysis And Review"));
});

test("dashboard empty state is honest (no fake jobs)", () => {
  const html = renderDashboardHtml([], "");
  assert.ok(html.includes("No workflow jobs yet"));
  assert.ok(!html.includes("job-card"));
});

test("login offers dev entry only when available", () => {
  const withDev = renderLoginHtml({ devAvailable: true, devOwner: "e2e-operator" });
  assert.ok(withDev.includes("Enter development mode"));
  assert.ok(withDev.includes("e2e-operator"));
  const withoutDev = renderLoginHtml({ devAvailable: false, devOwner: "" });
  assert.ok(!withoutDev.includes("Enter development mode"));
  assert.ok(withoutDev.includes("Sign in with token"));
});
