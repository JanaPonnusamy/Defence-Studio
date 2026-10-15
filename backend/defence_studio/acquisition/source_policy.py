"""Controlled source registry: only allowlisted domains / URL patterns may be fetched.

The registry is data (``config/source_policy.json``), not code, so the project owner maintains
it. ``SourcePolicy.check`` is called by :class:`UrlGuard` for the initial URL and again for every
redirect hop, so a redirect to a non-allowlisted destination is refused exactly like a private
address. An allowlisted domain says where we may *fetch* from; it never says a statement is true.

Not implemented here (documented limitation): robots.txt and per-site rate-limit negotiation.
Fetch volume is already bounded by the acquisition limits (max sources, bytes, redirects, timeout).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

POLICY_SCHEMA = "defence-studio.source-policy.v1"
_DEFAULT_FILE = Path(__file__).resolve().parents[2] / "config" / "source_policy.json"
CATEGORIES = frozenset({"GOVERNMENT_OFFICIAL", "OFFICIAL_TECHNICAL", "RESEARCH_INSTITUTION",
                        "INDEPENDENT_REPORTING", "MANUFACTURER"})
QUANT_TRUST = frozenset({"CORROBORATION_REQUIRED", "ATTRIBUTED_ONLY", "NOT_TRUSTED"})


class SourcePolicyError(ValueError):
    """Registry file is invalid or a URL violates it."""


@dataclass(frozen=True)
class SourceEntry:
    id: str
    domain: str
    include_subdomains: bool
    patterns: tuple
    category: str
    publisher: str
    independence_group: str
    descriptive_trusted: bool
    quantitative_trust: str
    enabled: bool
    supported_subjects: tuple = ()          # subject categories this source is configured for ([] = any)
    allowed_content_types: tuple = ()       # media types permitted for this source ([] = any)
    exclusions: tuple = ()                  # compiled path regexes that are never fetched from this source
    acquisition_limits: dict = field(default_factory=dict)   # e.g. {"max_bytes": 2000000}
    provenance_requirements: tuple = ()     # e.g. ("published_at",): recorded as UNKNOWN when absent

    def matches_host(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return host == self.domain or (self.include_subdomains and host.endswith("." + self.domain))


@dataclass(frozen=True)
class SourcePolicy:
    version: str
    entries: tuple = field(default_factory=tuple)

    # -- loading -------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "SourcePolicy":
        if data.get("version") != POLICY_SCHEMA:
            raise SourcePolicyError(f"source policy version must be {POLICY_SCHEMA}")
        entries, seen = [], set()
        for raw in data.get("sources", []):
            try:
                sid, domain = str(raw["id"]), str(raw["domain"]).lower().strip()
                category = raw["category"]
                trust = raw.get("trust", {})
                quant = trust.get("quantitative_claims", "CORROBORATION_REQUIRED")
                patterns = tuple(re.compile(p) for p in raw.get("url_patterns", ["^/"]))
                exclusions = tuple(re.compile(p) for p in raw.get("exclusions", []))
                for key in ("supported_subjects", "allowed_content_types", "exclusions", "provenance_requirements"):
                    if not isinstance(raw.get(key, []), list):
                        raise TypeError(f"{key} must be a list")
                limits = raw.get("acquisition_limits", {})
                if not isinstance(limits, dict) or any(
                        (not isinstance(v, int) or isinstance(v, bool) or v < 1) for v in limits.values()):
                    raise TypeError("acquisition_limits must map names to positive integers")
            except (KeyError, re.error, TypeError) as exc:
                raise SourcePolicyError(f"invalid source entry: {exc}") from None
            if not domain or "/" in domain or ":" in domain or domain.startswith("."):
                raise SourcePolicyError(f"{sid}: domain must be a bare host name")
            if category not in CATEGORIES:
                raise SourcePolicyError(f"{sid}: unknown category {category!r}")
            if quant not in QUANT_TRUST:
                raise SourcePolicyError(f"{sid}: unknown quantitative_claims trust {quant!r}")
            if sid in seen:
                raise SourcePolicyError(f"duplicate source id {sid}")
            seen.add(sid)
            entries.append(SourceEntry(
                id=sid, domain=domain, include_subdomains=bool(raw.get("include_subdomains", False)),
                patterns=patterns, category=category, publisher=str(raw.get("publisher") or ""),
                independence_group=str(raw.get("independence_group") or sid),
                descriptive_trusted=bool(trust.get("descriptive_claims", False)),
                quantitative_trust=quant, enabled=bool(raw.get("enabled", False)),
                supported_subjects=tuple(str(x) for x in raw.get("supported_subjects", [])),
                allowed_content_types=tuple(str(x).lower() for x in raw.get("allowed_content_types", [])),
                exclusions=exclusions,
                acquisition_limits=dict(raw.get("acquisition_limits", {})),
                provenance_requirements=tuple(str(x) for x in raw.get("provenance_requirements", []))))
        return cls(version=data["version"], entries=tuple(entries))

    @classmethod
    def load(cls, path: Path | str | None = None) -> "SourcePolicy":
        file = Path(path or os.environ.get("DEFENCE_STUDIO_SOURCE_POLICY") or _DEFAULT_FILE)
        try:
            return cls.from_dict(json.loads(file.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            if isinstance(exc, SourcePolicyError):
                raise
            raise SourcePolicyError(f"cannot read source policy {file}: {exc}") from None

    # -- matching ------------------------------------------------------------
    def entry_for(self, url: str) -> SourceEntry | None:
        """Return the enabled entry that permits ``url`` or None (never raises)."""
        try:
            parts = urlsplit(url)
        except ValueError:
            return None
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username:
            return None
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        for e in self.entries:
            if e.enabled and e.matches_host(parts.hostname) and any(p.match(target) for p in e.patterns) \
                    and not any(x.search(target) for x in e.exclusions):
                return e
        return None

    def require(self, url: str) -> SourceEntry:
        entry = self.entry_for(url)
        if entry is None:
            host = urlsplit(url).hostname or "(none)"
            raise SourcePolicyError(
                f"URL host/path is not permitted by the source registry (host {host}); "
                "add an enabled entry to config/source_policy.json to allow it")
        return entry

    def to_public(self) -> dict:
        return {"version": self.version, "sources": [
            {"id": e.id, "domain": e.domain, "category": e.category, "enabled": e.enabled,
             "publisher": e.publisher, "independence_group": e.independence_group,
             "quantitative_trust": e.quantitative_trust, "supported_subjects": list(e.supported_subjects),
             "allowed_content_types": list(e.allowed_content_types),
             "acquisition_limits": e.acquisition_limits} for e in self.entries]}


def default_policy() -> SourcePolicy | None:
    """The registry used by production handlers.

    ``DEFENCE_STUDIO_SOURCE_POLICY=off`` (owner opt-out, development/tests only) returns None."""
    if (os.environ.get("DEFENCE_STUDIO_SOURCE_POLICY") or "").lower() == "off":
        return None
    return SourcePolicy.load()
