import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from defence_studio.dev_auth import DevAuthConfig, request_is_loopback

_bearer = HTTPBearer(auto_error=False)


def _verify_token(secret: str, credentials: HTTPAuthorizationCredentials) -> str:
    """Validate a bearer token and return its subject, or raise ``ValueError``.

    Pure verification with no HTTP concern, so both the production and the
    dev-bypass dependencies share one authoritative code path.
    """
    if credentials.scheme.lower() != "bearer":
        raise ValueError("Bearer scheme required")
    header_part, payload_part, signature_part = credentials.credentials.split(".")
    header = json.loads(_decode(header_part))
    payload = json.loads(_decode(payload_part))
    supplied = _decode_bytes(signature_part)
    expected = hmac.new(secret.encode(), f"{header_part}.{payload_part}".encode(), hashlib.sha256).digest()
    subject = payload.get("sub")
    expiry = payload.get("exp")
    if header.get("alg") != "HS256" or not hmac.compare_digest(supplied, expected):
        raise ValueError("Invalid signature")
    if not isinstance(subject, str) or not subject.strip() or not isinstance(expiry, (int, float)):
        raise ValueError("Missing identity or expiry")
    if expiry <= time.time():
        raise ValueError("Token expired")
    return subject


def authenticated_user(secret: str, dev_config: DevAuthConfig | None = None):
    """Dependency resolving the request's owner id.

    Default (production) behaviour is unchanged: a valid HS256 bearer token is
    required, and anything missing/invalid/expired is a 401.

    When ``dev_config`` is a *safe* dev-bypass config, and only then, a request
    that carries **no** credentials is allowed through as the single configured
    dev owner -- but exclusively for a direct loopback request with no proxy
    headers (see ``dev_auth``). A supplied token is always validated normally
    and its subject wins; an *invalid* supplied token is still rejected, never
    silently bypassed. The bypass can never select an owner from request input,
    so cross-owner isolation holds.
    """
    bypass_active = dev_config is not None and dev_config.safe

    def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> str:
        if credentials is None:
            if bypass_active and request_is_loopback(
                request.client.host if request.client else None, request.headers
            ):
                return dev_config.owner_id
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
        try:
            return _verify_token(secret, credentials)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from None

    return dependency


def mint_dev_session_token(secret: str, subject: str, minutes: int = 60) -> str:
    """Mint a short-lived HS256 token for the dev-session endpoint.

    Uses the server's own signing secret so the resulting token validates
    through the ordinary production path -- the dev endpoint only ever calls
    this under the safe loopback dev-bypass config. The secret is used here and
    nowhere written or logged.
    """
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"sub": subject, "exp": int(time.time()) + minutes * 60}).encode())
    signing_input = f"{header}.{payload}"
    signature = _b64url(hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(value: str) -> str:
    return _decode_bytes(value).decode("utf-8")


def _decode_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
