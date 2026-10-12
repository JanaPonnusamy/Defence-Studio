"""Local-only HS256 bearer token minter for Defence Studio development.

Mirrors the exact token format verified by ``defence_studio.auth.authenticated_user``:
compact ``header.payload.signature`` with base64url (no padding) segments, HS256
HMAC-SHA256 signature, and a payload of ``{"sub": <owner_id>, "exp": <unix ts>}``.

The signing secret is read only from the ``DEFENCE_STUDIO_AUTH_SECRET`` environment
variable of this process - it is never logged, printed, or written to disk. Run this
script in the same shell/session where that variable is already set to the value the
target server was started with; only the resulting token is printed.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time

MIN_SECRET_BYTES = 32
DEFAULT_MINUTES = 15
MAX_MINUTES = 24 * 60


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_token(sub: str, minutes: int, secret: str) -> str:
    if not isinstance(sub, str) or not sub.strip():
        raise ValueError("--sub must be a non-empty string")
    if not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("--minutes must be a positive integer")
    if minutes > MAX_MINUTES:
        raise ValueError(f"--minutes must be <= {MAX_MINUTES}")
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise ValueError(
            f"DEFENCE_STUDIO_AUTH_SECRET must be set to a secret of at least {MIN_SECRET_BYTES} bytes"
        )

    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps({"sub": sub, "exp": int(time.time()) + minutes * 60}).encode()
    )
    signing_input = f"{header}.{payload}"
    signature = _b64url(
        hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    )
    return f"{signing_input}.{signature}"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mint a local-development HS256 bearer token for Defence Studio."
    )
    parser.add_argument("--sub", required=True, help="Owner id to embed as the token subject")
    parser.add_argument(
        "--minutes",
        type=int,
        default=DEFAULT_MINUTES,
        help=f"Minutes until expiry (default: {DEFAULT_MINUTES}, max: {MAX_MINUTES})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    secret = os.environ.get("DEFENCE_STUDIO_AUTH_SECRET", "")
    try:
        token = mint_token(args.sub, args.minutes, secret)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
