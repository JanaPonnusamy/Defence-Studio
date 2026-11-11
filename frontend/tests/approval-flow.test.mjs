import test from "node:test";
import assert from "node:assert/strict";
import { mountEvidence, recommendationHtml } from "../js/views/evidence-view.js";

test("blocked evidence explains human approval and reassesses without an approve button", async () => {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { innerHTML: "", handlers: {},
      addEventListener(type, handler) { this.handlers[type] = handler; } });
    return nodes.get(id);
  };
  const list = { innerHTML: "", querySelector(selector) {
    assert.ok(["#package-action-status", "#run-assessment", "#export-approved", "#export-draft"].includes(selector), selector);
    return node(selector);
  } };
  const detail = { innerHTML: "", classList: { remove() {} }, querySelector: node };
  const filters = { innerHTML: "" };
  let assessmentRuns = 0;
  const api = {
    evidencePackage: async () => ({ revision: "current", current_approval: null, approvals: [],
      package: { ready: false, sources: [], claims: [], eligible_claim_ids: [],
        blockers: ["Need another independent source"], limitations: [], conflicts: [] } }),
    request: async method => {
      if (method === "POST") assessmentRuns++;
      return { ready_for_generation: false, explanation: "Need another independent source", policy_criteria_failed: [] };
    },
    evidenceTimeline: async () => ({ note: "No records", items: [] }),
  };
  await mountEvidence({ api, jobId: "j1", list, detail, filters, isCurrent: () => true });
  assert.match(list.innerHTML, /separate human action|Resolve the requirements/);
  assert.match(list.innerHTML, /Need another independent source/);
  assert.doesNotMatch(list.innerHTML, /approve-package|package-note/);
  assert.match(filters.innerHTML, /person approves/);
  assert.doesNotMatch(list.innerHTML, /Approval is automatic/);
  await node("#run-assessment").handlers.click({ target: node("#run-assessment") });
  assert.equal(assessmentRuns, 1);
  assert.doesNotMatch(detail.innerHTML, /error-state/);
});

test("policy recommendation stays visible and never adds an automatic approval", () => {
  const html = recommendationHtml({
    recommendation: "RECOMMEND_HUMAN_APPROVAL",
    awaiting_human_review: true,
    legacy_automated_approval_ignored: true,
    missing_information: ["accepted_claims: required 8, got 5"],
  });
  assert.match(html, /id="policy-recommendation"/);
  assert.match(html, /person still has to approve/);
  assert.match(html, /id="awaiting-human"/);
  assert.match(html, /id="legacy-approval"/);
  assert.match(html, /accepted_claims: required 8, got 5/);
  assert.doesNotMatch(html, /approve-package|Automatically approved/);
  assert.equal(recommendationHtml(null), "");
  assert.equal(recommendationHtml({ ready_for_generation: false }), "");
});

test("a ready package still requires the human approve control", async () => {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { innerHTML: "", value: "Reviewed the sources.", handlers: {},
      addEventListener(type, handler) { this.handlers[type] = handler; } });
    return nodes.get(id);
  };
  const list = { innerHTML: "", querySelector(selector) { return node(selector); } };
  const detail = { innerHTML: "", classList: { remove() {} }, querySelector: node };
  const api = {
    evidencePackage: async () => ({
      revision: "rev-1", current_approval: null,
      approvals: [{ version: 1, created_at: "2026-11-01", reviewer_id: "AUTOMATED_POLICY",
        approval_kind: "LEGACY_AUTOMATED", counts_for_production: false, note: "old",
        revision: "rev-0", sha256: "abc" }],
      package: { ready: true, sources: [{}], claims: [{}], eligible_claim_ids: ["c1"],
        blockers: [], limitations: [], conflicts: [] },
    }),
    request: async () => ({
      ready_for_generation: false,
      recommendation: "RECOMMEND_HUMAN_APPROVAL",
      awaiting_human_review: true,
      legacy_automated_approval_ignored: true,
      explanation: "Evidence criteria are met.",
      policy_criteria_failed: [],
      missing_information: [],
    }),
    evidenceTimeline: async () => ({ note: "No records", items: [] }),
  };
  await mountEvidence({ api, jobId: "j2", list, detail, filters: { innerHTML: "" }, isCurrent: () => true });
  assert.match(list.innerHTML, /id="approve-package"/);
  assert.match(list.innerHTML, /id="policy-recommendation"/);
  assert.match(list.innerHTML, /id="legacy-approval"/);
  assert.match(list.innerHTML, /History only/);
  assert.doesNotMatch(list.innerHTML, /Automatically approved/);
});
