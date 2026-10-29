import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient, ApiError } from "../js/api.js";
import { renderClaimDetail } from "../js/views/claims-view.js";
import { renderDecisionPanelHtml } from "../js/views/decision-panel.js";
import { startFormHtml } from "../js/views/research-view.js";

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

test("research language: the form offers only supported codes and a rejected code reads clearly", async () => {
  const offered = [...startFormHtml().matchAll(/<option value="([^"]*)"/g)].map(m => m[1]).sort();
  assert.deepEqual(offered, ["en", "ta"]);
  const originalFetch = globalThis.fetch;
  const calls = [];
  const client = createApiClient(() => "test");
  try {
    globalThis.fetch = async (url, options) => {
      calls.push(JSON.parse(options.body));
      return new Response(JSON.stringify({detail:[{loc:["body","language"],msg:"Input should be 'en' or 'ta'"}]}), {status:422});
    };
    for (const language of ["fr", "EN", "", undefined]) {
      await assert.rejects(client.createJob("t", language, "k"),
        e => e instanceof ApiError && e.status === 422 && e.message === "body.language: Input should be 'en' or 'ta'");
    }
    assert.deepEqual(calls.map(c => c.language), ["fr", "EN", "", undefined]);  // sent as typed, never rewritten
    globalThis.fetch = async (url, options) => new Response(JSON.stringify({job_id:"j", language:JSON.parse(options.body).language}), {status:201});
    for (const language of ["en", "ta"]) assert.equal((await client.createJob("t", language, "k")).language, language);
  } finally { globalThis.fetch = originalFetch; }
});

test("server faults, empty bodies and network failures each get an accurate message", async (t) => {
  const originalFetch = globalThis.fetch;
  t.mock.method(console, "error", () => {});
  const client = createApiClient(() => "test");
  try {
    // The Start research failure: Starlette's plain-text 500 after the job was created.
    globalThis.fetch = async () => new Response("Internal Server Error", {status:500, headers:{"content-type":"text/plain"}});
    await assert.rejects(client.createDiscoveryRequest("job", {topic:"t"}),
      e => e instanceof ApiError && e.status === 500 && /unreadable response with an error \(HTTP 500\)/.test(e.message));
    globalThis.fetch = async () => new Response("", {status:502});
    await assert.rejects(client.queueJob("job"), e => e.status === 502 && /HTTP 502/.test(e.message));
    globalThis.fetch = async () => new Response(JSON.stringify({detail:"Server storage is misconfigured. (Reference abc)",
      error_code:"STORAGE_PATH_TOO_LONG", error_id:"abc"}), {status:500});
    await assert.rejects(client.createJob("t", "en", "k"),
      e => e.code === "STORAGE_PATH_TOO_LONG" && e.errorId === "abc" && /storage is misconfigured/.test(e.message));
    globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
    await assert.rejects(client.createJob("t", "en", "k"),
      e => e instanceof ApiError && e.status === 0 && /Could not reach the Defence Studio server/.test(e.message));
  } finally { globalThis.fetch = originalFetch; }
});

test("passage details escape source content and show exact offsets and stale review state", () => {
  const html = renderClaimDetail({claim_text:'<img src=x onerror="alert(1)">',source_id:"s",review_status:"PENDING",
    passage:{start_offset:12,end_offset:40,artifact_uri:"jobs/test/raw.txt"}}, []);
  assert(!html.includes('<img src=x'));
  assert(html.includes("&lt;img"));
  assert(html.includes("12–40"));
  assert(html.includes("jobs/test/raw.txt"));
  assert(html.includes("decision-panel-mount"));
  const stale = renderDecisionPanelHtml({decision:"APPROVED_FOR_USE",reviewer_id:"owner",revision:1,note:"old"},true);
  assert(stale.includes("superseded"));
  assert(stale.includes("Required:"));
});
