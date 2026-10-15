"""Capability registry. Availability is probed; nothing here fabricates a result."""

from __future__ import annotations

import os
import shutil


COST_RANK = {"none": 0, "local": 1, "metered": 2, "paid": 3}

_POLICY = {
    "timeout_seconds": 20,
    "retries": 1,
    "permanent_failure": "Do not retry a 4xx, a schema rejection, or a missing credential.",
}


def _probe_tesseract() -> tuple[bool, str]:
    path = shutil.which("tesseract")
    if not path:
        return False, "tesseract is not on PATH"
    return True, path


def _probe_ffmpeg() -> tuple[bool, str]:
    path = shutil.which("ffmpeg")
    if not path:
        return False, "ffmpeg is not on PATH"
    return True, path


def capability_registry() -> list[dict]:
    """Least-cost capable method first. Hosted adapters stay unavailable until configured."""
    tess_ok, tess_detail = _probe_tesseract()
    ff_ok, ff_detail = _probe_ffmpeg()
    reverse = (os.environ.get("DEFENCE_STUDIO_REVERSE_IMAGE_URL") or "").strip()
    search = (os.environ.get("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL") or "").strip()
    search_key = (os.environ.get("DEFENCE_STUDIO_DISCOVERY_SEARCH_API_KEY") or "").strip()
    openai = bool((os.environ.get("OPENAI_API_KEY") or os.environ.get("DEFENCE_STUDIO_OPENAI_API_KEY") or "").strip())
    try:
        from defence_studio.discovery.taxonomy import load_feed_configs
        rss_feeds = load_feed_configs()
        rss_ok, rss_detail = bool(rss_feeds), f"{len(rss_feeds)} curated RSS feed(s) in discovery_feeds.json"
    except Exception as exc:
        rss_ok, rss_detail = False, f"RSS config failed closed: {exc}"
    return [
        _row("claim_normalization", "stdlib-rules", "local", True, "Unicode NFKC and token rules",
             "none", "verified-local", "No model. Preserves hedges and negations."),
        _row("claim_extraction", "defence_studio.analysis.claims", "local", True,
             "Verbatim spans from already acquired text", "none", "verified-local",
             "Reuses F4. Does not paraphrase or assert truth."),
        _row("entity_date_match", "stdlib-rules", "local", True, "Shared tokens, dates, quantities",
             "none", "verified-local", "Overlap is not identity."),
        _row("source_independence", "content-hash-and-jaccard", "local", True, "Excerpt hash and near-copy text",
             "none", "verified-local", "Counts chains, not URLs."),
        _row("corroboration", "relationship-rules", "local", True, "Support, partial, contradiction",
             "none", "verified-local", "Missing evidence is not a disproof."),
        _row("web_search", "rss_feed", "local", rss_ok, rss_detail, "none",
             "configured-feeds" if rss_ok else "unconfigured",
             "Public RSS is the authorized live search path. Results are feed entries, not a web index."),
        _row("web_search", "configured_search_api", "hosted", bool(search and search_key),
             "Existing discovery search adapter" if search and search_key else
             "DEFENCE_STUDIO_DISCOVERY_SEARCH_API_URL and _KEY are both required; no network call is made.",
             "metered", "configured" if search and search_key else "unverified",
             "Hosted search stays unused until both URL and key are set."),
        _row("article_extraction", "defence_studio.analysis.normalize", "local", True,
             "HTML and subtitle normalization already in F4", "none", "verified-local",
             "Inaccessible sources stay errors."),
        _row("reverse_image_search", "configured-http", "hosted", bool(reverse),
             reverse or "DEFENCE_STUDIO_REVERSE_IMAGE_URL is unset",
             "metered", "unverified",
             "No match list is invented when the adapter is unset."),
        _row("ocr", "tesseract", "local", tess_ok, tess_detail, "local",
             "binary-present" if tess_ok else "unavailable",
             "Absence of OCR text is uncertainty, not a clean image."),
        _row("object_detection", "none-configured", "local", False,
             "No local detection weights are configured", "local", "unavailable",
             "A label is never treated as identification."),
        _row("image_similarity", "average-hash", "local", True, "8x8 average hash for PNG and PPM",
             "none", "verified-local", "Near-duplicate is not proof of the same event."),
        _row("video_frames", "ffmpeg", "local", ff_ok, ff_detail, "local",
             "binary-present" if ff_ok else "unavailable",
             "Upload sampling extracts up to three frames. timestamp_ms is ffmpeg showinfo pts_time when printed."),
        _row("metadata", "exif-and-png-text", "local", True, "JPEG EXIF and PNG tEXt",
             "none", "verified-local", "EXIF time is untrusted and may be missing."),
        _row("llm_reasoning", "openai-text-adapter", "hosted", openai,
             "Existing OpenAI adapter" if openai else "No OpenAI key configured",
             "paid", "unverified" if not openai else "configured-not-used-for-verdicts",
             "Verdicts do not call this provider. Script generation is a separate gated step."),
        _row("report", "canonical-json-and-markdown", "local", True, "Versioned package hash",
             "none", "verified-local", "Prior package versions are retained."),
    ]


def _readiness(capability, available, verification, detail) -> str:
    """UI vocabulary. LIVE_VERIFIED is only for local rules actually exercised; hosted/mock states stay explicit."""
    if capability == "object_detection":
        return "UNSUPPORTED"
    if verification == "verified-local":
        return "LIVE_VERIFIED" if available else "DEPENDENCY_MISSING"
    if available:
        return "READY"
    unset = any(token in detail for token in ("unset", "required", "No OpenAI"))
    return "NOT_CONFIGURED" if unset else "DEPENDENCY_MISSING"


def _row(capability, provider, execution, available, detail, cost, verification, limitation) -> dict:
    return {
        "readiness": _readiness(capability, available, verification, detail),
        "capability": capability,
        "provider": provider,
        "model": provider,
        "execution": execution,
        "inputs": ["text"] if "image" not in capability and capability not in {"ocr", "video_frames", "metadata", "image_similarity", "reverse_image_search"} else ["bytes", "text"],
        "languages": ["en", "ta", "und"],
        "available": available,
        "dependency": detail,
        "licence": "Project code and the named tool's own licence",
        "cost": cost,
        "timeout_seconds": _POLICY["timeout_seconds"],
        "retries": 0 if not available else _POLICY["retries"],
        "permanent_failure": _POLICY["permanent_failure"],
        "limitation": limitation,
        "fallback": "next cheaper available provider for the same capability, else an explicit unavailable state",
        "verification_status": verification,
    }


def select_provider(capability: str, registry: list[dict] | None = None) -> dict:
    """Pick the cheapest available provider. Unavailable is a result, not a silent substitute."""
    rows = [row for row in (registry if registry is not None else capability_registry()) if row["capability"] == capability]
    rows.sort(key=lambda row: COST_RANK.get(row["cost"], 9))
    if not rows:
        return {"capability": capability, "available": False, "provider": None,
                "verification_status": "unknown-capability", "limitation": "No provider is registered."}
    available = [row for row in rows if row["available"]]
    if not available:
        return {**rows[0], "available": False, "fallback_used": False, "reason": "UNAVAILABLE"}
    chosen = available[0]
    return {**chosen, "fallback_used": chosen is not rows[0]}
