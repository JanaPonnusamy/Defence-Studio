"""Development-only authentication bypass.

Production authentication (HS256 bearer validation in ``auth.py``) is always the
default. This module adds an *opt-in*, *loopback-only*, *single-owner* bypass so a
local operator can open the UI without manually minting and pasting a bearer token.

Every safety property here is deliberate and fails closed:

* **Opt-in only.** Disabled unless ``DEFENCE_STUDIO_DEV_AUTH_BYPASS=1`` *and*
  ``DEFENCE_STUDIO_DEV_MODE=1`` are both set. A single flag is not enough.
* **Loopback only.** A bypassed request is accepted only when it arrives from
  ``127.0.0.1``/``::1`` and carries no forwarding headers (``X-Forwarded-For`` /
  ``X-Real-IP`` / ``Forwarded``). Behind any proxy the client address cannot be
  trusted, so the bypass refuses rather than guesses.
* **No cross-owner impersonation.** The bypass always resolves to exactly one
  configured owner id (``DEFENCE_STUDIO_DEV_AUTH_OWNER``). Request input can never
  choose which owner is assumed, so ownership isolation is preserved: a bypassed
  session sees only that one owner's jobs, exactly as a real token for that owner
  would.
* **A real token still wins.** If a valid bearer token is present it is validated
  normally and its subject is used; the bypass only fills in when no token is
  supplied. An *invalid* token is still rejected (never silently bypassed).
* **No secret exposure.** The signing secret is never read here, never logged, and
  never returned. The dev-session endpoint mints a normal short-lived token using
  the server's own secret so the rest of the app keeps using ordinary tokens.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Addresses we treat as genuinely local. A bypass is refused for anything else.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
# Any of these headers means the request may have been relayed; we cannot trust
# ``request.client.host`` as the real origin, so the bypass fails closed.
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host")

DEFAULT_DEV_OWNER = "e2e-operator"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


@dataclass(frozen=True)
class DevAuthConfig:
    enabled: bool
    owner_id: str

    @property
    def safe(self) -> bool:
        return self.enabled and bool(self.owner_id)


def load_dev_auth_config() -> DevAuthConfig:
    """Resolve the dev-bypass configuration from the environment.

    Both ``DEFENCE_STUDIO_DEV_MODE=1`` and ``DEFENCE_STUDIO_DEV_AUTH_BYPASS=1``
    must be set for the bypass to be considered enabled at all.
    """
    enabled = _flag("DEFENCE_STUDIO_DEV_MODE") and _flag("DEFENCE_STUDIO_DEV_AUTH_BYPASS")
    owner_id = (os.environ.get("DEFENCE_STUDIO_DEV_AUTH_OWNER") or DEFAULT_DEV_OWNER).strip()
    if not enabled:
        # Fail closed: when not explicitly enabled, carry no owner either.
        return DevAuthConfig(enabled=False, owner_id="")
    if not owner_id:
        return DevAuthConfig(enabled=False, owner_id="")
    return DevAuthConfig(enabled=True, owner_id=owner_id)


def request_is_loopback(client_host: str | None, headers) -> bool:
    """True only for a direct loopback request with no forwarding headers.

    ``headers`` is anything supporting case-insensitive ``in`` / ``.get`` like a
    Starlette ``Headers`` mapping. The presence of *any* proxy header fails the
    check: we refuse rather than trust a relayed address.
    """
    if client_host not in _LOOPBACK_HOSTS:
        return False
    for name in _PROXY_HEADERS:
        try:
            if headers.get(name):
                return False
        except AttributeError:
            # Fall back to membership if ``.get`` is unavailable.
            if name in headers:
                return False
    return True
