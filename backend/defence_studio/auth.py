import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def authenticated_user(secret: str):
    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> str:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
        try:
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
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from None

    return dependency


def _decode(value: str) -> str:
    return _decode_bytes(value).decode("utf-8")


def _decode_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
