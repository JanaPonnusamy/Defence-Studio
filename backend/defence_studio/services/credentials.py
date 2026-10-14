"""Per-owner provider credentials: Fernet ciphertext at rest, never returned."""
from __future__ import annotations

import os
from contextlib import closing
from datetime import datetime, timezone

from defence_studio.database import connect

PROVIDERS = ("openai",)


class CredentialConfigError(ValueError):
    pass


def _fernet():
    key = os.environ.get("DEFENCE_STUDIO_SECRET_KEY") or os.environ.get("DEFENCE_STUDIO_YT_TOKEN_KEY", "")
    if not key:
        raise CredentialConfigError("DEFENCE_STUDIO_SECRET_KEY is not set; credentials cannot be stored")
    from cryptography.fernet import Fernet
    try:
        return Fernet(key.encode())
    except Exception:
        raise CredentialConfigError("DEFENCE_STUDIO_SECRET_KEY is not a valid Fernet key") from None


def set_credential(db, owner_id: str, provider: str, value: str) -> None:
    if provider not in PROVIDERS:
        raise CredentialConfigError(f"Unknown provider {provider!r}; supported: {list(PROVIDERS)}")
    value = (value or "").strip()
    if len(value) < 8 or any(ch.isspace() for ch in value):
        raise CredentialConfigError("The credential looks invalid")
    enc = _fernet().encrypt(value.encode()).decode()
    with closing(connect(db)) as c:
        c.execute("INSERT INTO provider_credentials(owner_id,provider,encrypted_value,last4,updated_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(owner_id,provider) DO UPDATE SET encrypted_value=excluded.encrypted_value, "
                  "last4=excluded.last4, updated_at=excluded.updated_at",
                  (owner_id, provider, enc, value[-4:], datetime.now(timezone.utc).isoformat()))


def delete_credential(db, owner_id: str, provider: str) -> None:
    with closing(connect(db)) as c:
        c.execute("DELETE FROM provider_credentials WHERE owner_id=? AND provider=?", (owner_id, provider))


def status(db, owner_id: str) -> list[dict]:
    with closing(connect(db)) as c:
        rows = {r["provider"]: dict(r) for r in c.execute(
            "SELECT provider,last4,updated_at FROM provider_credentials WHERE owner_id=?", (owner_id,))}
    out = []
    for p in PROVIDERS:
        env = bool(os.environ.get("DEFENCE_STUDIO_OPENAI_API_KEY")) if p == "openai" else False
        r = rows.get(p)
        out.append({"provider": p, "configured": bool(r) or env,
                    "source": "owner" if r else ("server_environment" if env else None),
                    "last4": r["last4"] if r else None, "updated_at": r["updated_at"] if r else None})
    return out


def resolve_credential(db, owner_id: str, provider: str) -> str | None:
    with closing(connect(db)) as c:
        r = c.execute("SELECT encrypted_value FROM provider_credentials WHERE owner_id=? AND provider=?",
                      (owner_id, provider)).fetchone()
    if not r:
        return None
    return _fernet().decrypt(r["encrypted_value"].encode()).decode()
