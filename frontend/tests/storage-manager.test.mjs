import test from "node:test";
import assert from "node:assert/strict";
import {
  cleanupConfirmPhrase, cleanupConfirmed, formatBytes, renderRenderControlsHtml, renderStorageSummaryHtml,
} from "../js/views/storage-manager-view.js";

test("storage summary prints only reported measurements", () => {
  const html = renderStorageSummaryHtml({
    measured_at: "2026-11-01T00:00:00Z",
    managed_root: "D:/data/storage",
    total_bytes: null,
    permanent_bytes: 2048,
    temporary_bytes: 0,
    orphan_count: null,
    reclaimed_bytes_total: null,
    cleanup_error_count: 0,
    by_module: [{ module: "renderer", bytes: 4096, count: 2 }],
    largest: [],
    last_cleanup: null,
  });
  assert.match(html, /not reported/);
  assert.match(html, /renderer/);
  assert.match(html, /No cleanup has been recorded/);
  assert.doesNotMatch(html, /100%/);
});

test("render controls stay unconfigured when the API has no value", () => {
  const html = renderRenderControlsHtml({
    note: "No render limit is configured.",
    max_simultaneous_renders: { value: null, status: "NOT_CONFIGURED", detail: "renderer has no concurrency cap" },
  });
  assert.match(html, /NOT_CONFIGURED/);
  assert.match(html, /no concurrency cap/);
  assert.doesNotMatch(html, /100%/);
});

test("manual cleanup confirmation matches the dry-run file count", () => {
  assert.equal(cleanupConfirmPhrase(3), "DELETE 3 FILES");
  assert.equal(cleanupConfirmed("DELETE 3 FILES", 3), true);
  assert.equal(cleanupConfirmed("DELETE 3 FILES", 4), false);
  assert.equal(formatBytes(null), "not reported");
  assert.equal(formatBytes(1536), "1.5 KB");
});

test("summary renders measured roots without inventing a total", () => {
  const html = renderStorageSummaryHtml({
    measured_at: "2026-11-01T00:00:00Z",
    permanent_bytes: 10,
    temporary_bytes: 0,
    roots: [{ root: "D:/data/storage", bytes: 100, disk_free_bytes: 50 }],
    by_module: { renderer: 100 },
    largest: [],
    last_cleanup: null,
  });
  assert.match(html, /D:\/data\/storage/);
  assert.match(html, /not reported/);
  assert.match(html, /renderer/);
});
