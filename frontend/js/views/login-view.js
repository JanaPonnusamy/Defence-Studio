// Development/entry login view. A real entry experience with authentication
// handled internally: in a safely-configured local dev environment the operator
// clicks "Enter development mode" and the server mints a short-lived token for
// them (via GET /api/dev/session) — no manual token copy/paste. A production
// operator can still paste a bearer token issued by their trusted issuer.
//
// The dev button is only offered after we probe /api/dev/session and find it
// available; otherwise only the production token entry is shown. The token is
// never fabricated client-side and the signing secret is never exposed here.

import { escapeHtml } from "../components/status-badge.js";
import { textFieldHtml } from "../components/text-field.js";
import { icon } from "../components/icons.js";

export function renderLoginHtml({ devAvailable, devOwner }) {
  const devBlock = devAvailable
    ? `
      <div class="login-dev">
        <button type="button" id="dev-enter-btn" class="primary login-primary">
          ${icon("shield", { size: 16 })} Enter development mode
        </button>
        <p class="meta login-dev-meta">
          Local loopback session as
          <span class="mono">${escapeHtml(devOwner || "the configured dev owner")}</span>.
          The server issues a short-lived token for you.
        </p>
      </div>
      <div class="login-divider"><span>or use a bearer token</span></div>`
    : "";
  return `
    <div class="login-screen">
      <div class="login-card">
        <div class="login-brand">
          <span class="brand-mark login-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
                 stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 2 20 5v6c0 5.2-3.6 8.1-8 9.6C7.6 19.1 4 16.2 4 11V5Z"/>
              <path d="M12 7v10M8.5 10.5 12 7l3.5 3.5"/>
            </svg>
          </span>
          <h1>Defence Studio</h1>
          <p class="login-tagline">Content Intelligence Workspace</p>
        </div>
        ${devBlock}
        <form id="login-token-form" class="login-form" novalidate>
          ${textFieldHtml({ id: "login-token-field", label: "Bearer token", type: "password", placeholder: "signed HS256 token from your issuer" })}
          <button type="submit" class="login-secondary">Sign in with token</button>
        </form>
        <p id="login-error" class="error-state login-error" role="alert" hidden></p>
      </div>
    </div>`;
}
