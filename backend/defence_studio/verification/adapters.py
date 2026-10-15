"""Replaceable media-analysis adapters: reverse-image lookup, OCR, object detection.

Nothing here invents a result. Every call returns an explicit status; "no match" is
never evidence of originality and OCR/detector output is untrusted, not verification.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

NOT_CONFIGURED = "NOT_CONFIGURED"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
NO_MATCH_FOUND = "NO_MATCH_FOUND"
MATCHES_FOUND = "MATCHES_FOUND"
INVALID_RESPONSE = "INVALID_PROVIDER_RESPONSE"

REVERSE_TIMEOUT_S = 20
REVERSE_RETRIES = 1
MAX_MATCHES = 25
MAX_RESPONSE_BYTES = 1_000_000

# Documented request/response contract for the configured endpoint. The vendor's own
# API is unknown, so a gateway must speak this shape:
#   POST <DEFENCE_STUDIO_REVERSE_IMAGE_URL>   Authorization: Bearer <..._KEY> (optional)
#   {"sha256": "...", "average_hash": "...", "image_base64": "..." (only if ..._SEND_IMAGE=1)}
#   200 {"matches": [{"url": "https://..", "title": "..", "published_at": "ISO-8601|null"}]}


def _config() -> dict:
    return {
        "url": (os.environ.get("DEFENCE_STUDIO_REVERSE_IMAGE_URL") or "").strip(),
        "key": (os.environ.get("DEFENCE_STUDIO_REVERSE_IMAGE_KEY") or "").strip(),
        "send_image": os.environ.get("DEFENCE_STUDIO_REVERSE_IMAGE_SEND_IMAGE") == "1",
    }


def _default_transport(url: str, headers: dict, body: bytes, timeout: float):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (scheme validated by caller)
            return resp.status, resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def _parse_matches(raw: bytes):
    if len(raw) > MAX_RESPONSE_BYTES:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    items = doc.get("matches") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return None
    out = []
    for item in items[:MAX_MATCHES]:
        if not isinstance(item, dict):
            return None
        url = item.get("url")
        parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue                                   # unusable match is dropped, never repaired
        published = item.get("published_at")
        out.append({"url": url, "title": str(item.get("title") or "")[:300],
                    "published_at": published if isinstance(published, str) else None,
                    "published_at_trust": "provider-reported" if isinstance(published, str) else "absent"})
    return out


def reverse_image_lookup(data: bytes, digest: str, visual_hash, *, transport=None, sleep=time.sleep) -> dict:
    """Explicit live call. `transport(url, headers, body, timeout) -> (status, bytes)` is injectable for mocks."""
    cfg = _config()
    base = {"query": digest, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "matches": None, "available": False}
    if not cfg["url"]:
        return {**base, "status": NOT_CONFIGURED, "reason": "UNAVAILABLE_NOT_CONFIGURED"}
    parsed = urllib.parse.urlparse(cfg["url"])
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
        return {**base, "status": NOT_CONFIGURED, "reason": "INSECURE_OR_INVALID_URL",
                "detail": "Reverse-image URL must be https (http only for loopback)."}
    payload = {"sha256": digest, "average_hash": visual_hash}
    if cfg["send_image"]:
        payload["image_base64"] = base64.b64encode(data).decode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg["key"]:
        headers["Authorization"] = f"Bearer {cfg['key']}"
    send = transport or _default_transport
    detail = ""
    for attempt in range(REVERSE_RETRIES + 1):
        try:
            status, raw = send(cfg["url"], headers, json.dumps(payload).encode(), REVERSE_TIMEOUT_S)
        except Exception as exc:                       # network/timeout: retryable
            detail = type(exc).__name__
            if attempt < REVERSE_RETRIES:
                sleep(0.5)
            continue
        if status in (401, 403):
            return {**base, "status": PROVIDER_UNAVAILABLE, "reason": "AUTH_REJECTED", "detail": f"HTTP {status}"}
        if 400 <= status < 500:
            return {**base, "status": PROVIDER_UNAVAILABLE, "reason": "REQUEST_REJECTED", "detail": f"HTTP {status}"}
        if status >= 500:
            detail = f"HTTP {status}"
            if attempt < REVERSE_RETRIES:
                sleep(0.5)
            continue
        matches = _parse_matches(raw)
        if matches is None:
            return {**base, "status": INVALID_RESPONSE, "reason": "SCHEMA_REJECTED"}
        return {**base, "available": True, "matches": matches,
                "status": MATCHES_FOUND if matches else NO_MATCH_FOUND,
                "note": "No match is not evidence the image is original; it only means this provider returned none."}
    return {**base, "status": PROVIDER_UNAVAILABLE, "reason": "UNREACHABLE", "detail": detail}


def ocr_image(data: bytes, *, lang: str = "eng") -> dict:
    exe = shutil.which("tesseract")
    if not exe:
        return {"available": False, "status": "DEPENDENCY_MISSING", "text": None,
                "reason": "tesseract is not installed; no text was invented from the image."}
    suffix = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in" + suffix)
        with open(src, "wb") as fh:
            fh.write(data)
        try:
            run = subprocess.run([exe, src, "stdout", "-l", lang, "tsv"], capture_output=True, timeout=60, text=True)
        except Exception as exc:
            return {"available": False, "status": PROVIDER_UNAVAILABLE, "text": None, "reason": type(exc).__name__}
    if run.returncode != 0:
        return {"available": False, "status": PROVIDER_UNAVAILABLE, "text": None, "reason": run.stderr[:200]}
    words, confs = [], []
    for line in run.stdout.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 12 and cols[11].strip():
            words.append(cols[11].strip())
            try:
                c = float(cols[10])
                if c >= 0:
                    confs.append(c)
            except ValueError:
                pass
    return {"available": True, "status": "READY", "text": " ".join(words) or None, "language": lang,
            "mean_confidence": round(sum(confs) / len(confs), 1) if confs else None,
            "trust": "untrusted-evidence", "note": "OCR text is not proof that an event occurred."}


def detect_objects(data: bytes) -> dict:
    """Detector slot. No weights are bundled or verified, so no label is ever produced."""
    return {"available": False, "status": "UNSUPPORTED", "labels": None,
            "reason": "No object-detection model with verified weights/licence is configured.",
            "note": "Detector output, when added, is a model detection, not a confirmed finding."}
