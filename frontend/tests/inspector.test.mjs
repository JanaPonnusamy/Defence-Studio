import test from "node:test";
import assert from "node:assert/strict";
import { renderInspectionHtml, renderTraceHtml } from "../js/views/inspector-view.js";

const rec = (over = {}) => ({
  record_id: "r1", outcome: "VALIDATION_FAILED", script_accepted: false, provider_calls: 1, request_sha256: "a",
  request: { provider: "openai", model: "gpt-4o-mini", wire_body: { messages: [] }, wire_body_bytes: 12,
    structure: { instructions: { system: "SYS <b>", task: "TASK" }, normalized_source_material: [{ claim_id: "c1" }],
      evidence_references: { claim_ids: ["c1"] }, wire_messages_match_client_input: true }, budget_policy: { max_text_cost_inr: 10 } },
  raw_response: { available: true, http: { http_status: 200, body: "{raw}", provider_request_id: "req_1" },
    attempts: [{}], usage: { input_tokens: 5, output_tokens: 3, cost_is_measured: false } },
  parsed_response: { parsed: true, field_errors: ["unexpected top-level field 'x'"], schema_errors: [],
    evidence_reference_checks: [{ section_id: "s1", claim_id: "bad", in_eligible_set: false }] },
  clean_output: { status_label: "REJECTED_NOT_A_SCRIPT", title: "T", opening_hook: "", closing: "", verified_facts: [],
    attributed_claims: [{ section_id: "s1", statement_kind: "ATTRIBUTED_CLAIM", narration: "n", evidence: [{ claim_id: "bad", resolved: false }] }],
    uncertain_or_analysis: [], other: [] },
  validation_report: { passed: false, issues: [{ severity: "ERROR", code: "UNKNOWN_CLAIM", detail: "d" }] },
  ...over,
});

test("failed validation is never shown as success and unresolved refs are flagged", () => {
  const html = renderInspectionHtml(rec());
  assert.ok(html.includes("Not an accepted script") && html.includes("REJECTED_NOT_A_SCRIPT"));
  assert.ok(html.includes("UNRESOLVED") && html.includes("NOT in eligible set") && html.includes("UNKNOWN_CLAIM"));
  assert.ok(!html.includes("Accepted for human review"));
  assert.ok(html.includes("SYS &lt;b&gt;"), "instructions are escaped");
});

test("no-call record states that the provider was not called", () => {
  const html = renderInspectionHtml(rec({ outcome: "BLOCKED_BUDGET", provider_calls: 0,
    raw_response: { available: false, attempts: [] }, request: { structure: {}, budget_policy: {} },
    parsed_response: {}, clean_output: null, validation_report: null }));
  assert.ok(html.includes("provider was not called") && html.includes("Validation did not run"));
});

test("trace names the defect origin stage and escapes source text", () => {
  const html = renderTraceHtml({ available: true, claims_total: 0, hashes: {}, sources: [{
    source_id: "a", status: "NORMALIZED", defect_origin: "claim_extraction", findings: ["no claims"],
    acquisition: {}, raw: { text: "<script>x</script>" }, clean: { text: "t", numeric_tokens_dropped_vs_raw_visible_text: ["42"] },
    structured: {}, claims: [], passage_checks: [] }] });
  assert.ok(html.includes("Defect origin: claim_extraction") && !html.includes("<script>x"));
  assert.ok(html.includes("42"));
  assert.ok(renderTraceHtml({ available: false, reason: "none" }).includes("none"));
});
