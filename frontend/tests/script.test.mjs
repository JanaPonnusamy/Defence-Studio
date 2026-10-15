import test from "node:test";
import assert from "node:assert/strict";
import { renderStalenessBannerHtml, renderRepetitionHtml, renderComparisonHtml, renderRevisionDetail } from "../js/views/script-view.js";

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

test("revision detail shows source attribution, the claim warning, and an offline draft", () => {
  const html = renderRevisionDetail({
    title: "S-400", language: "en", version: 1, status: "AWAITING_REVIEW",
    word_count: { words: 10, estimated_duration_seconds: 4, words_per_minute: 150 },
    validation: { passed: true, validator_version: "v2", issues: [
      { severity: "WARNING", detail: "Claim claim-05608baa58e4c016: source publication date is unavailable", section_id: "s001" },
    ] },
    usage: { provider: "offline-extractive", model: "gpt-4o-mini", retries: 0, cost_is_measured: true, measured_text_cost_inr: 0, pricing_source: "manual" },
    sections: [{ section_id: "s001", statement_kind: "ATTRIBUTED_CLAIM", narration: "Reported range.",
      evidence: { claim_ids: ["claim-05608baa58e4c016"], source_ids: ["148827db7320d204c6c3f4e53296339538cf636f"], qualification: "Reported by the cited source." } }],
    closing: "End.", approval: null,
  }, []);
  assert.ok(html.includes("claim-05608baa58e4c016") && html.includes("148827db7320d204c6c3f4e53296339538cf636f"));
  assert.ok(html.includes("Offline extractive draft") && html.includes("Recorded token usage: none"));
  assert.ok(html.includes('id="approve-revision-btn"') && !html.includes('id="approve-revision-btn" disabled'));
  const provider = renderRevisionDetail({
    title: "T", language: "en", version: 1, status: "AWAITING_REVIEW",
    word_count: { words: 1, estimated_duration_seconds: 1, words_per_minute: 150 },
    validation: { passed: true, validator_version: "v2", issues: [] },
    usage: { provider: "openai", model: "gpt-4o-mini", retries: 0, cost_is_measured: false },
    sections: [{ section_id: "s001", statement_kind: "ATTRIBUTED_CLAIM", narration: "x", evidence: { claim_ids: [], source_ids: [] } }],
    closing: "", approval: null,
  }, []);
  assert.ok(!provider.includes("Offline extractive draft"));
});

test("comparison lists section and claim changes", () => {
  const html = renderComparisonHtml({ old_version: 1, new_version: 2, overlap_ratio: 0.8, word_delta: -5,
    sections_added: ["s2"], sections_removed: [], sections_changed: [], claims_added: [], claims_removed: ["c1"], package_changed: true });
  assert.ok(html.includes("80% phrase overlap") && html.includes("s2") && html.includes("different evidence"));
  assert.ok(renderComparisonHtml(null).includes("No earlier revision"));
});
