// Minimal transient status primitive for save/error feedback.
import { escapeHtml } from "./status-badge.js";

let activeToast = null;

export function showToast(message, { isError = false, timeoutMs = 4000 } = {}) {
  if (activeToast) activeToast.remove();
  const el = document.createElement("div");
  el.className = `toast${isError ? " error" : ""}`;
  el.setAttribute("role", "status");
  el.innerHTML = escapeHtml(message);
  document.body.appendChild(el);
  activeToast = el;
  window.setTimeout(() => {
    if (activeToast === el) {
      el.remove();
      activeToast = null;
    }
  }, timeoutMs);
}
