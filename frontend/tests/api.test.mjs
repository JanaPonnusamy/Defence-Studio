import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient, ApiError } from "../js/api.js";
import { renderClaimDetail } from "../js/views/claims-view.js";
import { renderDecisionPanelHtml } from "../js/views/decision-panel.js";

test("evidence API authenticates without putting credentials in URLs", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    return new Response(JSON.stringify({ok:true}), {status:200});
  };
  try {
    const client = createApiClient(() => "offline-test-credential");
    await client.evidencePackage("job/a");
    await client.evidenceTimeline("job/a", {source_id:"source/a",start:"2026-10-11T10:00:00+05:30"});
    await client.approveEvidence("job/a", "f".repeat(64), "Reviewed", "stable-key");
    await client.exportEvidence("job/a", true);
    assert.equal(calls.length, 4);
    assert(calls.every(c => c.options.headers.Authorization === "Bearer offline-test-credential"));
    assert(calls.every(c => !c.url.includes("offline-test-credential")));
    assert(calls[0].url.includes("job%2Fa"));
    assert.equal(calls[2].options.headers["Idempotency-Key"], "stable-key");
    assert.equal(JSON.parse(calls[2].options.body).note, "Reviewed");
    assert(calls[3].url.endsWith("draft=true"));
  } finally { globalThis.fetch = originalFetch; }
});

test("failed approvals and validation errors never become successful UI responses", async () => {
  const originalFetch = globalThis.fetch;
  const client = createApiClient(() => "test");
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({detail:"Evidence changed"}),{status:409});
    await assert.rejects(client.approveEvidence("job","a".repeat(64),"Note","key"), e => e instanceof ApiError && e.status === 409 && e.message === "Evidence changed");
    globalThis.fetch = async () => new Response(JSON.stringify({detail:[{loc:["body","note"],msg:"Required"}]}),{status:422});
    await assert.rejects(client.evidencePackage("job"), /body.note: Required/);
    globalThis.fetch = async () => new Response("<html>Proxy unavailable</html>",{status:503});
    await assert.rejects(client.exportEvidence("job"), /unreadable response/);
  } finally { globalThis.fetch = originalFetch; }
});

test("passage details escape source content and show exact offsets and stale review state", () => {
  const html = renderClaimDetail({claim_text:'<img src=x onerror="alert(1)">',source_id:"s",review_status:"PENDING",
    passage:{start_offset:12,end_offset:40,artifact_uri:"jobs/test/raw.txt"}}, []);
  assert(!html.includes('<img src=x'));
  assert(html.includes("&lt;img"));
  assert(html.includes("12–40"));
  assert(html.includes("jobs/test/raw.txt"));
  const stale = renderDecisionPanelHtml({decision:"APPROVED_FOR_USE",reviewer_id:"owner",revision:1,note:"old"},true);
  assert(stale.includes("superseded"));
  assert(stale.includes("Required:"));
});
