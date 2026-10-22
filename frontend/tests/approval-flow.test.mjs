import test from "node:test";
import assert from "node:assert/strict";
import { mountEvidence } from "../js/views/evidence-view.js";

test("evidence view mounts and reassesses without a manual approval control", async () => {
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
  assert.match(list.innerHTML, /Approval is automatic/);
  assert.match(list.innerHTML, /Need another independent source/);
  assert.doesNotMatch(list.innerHTML, /approve-package|package-note/);
  assert.match(filters.innerHTML, /No manual package approval/);
  await node("#run-assessment").handlers.click({ target: node("#run-assessment") });
  assert.equal(assessmentRuns, 1);
  assert.doesNotMatch(detail.innerHTML, /error-state/);
});
