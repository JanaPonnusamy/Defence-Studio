import base64
import hashlib
import hmac
import json
import time

import pytest

from defence_studio.auth import _decode, _decode_bytes
from scripts.mint_dev_token import MIN_SECRET_BYTES, main, mint_token, parse_args

SECRET = "x" * MIN_SECRET_BYTES


def _verify(token: str, secret: str) -> dict:
    header_part, payload_part, signature_part = token.split(".")
    header = json.loads(_decode(header_part))
    payload = json.loads(_decode(payload_part))
    supplied = _decode_bytes(signature_part)
    expected = hmac.new(secret.encode(), f"{header_part}.{payload_part}".encode(), hashlib.sha256).digest()
    assert header == {"alg": "HS256", "typ": "JWT"}
    assert hmac.compare_digest(supplied, expected)
    return payload


def test_mint_token_valid_and_verifiable():
    token = mint_token("owner-123", 15, SECRET)
    payload = _verify(token, SECRET)
    assert payload["sub"] == "owner-123"
    assert payload["exp"] > time.time()


def test_mint_token_default_expiry_window():
    before = time.time()
    token = mint_token("owner-123", 5, SECRET)
    payload = _verify(token, SECRET)
    assert 4 * 60 <= payload["exp"] - before <= 5 * 60 + 5


def test_mint_token_rejects_short_secret():
    with pytest.raises(ValueError, match="at least"):
        mint_token("owner-123", 15, "short-secret")


def test_mint_token_rejects_missing_secret():
    with pytest.raises(ValueError, match="at least"):
        mint_token("owner-123", 15, "")


def test_mint_token_rejects_empty_sub():
    with pytest.raises(ValueError, match="--sub"):
        mint_token("", 15, SECRET)


def test_mint_token_rejects_whitespace_sub():
    with pytest.raises(ValueError, match="--sub"):
        mint_token("   ", 15, SECRET)


def test_mint_token_rejects_non_positive_minutes():
    with pytest.raises(ValueError, match="--minutes"):
        mint_token("owner-123", 0, SECRET)
    with pytest.raises(ValueError, match="--minutes"):
        mint_token("owner-123", -5, SECRET)


def test_mint_token_rejects_excessive_minutes():
    with pytest.raises(ValueError, match="--minutes"):
        mint_token("owner-123", 24 * 60 + 1, SECRET)


def test_mint_token_produces_expired_token_when_minutes_elapsed():
    token = mint_token("owner-123", 1, SECRET)
    header_part, payload_part, _ = token.split(".")
    payload = json.loads(_decode(payload_part))
    assert payload["exp"] <= time.time() + 60


def test_parse_args_requires_sub():
    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_defaults_minutes():
    args = parse_args(["--sub", "owner-123"])
    assert args.sub == "owner-123"
    assert args.minutes == 15


def test_main_missing_secret_env_returns_error(monkeypatch, capsys):
    monkeypatch.delenv("DEFENCE_STUDIO_AUTH_SECRET", raising=False)
    exit_code = main(["--sub", "owner-123"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error" in captured.err
    assert captured.out == ""


def test_main_prints_token_on_success(monkeypatch, capsys):
    monkeypatch.setenv("DEFENCE_STUDIO_AUTH_SECRET", SECRET)
    exit_code = main(["--sub", "owner-123", "--minutes", "10"])
    captured = capsys.readouterr()
    assert exit_code == 0
    token = captured.out.strip()
    payload = _verify(token, SECRET)
    assert payload["sub"] == "owner-123"


def test_main_invalid_sub_returns_error(monkeypatch, capsys):
    monkeypatch.setenv("DEFENCE_STUDIO_AUTH_SECRET", SECRET)
    exit_code = main(["--sub", "  "])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "error" in captured.err
