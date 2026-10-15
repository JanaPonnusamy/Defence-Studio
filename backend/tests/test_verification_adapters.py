"""Adapter boundary tests. Reverse-image tests are MOCKED transports, not live-provider verification."""
import json

import pytest

from defence_studio.verification import adapters
from defence_studio.verification.providers import capability_registry

DIGEST = "a" * 64


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DEFENCE_STUDIO_REVERSE_IMAGE_URL", "DEFENCE_STUDIO_REVERSE_IMAGE_KEY",
                 "DEFENCE_STUDIO_REVERSE_IMAGE_SEND_IMAGE"):
        monkeypatch.delenv(name, raising=False)


def configure(monkeypatch, url="https://gateway.example/reverse", key=""):
    monkeypatch.setenv("DEFENCE_STUDIO_REVERSE_IMAGE_URL", url)
    if key:
        monkeypatch.setenv("DEFENCE_STUDIO_REVERSE_IMAGE_KEY", key)


def call(transport):
    return adapters.reverse_image_lookup(b"img", DIGEST, "ff00", transport=transport, sleep=lambda s: None)


def test_not_configured_makes_no_call():
    def boom(*a):
        raise AssertionError("must not be called")
    r = call(boom)
    assert r["status"] == "NOT_CONFIGURED" and r["matches"] is None and r["available"] is False


def test_insecure_url_rejected(monkeypatch):
    configure(monkeypatch, url="http://evil.example/x")
    assert call(lambda *a: (200, b"{}"))["status"] == "NOT_CONFIGURED"


def test_mock_matches_normalized_and_key_not_leaked(monkeypatch):
    configure(monkeypatch, key="SECRET-KEY-123")
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(headers=headers, body=json.loads(body), timeout=timeout)
        return 200, json.dumps({"matches": [
            {"url": "https://news.example/a", "title": "T", "published_at": "2024-01-02"},
            {"url": "javascript:alert(1)", "title": "bad"},
            {"url": "https://news.example/b"}]}).encode()
    r = call(transport)
    assert r["status"] == "MATCHES_FOUND" and [m["url"] for m in r["matches"]] == ["https://news.example/a", "https://news.example/b"]
    assert r["matches"][0]["published_at_trust"] == "provider-reported" and r["matches"][1]["published_at_trust"] == "absent"
    assert "image_base64" not in seen["body"] and seen["body"]["sha256"] == DIGEST and seen["timeout"] == 20
    assert "SECRET-KEY-123" not in json.dumps(r)


def test_no_match_is_not_originality(monkeypatch):
    configure(monkeypatch)
    r = call(lambda *a: (200, b'{"matches": []}'))
    assert r["status"] == "NO_MATCH_FOUND" and r["matches"] == [] and "not evidence" in r["note"]


def test_error_classification_and_bounded_retry(monkeypatch):
    configure(monkeypatch)
    calls = []

    def flaky(*a):
        calls.append(1)
        return 503, b""
    r = call(flaky)
    assert r["status"] == "PROVIDER_UNAVAILABLE" and r["reason"] == "UNREACHABLE" and len(calls) == 2
    assert call(lambda *a: (401, b""))["reason"] == "AUTH_REJECTED"
    assert call(lambda *a: (400, b""))["reason"] == "REQUEST_REJECTED"
    n = []
    assert call(lambda *a: (n.append(1) or (403, b"")))["status"] == "PROVIDER_UNAVAILABLE" and len(n) == 1   # 4xx: no retry

    def raiser(*a):
        raise TimeoutError()
    assert call(raiser)["detail"] == "TimeoutError"


@pytest.mark.parametrize("raw", [b"not json", b'{"matches": "x"}', b'{"matches": [1]}',
                                 pytest.param(b"x" * 1_000_001, id="oversized")])
def test_invalid_response_rejected(monkeypatch, raw):
    configure(monkeypatch)
    assert call(lambda *a: (200, raw))["status"] == "INVALID_PROVIDER_RESPONSE"


def test_ocr_reports_dependency_missing_without_inventing_text(monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda name: None)
    r = adapters.ocr_image(b"\x89PNG\r\n\x1a\n")
    assert r["status"] == "DEPENDENCY_MISSING" and r["text"] is None and r["available"] is False


def test_ocr_tsv_parsing_with_mock_subprocess(monkeypatch):
    """MOCK: tesseract is not installed here, so parsing is verified against canned TSV only."""
    monkeypatch.setattr(adapters.shutil, "which", lambda name: "tesseract")
    tsv = "level\tpage\tblock\tpar\tline\tword\tl\tt\tw\th\tconf\ttext\n" \
          "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t91.5\tHELLO\n5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t-1\t\n5\t1\t1\t1\t1\t3\t0\t0\t1\t1\t80\tWORLD\n"

    class Done:
        returncode, stdout, stderr = 0, tsv, ""
    monkeypatch.setattr(adapters.subprocess, "run", lambda *a, **k: Done())
    r = adapters.ocr_image(b"\x89PNG\r\n\x1a\n")
    assert r["text"] == "HELLO WORLD" and r["mean_confidence"] == 85.8 and r["trust"] == "untrusted-evidence"


def test_object_detection_never_labels():
    r = adapters.detect_objects(b"x")
    assert r["labels"] is None and r["status"] == "UNSUPPORTED"


def test_registry_readiness_vocabulary(monkeypatch):
    rows = {(r["capability"], r["provider"]): r for r in capability_registry()}
    allowed = {"READY", "NOT_CONFIGURED", "DEPENDENCY_MISSING", "PROVIDER_UNAVAILABLE", "LIVE_VERIFIED",
               "TESTED_WITH_MOCKS", "UNSUPPORTED"}
    assert all(r["readiness"] in allowed for r in rows.values())
    assert rows[("reverse_image_search", "configured-http")]["readiness"] == "NOT_CONFIGURED"
    assert rows[("object_detection", "none-configured")]["readiness"] == "UNSUPPORTED"
    assert rows[("claim_normalization", "stdlib-rules")]["readiness"] == "LIVE_VERIFIED"


# --- wiring: verify_media only calls the provider on explicit opt-in (MOCKED transport) -------------
def test_verify_media_default_never_calls_provider(monkeypatch):
    from defence_studio.verification.media import verify_media
    configure(monkeypatch)
    monkeypatch.setattr(adapters, "_default_transport",
                        lambda *a: (_ for _ in ()).throw(AssertionError("provider must not be called")))
    r = verify_media(b"\x89PNG\r\n\x1a\n" + b"0" * 32, {})
    assert r["reverse_search"]["reason"] == "CONFIGURED_BUT_NOT_CALLED" and r["reverse_search"]["matches"] is None


def test_verify_media_opt_in_uses_adapter_and_never_calls_not_configured(monkeypatch):
    from defence_studio.verification.media import verify_media
    r = verify_media(b"\x89PNG\r\n\x1a\n" + b"0" * 32, {}, live_reverse_search=True)
    assert r["reverse_search"]["status"] == "NOT_CONFIGURED"
    configure(monkeypatch)
    monkeypatch.setattr(adapters, "_default_transport", lambda *a: (200, b'{"matches": []}'))
    r = verify_media(b"\x89PNG\r\n\x1a\n" + b"0" * 32, {}, live_reverse_search=True)
    assert r["reverse_search"]["status"] == "NO_MATCH_FOUND"
    assert r["context_status"] != "CONTEXT_MATCHED"      # no-match never certifies originality or context
