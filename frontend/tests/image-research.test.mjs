import test from "node:test";
import assert from "node:assert/strict";
import { renderCandidateCardHtml, renderImageTallyHtml, renderProviderStatusHtml } from "../js/views/image-research-view.js";

test("image tally shows the real shortfall and no fake completion percentage", () => {
  const html = renderImageTallyHtml({
    shortfall: 28, tally: "30 required | 4 discovered | 1 duplicates | 0 rejected by relevance | 1 rights unclear | 0 rights restricted | 1 broken | 2 eligible",
    accepted: 0, rejected: 1, pending_review: 1, needs_verification: 0,
  });
  assert.match(html, /30 required/);
  assert.match(html, /Shortfall: 28/);
  assert.match(html, /not complete/);
  assert.doesNotMatch(html, /100%/);
});

test("unclear rights stay labelled and markup in titles is escaped", () => {
  const html = renderCandidateCardHtml({
    image_id: "img-1", title: "<b>Rafale</b>", rights_status: "UNCLEAR", rights_notes: "Not rights-cleared",
    download_status: "FAILED", download_error: "HTTP 404", review_status: "PENDING_REVIEW",
    duplicate_role: "UNIQUE", relevance_status: "RELEVANT", relevance_score: 1,
    source_website: "commons.wikimedia.org", creator: "A", license_name: null,
  });
  assert.match(html, /UNCLEAR/);
  assert.match(html, /Not rights-cleared/);
  assert.match(html, /HTTP 404/);
  assert.doesNotMatch(html, /<b>Rafale<\/b>/);
});

test("social media is shown as unsupported rather than as a search result", () => {
  const html = renderProviderStatusHtml({
    live_search: false,
    providers: [{ name: "social_media", label: "Social media", status: "UNSUPPORTED", detail: "Public visibility is not permission" }],
  });
  assert.match(html, /UNSUPPORTED/);
  assert.match(html, /not a completed search/);
});
