"""Canonical JSON + SHA-256 helpers shared across contracts, store, and service.

Canonical form is sorted-key, compact, UTF-8 JSON with NaN/Infinity rejected,
so identical content hashes identically regardless of dict ordering — the basis
for deterministic artifact hashes and content-addressed storage.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Stream a file through SHA-256 without loading it entirely into memory."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            hasher.update(block)
    return hasher.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
