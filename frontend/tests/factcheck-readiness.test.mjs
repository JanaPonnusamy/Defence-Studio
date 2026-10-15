import test from "node:test";
import assert from "node:assert/strict";
import { providerLineHtml } from "../js/views/factcheck-view.js";

const base = { capability: "ocr", provider: "tesseract", execution: "local", cost: "local", dependency: "detail" };

for (const state of ["READY", "NOT_CONFIGURED", "DEPENDENCY_MISSING", "UNSUPPORTED", "LIVE_VERIFIED"]) {
  test(`readiness label shows ${state}`, () => {
    const html = providerLineHtml({ ...base, readiness: state, available: state === "READY" });
    assert.match(html, new RegExp(`data-readiness="${state}"`));
    assert.match(html, new RegExp(`<span class="chip">${state}</span>`));
  });
}

test("falls back to availability only when backend sent no readiness", () => {
  assert.match(providerLineHtml({ ...base, available: true }), /data-readiness="READY"/);
  assert.match(providerLineHtml({ ...base, available: false }), /data-readiness="NOT_CONFIGURED"/);
});

test("readiness text is escaped", () => {
  const html = providerLineHtml({ ...base, readiness: "<img src=x>" });
  assert.doesNotMatch(html, /<img/);
});

import { singleFlight } from "../js/views/factcheck-view.js";

test("singleFlight ignores re-entry while in flight and re-enables the button afterwards", async () => {
  const once = singleFlight();
  const button = { disabled: false, isConnected: true };
  let runs = 0;
  let release;
  const gate = new Promise((r) => { release = r; });
  const first = once(button, async () => { runs++; await gate; });
  const second = await once(button, async () => { runs++; });   // second rapid click
  assert.equal(second, false);
  assert.equal(button.disabled, true);
  release();
  assert.equal(await first, true);
  assert.equal(runs, 1);
  assert.equal(button.disabled, false);
  assert.equal(await once(button, async () => { runs++; }), true);   // usable again
  assert.equal(runs, 2);
});

test("singleFlight releases the lock when the action throws", async () => {
  const once = singleFlight();
  await assert.rejects(once(null, async () => { throw new Error("boom"); }), /boom/);
  assert.equal(await once(null, async () => {}), true);
});
