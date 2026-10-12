// F5-specific review decision panel (not a generic UI primitive -- the four
// decision states and their meaning are business logic, kept out of
// components/ per the registry's "primitives stay business-free" rule).
// Shared between the claim detail view and the source detail view, since
// both record an APPROVED_FOR_USE / REJECTED / NEEDS_MORE_EVIDENCE /
// CONFLICT_UNRESOLVED decision against an exact artifact revision.

import { escapeHtml, statusBadgeHtml } from "../components/status-badge.js";
import { textFieldHtml } from "../components/text-field.js";
import { confirmDialog } from "../components/confirm-dialog.js";
import { newIdempotencyKey } from "../api.js";
import { showToast } from "../components/toast.js";
import { icon } from "../components/icons.js";

const DECISIONS = [
  { value: "APPROVED_FOR_USE", label: "Approve for use", danger: false,
    confirmMessage: "Record this claim/source as approved for potential downstream use? This does not certify it as objectively true." },
  { value: "REJECTED", label: "Reject", danger: true,
    confirmMessage: "Record this as rejected? It will be blocked from downstream use until a later explicit decision changes this." },
  { value: "NEEDS_MORE_EVIDENCE", label: "Needs more evidence", danger: false,
    confirmMessage: "Mark this as needing more evidence before a decision can be made?" },
  { value: "CONFLICT_UNRESOLVED", label: "Mark conflict unresolved", danger: false,
    confirmMessage: "Mark this conflict as unresolved for now?" },
];

export function renderDecisionPanelHtml(review, isStale) {
  const current = review
    ? `<p>Current decision: ${statusBadgeHtml(review.decision, { stale: isStale })}
       by ${escapeHtml(review.reviewer_id)} (revision ${review.revision})</p>
       <p class="meta">${escapeHtml(review.note || "(no note)")}</p>`
    : `<p>${statusBadgeHtml("PENDING")} -- no decision recorded yet.</p>`;

  const staleWarning = isStale
    ? `<p class="inline-warn" role="status"><span aria-hidden="true">${icon("alert", { size: 14 })}</span>
       <span>This decision was made against a revision that has since been superseded.
       Reload to review the current evidence before deciding again.</span></p>`
    : "";

  return `
    <div class="decision-panel">
      ${current}
      ${staleWarning}
      ${textFieldHtml({ id: "decision-note", label: "Review note / reason", multiline: true, maxLength: 4000,
        placeholder: "Required: explain the evidence behind your decision." })}
      <div class="decision-actions">
        ${DECISIONS.map((d) => `<button type="button" data-decision="${d.value}" class="${d.danger ? "danger" : ""}">${escapeHtml(d.label)}</button>`).join("")}
      </div>
    </div>`;
}

/**
 * Wires the buttons rendered by renderDecisionPanelHtml. `onDecide` must be an
 * async function(decisionValue, note) -> Promise<void> that calls the real
 * API and throws on failure (ApiError with .status 409 for stale/conflict).
 */
export function wireDecisionPanel(container, { expectedRevision, reviewedArtifactSha256, onDecide, disabled }) {
  if (disabled) {
    container.querySelectorAll("[data-decision]").forEach((btn) => { btn.disabled = true; });
    return;
  }
  container.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", async () => {
      const note = container.querySelector("#decision-note")?.value.trim() || "";
      if (!note) {
        showToast("Enter a review explanation before submitting.", { isError: true });
        container.querySelector("#decision-note")?.focus();
        return;
      }
      const decisionDef = DECISIONS.find((d) => d.value === button.dataset.decision);
      const confirmed = await confirmDialog({
        title: "Confirm review decision",
        message: decisionDef.confirmMessage,
        confirmLabel: decisionDef.label,
        danger: decisionDef.danger,
      });
      if (!confirmed) return;
      container.querySelectorAll("[data-decision]").forEach((b) => { b.disabled = true; });
      try {
        await onDecide(decisionDef.value, note, expectedRevision, reviewedArtifactSha256, newIdempotencyKey());
        showToast("Decision saved.");
      } catch (err) {
        if (err && err.status === 409) {
          showToast(err.detail || "This evidence changed since you loaded it. Reloading the latest revision.", { isError: true });
        } else if (err && err.status === 422) {
          showToast(err.detail || "That decision could not be recorded.", { isError: true });
        } else {
          showToast((err && err.message) || "Could not save the decision.", { isError: true });
        }
        container.querySelectorAll("[data-decision]").forEach((b) => { b.disabled = false; });
      }
    });
  });
}

export function renderHistoryHtml(history) {
  if (!history || history.length === 0) {
    return `<p class="meta">No review history yet.</p>`;
  }
  const rows = history.slice().reverse().map((entry) => `
    <li>
      ${statusBadgeHtml(entry.decision)} by ${escapeHtml(entry.reviewer_id)}
      &mdash; revision ${entry.revision} at ${escapeHtml(entry.occurred_at)}
      ${entry.previous_decision ? `<br><span class="meta">Previously: ${escapeHtml(entry.previous_decision)}</span>` : ""}
      ${entry.note ? `<br><span class="meta">${escapeHtml(entry.note)}</span>` : ""}
    </li>`).join("");
  return `<ul class="history-list">${rows}</ul>`;
}
