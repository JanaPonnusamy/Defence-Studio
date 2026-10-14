import test from "node:test";
import assert from "node:assert/strict";
import { renderStalenessBannerHtml, renderRepetitionHtml, renderComparisonHtml } from "../js/views/script-view.js";

test("stale banner appears only when a revision is stale and escapes the reason", () => {
  assert.equal(renderStalenessBannerHtml({ revisions: [{ state: "CURRENT" }] }), "");
  const html = renderStalenessBannerHtml({ revisions: [{ state: "STALE", reason: "<b>x</b>" }] });
  assert.ok(html.includes("no longer match") && !html.includes("<b>x</b>"));
});

test("repetition report flags matches and states the comparison size when clean", () => {
  assert.ok(renderRepetitionHtml({ compared_against: 3, matches: [] }).includes("3 other script"));
  const html = renderRepetitionHtml({ compared_against: 1, matches: [{ topic: "S-400", version: 2, overlap_ratio: 0.5 }] });
  assert.ok(html.includes("50% shared"));
});

test("comparison lists section and claim changes", () => {
  const html = renderComparisonHtml({ old_version: 1, new_version: 2, overlap_ratio: 0.8, word_delta: -5,
    sections_added: ["s2"], sections_removed: [], sections_changed: [], claims_added: [], claims_removed: ["c1"], package_changed: true });
  assert.ok(html.includes("80% phrase overlap") && html.includes("s2") && html.includes("different evidence"));
  assert.ok(renderComparisonHtml(null).includes("No earlier revision"));
});
