"""Discovery taxonomy: categories, regions, source classes and feed config.

Classification is deterministic and explainable (spec §6): a candidate's
category comes from, in order, the feed's configured category, a
provider-declared tag, then keyword rules; otherwise it stays
`UNCLASSIFIED`. An LLM never guesses a category in this phase. Each
assignment records its basis so the UI can show where it came from.

Feed configuration lives in a JSON file (``DEFENCE_STUDIO_DISCOVERY_CONFIG``
or ``backend/config/discovery_feeds.json``), not in code. An invalid file
fails closed with a clear message rather than silently dropping feeds.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = (
    "INDIAN_DEFENCE", "INTERNATIONAL_DEFENCE", "RUSSIA_UKRAINE",
    "PROCUREMENT_INDUSTRY", "NEIGHBOURHOOD", "GLOBAL_DEFENCE_TECH", "OTHER",
)
UNCLASSIFIED = "UNCLASSIFIED"
SOURCE_CLASSES = ("OFFICIAL", "INDEPENDENT_REPORTING", "EXPERT_ANALYSIS", "COMPANY_RELEASE", "UNKNOWN")
REGIONS = ("INDIA", "PAKISTAN", "CHINA", "RUSSIA", "UKRAINE", "USA", "EUROPE", "MIDDLE_EAST", "ASIA_PACIFIC", "GLOBAL")

BASIS_FEED_CONFIG = "FEED_CONFIG"
BASIS_PROVIDER_TAG = "PROVIDER_TAG"
BASIS_KEYWORD_RULE = "KEYWORD_RULE"
BASIS_UNCLASSIFIED = "UNCLASSIFIED"

# Ordered: first matching rule wins for category; regions may match several.
_CATEGORY_RULES = (
    ("RUSSIA_UKRAINE", ("ukraine", "ukrainian", "kyiv", "kiev", "russia", "russian", "kremlin", "zelensky", "putin", "donbas")),
    ("INDIAN_DEFENCE", ("indian army", "indian navy", "indian air force", "iaf", "drdo", "hal ", "ministry of defence", "rajnath", "india's", "indian defence", "indian armed", "agniveer", "tejas", "brahmos", "s-400 india")),
    ("NEIGHBOURHOOD", ("pakistan", "china border", "lac ", "line of control", "bangladesh", "sri lanka", "nepal", "myanmar", "pla ", "south china sea")),
    ("PROCUREMENT_INDUSTRY", ("procurement", "contract", "tender", "acquisition", "defence deal", "defense deal", "order for", "shipbuilding")),
    ("GLOBAL_DEFENCE_TECH", ("drone", "hypersonic", "missile", "fighter", "stealth", "radar", "ai-enabled", "satellite", "cyber", "laser")),
    ("INTERNATIONAL_DEFENCE", ("nato", "pentagon", "u.s. army", "us army", "royal navy", "defense", "defence", "military", "armed forces")),
)
_REGION_RULES = (
    ("INDIA", ("india", "indian", "delhi", "drdo", "iaf")),
    ("PAKISTAN", ("pakistan", "pakistani", "islamabad")),
    ("CHINA", ("china", "chinese", "beijing", "pla ")),
    ("RUSSIA", ("russia", "russian", "kremlin", "moscow")),
    ("UKRAINE", ("ukraine", "ukrainian", "kyiv", "kiev")),
    ("USA", ("pentagon", "u.s.", "united states", "american", "us army", "us navy")),
    ("EUROPE", ("nato", "european", "eu ", "germany", "france", "britain", "uk ", "poland")),
    ("MIDDLE_EAST", ("israel", "iran", "gaza", "saudi", "yemen", "houthi", "syria")),
    ("ASIA_PACIFIC", ("japan", "taiwan", "korea", "australia", "indo-pacific", "south china sea")),
)


@dataclass(frozen=True)
class FeedConfig:
    url: str
    language: str = "en"
    category: str | None = None
    source_class: str = "UNKNOWN"
    publisher: str | None = None
    region: str | None = None


class DiscoveryConfigError(ValueError):
    """The discovery feed configuration is malformed (fails closed)."""


def default_config_path() -> Path:
    override = os.environ.get("DEFENCE_STUDIO_DISCOVERY_CONFIG")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "config" / "discovery_feeds.json"


def load_feed_configs(path: Path | None = None) -> list[FeedConfig]:
    """Load curated feeds. A missing default file means "no curated feeds"
    (the env-var path still works); a present-but-invalid file raises."""
    path = path or default_config_path()
    if not path.exists():
        if os.environ.get("DEFENCE_STUDIO_DISCOVERY_CONFIG"):
            raise DiscoveryConfigError(f"Discovery config {path} does not exist")
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DiscoveryConfigError(f"Discovery config {path} is unreadable: {exc}") from None
    feeds = payload.get("feeds") if isinstance(payload, dict) else None
    if not isinstance(feeds, list):
        raise DiscoveryConfigError("Discovery config must be an object with a 'feeds' list")
    out: list[FeedConfig] = []
    for index, raw in enumerate(feeds):
        if not isinstance(raw, dict) or not isinstance(raw.get("url"), str) or not raw["url"].startswith(("http://", "https://")):
            raise DiscoveryConfigError(f"feeds[{index}] needs an http(s) 'url'")
        category = raw.get("category")
        if category is not None and category not in CATEGORIES:
            raise DiscoveryConfigError(f"feeds[{index}].category {category!r} is not a known category")
        source_class = raw.get("source_class", "UNKNOWN")
        if source_class not in SOURCE_CLASSES:
            raise DiscoveryConfigError(f"feeds[{index}].source_class {source_class!r} is not a known source class")
        region = raw.get("region")
        if region is not None and region not in REGIONS:
            raise DiscoveryConfigError(f"feeds[{index}].region {region!r} is not a known region")
        out.append(FeedConfig(
            url=raw["url"], language=str(raw.get("language", "en")), category=category,
            source_class=source_class, publisher=raw.get("publisher"), region=region,
        ))
    return out


def _contains(text: str, needles) -> bool:
    return any(n in text for n in needles)


def classify(title: str | None, snippet: str | None, feed_category: str | None = None,
             provider_tag: str | None = None) -> tuple[str, str]:
    """Return (category, basis)."""
    if feed_category in CATEGORIES:
        return feed_category, BASIS_FEED_CONFIG
    if provider_tag in CATEGORIES:
        return provider_tag, BASIS_PROVIDER_TAG
    text = f" {title or ''} {snippet or ''} ".lower()
    text = re.sub(r"\s+", " ", text)
    for category, needles in _CATEGORY_RULES:
        if _contains(text, needles):
            return category, BASIS_KEYWORD_RULE
    return UNCLASSIFIED, BASIS_UNCLASSIFIED


def classify_regions(title: str | None, snippet: str | None, feed_region: str | None = None) -> tuple[list[str], str]:
    if feed_region in REGIONS:
        return [feed_region], BASIS_FEED_CONFIG
    text = f" {title or ''} {snippet or ''} ".lower()
    found = [region for region, needles in _REGION_RULES if _contains(text, needles)]
    if found:
        return found, BASIS_KEYWORD_RULE
    return [], BASIS_UNCLASSIFIED


def taxonomy_payload() -> dict:
    return {
        "categories": list(CATEGORIES) + [UNCLASSIFIED],
        "regions": list(REGIONS),
        "source_classes": list(SOURCE_CLASSES),
        "languages": ["en", "ta"],
    }
