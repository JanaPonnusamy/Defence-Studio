// Reusable modal/dialog-shell primitive (registry UI-04). Used before every
// consequential review decision so a tap/click cannot silently commit a
// decision. Touch- and keyboard-usable: Escape and a visible Cancel button
// both close without side effects; focus starts on the primary action.

import { escapeHtml } from "./status-badge.js";

export function confirmDialog({ title, message, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false }) {
  return new Promise((resolve) => {
    const previousFocus = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.setAttribute("role", "presentation");
    overlay.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <h2 id="confirm-title">${escapeHtml(title)}</h2>
        <p>${escapeHtml(message)}</p>
        <div class="modal-actions">
          <button type="button" data-action="cancel">${escapeHtml(cancelLabel)}</button>
          <button type="button" data-action="confirm" class="${danger ? "danger" : "primary"}">${escapeHtml(confirmLabel)}</button>
        </div>
      </div>`;

    function close(result) {
      document.removeEventListener("keydown", onKeydown);
      overlay.remove();
      if (previousFocus?.isConnected) previousFocus.focus();
      resolve(result);
    }
    function onKeydown(event) {
      if (event.key === "Escape") close(false);
      if (event.key === "Tab") {
        const buttons = [...overlay.querySelectorAll("button")];
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close(false);
    });
    overlay.querySelector('[data-action="cancel"]').addEventListener("click", () => close(false));
    overlay.querySelector('[data-action="confirm"]').addEventListener("click", () => close(true));
    document.addEventListener("keydown", onKeydown);

    document.body.appendChild(overlay);
    overlay.querySelector('[data-action="confirm"]').focus();
  });
}
