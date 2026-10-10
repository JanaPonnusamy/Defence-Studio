// Reusable status-badge primitive (registry UI-03). Renders any of the F5
// review decisions, F3 acquisition statuses, or F4 normalization statuses
// with a consistent, color-coded shape -- never a fabricated percentage,
// only the real status string the backend returned.

const LABELS = {
  PENDING: "Pending review",
  APPROVED_FOR_USE: "Approved for use",
  REJECTED: "Rejected",
  NEEDS_MORE_EVIDENCE: "Needs more evidence",
  CONFLICT_UNRESOLVED: "Conflict unresolved",
  ACQUIRED: "Acquired",
  FAILED: "Failed",
  SKIPPED_DUPLICATE: "Skipped (duplicate)",
  NORMALIZED: "Normalized",
  COLLECTED: "Collected",
  UNAVAILABLE: "Unavailable",
  NOT_COLLECTED: "Not collected",
};

export function statusBadgeHtml(status, { stale = false } = {}) {
  const safeStatus = String(status || "PENDING");
  const label = LABELS[safeStatus] || safeStatus;
  const staleSuffix = stale ? ' <span class="badge stale">Stale revision</span>' : "";
  return `<span class="badge ${escapeAttr(safeStatus)}">${escapeHtml(label)}</span>${staleSuffix}`;
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function escapeAttr(value) {
  return String(value ?? "").replace(/[^A-Za-z0-9_-]/g, "");
}
