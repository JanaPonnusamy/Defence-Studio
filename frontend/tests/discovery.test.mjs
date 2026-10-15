import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../js/api.js";
import {
  renderDiscoveryCandidatesListHtml, renderCoverageHtml, renderEmptyResultsHtml,
  renderPaginationHtml, renderSearchHistoryHtml, renderDiscoveryFilterFormHtml,
  renderDiscoveryRequestFormHtml, parseSeedUrlInput,
} from "../js/views/discovery-view.js";

const candidate = {
  candidate_id: "c1", canonical_url: "https://x.example/a", provider: "rss_feed",
  declared_metadata: { title: "<script>alert(1)</script>", publisher: "Pub", published_at: null },
  ranking: { total_score: 0.7, explanation: ["topic match"] }, category: "RUSSIA_UKRAINE",
  regions: ["UKRAINE"], source_class: "INDEPENDENT_REPORTING", snippet: "a <b>snippet</b>",
  retrieved_at: "2026-10-14T00:00:00Z", filter_flags: ["date_filter_unverifiable"],
};

test("candidate card escapes content and shows taxonomy, retrieval time and unverifiable flags", () => {
  const html = renderDiscoveryCandidatesListHtml([candidate], new Set());
  assert.ok(!html.includes("<script>alert"));
  assert.ok(html.includes("category: russia ukraine"));
  assert.ok(html.includes("region: ukraine"));
  assert.ok(html.includes("date not declared"));
  assert.ok(html.includes("publication date not declared"));
  assert.ok(html.includes("retrieved 2026-10-14"));
});

test("coverage: all unavailable shows error banner, partial shows warning, healthy shows neither", () => {
  const unavailable = renderCoverageHtml({ providers: [
    { provider: "rss_feed", status: "UNAVAILABLE_NOT_CONFIGURED", message: "no feeds", candidates_returned: 0 },
  ] });
  assert.ok(unavailable.includes("banner error") && unavailable.includes("no feeds"));
  const partial = renderCoverageHtml({ providers: [
    { provider: "rss_feed", status: "PARTIAL", message: "1 of 2 failed", candidates_returned: 3 },
    { provider: "configured_search_api", status: "OK", message: "", candidates_returned: 1 },
  ] });
  assert.ok(partial.includes("banner warn") && partial.includes("results are partial"));
  const ok = renderCoverageHtml({ providers: [{ provider: "rss_feed", status: "OK", message: "", candidates_returned: 2 }] });
  assert.ok(!ok.includes("banner"));
  assert.equal(renderCoverageHtml({ providers: [] }), "");
});

test("empty state never claims the web has no results", () => {
  const html = renderEmptyResultsHtml({ providers: [{}, {}] });
  assert.ok(html.includes("2 configured providers"));
  assert.ok(html.includes("does not mean"));
});

test("pagination shows Load more only when a cursor exists", () => {
  assert.ok(renderPaginationHtml({ candidates: [1, 2], total_count: 5, next_cursor: "2" }).includes("discovery-load-more"));
  assert.ok(!renderPaginationHtml({ candidates: [1, 2], total_count: 2, next_cursor: null }).includes("discovery-load-more"));
});

test("history lists reopen and rerun actions, and an empty state", () => {
  const html = renderSearchHistoryHtml([{ job_id: "j1", topic: "S-400", status: "COMPLETED", candidate_count: 4, created_at: "t" }]);
  assert.ok(html.includes('data-reopen-search="j1"') && html.includes('data-rerun-search="j1"'));
  assert.ok(renderSearchHistoryHtml([]).includes("No previous searches"));
});

test("filter form is built from the backend taxonomy", () => {
  const html = renderDiscoveryFilterFormHtml(
    { categories: ["INDIAN_DEFENCE"], regions: ["INDIA"], source_classes: ["OFFICIAL"] },
    { category: "INDIAN_DEFENCE" });
  assert.ok(html.includes('value="INDIAN_DEFENCE" selected'));
  assert.ok(html.includes("discovery-filter-date-from"));
});

test("listDiscoveryCandidates sends the new filters and the new endpoints are auth'd", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({}), { status: 200 });
  };
  try {
    const client = createApiClient(() => "cred");
    await client.listDiscoveryCandidates("j1", { category: "INDIAN_DEFENCE", dateFrom: "2026-10-01", cursor: "20", pageSize: 20 });
    await client.getDiscoveryProviders();
    await client.getDiscoveryTaxonomy();
    await client.listDiscoverySearches();
    await client.rerunDiscovery("j1");
    const url = new URL(calls[0].url, "http://x");
    assert.equal(url.searchParams.get("category"), "INDIAN_DEFENCE");
    assert.equal(url.searchParams.get("date_from"), "2026-10-01");
    assert.equal(url.searchParams.get("cursor"), "20");
    assert.equal(url.searchParams.get("page_size"), "20");
    assert.ok(calls[1].url.endsWith("/api/discovery/providers"));
    assert.ok(calls[3].url.endsWith("/api/discovery/searches"));
    assert.equal(calls[4].options.method, "POST");
    assert.ok(calls.every((c) => c.options.headers.Authorization === "Bearer cred"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("source URL field rejects empty, malformed, and credentialed URLs before a request exists", () => {
  assert.match(parseSeedUrlInput("  \n", { required: true }).error, /at least one/);
  assert.equal(parseSeedUrlInput("", { required: false }).error, null);
  assert.match(parseSeedUrlInput("not a url", { required: true }).error, /absolute http or https/);
  assert.match(parseSeedUrlInput("ftp://example.com/a", { required: true }).error, /http or https/);
  assert.match(parseSeedUrlInput("https://user:pw@example.com/a", { required: true }).error, /username or password/);
  const ok = parseSeedUrlInput("https://example.com/a\nhttps://example.com/a\nhttps://example.com/b", { required: true });
  assert.deepEqual(ok.urls, ["https://example.com/a", "https://example.com/b"]);
  assert.equal(ok.error, null);
});

test("discovery form keeps saved source URLs and does not imply approval", () => {
  const html = renderDiscoveryRequestFormHtml("Northwind", ["https://example.com/a"]);
  assert.ok(html.includes("https://example.com/a"));
  assert.ok(html.includes("discovery-save-urls"));
  assert.ok(html.includes("does not approve"));
  assert.ok(!html.includes("<script"));
});

test("createDiscoveryRequest sends seed_urls and does not put them in the URL", async () => {
  const originalFetch = globalThis.fetch;
  let sent;
  globalThis.fetch = async (url, options) => {
    sent = { url, options };
    return new Response(JSON.stringify({ request: { seed_urls: ["https://example.com/a"] } }), { status: 201 });
  };
  try {
    const client = createApiClient(() => "cred");
    await client.createDiscoveryRequest("job-1", {
      topic: "Northwind", language: "en", search_terms: [], seed_urls: ["https://example.com/a"],
    });
    assert.equal(sent.options.method, "POST");
    assert.ok(sent.url.endsWith("/api/jobs/job-1/discovery/requests"));
    assert.equal(JSON.parse(sent.options.body).seed_urls[0], "https://example.com/a");
    assert.ok(!sent.url.includes("example.com"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
