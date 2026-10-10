// Reusable text-input primitive (registry UI-01). One canonical markup/
// validation shape for every single-line or multi-line text input in the
// review UI (job id, bearer token, review notes) instead of re-implementing
// labelled inputs per screen.

import { escapeHtml } from "./status-badge.js";

export function textFieldHtml({ id, label, value = "", type = "text", maxLength, placeholder = "", multiline = false }) {
  const safeValue = escapeHtml(value);
  const lengthAttr = maxLength ? `maxlength="${maxLength}"` : "";
  const tag = multiline
    ? `<textarea id="${id}" ${lengthAttr} placeholder="${escapeHtml(placeholder)}">${safeValue}</textarea>`
    : `<input id="${id}" type="${type}" ${lengthAttr} placeholder="${escapeHtml(placeholder)}" value="${safeValue}">`;
  return `<div class="field"><label for="${id}">${escapeHtml(label)}</label>${tag}</div>`;
}
